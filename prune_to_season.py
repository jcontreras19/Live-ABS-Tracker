#!/usr/bin/env python3
"""
prune_to_season.py — Shrinks baseball_live.duckdb down to just the current
season's data. Run this AFTER materialize.py (it needs physical tables, not
views, to delete from).

Why: after materializing, the database contained all seasons ever pulled
into data/processed/ (2024-2026), which is unnecessary bulk for a "live,
current" tracker and blew past GitHub's 100MB blob size limit. The app's
own features (yesterday's games, 7-day rolling rate, season-to-date stats)
only ever need the current season anyway.
"""
import duckdb
import os

DB_PATH = os.environ.get("FFDB_LIVE_PATH", "./baseball_live.duckdb")


def prune_to_current_season(db_path):
    conn = duckdb.connect(db_path, read_only=False)

    current_season = conn.execute("SELECT MAX(season) FROM games").fetchone()[0]
    print(f"Pruning database to season {current_season} only...")

    tables_with_game_id = conn.execute("""
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'game_id' AND table_schema = 'main'
    """).fetchall()

    keep_ids = f"(SELECT game_id FROM games WHERE season = {current_season})"

    for (t,) in tables_with_game_id:
        if t == "games":
            continue
        before = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        conn.execute(f'DELETE FROM "{t}" WHERE game_id NOT IN {keep_ids}')
        after = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f"  {t}: {before:,} -> {after:,} rows")

    before_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    conn.execute(f"DELETE FROM games WHERE season != {current_season}")
    after_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    print(f"  games: {before_games:,} -> {after_games:,} rows")

    print("Reclaiming disk space (CHECKPOINT)...")
    conn.execute("CHECKPOINT")
    conn.close()

    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"Done. Final file size: {size_mb:.1f} MB")


if __name__ == "__main__":
    prune_to_current_season(DB_PATH)
