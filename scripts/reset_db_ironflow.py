#!/usr/bin/env python3
"""
Minimal DB reset for import: drop all tables, recreate schema, create bootstrap
party and fiscal periods from the config set's import_bootstrap.yaml. No seed CoA
or demo transactions. Configuration-driven so it works for any company.

The config set (e.g. US-GAAP-2026-IRONFLOW-AI) may define import_bootstrap.yaml
with fiscal_periods (and optional bootstrap_party_code). If absent, only SYSTEM
party is created and no fiscal periods (add import_bootstrap.yaml to your set).

Usage:
  python3 scripts/reset_db_ironflow.py [--config-id CONFIG_ID]

Prerequisites:
  - PostgreSQL running; finance_kernel_test database exists.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
DEFAULT_CONFIG_ID = os.environ.get("FINANCE_IMPORT_CONFIG_ID", "US-GAAP-2026-IRONFLOW-AI")


def _kill_orphaned_connections() -> None:
    """Kill idle connections to finance_kernel_test so drop_tables can run."""
    from sqlalchemy import create_engine, text

    admin_url = DB_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            conn.execute(
                text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = 'finance_kernel_test'
                  AND pid <> pg_backend_pid()
                """)
            )
        eng.dispose()
    except Exception:
        pass  # best-effort


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reset DB and bootstrap from config set import_bootstrap.yaml")
    p.add_argument(
        "--config-id",
        default=DEFAULT_CONFIG_ID,
        help=f"Config set id for import_bootstrap.yaml (default: {DEFAULT_CONFIG_ID!r}, or FINANCE_IMPORT_CONFIG_ID)",
    )
    p.add_argument(
        "--db-url",
        default=DB_URL,
        help="Database URL",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    from scripts.qbo.coa_config import get_config_set_dir
    config_set_dir = get_config_set_dir(args.config_id)
    if not config_set_dir:
        print(f"  WARNING: Config set {args.config_id!r} not found; no fiscal periods will be created.", file=sys.stderr)
        print("  Add import_bootstrap.yaml to your config set to define fiscal_periods.", file=sys.stderr)

    from finance_kernel.db.engine import (
        drop_tables,
        get_session,
        init_engine_from_url,
    )
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_kernel.models.party import PartyType
    from finance_kernel.services.party_service import PartyService
    from finance_modules._orm_registry import create_all_tables

    print()
    print("  [1/4] Connecting to PostgreSQL...")
    try:
        init_engine_from_url(args.db_url, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    print("  [2/4] Dropping tables and recreating schema...")
    _kill_orphaned_connections()
    time.sleep(0.5)

    from finance_modules._orm_registry import import_all_orm_models
    import_all_orm_models()

    try:
        drop_tables()
    except Exception as e1:
        _kill_orphaned_connections()
        time.sleep(1.0)
        try:
            drop_tables()
        except Exception as e2:
            print(
                "  ERROR: Could not drop tables. Close other connections and retry.",
                file=sys.stderr,
            )
            print(f"  {e2}", file=sys.stderr)
            return 1

    create_all_tables(install_triggers=True)
    register_immutability_listeners()

    session = get_session()
    bootstrap_actor = uuid4()

    # Bootstrap party from config or default SYSTEM
    from scripts.import_bootstrap import load_import_bootstrap
    bootstrap = load_import_bootstrap(config_set_dir) if config_set_dir is not None else {}
    party_code = (bootstrap.get("bootstrap_party_code") or "SYSTEM").strip() or "SYSTEM"

    print(f"  [3/4] Ensuring bootstrap party {party_code!r} exists...")
    party_svc = PartyService(session)
    existing_party = party_svc.find_by_code(party_code)
    if existing_party:
        system_party_id = existing_party.id
    else:
        created = party_svc.create_party(
            party_code=party_code,
            party_type=PartyType.SYSTEM,
            name=party_code,
            actor_id=bootstrap_actor,
        )
        system_party_id = created.id
    session.flush()

    print("  [4/4] Ensuring fiscal periods from import_bootstrap.yaml...")
    if config_set_dir:
        from scripts.import_bootstrap import ensure_fiscal_periods_from_config
        ensure_fiscal_periods_from_config(session, config_set_dir, system_party_id)
    else:
        print("  (no config set; skipping fiscal periods)")
    session.flush()
    session.commit()

    print()
    print("  Done. DB reset: schema + bootstrap party + fiscal periods from config.")
    print("  Next: run account import, then journal import (run_ironflow_import.py --config-id ...).")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
