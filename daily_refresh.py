#!/usr/bin/env python3
"""
daily_refresh.py — Nightly FFDB refresh with data-longevity safeguards.

Design goals:
1. Never let a bad refresh silently corrupt the live database.
2. Keep a rolling window of backups so any bad night can be rolled back.
3. Log every run (success or failure) to a human-readable status file the
   Streamlit app can display ("Last updated: ...").
4. Prevent two refreshes from running concurrently (lock file).

Intended usage: run once daily via cron, e.g.
    0 9 * * * /path/to/venv/bin/python /path/to/daily_refresh.py >> /path/to/refresh.log 2>&1

Environment variables (set these, or edit the DEFAULTS below):
    FFDB_DIR          - path to the cloned ffdb repo (where baseball.duckdb lives)
    FFDB_BACKUP_DIR    - where to store rotating backups (default: FFDB_DIR/backups)
    FFDB_KEEP_BACKUPS  - how many backups to retain (default: 14, ~2 weeks)
"""
import os
import sys
import json
import shutil
import subprocess
import fcntl
from datetime import datetime, timedelta

# ── Configuration ──────────────────────────────────────────────────────────────
FFDB_DIR = os.environ.get("FFDB_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(FFDB_DIR, "baseball_live.duckdb")
BACKUP_DIR = os.environ.get("FFDB_BACKUP_DIR", os.path.join(FFDB_DIR, "backups"))
KEEP_BACKUPS = int(os.environ.get("FFDB_KEEP_BACKUPS", "14"))
STATUS_PATH = os.path.join(FFDB_DIR, "refresh_status.json")
LOCK_PATH = os.path.join(FFDB_DIR, ".refresh.lock")


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def load_status():
    if os.path.exists(STATUS_PATH):
        with open(STATUS_PATH, "r") as f:
            return json.load(f)
    return {"last_refresh_time": None, "last_success": None, "seasons": {}, "history": []}


def save_status(status):
    # keep history bounded so the file doesn't grow forever
    status["history"] = status["history"][-60:]
    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2, default=str)


def get_row_counts():
    """Quick sanity-check query: pitch counts per season.
    Used before/after refresh to catch a refresh that silently lost data.
    On a fresh checkout (e.g. a new GitHub Actions runner), the committed
    .duckdb file's views point to relative Parquet paths that don't exist
    until `ffdb refresh` rebuilds them locally -- so a failure here before
    the refresh step is expected and should be treated as "no prior data",
    not a fatal error."""
    import duckdb
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
        result = conn.execute("""
            SELECT g.season, COUNT(*) as n
            FROM events e JOIN games g ON g.game_id = e.game_id
            WHERE g.game_type = 'R' AND e.is_pitch = true
            GROUP BY g.season ORDER BY g.season
        """).fetchall()
        conn.close()
        return {int(season): int(n) for season, n in result}
    except Exception as e:
        log(f"Could not read row counts from {DB_PATH} ({e}) -- treating as no prior data")
        return {}


def backup_database():
    """Copy the current database to a timestamped backup before touching it."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"baseball_{timestamp}.duckdb")
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, backup_path)
        log(f"Backed up database to {backup_path}")
        return backup_path
    else:
        log("No existing database found to back up (first run?)")
        return None


def rotate_backups():
    """Keep only the most recent KEEP_BACKUPS backup files."""
    if not os.path.exists(BACKUP_DIR):
        return
    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith("baseball_") and f.endswith(".duckdb")]
    )
    excess = len(backups) - KEEP_BACKUPS
    if excess > 0:
        for old_backup in backups[:excess]:
            os.remove(os.path.join(BACKUP_DIR, old_backup))
            log(f"Removed old backup: {old_backup}")


def restore_backup(backup_path):
    """Roll back to a known-good backup if the refresh fails health checks."""
    if backup_path and os.path.exists(backup_path):
        shutil.copy2(backup_path, DB_PATH)
        log(f"ROLLED BACK database to {backup_path}")
        return True
    log("No backup available to roll back to — leaving database as-is")
    return False


def materialize_views():
    """Convert views (pointing at external Parquet) into physical tables, so
    the committed .duckdb file is fully self-contained and works when deployed
    anywhere -- not just in an environment with the matching data/processed/
    Parquet files sitting alongside it."""
    import duckdb
    conn = duckdb.connect(DB_PATH, read_only=False)
    views = conn.execute("""
        SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal
    """).fetchall()
    if not views:
        conn.close()
        return
    log(f"Materializing {len(views)} views into physical tables...")
    for schema, view in views:
        qualified = f'"{schema}"."{view}"' if schema != "main" else f'"{view}"'
        tmp_qualified = f'"{schema}"."__tmp_{view}"' if schema != "main" else f'"__tmp_{view}"'
        conn.execute(f"CREATE TABLE {tmp_qualified} AS SELECT * FROM {qualified}")
        conn.execute(f"DROP VIEW {qualified}")
        conn.execute(f'ALTER TABLE {tmp_qualified} RENAME TO "{view}"')
    conn.close()
    log("Materialization complete -- database is now self-contained.")


def run_ffdb_refresh():
    """Call the ffdb CLI refresh command."""
    log("Running `ffdb refresh`...")
    result = subprocess.run(
        [sys.executable, "-m", "cli.main", "refresh"],
        cwd=FFDB_DIR,
        capture_output=True,
        text=True,
        timeout=1800  # 30 min safety timeout
    )
    log(f"ffdb refresh exit code: {result.returncode}")
    if result.stdout:
        log(f"stdout:\n{result.stdout}")
    if result.stderr:
        log(f"stderr:\n{result.stderr}")
    return result.returncode == 0


def health_check(before_counts, after_counts):
    """A refresh should only ever ADD rows, never remove them, for past seasons.
    The current season's count should also not decrease.
    Returns (passed: bool, reason: str)."""
    for season, before_n in before_counts.items():
        after_n = after_counts.get(season, 0)
        if after_n < before_n:
            return False, f"Season {season} row count DROPPED ({before_n} -> {after_n})"

    # sanity floor: total row count should never be near-zero after a real refresh
    if sum(after_counts.values()) < 1000:
        return False, "Post-refresh row count is implausibly low"

    return True, "OK"


def main():
    # ── acquire lock to prevent concurrent refresh runs ───────────────────────
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another refresh is already running. Exiting.")
        sys.exit(1)

    status = load_status()
    run_record = {"timestamp": datetime.now().isoformat(), "success": False, "reason": ""}

    try:
        log("=" * 60)
        log("Starting nightly FFDB refresh")

        before_counts = get_row_counts() if os.path.exists(DB_PATH) else {}
        log(f"Row counts before refresh: {before_counts}")

        backup_path = backup_database()

        refresh_ok = run_ffdb_refresh()
        if not refresh_ok:
            raise RuntimeError("ffdb refresh command failed (non-zero exit code)")

        materialize_views()

        after_counts = get_row_counts()
        log(f"Row counts after refresh: {after_counts}")

        passed, reason = health_check(before_counts, after_counts)
        if not passed:
            log(f"HEALTH CHECK FAILED: {reason}")
            restore_backup(backup_path)
            raise RuntimeError(f"Health check failed: {reason}")

        log("Health check passed.")
        rotate_backups()

        status["last_refresh_time"] = datetime.now().isoformat()
        status["last_success"] = True
        status["seasons"] = after_counts
        run_record["success"] = True
        run_record["reason"] = "OK"
        log("Refresh completed successfully.")

    except Exception as e:
        log(f"REFRESH FAILED: {e}")
        status["last_refresh_time"] = datetime.now().isoformat()
        status["last_success"] = False
        run_record["success"] = False
        run_record["reason"] = str(e)

    finally:
        status["history"].append(run_record)
        save_status(status)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
        log("Lock released. Done.")

    # Non-zero exit on failure lets CI systems (e.g. GitHub Actions) gate
    # follow-up steps like "commit the new database" behind success only.
    sys.exit(0 if run_record["success"] else 1)


if __name__ == "__main__":
    main()
