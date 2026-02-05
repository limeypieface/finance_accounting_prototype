#!/usr/bin/env python3
"""
Run the data transition pipeline: load a source file into staging, validate, and optionally promote.

Uses import mappings from the active config (get_active_config). Ensure:
  - The config set has import_mappings.yaml or import_mappings/*.yaml.
  - Staging tables exist (run once: finance_modules._orm_registry.create_all_tables).

Usage:
    python3 scripts/run_import.py --mapping <name> --file <path> [options]

Examples:
    # Load and validate only (no promotion)
    python3 scripts/run_import.py --mapping qb_vendors --file vendors.csv --no-promote

    # Full pipeline: load, validate, promote
    python3 scripts/run_import.py --mapping qb_accounts --file chart_of_accounts.csv

    # Probe source file (row count, columns, sample) without loading
    python3 scripts/run_import.py --mapping qb_vendors --file vendors.csv --probe-only
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

# Project root on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run import pipeline: load -> validate -> [promote] using config import mappings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mapping",
        required=True,
        help="Import mapping name (must exist in active config import_mappings).",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to source file (CSV or JSON).",
    )
    parser.add_argument(
        "--legal-entity",
        default="*",
        help="Legal entity for config scope (default: *).",
    )
    parser.add_argument(
        "--as-of-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="As-of date for config (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Only load and validate; do not promote to live tables.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate promotion preflight only; do not write to live tables.",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Probe source file (row count, columns, sample rows) and exit. No DB writes.",
    )
    parser.add_argument(
        "--actor-id",
        default=None,
        help="Actor UUID for audit (default: RUN_IMPORT_ACTOR_ID env or new UUID).",
    )
    parser.add_argument(
        "--db-url",
        default=DB_URL,
        help=f"Database URL (default: {DB_URL!r}).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    as_of = args.as_of_date or date.today()
    actor_id = UUID(args.actor_id) if args.actor_id else UUID(os.environ.get("RUN_IMPORT_ACTOR_ID", str(uuid4())))
    source_path = args.file.resolve()
    if not source_path.is_file():
        print(f"ERROR: File not found: {source_path}", file=sys.stderr)
        return 1

    # Lazy imports so we fail fast on args first
    from finance_config import get_active_config
    from finance_ingestion.promoters import default_promoter_registry
    from finance_ingestion.services import (
        ImportService,
        PromotionService,
        build_mapping_registry_from_defs,
    )
    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.domain.clock import SystemClock
    from finance_kernel.services.auditor_service import AuditorService

    # Load config and resolve mapping
    try:
        pack = get_active_config(args.legal_entity, as_of)
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
        return 1

    registry = build_mapping_registry_from_defs(pack.import_mappings)
    mapping = registry.get(args.mapping)
    if not mapping:
        names = sorted(registry.keys()) or ["(none defined)"]
        print(f"ERROR: Mapping {args.mapping!r} not found in config.", file=sys.stderr)
        print(f"Available mappings: {names}", file=sys.stderr)
        return 1

    # Probe-only: need session for ImportService but no staging writes
    if args.probe_only:
        init_engine_from_url(args.db_url)
        session = get_session()
        try:
            svc = ImportService(session, clock=SystemClock(), mapping_registry=registry)
            probe = svc.probe_source(source_path, mapping)
            print(f"Rows: {probe.row_count}")
            print(f"Columns: {list(probe.columns)}")
            print("Sample (first 3):")
            for i, row in enumerate(probe.sample_rows[:3], 1):
                print(f"  {i}: {row}")
        finally:
            session.close()
        return 0

    # Init DB and build services
    try:
        init_engine_from_url(args.db_url)
    except Exception as e:
        print(f"ERROR: Database init failed: {e}", file=sys.stderr)
        return 1

    session = get_session()
    clock = SystemClock()
    auditor = AuditorService(session, clock)
    import_svc = ImportService(
        session,
        clock=clock,
        mapping_registry=registry,
        auditor_service=auditor,
    )
    promotion_svc = PromotionService(
        session,
        promoters=default_promoter_registry(),
        clock=clock,
        auditor_service=auditor,
    )

    try:
        # Load
        print(f"Loading {source_path} with mapping {args.mapping}...")
        batch = import_svc.load_batch(source_path, mapping, actor_id)
        session.commit()
        print(f"  Staged {batch.total_records} records (batch_id={batch.batch_id})")

        # Validate
        print("Validating...")
        validated = import_svc.validate_batch(batch.batch_id, actor_id=actor_id)
        session.commit()
        print(f"  Valid: {validated.valid_records}, Invalid: {validated.invalid_records}")

        if validated.invalid_records:
            errors = import_svc.get_batch_errors(batch.batch_id)
            for rec in errors[:10]:
                print(f"  Row {rec.source_row}: {rec.validation_errors}")
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more invalid records.")

        if args.no_promote:
            print("Skipping promotion (--no-promote).")
            return 0

        if validated.valid_records == 0:
            print("No valid records to promote.")
            return 0

        # Promote
        if args.dry_run:
            preflight = promotion_svc.compute_preflight_graph(batch.batch_id)
            print(f"Preflight: ready={preflight.ready_count}, blocked={preflight.blocked_count}")
            print("Dry run: no promotion performed.")
            return 0

        print("Promoting...")
        result = promotion_svc.promote_batch(batch.batch_id, actor_id)
        session.commit()
        print(f"  Promoted: {result.promoted}, Failed: {result.failed}, Skipped: {result.skipped}")
        if result.errors:
            for err in result.errors[:5]:
                print(f"  Row {err.source_row}: {err.message}")
            if len(result.errors) > 5:
                print(f"  ... and {len(result.errors) - 5} more errors.")
        return 0 if result.failed == 0 else 1
    except Exception as e:
        session.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
