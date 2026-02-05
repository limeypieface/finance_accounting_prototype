#!/usr/bin/env python3
"""
Compare balance sheet from DB (our report) to balance sheet from QBO journal JSON.

Helps diagnose why interactive.py shows different totals than QBO.
Run from project root. Uses finance_kernel_test by default.

Usage:
  python3 scripts/compare_bs_to_qbo.py [--as-of 2025-12-31] [--db-url ...]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = os.environ.get("DATABASE_URL", "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare DB balance sheet to QBO journal-derived totals.")
    parser.add_argument("--as-of", type=str, default="2025-12-31", help="Report date YYYY-MM-DD")
    parser.add_argument("--db-url", type=str, default=DB_URL)
    parser.add_argument("--journal", type=Path, default=ROOT / "upload/qbo_journal_Journal.json")
    parser.add_argument("--accounts", type=Path, default=ROOT / "upload/qbo_accounts_Ironflow AI INC_Account List _2_.json")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of)

    # 1. DB balance sheet (must use same DB as interactive: finance_kernel_test)
    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_modules.reporting.config import ReportingConfig
    from finance_modules.reporting.service import ReportingService
    from finance_kernel.domain.clock import SystemClock

    try:
        init_engine_from_url(args.db_url)
    except Exception as e:
        print(f"  ERROR: Could not connect to DB: {e}")
        return 1
    session = get_session()
    try:
        config = ReportingConfig(entity_name="Compare")
        svc = ReportingService(session=session, clock=SystemClock(), config=config)
        bs = svc.balance_sheet(as_of_date=as_of)
        db_assets = bs.total_assets
        db_l_and_e = bs.total_liabilities_and_equity
        db_balanced = bs.is_balanced
    except Exception as e:
        print(f"  ERROR: Could not generate DB report (wrong DB or no schema?): {e}")
        print("  Use --db-url to point at finance_kernel_test where you ran the import.")
        return 1
    finally:
        session.close()

    # 2. Journal entry count in DB
    from sqlalchemy import select, func
    from finance_kernel.models.journal import JournalEntry, JournalEntryStatus

    session = get_session()
    try:
        entry_count = session.scalar(
            select(func.count()).select_from(JournalEntry).where(
                JournalEntry.status == JournalEntryStatus.POSTED.value
            )
        ) or 0
    finally:
        session.close()

    # 3. QBO journal-derived total (reuse balance_sheet_from_qbo_journal logic in process)
    if args.journal.exists() and args.accounts.exists():
        from scripts.balance_sheet_from_qbo_journal import (
            build_account_type_map,
            trial_balance_from_journal,
            natural_balance,
        )
        from decimal import Decimal
        from collections import defaultdict

        account_type_map = build_account_type_map(args.accounts)
        tb, _ = trial_balance_from_journal(args.journal, datetime.combine(as_of, datetime.min.time()))
        total_assets = Decimal("0")
        for name, (dr, cr) in tb.items():
            cat = account_type_map.get(name, "asset")
            if cat == "asset":
                total_assets += natural_balance(dr, cr, cat)
        qbo_assets = total_assets
    else:
        qbo_assets = None

    # Report
    print()
    print("  Balance sheet comparison (as-of", as_of.isoformat() + ")")
    print("  " + "-" * 50)
    print(f"  Journal entries in DB (posted): {entry_count}")
    print(f"  Expected from QBO journal:      916")
    if entry_count > 916:
        print(f"  --> Extra/duplicate entries likely. Reset and run full import once.")
    print()
    print(f"  DB report total assets:        {db_assets:,.2f}")
    if qbo_assets is not None:
        print(f"  QBO journal total assets:      {qbo_assets:,.2f}")
        diff = db_assets - qbo_assets
        print(f"  Difference:                     {diff:+,.2f}")
    print(f"  DB total liabilities + equity: {db_l_and_e:,.2f}")
    print(f"  DB A = L+E:                    {db_balanced}")
    print()
    print("  If you see different totals:")
    print("    1. Run: python3 scripts/reset_db_ironflow.py")
    print("    2. Run: python3 scripts/run_ironflow_full_import.py --dir upload --reset")
    print("    3. Run interactive with: FINANCE_CLI_FY_YEAR=2025 python3 scripts/interactive.py")
    print("    4. Use R for reports (report will be as of 2025-12-31)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
