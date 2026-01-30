#!/usr/bin/env python3
"""
View financial reports from persisted database data.

Connects to the database (assumes tables and data already exist —
run seed_data.py first) and prints all 5 financial statements.

Usage:
    python3 scripts/view_reports.py
"""

import logging
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants (must match seed_data.py)
# ---------------------------------------------------------------------------
DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
ENTITY = "Acme Manufacturing Co."
FY_START = date(2025, 1, 1)
FY_END = date(2025, 12, 31)


def main() -> int:
    logging.disable(logging.CRITICAL)

    from finance_kernel.db.engine import init_engine_from_url, get_session
    from finance_kernel.domain.clock import SystemClock
    from finance_modules.reporting.service import ReportingService
    from finance_modules.reporting.models import IncomeStatementFormat
    from finance_modules.reporting.config import ReportingConfig

    # Import pretty-print helpers from demo_reports
    from scripts.demo_reports import (
        print_trial_balance,
        print_balance_sheet,
        print_income_statement,
        print_equity_changes,
        print_cash_flow,
    )

    # -----------------------------------------------------------------
    # Connect
    # -----------------------------------------------------------------
    try:
        init_engine_from_url(DB_URL, echo=False)
    except Exception as exc:
        print(f"  ERROR: Could not connect: {exc}", file=sys.stderr)
        return 1

    session = get_session()

    try:
        # Quick sanity check — do tables/data exist?
        from finance_kernel.models.account import Account
        count = session.query(Account).count()
        if count == 0:
            print("  No accounts found. Run seed_data.py first.", file=sys.stderr)
            return 1

        # -----------------------------------------------------------------
        # Generate reports
        # -----------------------------------------------------------------
        config = ReportingConfig(entity_name=ENTITY)
        svc = ReportingService(session=session, clock=SystemClock(), config=config)

        tb = svc.trial_balance(as_of_date=FY_END)
        bs = svc.balance_sheet(as_of_date=FY_END)
        is_rpt = svc.income_statement(
            period_start=FY_START,
            period_end=FY_END,
            format=IncomeStatementFormat.MULTI_STEP,
        )
        eq = svc.equity_changes(period_start=FY_START, period_end=FY_END)
        cf = svc.cash_flow_statement(period_start=FY_START, period_end=FY_END)

        # -----------------------------------------------------------------
        # Print
        # -----------------------------------------------------------------
        print_trial_balance(tb)
        print_balance_sheet(bs)
        print_income_statement(is_rpt)
        print_equity_changes(eq)
        print_cash_flow(cf)

        # Verification summary
        W = 72
        print("=" * W)
        print("  VERIFICATION SUMMARY".center(W))
        print("=" * W)

        def _status(label: str, ok: bool) -> str:
            return f"  [{'OK' if ok else 'FAIL'}] {label}"

        print(_status("Trial Balance balanced", tb.is_balanced))
        print(_status("Balance Sheet balanced (A = L + E)", bs.is_balanced))
        print(_status("Net Income = Revenue - Expenses",
                      is_rpt.net_income == is_rpt.total_revenue - is_rpt.total_expenses))
        print(_status("Equity reconciles", eq.reconciles))
        print(_status("Cash flow reconciles", cf.cash_change_reconciles))
        print()

        return 0

    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
