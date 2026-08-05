#!/usr/bin/env python3
"""
build_slim_db.py — Rebuilds baseball_live.duckdb containing ONLY what the
Streamlit app actually queries: a handful of `events` columns, `games`, and
the small `ref.players` / `ref.teams` tables.

Why: the materialized database was 654MB even after pruning to one season,
because `events` has ~90 columns (the app uses ~15) and the database also
carried 8 large per-play tables (play_credits, runners, plays, player_logs,
etc.) that the app never queries at all. This rebuilds from scratch with
only the necessary data, which should shrink the file dramatically.

Run this from inside your ffdb folder, where baseball_live.duckdb (with its
data/processed Parquet still sitting nearby OR already materialized as
physical tables) currently lives.
"""
import duckdb
import os

SRC_PATH = "./baseball_live.duckdb"
OUT_PATH = "./baseball_live_slim.duckdb"

if os.path.exists(OUT_PATH):
    os.remove(OUT_PATH)

print(f"Attaching source: {SRC_PATH}")
conn = duckdb.connect(OUT_PATH)
conn.execute(f"ATTACH '{SRC_PATH}' AS src (READ_ONLY)")

print("Building slim games table (regular season only)...")
conn.execute("""
    CREATE TABLE games AS
    SELECT * FROM src.games WHERE game_type = 'R'
""")

print("Building slim events table (only columns the app uses)...")
conn.execute("""
    CREATE TABLE events AS
    SELECT
        game_id, p_x, p_z, strike_zone_top, strike_zone_bottom, code,
        pre_balls, pre_strikes, bat_side, pitch_hand, pitch_type,
        has_review, is_overturned, is_pitch, batter, pitcher
    FROM src.events
    WHERE game_id IN (SELECT game_id FROM games)
""")

print("Copying ref.players and ref.teams (name lookups)...")
conn.execute("CREATE SCHEMA ref")
conn.execute("CREATE TABLE ref.players AS SELECT id, full_name FROM src.ref.players")
conn.execute("CREATE TABLE ref.teams AS SELECT id, name FROM src.ref.teams")

conn.execute("DETACH src")
conn.execute("CHECKPOINT")
conn.close()

size_mb = os.path.getsize(OUT_PATH) / (1024 * 1024)
print(f"\nDone. Slim database size: {size_mb:.1f} MB")
print(f"Saved to: {OUT_PATH}")
print("\nIf this looks good, replace the original:")
print(f"  mv {OUT_PATH} baseball_live.duckdb")
