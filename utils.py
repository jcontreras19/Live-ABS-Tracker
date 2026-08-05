"""
Shared utilities for the ABS Shadow Zone Live Tracker.
Used by both app.py (Streamlit UI) and daily_refresh.py (health checks).
"""
import duckdb
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta

# ── Constants (match the finalized project methodology) ──────────────────────
PLATE_HALF = 0.833   # half plate width + ball radius, ft
SHADOW_ZONE = 0.242  # Statcast official: one ball-width (2.9 in) beyond zone edge

DB_PATH = os.environ.get("FFDB_PATH", "./baseball.duckdb")
STATUS_PATH = os.environ.get("FFDB_STATUS_PATH", "./refresh_status.json")


def get_connection(read_only=True):
    """Standard connection helper. Always read-only from the app side."""
    return duckdb.connect(DB_PATH, read_only=read_only)


def load_status():
    """Load the refresh status file written by daily_refresh.py.
    Returns a dict with last_refresh_time, success, row_counts, etc.
    Falls back to a safe default if the file doesn't exist yet."""
    if not os.path.exists(STATUS_PATH):
        return {
            "last_refresh_time": None,
            "last_success": None,
            "seasons": {},
            "history": []
        }
    with open(STATUS_PATH, "r") as f:
        return json.load(f)


def add_zone_flags(df):
    """Add signed distance-to-edge and shadow zone flags to a pitch DataFrame.
    Identical logic to the core project analysis -- keep this in sync if the
    shadow zone definition ever changes."""
    df = df.copy()
    df['dist_left']   = df['p_x'] - (-PLATE_HALF)
    df['dist_right']  = PLATE_HALF - df['p_x']
    df['dist_bottom'] = df['p_z'] - df['strike_zone_bottom']
    df['dist_top']    = df['strike_zone_top'] - df['p_z']
    df['in_zone'] = (
        (df['dist_left']   >= 0) &
        (df['dist_right']  >= 0) &
        (df['dist_bottom'] >= 0) &
        (df['dist_top']    >= 0)
    )
    df['dist_to_edge'] = df[['dist_left', 'dist_right', 'dist_bottom', 'dist_top']].min(axis=1)
    df['in_shadow'] = df['dist_to_edge'].abs() <= SHADOW_ZONE
    return df


def get_latest_game_date(conn):
    """Returns the most recent COMPLETED game date present in the database.
    MLB's schedule API returns the full remaining season upfront, including
    games that haven't been played yet (status_code = 'S' for Scheduled) or
    were postponed ('P', 'PW'). Only status_code = 'F' (Final) reflects an
    actually-completed game, so we filter to that explicitly -- otherwise
    MAX(date_time) picks up a future scheduled game instead of yesterday's
    real action."""
    result = conn.execute("""
        SELECT MAX(CAST(date_time AS DATE)) as latest_date
        FROM games
        WHERE game_type = 'R'
          AND status_code = 'F'
    """).fetchone()
    return result[0] if result else None


def get_called_pitches(conn, start_date=None, end_date=None, season=None):
    """Pull called pitches (code IN C, B), excluding overturned calls,
    optionally filtered by date range or season."""
    filters = ["e.is_pitch = true", "g.game_type = 'R'", "e.code IN ('C', 'B')",
               "e.p_x IS NOT NULL", "e.p_z IS NOT NULL",
               "e.strike_zone_top IS NOT NULL", "e.strike_zone_bottom IS NOT NULL",
               "(e.is_overturned IS NULL OR e.is_overturned = false)"]

    if season is not None:
        filters.append(f"g.season = {season}")
    if start_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) >= '{start_date}'")
    if end_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) <= '{end_date}'")

    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
            e.game_id, e.p_x, e.p_z, e.strike_zone_top, e.strike_zone_bottom,
            e.code, e.pre_balls, e.pre_strikes, e.bat_side, e.pitch_hand,
            e.pitch_type, e.has_review, e.is_overturned,
            g.season, CAST(g.date_time AS DATE) as game_date
        FROM events e
        JOIN games g ON g.game_id = e.game_id
        WHERE {where_clause}
    """
    df = conn.execute(query).df()
    df['is_called_strike'] = (df['code'] == 'C').astype(int)
    return add_zone_flags(df)


def get_challenge_leaderboard(conn, start_date=None, end_date=None, role='batter', limit=10):
    """Players with the most ABS challenges, by batter or pitcher role.
    role must be 'batter' or 'pitcher'."""
    assert role in ('batter', 'pitcher')
    filters = ["g.game_type = 'R'", "e.is_pitch = true", "e.has_review = true"]
    if start_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) >= '{start_date}'")
    if end_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) <= '{end_date}'")
    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
            p.full_name,
            COUNT(*) as challenges,
            SUM(CASE WHEN e.is_overturned THEN 1 ELSE 0 END) as overturned,
            ROUND(100.0 * SUM(CASE WHEN e.is_overturned THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_overturned
        FROM events e
        JOIN games g ON g.game_id = e.game_id
        JOIN ref.players p ON p.id = e.{role}
        WHERE {where_clause}
        GROUP BY p.full_name
        ORDER BY challenges DESC
        LIMIT {limit}
    """
    return conn.execute(query).df()


def get_hot_umpire_games(conn, start_date=None, end_date=None, limit=5):
    """Games with the most overturned challenges -- a proxy for 'roughest night
    for the home plate umpire', since umpire ID itself isn't populated in FFDB."""
    filters = ["g.game_type = 'R'", "e.is_pitch = true"]
    if start_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) >= '{start_date}'")
    if end_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) <= '{end_date}'")
    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
            g.game_id,
            CAST(g.date_time AS DATE) as game_date,
            ht.name as home_team,
            awt.name as away_team,
            SUM(CASE WHEN e.has_review THEN 1 ELSE 0 END) as challenges,
            SUM(CASE WHEN e.is_overturned THEN 1 ELSE 0 END) as overturned
        FROM events e
        JOIN games g ON g.game_id = e.game_id
        LEFT JOIN ref.teams ht ON ht.id = g.home_team_id
        LEFT JOIN ref.teams awt ON awt.id = g.away_team_id
        WHERE {where_clause}
        GROUP BY g.game_id, g.date_time, ht.name, awt.name
        HAVING SUM(CASE WHEN e.is_overturned THEN 1 ELSE 0 END) > 0
        ORDER BY overturned DESC
        LIMIT {limit}
    """
    return conn.execute(query).df()


def get_challenge_summary(conn, start_date=None, end_date=None):
    """Summarize ABS challenge activity over a date range."""
    filters = ["g.game_type = 'R'", "e.is_pitch = true"]
    if start_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) >= '{start_date}'")
    if end_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) <= '{end_date}'")
    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
            SUM(CASE WHEN e.has_review THEN 1 ELSE 0 END) as total_challenges,
            SUM(CASE WHEN e.is_overturned THEN 1 ELSE 0 END) as overturned
        FROM events e
        JOIN games g ON g.game_id = e.game_id
        WHERE {where_clause}
    """
    result = conn.execute(query).fetchone()
    total = result[0] or 0
    overturned = result[1] or 0
    pct = round(100 * overturned / total, 2) if total > 0 else 0.0
    return {"total_challenges": int(total), "overturned": int(overturned), "pct_overturned": pct}


def get_challenge_of_the_day(conn, target_date):
    """The single most 'controversial' overturned call from a given day --
    defined as the overturned pitch whose location was closest to the
    absolute center of the shadow zone (dist_to_edge nearest 0), since that's
    the most genuinely borderline, camera-flips-the-call kind of pitch."""
    query = f"""
        SELECT
            e.game_id, e.p_x, e.p_z, e.strike_zone_top, e.strike_zone_bottom,
            e.code, bp.full_name as batter_name, pp.full_name as pitcher_name,
            CAST(g.date_time AS DATE) as game_date
        FROM events e
        JOIN games g ON g.game_id = e.game_id
        LEFT JOIN ref.players bp ON bp.id = e.batter
        LEFT JOIN ref.players pp ON pp.id = e.pitcher
        WHERE g.game_type = 'R'
          AND e.is_pitch = true
          AND e.is_overturned = true
          AND CAST(g.date_time AS DATE) = '{target_date}'
          AND e.p_x IS NOT NULL AND e.p_z IS NOT NULL
          AND e.strike_zone_top IS NOT NULL AND e.strike_zone_bottom IS NOT NULL
    """
    df = conn.execute(query).df()
    if len(df) == 0:
        return None
    df = add_zone_flags(df)
    df['abs_dist'] = df['dist_to_edge'].abs()
    return df.sort_values('abs_dist').iloc[0].to_dict()
    """Summarize ABS challenge activity over a date range."""
    filters = ["g.game_type = 'R'", "e.is_pitch = true"]
    if start_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) >= '{start_date}'")
    if end_date is not None:
        filters.append(f"CAST(g.date_time AS DATE) <= '{end_date}'")
    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
            SUM(CASE WHEN e.has_review THEN 1 ELSE 0 END) as total_challenges,
            SUM(CASE WHEN e.is_overturned THEN 1 ELSE 0 END) as overturned
        FROM events e
        JOIN games g ON g.game_id = e.game_id
        WHERE {where_clause}
    """
    result = conn.execute(query).fetchone()
    total = result[0] or 0
    overturned = result[1] or 0
    pct = round(100 * overturned / total, 2) if total > 0 else 0.0
    return {"total_challenges": int(total), "overturned": int(overturned), "pct_overturned": pct}
