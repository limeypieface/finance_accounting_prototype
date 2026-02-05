#!/usr/bin/env python3
"""
Single-command full Ironflow import: CoA, customers, vendors, journal in order,
then confirm load from DB counts and trace/logs.

Runs in the correct sequence: optional DB reset → accounts → customers → vendors
→ journal. Uses run_ironflow_import.py for each step. Confirmation queries
accounts, parties (customers/vendors), and posted journal outcomes.

Usage:
  # Full import from upload/ with optional reset
  python3 scripts/run_ironflow_full_import.py [--dir upload] [--reset]

  # Specify config and directory
  python3 scripts/run_ironflow_full_import.py --config-id US-GAAP-2026-IRONFLOW-AI --dir path/to/json

  # No reset, no confirmation (import only)
  python3 scripts/run_ironflow_full_import.py --no-reset --no-confirm
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = os.environ.get("FINANCE_DB_URL", "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test")
IRONFLOW_CONFIG_ID = os.environ.get("FINANCE_IMPORT_CONFIG_ID", "US-GAAP-2026-IRONFLOW-AI")

# (glob_pattern, mapping_name) — order matters: accounts, customers, vendors, journal
IMPORT_STEPS = [
    ("qbo_accounts_*.json", "qbo_json_accounts"),
    ("qbo_customers_*.json", "qbo_json_customers"),
    ("qbo_vendors_*.json", "qbo_json_vendors"),
    ("qbo_journal_*.json", "qbo_json_journal"),
]

# QBO type (from detect_qbo_type) -> mapping name (Excel files use same mappings as JSON)
QBO_TYPE_TO_MAPPING = {
    "accounts": "qbo_json_accounts",
    "customers": "qbo_json_customers",
    "vendors": "qbo_json_vendors",
    "journal": "qbo_json_journal",
    "general_ledger": "qbo_json_journal",
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Full Ironflow import: CoA, customers, vendors, journal; then confirm from DB and trace.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--config-id",
        default=IRONFLOW_CONFIG_ID,
        help=f"Config set (default: {IRONFLOW_CONFIG_ID!r} or FINANCE_IMPORT_CONFIG_ID)",
    )
    p.add_argument(
        "--dir",
        type=Path,
        default=Path("upload"),
        help="Directory containing QBO files: .xlsx (Account List, Journal, etc.) or qbo_*.json (default: upload)",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Run reset_db_ironflow.py first (drop tables, bootstrap party + periods).",
    )
    p.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset DB even if --reset was implied (default: do not reset unless --reset).",
    )
    p.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation step (DB counts and trace summary).",
    )
    p.add_argument(
        "--db-url",
        default=DB_URL,
        help="Database URL",
    )
    p.add_argument(
        "--journal-chunk-size",
        type=int,
        default=0,
        metavar="N",
        help="Process journal import in chunks of N rows (e.g. 100). Passed as --chunk-size to run_ironflow_import.py.",
    )
    return p.parse_args()


def _discover_files(data_dir: Path) -> dict[str, Path]:
    """Return mapping name -> file path. Prefers Excel (.xlsx) over JSON when both exist."""
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        return {}
    out: dict[str, Path] = {}

    # Prefer Excel: discover by type so user can point at a folder of .xlsx only
    try:
        from scripts.qbo.detect import detect_qbo_type
        for path in sorted(data_dir.glob("*.xlsx")):
            if not path.is_file():
                continue
            qbo_type = detect_qbo_type(path)
            mapping = QBO_TYPE_TO_MAPPING.get(qbo_type)
            if mapping and mapping not in out:
                out[mapping] = path
    except Exception:
        pass

    # Fill remaining steps from JSON patterns
    for pattern, mapping in IMPORT_STEPS:
        if mapping in out:
            continue
        matches = sorted(data_dir.glob(pattern))
        if matches:
            out[mapping] = matches[0]
    return out


def _run_reset(config_id: str, db_url: str) -> int:
    script = ROOT / "scripts" / "reset_db_ironflow.py"
    cmd = [sys.executable, str(script), "--config-id", config_id, "--db-url", db_url]
    r = subprocess.run(cmd, cwd=str(ROOT))
    return r.returncode


def _run_import(
    config_id: str,
    mapping: str,
    file_path: Path,
    db_url: str,
    journal_chunk_size: int = 0,
) -> int:
    script = ROOT / "scripts" / "run_ironflow_import.py"
    cmd = [
        sys.executable,
        str(script),
        "--config-id", config_id,
        "--mapping", mapping,
        "--file", str(file_path.resolve()),
        "--db-url", db_url,
    ]
    if mapping == "qbo_json_journal" and journal_chunk_size > 0:
        cmd.extend(["--chunk-size", str(journal_chunk_size)])
    r = subprocess.run(cmd, cwd=str(ROOT))
    return r.returncode


def _confirm_load(config_id: str, db_url: str) -> int:
    """Print DB counts and trace hint. Returns 0."""
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import func, select
    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.models.account import Account
    from finance_kernel.models.party import Party, PartyType
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome, OutcomeStatus
    from finance_kernel.models.journal import JournalEntry

    try:
        init_engine_from_url(db_url)
    except Exception as e:
        print(f"  Confirm (DB): could not connect: {e}", file=sys.stderr)
        return 0
    session = get_session()
    try:
        accounts = session.scalar(select(func.count()).select_from(Account)) or 0
        customers = session.scalar(
            select(func.count()).select_from(Party).where(Party.party_type == PartyType.CUSTOMER.value)
        ) or 0
        vendors = session.scalar(
            select(func.count()).select_from(Party).where(Party.party_type == PartyType.SUPPLIER.value)
        ) or 0
        posted_outcomes = session.scalar(
            select(func.count()).select_from(InterpretationOutcome).where(
                InterpretationOutcome.status == OutcomeStatus.POSTED.value
            )
        ) or 0
        journal_entries = session.scalar(
            select(func.count()).select_from(JournalEntry)
        ) or 0
    finally:
        session.close()

    print()
    print("  --- Load confirmation (DB) ---")
    print(f"  Accounts:     {accounts}")
    print(f"  Customers:   {customers}")
    print(f"  Vendors:     {vendors}")
    print(f"  Journal entries: {journal_entries}")
    print(f"  Posted outcomes (events): {posted_outcomes}")
    print("  Trace/logs:  Use scripts/trace_render.py or CLI (T) to view decision_log and audit trail.")
    print()
    return 0


def main() -> int:
    args = _parse_args()
    data_dir = args.dir if args.dir.is_absolute() else (ROOT / args.dir)

    if args.reset and not args.no_reset:
        print("  [Reset] Running reset_db_ironflow.py...")
        rc = _run_reset(args.config_id, args.db_url)
        if rc != 0:
            print("  Reset failed.", file=sys.stderr)
            return rc
        print()

    discovered = _discover_files(data_dir)
    if not discovered:
        print(
            f"  No QBO files found in {data_dir}. Expected .xlsx (Account List, Journal, etc.) or qbo_*.json.",
            file=sys.stderr,
        )
        return 1

    for pattern, mapping in IMPORT_STEPS:
        if mapping not in discovered:
            print(f"  [Skip] No file for {mapping} (pattern: {pattern})")
            continue
        path = discovered[mapping]
        print(f"  [Import] {mapping} <- {path.name}")
        rc = _run_import(
            args.config_id,
            mapping,
            path,
            args.db_url,
            journal_chunk_size=args.journal_chunk_size,
        )
        if rc != 0:
            print(f"  Import failed for {mapping}.", file=sys.stderr)
            return rc
        print()

    if not args.no_confirm:
        _confirm_load(args.config_id, args.db_url)

    return 0


if __name__ == "__main__":
    sys.exit(main())
