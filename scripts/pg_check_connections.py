#!/usr/bin/env python3
"""
Check active PostgreSQL connections for the configured database.

Shows PID, state, query duration, wait events, and the current query
for every backend connected to the target database.

Uses DATABASE_URL if set (same as pytest), otherwise defaults to
finance_kernel_test (interactive/scripts DB). So:
  - Default: connections to finance_kernel_test
  - DATABASE_URL=.../finance_kernel_pytest: connections to the test DB

Usage:
    python3 scripts/pg_check_connections.py
    DATABASE_URL=postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_pytest python3 scripts/pg_check_connections.py
"""

import os
import sys
from urllib.parse import urlparse

# Same default as scripts (interactive DB); tests use finance_kernel_pytest via DATABASE_URL
DEFAULT_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"


def _db_name_from_url(url: str) -> str:
    path = urlparse(url).path
    return (path.lstrip("/").split("/")[0] or "postgres").split("?")[0]


def _parse_url(url: str) -> tuple[str, str, str, int, str | None]:
    p = urlparse(url)
    return (
        _db_name_from_url(url),
        p.username or "finance",
        p.hostname or "localhost",
        p.port or 5432,
        p.password or None,
    )


def main() -> int:
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 is not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        return 1

    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    db_name, user, host, port, password = _parse_url(url)

    connect_kw: dict = dict(dbname="postgres", user=user, host=host, port=port)
    if password:
        connect_kw["password"] = password

    try:
        conn = psycopg2.connect(**connect_kw)
    except Exception as exc:
        print(f"ERROR: Could not connect to PostgreSQL at {host}:{port}: {exc}", file=sys.stderr)
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
    """, (db_name,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print(f"No active connections to '{db_name}'.")
        return 0

    print(f"Active connections to '{db_name}' (host={host} port={port}): {len(rows)}\n")
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
