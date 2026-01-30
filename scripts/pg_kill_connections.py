#!/usr/bin/env python3
"""
Kill orphaned PostgreSQL connections to the finance_kernel_test database.

Terminates all backends connected to finance_kernel_test (except itself).
Useful when previous test runs left connections holding locks.

Usage:
    python3 scripts/pg_kill_connections.py           # kill all connections
    python3 scripts/pg_kill_connections.py --idle     # kill only idle connections
    python3 scripts/pg_kill_connections.py --pid 123  # kill a specific PID
"""

import argparse
import sys

DB_NAME = "finance_kernel_test"
PG_USER = "finance"
PG_HOST = "localhost"
PG_PORT = 5432


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Kill connections to {DB_NAME}")
    parser.add_argument("--idle", action="store_true", help="Only kill idle connections")
    parser.add_argument("--pid", type=int, help="Kill a specific backend PID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be killed without acting")
    args = parser.parse_args()

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

    # Build filter conditions
    conditions = ["datname = %s", "pid <> pg_backend_pid()"]
    params: list = [DB_NAME]

    if args.pid:
        conditions.append("pid = %s")
        params.append(args.pid)
    if args.idle:
        conditions.append("state = 'idle'")

    where = " AND ".join(conditions)

    if args.dry_run:
        cur.execute(f"""
            SELECT pid, usename, state,
                   EXTRACT(EPOCH FROM (now() - state_change))::int AS state_age_s,
                   LEFT(query, 80) AS query_prefix
            FROM pg_stat_activity
            WHERE {where}
            ORDER BY backend_start
        """, params)
        rows = cur.fetchall()
        if not rows:
            print("No matching connections found.")
        else:
            print(f"Would terminate {len(rows)} connection(s):\n")
            for pid, user, state, age, query in rows:
                q = (query or "").replace("\n", " ").strip()
                print(f"  PID {pid}  user={user}  state={state}  age={age or 0}s  query={q}")
        cur.close()
        conn.close()
        return 0

    cur.execute(f"""
        SELECT pid, pg_terminate_backend(pid) AS terminated
        FROM pg_stat_activity
        WHERE {where}
    """, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    terminated = [r[0] for r in rows if r[1]]
    failed = [r[0] for r in rows if not r[1]]

    if not rows:
        print(f"No matching connections to '{DB_NAME}'.")
        return 0

    if terminated:
        print(f"Terminated {len(terminated)} connection(s): {terminated}")
    if failed:
        print(f"Failed to terminate {len(failed)} connection(s): {failed}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
