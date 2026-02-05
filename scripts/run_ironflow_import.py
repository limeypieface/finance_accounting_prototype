#!/usr/bin/env python3
"""
Run the import pipeline for Ironflow data using US-GAAP-2026-IRONFLOW-AI config.

Runs entirely outside the engine: no changes to kernel, engines, or services.
Loads the IRONFLOW config set by path (assemble + compile), builds
account_key_to_role from qbo_coa_mapping.yaml, ensures a SYSTEM actor exists,
and runs load -> validate -> promote with ModulePostingService and
account_key_to_role wired into PromotionService for journal (import.historical_journal).

Prerequisites:
  - Accounts must exist in the DB for the journal's lines (run account import
    first, e.g. --mapping qbo_json_accounts, or seed the CoA).
  - PostgreSQL running; finance_kernel_test DB and staging tables exist.

Usage:
  # Journal import (full pipeline; requires accounts first)
  python3 scripts/run_ironflow_import.py --mapping qbo_json_journal --file upload/qbo_journal_*.json

  # Account import first (no posting service needed)
  python3 scripts/run_ironflow_import.py --mapping qbo_json_accounts --file upload/qbo_accounts_*.json

  # Probe only
  python3 scripts/run_ironflow_import.py --mapping qbo_json_journal --file upload/qbo_journal_*.json --probe-only
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4, uuid5

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
IRONFLOW_CONFIG_ID = "US-GAAP-2026-IRONFLOW-AI"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run import pipeline for Ironflow (US-GAAP-2026-IRONFLOW-AI) with full journal posting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config-id",
        default=IRONFLOW_CONFIG_ID,
        help=f"Config set to load (default: {IRONFLOW_CONFIG_ID}).",
    )
    parser.add_argument(
        "--mapping",
        required=True,
        help="Import mapping name (e.g. qbo_json_journal, qbo_json_accounts).",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to source file (JSON, CSV, or Excel .xlsx). Errors reference file/sheet/row when from Excel.",
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
        help="Only load and validate; do not promote.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preflight only; do not promote.",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Probe source file and exit. No DB writes.",
    )
    parser.add_argument(
        "--db-url",
        default=DB_URL,
        help=f"Database URL (default: {DB_URL!r}).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=0,
        metavar="N",
        help="For journal import only: process in chunks of N rows (e.g. 100). Smaller transactions; commit after each chunk.",
    )
    return parser.parse_args()


def _load_qbo_coa_mapping(config_set_dir: Path) -> dict[str, str]:
    """Build account_key -> role from import_mappings/qbo_coa_mapping.yaml.
    Keys: input_name and (when non-empty) input_code. Values: ACCT_<target_code>.
    """
    import yaml

    path = config_set_dir / "import_mappings" / "qbo_coa_mapping.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mappings = data.get("mappings") or []
    out: dict[str, str] = {}
    for m in mappings:
        if not isinstance(m, dict):
            continue
        target = m.get("target_code")
        if not target:
            continue
        role = f"ACCT_{str(target).strip()}"
        name = m.get("input_name")
        if name is not None and str(name).strip():
            out[str(name).strip()] = role
        code = m.get("input_code")
        if code is not None and str(code).strip():
            out[str(code).strip()] = role
    return out


# Same namespace as finance_config.bridges so account_id matches role resolver
_COA_UUID_NAMESPACE = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _load_qbo_coa_target_codes(config_set_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Build input_key -> target_code and input_key -> target_name from qbo_coa_mapping.yaml.
    Used so account import creates accounts with config-aligned codes and deterministic ids.
    """
    import yaml

    path = config_set_dir / "import_mappings" / "qbo_coa_mapping.yaml"
    if not path.exists():
        return {}, {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mappings = data.get("mappings") or []
    to_code: dict[str, str] = {}
    to_name: dict[str, str] = {}
    for m in mappings:
        if not isinstance(m, dict):
            continue
        target_code = m.get("target_code")
        if not target_code:
            continue
        target_code = str(target_code).strip()
        target_name = (m.get("target_name") or target_code).strip()
        name = m.get("input_name")
        if name is not None and str(name).strip():
            k = str(name).strip()
            to_code[k] = target_code
            to_name[k] = target_name
        code = m.get("input_code")
        if code is not None and str(code).strip():
            k = str(code).strip()
            to_code[k] = target_code
            to_name[k] = target_name
    return to_code, to_name


def _build_account_key_to_role(config_set_dir: Path):
    """Return a callable (account_key: str) -> role str | None using qbo_coa_mapping."""
    mapping = _load_qbo_coa_mapping(config_set_dir)
    if not mapping:
        return None

    def account_key_to_role(account_key: str) -> str | None:
        if not account_key:
            return None
        k = account_key.strip()
        return mapping.get(k) or mapping.get(k.strip())

    return account_key_to_role


def _format_source_location(raw_data: dict | None, source_row: int, fallback: str = "row") -> str:
    """Format traceability string for errors: 'filename.xlsx, sheet \"Name\", row N' when available."""
    if not raw_data:
        return f"{fallback} {source_row}"
    file_name = raw_data.get("_source_file")
    sheet_name = raw_data.get("_source_sheet")
    if file_name and sheet_name:
        return f"{file_name}, sheet {sheet_name!r}, row {source_row}"
    if file_name:
        return f"{file_name}, row {source_row}"
    return f"{fallback} {source_row}"


def _ensure_system_actor(session, party_service) -> UUID:
    """Return a party id that has party_type SYSTEM (create if missing)."""
    from finance_kernel.models.party import PartyType

    existing = party_service.find_by_code("SYSTEM")
    if existing:
        return existing.id
    created = party_service.create_party(
        party_code="SYSTEM",
        party_type=PartyType.SYSTEM,
        name="System",
        actor_id=uuid4(),
    )
    return created.id


def main() -> int:
    args = _parse_args()

    as_of = args.as_of_date or date.today()
    source_path = args.file.resolve()
    if not source_path.is_file():
        print(f"ERROR: File not found: {source_path}", file=sys.stderr)
        return 1

    # Resolve config set directory for qbo_coa_mapping (journal path only)
    from scripts.qbo.coa_config import get_config_set_dir

    config_set_dir = get_config_set_dir(args.config_id)
    if not config_set_dir:
        print(f"ERROR: Config set not found: {args.config_id}", file=sys.stderr)
        return 1

    from finance_config.assembler import assemble_from_directory
    from finance_config.bridges import build_role_resolver
    from finance_config.compiler import compile_policy_pack
    from finance_config.validator import validate_configuration
    from finance_ingestion.promoters import default_promoter_registry
    from finance_ingestion.services import (
        ImportService,
        PromotionService,
        build_mapping_registry_from_defs,
    )
    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.domain.clock import SystemClock
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_kernel.services.party_service import PartyService
    from finance_services.posting_orchestrator import PostingOrchestrator

    # Load IRONFLOW pack from disk (outside get_active_config / build_posting_orchestrator)
    try:
        config_set = assemble_from_directory(config_set_dir)
        validation = validate_configuration(config_set)
        if not validation.is_valid:
            raise ValueError(
                "Config validation failed: " + "; ".join(validation.errors)
            )
        pack = compile_policy_pack(config_set)
        if pack.checksum != config_set.checksum:
            raise ValueError("Checksum drift after compile")
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
        return 1

    registry = build_mapping_registry_from_defs(pack.import_mappings)
    mapping = registry.get(args.mapping)
    if not mapping:
        names = sorted(registry.keys()) or ["(none defined)"]
        print(f"ERROR: Mapping {args.mapping!r} not found.", file=sys.stderr)
        print(f"Available: {names}", file=sys.stderr)
        return 1

    is_journal = (mapping.entity_type or "").lower() == "journal"
    account_key_to_role = _build_account_key_to_role(config_set_dir) if is_journal else None

    is_xlsx = source_path.suffix.lower() in (".xlsx", ".xls")

    if args.probe_only:
        init_engine_from_url(args.db_url)
        session = get_session()
        try:
            if is_xlsx:
                from scripts.qbo.readers import read_qbo_file
                from scripts.qbo.detect import detect_qbo_type
                rows = read_qbo_file(source_path, detect_qbo_type(source_path))
                print(f"Rows: {len(rows)}")
                if rows:
                    sample = rows[0]
                    print(f"Columns (keys): {list(sample.keys()) if isinstance(sample, dict) else 'N/A'}")
                print("Sample (first 2):")
                for i, row in enumerate(rows[:2], 1):
                    print(f"  {i}: {row}")
            else:
                import_svc = ImportService(
                    session, clock=SystemClock(), mapping_registry=registry
                )
                probe = import_svc.probe_source(source_path, mapping)
                print(f"Rows: {probe.row_count}")
                print(f"Columns: {list(probe.columns)}")
                print("Sample (first 2):")
                for i, row in enumerate(probe.sample_rows[:2], 1):
                    print(f"  {i}: {row}")
        finally:
            session.close()
        return 0

    try:
        init_engine_from_url(args.db_url)
    except Exception as e:
        print(f"ERROR: Database init failed: {e}", file=sys.stderr)
        return 1

    session = get_session()
    clock = SystemClock()
    auditor = AuditorService(session, clock)
    party_svc = PartyService(session)

    # For journal we need SYSTEM actor and full posting pipeline
    actor_id: UUID
    module_posting_service = None
    if is_journal:
        actor_id = _ensure_system_actor(session, party_svc)
        session.flush()
        role_resolver = build_role_resolver(pack)
        orchestrator = PostingOrchestrator(
            session=session,
            compiled_pack=pack,
            role_resolver=role_resolver,
            clock=clock,
        )
        # Batch import: do not commit after each post so PromotionService savepoints
        # stay valid; the script commits once after promote_batch().
        module_posting_service = ModulePostingService.from_orchestrator(
            orchestrator, auto_commit=False
        )
        if not account_key_to_role:
            print(
                "WARNING: No qbo_coa_mapping found; journal lines may fail to resolve account roles.",
                file=sys.stderr,
            )
    else:
        actor_id = uuid4()

    account_key_to_target_code = None
    account_key_to_target_name = None
    account_id_for_code = None
    if (mapping.entity_type or "").lower() == "account" and config_set_dir:
        to_code, to_name = _load_qbo_coa_target_codes(config_set_dir)
        if to_code:
            account_key_to_target_code = to_code
            account_key_to_target_name = to_name
            account_id_for_code = lambda c: uuid5(_COA_UUID_NAMESPACE, c)

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
        module_posting_service=module_posting_service,
        account_key_to_role=account_key_to_role,
        account_key_to_target_code=account_key_to_target_code,
        account_key_to_target_name=account_key_to_target_name,
        account_id_for_code=account_id_for_code,
    )

    if is_journal:
        from scripts.import_bootstrap import ensure_fiscal_periods_from_config
        ensure_fiscal_periods_from_config(session, config_set_dir, actor_id)
        session.commit()

    use_chunks = is_journal and args.chunk_size > 0

    def _read_source_rows():
        if is_xlsx:
            from scripts.qbo.readers import read_qbo_file
            from scripts.qbo.detect import detect_qbo_type
            return read_qbo_file(source_path, detect_qbo_type(source_path))
        return import_svc.read_rows(source_path, mapping)

    try:
        if use_chunks:
            # Journal chunked: read all rows, process in chunks of N, commit after each chunk
            print(f"Reading rows from {source_path.name} (chunk size={args.chunk_size})...")
            all_rows = _read_source_rows()
            total = len(all_rows)
            n_chunks = (total + args.chunk_size - 1) // args.chunk_size if total else 0
            print(f"  Total rows: {total}, chunks: {n_chunks}")

            total_promoted = 0
            total_failed = 0
            total_skipped = 0
            any_failed = False

            for start in range(0, total, args.chunk_size):
                chunk = all_rows[start : start + args.chunk_size]
                chunk_num = (start // args.chunk_size) + 1
                row_offset = start + 1
                print(f"\n  Chunk {chunk_num}/{n_chunks} (rows {row_offset}-{row_offset + len(chunk) - 1})...")

                batch = import_svc.load_batch_from_rows(
                    source_path, mapping, actor_id, chunk, row_offset=row_offset
                )
                session.flush()

                print("  Validating...")
                validated = import_svc.validate_batch(batch.batch_id, actor_id=actor_id)
                session.flush()
                print(f"  Valid: {validated.valid_records}, Invalid: {validated.invalid_records}")

                if validated.invalid_records:
                    errors = import_svc.get_batch_errors(batch.batch_id)
                    for rec in errors[:5]:
                        loc = _format_source_location(rec.raw_data, rec.source_row)
                        print(f"    {loc}: {rec.validation_errors}")
                    if len(errors) > 5:
                        print(f"    ... and {len(errors) - 5} more.")

                if validated.valid_records == 0:
                    session.commit()
                    continue

                if not args.no_promote and not args.dry_run:
                    print("  Promoting...")
                    result = promotion_svc.promote_batch(batch.batch_id, actor_id)
                    total_promoted += result.promoted
                    total_failed += result.failed
                    total_skipped += result.skipped
                    if result.failed:
                        any_failed = True
                    if result.errors:
                        for err in result.errors:
                            try:
                                rec = import_svc.get_record_by_batch_and_row(batch.batch_id, err.source_row)
                                loc = _format_source_location(rec.raw_data, rec.source_row)
                            except Exception:
                                loc = f"row {err.source_row}"
                            print(f"    {loc}: {err.message}")

                session.commit()

            print()
            print(f"  Total: Promoted: {total_promoted}, Failed: {total_failed}, Skipped: {total_skipped}")
            return 0 if not any_failed else 1

        # Non-chunked (default) path
        print(f"Loading {source_path} with mapping {args.mapping} (config_id={args.config_id})...")
        if is_xlsx:
            rows = _read_source_rows()
            batch = import_svc.load_batch_from_rows(
                source_path, mapping, actor_id, rows, row_offset=1
            )
        else:
            batch = import_svc.load_batch(source_path, mapping, actor_id)
        session.commit()
        print(f"  Staged {batch.total_records} records (batch_id={batch.batch_id})")

        print("Validating...")
        validated = import_svc.validate_batch(batch.batch_id, actor_id=actor_id)
        session.commit()
        print(f"  Valid: {validated.valid_records}, Invalid: {validated.invalid_records}")

        if validated.invalid_records:
            errors = import_svc.get_batch_errors(batch.batch_id)
            for rec in errors[:10]:
                loc = _format_source_location(rec.raw_data, rec.source_row)
                print(f"  {loc}: {rec.validation_errors}")
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more.")

        if args.no_promote:
            print("Skipping promotion (--no-promote).")
            return 0

        if validated.valid_records == 0:
            print("No valid records to promote.")
            return 0

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
            for err in result.errors:
                try:
                    rec = import_svc.get_record_by_batch_and_row(batch.batch_id, err.source_row)
                    loc = _format_source_location(rec.raw_data, rec.source_row)
                except Exception:
                    loc = f"row {err.source_row}"
                print(f"  {loc}: {err.message}")
        return 0 if result.failed == 0 else 1
    except Exception as e:
        session.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
