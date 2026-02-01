#!/usr/bin/env python3
"""
Demo: Financial Statement Report Viewer.

Posts a realistic set of business transactions and prints all five
financial statements to stdout.

All data is rolled back on exit — the database is left untouched.

Usage:
    python3 scripts/demo_reports.py
"""

import sys
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
ENTITY = "Acme Manufacturing Co."
FY_START = date(2025, 1, 1)
FY_END = date(2025, 12, 31)


# ===================================================================
# Pretty-print helpers
# ===================================================================

W = 72  # total line width
AMT_W = 16  # amount column width


def _hdr(title: str, subtitle: str = "") -> str:
    lines = [
        "",
        "=" * W,
        title.center(W),
        subtitle.center(W) if subtitle else "",
        "=" * W,
    ]
    return "\n".join(l for l in lines if l is not None)


def _row(label: str, amount, indent: int = 0) -> str:
    prefix = "  " * indent
    name = f"{prefix}{label}"
    return f"  {name:<{W - AMT_W - 2}}{_fmt(amount):>{AMT_W}}"


def _sep() -> str:
    return f"  {'':>{W - AMT_W - 2}}{'-' * AMT_W:>{AMT_W}}"


def _blank() -> str:
    return ""


def _fmt(v) -> str:
    """Format a Decimal as $1,234.56."""
    if v is None:
        return ""
    d = Decimal(str(v))
    negative = d < 0
    abs_d = abs(d)
    formatted = f"${abs_d:,.2f}"
    return f"({formatted})" if negative else f" {formatted} "


def _bold_row(label: str, amount) -> str:
    name = label.upper()
    return f"  {name:<{W - AMT_W - 2}}{_fmt(amount):>{AMT_W}}"


def _status(label: str, ok: bool) -> str:
    tag = "OK" if ok else "FAIL"
    return f"  [{tag}] {label}"


# ===================================================================
# Report printers
# ===================================================================


def print_trial_balance(tb) -> None:
    print(_hdr("TRIAL BALANCE", f"As of {FY_END}  —  {ENTITY}"))
    print(f"  {'Account':<{W - 2*AMT_W - 2}}{'Debit':>{AMT_W}}{'Credit':>{AMT_W}}")
    print(f"  {'-'*(W - 2*AMT_W - 2)}{'-'*AMT_W:>{AMT_W}}{'-'*AMT_W:>{AMT_W}}")
    for line in tb.lines:
        dr = _fmt(line.debit_balance) if line.debit_balance else ""
        cr = _fmt(line.credit_balance) if line.credit_balance else ""
        print(f"  {line.account_code + '  ' + line.account_name:<{W - 2*AMT_W - 2}}{dr:>{AMT_W}}{cr:>{AMT_W}}")
    print(f"  {'-'*(W - 2*AMT_W - 2)}{'-'*AMT_W:>{AMT_W}}{'-'*AMT_W:>{AMT_W}}")
    print(f"  {'TOTALS':<{W - 2*AMT_W - 2}}{_fmt(tb.total_debits):>{AMT_W}}{_fmt(tb.total_credits):>{AMT_W}}")
    print(_status("Debits = Credits", tb.is_balanced))
    print()


def _print_bs_section(section) -> None:
    print(f"  {section.label}")
    for line in section.lines:
        print(_row(f"{line.account_code}  {line.account_name}", line.net_balance, indent=1))
    print(_sep())
    print(_row(f"Total {section.label}", section.total, indent=1))
    print(_blank())


def print_balance_sheet(bs) -> None:
    print(_hdr("BALANCE SHEET", f"As of {FY_END}  —  {ENTITY}"))

    print()
    print("  ASSETS")
    _print_bs_section(bs.current_assets)
    _print_bs_section(bs.non_current_assets)
    print(_bold_row("Total Assets", bs.total_assets))

    print()
    print("  LIABILITIES")
    _print_bs_section(bs.current_liabilities)
    _print_bs_section(bs.non_current_liabilities)
    print(_bold_row("Total Liabilities", bs.total_liabilities))

    print()
    print("  EQUITY")
    _print_bs_section(bs.equity)
    print(_bold_row("Total Equity", bs.total_equity))

    print()
    print(_bold_row("Total Liabilities & Equity", bs.total_liabilities_and_equity))
    print(_status("Assets = Liabilities + Equity", bs.is_balanced))
    print()


def print_income_statement(is_rpt) -> None:
    print(_hdr("INCOME STATEMENT (Multi-Step)",
               f"Period {FY_START} to {FY_END}  —  {ENTITY}"))
    print()

    if is_rpt.revenue_section:
        print(f"  {is_rpt.revenue_section.label}")
        for line in is_rpt.revenue_section.lines:
            print(_row(f"{line.account_code}  {line.account_name}", line.net_balance, indent=1))
        print(_sep())
        print(_row("Total Revenue", is_rpt.total_revenue, indent=1))
        print()

    if is_rpt.cogs_section:
        print(f"  {is_rpt.cogs_section.label}")
        for line in is_rpt.cogs_section.lines:
            print(_row(f"{line.account_code}  {line.account_name}", line.net_balance, indent=1))
        print(_sep())
        print(_row("Total COGS", is_rpt.cogs_section.total, indent=1))
        print()

    if is_rpt.gross_profit is not None:
        print(_bold_row("Gross Profit", is_rpt.gross_profit))
        print()

    if is_rpt.operating_expense_section:
        print(f"  {is_rpt.operating_expense_section.label}")
        for line in is_rpt.operating_expense_section.lines:
            print(_row(f"{line.account_code}  {line.account_name}", line.net_balance, indent=1))
        print(_sep())
        print(_row("Total Operating Expenses", is_rpt.operating_expense_section.total, indent=1))
        print()

    if is_rpt.operating_income is not None:
        print(_bold_row("Operating Income", is_rpt.operating_income))
        print()

    if is_rpt.other_income_section and is_rpt.other_income_section.lines:
        print(f"  {is_rpt.other_income_section.label}")
        for line in is_rpt.other_income_section.lines:
            print(_row(f"{line.account_code}  {line.account_name}", line.net_balance, indent=1))
        print()

    if is_rpt.other_expense_section and is_rpt.other_expense_section.lines:
        print(f"  {is_rpt.other_expense_section.label}")
        for line in is_rpt.other_expense_section.lines:
            print(_row(f"{line.account_code}  {line.account_name}", line.net_balance, indent=1))
        print()

    print(_bold_row("Net Income", is_rpt.net_income))
    print(_status("NI = Revenue - Expenses",
                  is_rpt.net_income == is_rpt.total_revenue - is_rpt.total_expenses))
    print()


def print_equity_changes(eq) -> None:
    print(_hdr("STATEMENT OF CHANGES IN EQUITY",
               f"Period {FY_START} to {FY_END}  —  {ENTITY}"))
    print()
    print(_row("Beginning Equity", eq.beginning_equity))
    print()
    for m in eq.movements:
        print(_row(m.description, m.amount, indent=1))
    print(_sep())
    print(_bold_row("Ending Equity", eq.ending_equity))
    print(_status("Beginning + Movements = Ending", eq.reconciles))
    print()


def print_cash_flow(cf) -> None:
    print(_hdr("STATEMENT OF CASH FLOWS (Indirect)",
               f"Period {FY_START} to {FY_END}  —  {ENTITY}"))
    print()

    print(_row("Net Income", cf.net_income))
    print()

    print(f"  {cf.operating_adjustments.label}")
    for line in cf.operating_adjustments.lines:
        print(_row(line.description, line.amount, indent=1))
    print()

    print(f"  {cf.working_capital_changes.label}")
    for line in cf.working_capital_changes.lines:
        print(_row(line.description, line.amount, indent=1))
    print(_sep())
    print(_bold_row("Net Cash from Operations", cf.net_cash_from_operations))
    print()

    print(f"  {cf.investing_activities.label}")
    for line in cf.investing_activities.lines:
        print(_row(line.description, line.amount, indent=1))
    print(_sep())
    print(_bold_row("Net Cash from Investing", cf.net_cash_from_investing))
    print()

    print(f"  {cf.financing_activities.label}")
    for line in cf.financing_activities.lines:
        print(_row(line.description, line.amount, indent=1))
    print(_sep())
    print(_bold_row("Net Cash from Financing", cf.net_cash_from_financing))
    print()

    print(_bold_row("Net Change in Cash", cf.net_change_in_cash))
    print(_row("Beginning Cash", cf.beginning_cash))
    print(_bold_row("Ending Cash", cf.ending_cash))
    print(_status("Cash Reconciles", cf.cash_change_reconciles))
    print()


# ===================================================================
# Data setup
# ===================================================================

def create_chart_of_accounts(session, actor_id):
    """Create a realistic chart of accounts and return a dict keyed by role."""
    from finance_kernel.models.account import Account, AccountType, NormalBalance

    specs = [
        # code, name, type, normal, tags, role_key
        ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT, None, "CASH"),
        ("1100", "Accounts Receivable", AccountType.ASSET, NormalBalance.DEBIT, None, "AR"),
        ("1200", "Inventory", AccountType.ASSET, NormalBalance.DEBIT, None, "INVENTORY"),
        ("1500", "Equipment", AccountType.ASSET, NormalBalance.DEBIT, None, "EQUIPMENT"),
        ("2000", "Accounts Payable", AccountType.LIABILITY, NormalBalance.CREDIT, None, "AP"),
        ("3200", "Retained Earnings", AccountType.EQUITY, NormalBalance.CREDIT, None, "RETAINED_EARNINGS"),
        ("4000", "Sales Revenue", AccountType.REVENUE, NormalBalance.CREDIT, None, "REVENUE"),
        ("5000", "Cost of Goods Sold", AccountType.EXPENSE, NormalBalance.DEBIT, None, "COGS"),
        ("5100", "Operating Expense", AccountType.EXPENSE, NormalBalance.DEBIT, None, "EXPENSE"),
        ("5600", "Depreciation Expense", AccountType.EXPENSE, NormalBalance.DEBIT, ["depreciation"], "DEPRECIATION"),
        ("5800", "Salary Expense", AccountType.EXPENSE, NormalBalance.DEBIT, None, "SALARY"),
        ("9999", "Rounding", AccountType.EXPENSE, NormalBalance.DEBIT, ["rounding"], "ROUNDING"),
    ]

    accounts = {}
    for code, name, atype, nbal, tags, role_key in specs:
        acct = Account(
            code=code,
            name=name,
            account_type=atype,
            normal_balance=nbal,
            is_active=True,
            tags=tags,
            created_by_id=actor_id,
        )
        session.add(acct)
        session.flush()
        accounts[role_key] = acct

    return accounts


def create_fiscal_period(session, actor_id):
    """Create an open FY2025 period."""
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus

    period = FiscalPeriod(
        period_code="FY2025",
        name="Fiscal Year 2025",
        start_date=FY_START,
        end_date=FY_END,
        status=PeriodStatus.OPEN,
        created_by_id=actor_id,
    )
    session.add(period)
    session.flush()
    return period


def build_posting_pipeline(session, accounts, clock):
    """Wire up the full interpretation pipeline and return a post() helper."""
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent,
        AccountingIntentSnapshot,
        IntentLine,
        LedgerIntent,
    )
    from finance_kernel.domain.meaning_builder import (
        EconomicEventData,
        MeaningBuilderResult,
    )
    from finance_kernel.models.event import Event
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.interpretation_coordinator import (
        InterpretationCoordinator,
    )
    from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
    from finance_kernel.services.outcome_recorder import OutcomeRecorder
    from finance_kernel.utils.hashing import hash_payload

    auditor = AuditorService(session, clock)
    resolver = RoleResolver()
    for role_key, acct in accounts.items():
        resolver.register_binding(role_key, acct.id, acct.code)

    writer = JournalWriter(session, resolver, clock, auditor)
    recorder = OutcomeRecorder(session, clock)
    coordinator = InterpretationCoordinator(session, writer, recorder, clock)

    actor_id = uuid4()

    def post(debit_role: str, credit_role: str, amount: Decimal, memo: str = ""):
        source_event_id = uuid4()
        effective = clock.now().date()

        # Create source Event (FK requirement)
        payload = {"memo": memo, "amount": str(amount)}
        evt = Event(
            event_id=source_event_id,
            event_type="demo.posting",
            occurred_at=clock.now(),
            effective_date=effective,
            actor_id=actor_id,
            producer="demo_reports",
            payload=payload,
            payload_hash=hash_payload(payload),
            schema_version=1,
            ingested_at=clock.now(),
        )
        session.add(evt)
        session.flush()

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="demo.posting",
            effective_date=effective,
            profile_id="DemoProfile",
            profile_version=1,
            profile_hash=None,
            quantity=amount,
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=source_event_id,
            profile_id="DemoProfile",
            profile_version=1,
            effective_date=effective,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit(debit_role, amount, "USD"),
                        IntentLine.credit(credit_role, amount, "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
        )

        result = coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=actor_id,
        )
        session.flush()

        if not result.success:
            print(f"  WARNING: Post failed — {result.error_code}: {result.error_message}",
                  file=sys.stderr)
        return result

    return post


# ===================================================================
# Main
# ===================================================================


def main() -> int:
    import logging
    logging.disable(logging.CRITICAL)  # suppress kernel logs for clean output

    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.domain.clock import DeterministicClock
    from finance_modules._orm_registry import create_all_tables
    from finance_modules.reporting.config import ReportingConfig
    from finance_modules.reporting.models import IncomeStatementFormat
    from finance_modules.reporting.service import ReportingService

    # 1. Connect
    try:
        init_engine_from_url(DB_URL, echo=False)
        create_all_tables(install_triggers=False)
    except Exception as exc:
        print(f"ERROR: Could not connect to PostgreSQL: {exc}", file=sys.stderr)
        print("Make sure PostgreSQL is running and the database exists.", file=sys.stderr)
        return 1

    session = get_session()
    clock = DeterministicClock(datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC))
    actor_id = uuid4()

    try:
        # 2. Setup
        print()
        print(f"  Setting up {ENTITY}...")
        accounts = create_chart_of_accounts(session, actor_id)
        create_fiscal_period(session, actor_id)
        post = build_posting_pipeline(session, accounts, clock)

        # 3. Post transactions
        print("  Posting 8 business transactions...")
        print()

        txns = [
            ("CASH", "RETAINED_EARNINGS", Decimal("500000.00"), "Owner investment"),
            ("INVENTORY", "AP", Decimal("100000.00"), "Inventory purchased on account"),
            ("CASH", "REVENUE", Decimal("150000.00"), "Cash sales — Q1-Q4"),
            ("AR", "REVENUE", Decimal("75000.00"), "Credit sales on account"),
            ("COGS", "INVENTORY", Decimal("60000.00"), "Cost of goods sold"),
            ("SALARY", "CASH", Decimal("45000.00"), "Salaries paid"),
            ("CASH", "AR", Decimal("25000.00"), "AR collection"),
            ("EQUIPMENT", "CASH", Decimal("80000.00"), "Equipment purchase"),
        ]

        for i, (dr, cr, amt, memo) in enumerate(txns, 1):
            result = post(dr, cr, amt, memo)
            status = "OK" if result.success else "FAIL"
            print(f"    {i}. [{status}] {memo:<40} Dr {dr:<20} Cr {cr:<20} {_fmt(amt)}")

        print()

        # 4. Generate reports
        config = ReportingConfig(entity_name=ENTITY)
        svc = ReportingService(session=session, clock=clock, config=config)

        tb = svc.trial_balance(as_of_date=FY_END)
        bs = svc.balance_sheet(as_of_date=FY_END)
        is_rpt = svc.income_statement(
            period_start=FY_START,
            period_end=FY_END,
            format=IncomeStatementFormat.MULTI_STEP,
        )
        eq = svc.equity_changes(period_start=FY_START, period_end=FY_END)
        cf = svc.cash_flow_statement(period_start=FY_START, period_end=FY_END)

        # 5. Print reports
        print_trial_balance(tb)
        print_balance_sheet(bs)
        print_income_statement(is_rpt)
        print_equity_changes(eq)
        print_cash_flow(cf)

        # Summary
        print("=" * W)
        print("  VERIFICATION SUMMARY".center(W))
        print("=" * W)
        print(_status("Trial Balance balanced", tb.is_balanced))
        print(_status("Balance Sheet balanced (A = L + E)", bs.is_balanced))
        print(_status("Net Income = Revenue - Expenses",
                      is_rpt.net_income == is_rpt.total_revenue - is_rpt.total_expenses))
        print(_status("Equity reconciles", eq.reconciles))
        print(_status("Cash flow reconciles", cf.cash_change_reconciles))
        print()

        return 0

    finally:
        # Always rollback — demo data should not persist
        session.rollback()
        session.close()


if __name__ == "__main__":
    sys.exit(main())
