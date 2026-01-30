#!/usr/bin/env python3
"""
Check active PostgreSQL connections to the finance_kernel_test database.

Shows PID, state, query duration, wait events, and the current query
for every backend connected to finance_kernel_test.

Usage:
    python3 scripts/pg_check_connections.py
"""

import sys

DB_NAME = "finance_kernel_test"
PG_USER = "finance"
PG_HOST = "localhost"
PG_PORT = 5432


def main() -> int:
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 is not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        return 1

    try:
        conn = psycopg2.connect(dbname="postgres", user=PG_USER, host=PG_HOST, port=PG_PORT)
    except Exception as exc:
        print(f"ERROR: Could not connect to PostgreSQL: {exc}", file=sys.stderr)
        return 1

    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("""
        SELECT
            pid,
            usename,
            state,
            backend_type,
            wait_event_type,
            wait_event,
            EXTRACT(EPOCH FROM (now() - state_change))::int AS state_age_s,
            EXTRACT(EPOCH FROM (now() - backend_start))::int AS session_age_s,
            LEFT(query, 120) AS query_prefix
        FROM pg_stat_activity
        WHERE datname = %s
        ORDER BY backend_start
    """, (DB_NAME,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print(f"No active connections to '{DB_NAME}'.")
        return 0

    print(f"Active connections to '{DB_NAME}': {len(rows)}\n")
    print(f"{'PID':>7}  {'User':<10} {'State':<12} {'Type':<18} {'Wait':<20} {'State Age':>10} {'Session Age':>12}  Query")
    print("-" * 130)

    for pid, user, state, btype, we_type, we, state_age, sess_age, query in rows:
        wait = f"{we_type}/{we}" if we_type else "-"
        state_str = state or "-"
        query_str = (query or "").replace("\n", " ").strip()
        print(f"{pid:>7}  {user or '-':<10} {state_str:<12} {btype or '-':<18} {wait:<20} {state_age or 0:>8}s {sess_age or 0:>10}s  {query_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
