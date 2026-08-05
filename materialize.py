#!/usr/bin/env python3
"""
materialize.py — Converts baseball_live.duckdb from a set of VIEWS pointing
at external Parquet files into a fully self-contained database with the data
physically baked into TABLES.

Why this is needed: FFDB's `ffdb refresh` builds views like
    CREATE VIEW events AS SELECT * FROM read_parquet('./data/processed/events/*.parquet')
These work fine locally, where the Parquet files sit right next to the .duckdb
file. But when only the .duckdb file gets committed to git (Parquet files are
gitignored, correctly, since they're regenerable) and deployed elsewhere
(Streamlit Cloud, a fresh GitHub Actions runner, etc.), those views point at
paths that don't exist there -- so every query fails.

Run this once locally to fix the current deployment. daily_refresh.py now
runs this automatically after every future refresh, so this manual step
should only be needed this one time.
"""
import duckdb
import os
import sys

DB_PATH = os.environ.get("FFDB_LIVE_PATH", "./baseball_live.duckdb")


def materialize_views(db_path):
    conn = duckdb.connect(db_path, read_only=False)

    views = conn.execute("""
        SELECT schema_name, view_name
        FROM duckdb_views()
        WHERE NOT internal
    """).fetchall()

    if not views:
        print("No views found -- database may already be materialized.")
        conn.close()
        return

    print(f"Found {len(views)} views to materialize: {[v[1] for v in views]}")

    for schema, view in views:
        qualified = f'"{schema}"."{view}"' if schema != "main" else f'"{view}"'
        tmp_name = f"__tmp_{view}"
        tmp_qualified = f'"{schema}"."{tmp_name}"' if schema != "main" else f'"{tmp_name}"'

        print(f"  Materializing {schema}.{view} ...")
        conn.execute(f"CREATE TABLE {tmp_qualified} AS SELECT * FROM {qualified}")
        conn.execute(f"DROP VIEW {qualified}")
        conn.execute(f'ALTER TABLE {tmp_qualified} RENAME TO "{view}"')

    conn.close()
    print("Done. All views converted to physical tables.")


if __name__ == "__main__":
    materialize_views(DB_PATH)
