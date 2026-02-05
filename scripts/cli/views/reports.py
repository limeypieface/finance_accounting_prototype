"""CLI views: journal, financial reports, subledger reports."""

from scripts.cli import config as cli_config
from scripts.cli.util import fmt_amount


def show_journal(session):
    """List journal entries with lines."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.journal import JournalEntry, JournalLine

    acct_map = {a.id: a for a in session.query(Account).all()}
    event_map = {}
    for evt in session.query(Event).all():
        memo = evt.payload.get("memo", "") if evt.payload and isinstance(evt.payload, dict) else ""
        if not memo:
            memo = evt.event_type or ""
        event_map[evt.event_id] = memo

    entries = session.query(JournalEntry).order_by(JournalEntry.seq).all()
    if not entries:
        print("\n  No journal entries yet.\n")
        return

    print()
    print("=" * 72)
    print("  JOURNAL ENTRIES".center(72))
    print("=" * 72)
    print()
    for entry in entries:
        memo = event_map.get(entry.source_event_id, "")
        status_str = entry.status.value if hasattr(entry.status, "value") else entry.status
        print(f"  Entry #{entry.seq}  |  {status_str.upper()}  |  {entry.effective_date}")
        if memo:
            print(f"  Memo: {memo}")
        print(f"  {'Account':<30} {'Debit':>14} {'Credit':>14}")
        print(f"  {'-'*30} {'-'*14} {'-'*14}")
        lines = session.query(JournalLine).filter(JournalLine.journal_entry_id == entry.id).order_by(JournalLine.line_seq).all()
        for line in lines:
            acct = acct_map.get(line.account_id)
            name = f"{acct.code}  {acct.name}" if acct else "?"
            if len(name) > 30:
                name = name[:27] + "..."
            side_val = line.side.value if hasattr(line.side, "value") else line.side
            if side_val == "debit":
                print(f"  {name:<30} {fmt_amount(line.amount):>14} {'':>14}")
            else:
                print(f"  {name:<30} {'':>14} {fmt_amount(line.amount):>14}")
        print()
    print(f"  Total: {len(entries)} journal entries")
    print()


def show_reports(session, clock):
    """Print trial balance, balance sheet, income statement, equity, cash flow."""
    from finance_modules.reporting.config import ReportingConfig
    from finance_modules.reporting.models import IncomeStatementFormat
    from finance_modules.reporting.service import ReportingService
    from scripts.demo_reports import (
        print_balance_sheet,
        print_cash_flow,
        print_equity_changes,
        print_income_statement,
        print_trial_balance,
    )

    config = ReportingConfig(entity_name=cli_config.ENTITY)
    svc = ReportingService(session=session, clock=clock, config=config)
    tb = svc.trial_balance(as_of_date=cli_config.FY_END)
    bs = svc.balance_sheet(as_of_date=cli_config.FY_END)
    is_rpt = svc.income_statement(period_start=cli_config.FY_START, period_end=cli_config.FY_END, format=IncomeStatementFormat.MULTI_STEP)
    eq = svc.equity_changes(period_start=cli_config.FY_START, period_end=cli_config.FY_END)
    cf = svc.cash_flow_statement(period_start=cli_config.FY_START, period_end=cli_config.FY_END)
    print_trial_balance(tb)
    print_balance_sheet(bs)
    print_income_statement(is_rpt)
    print_equity_changes(eq)
    print_cash_flow(cf)
    W = 72
    print("=" * W)
    print("  VERIFICATION SUMMARY".center(W))
    print("=" * W)
    def _status(label, ok):
        return f"  [{'OK' if ok else 'FAIL'}] {label}"
    print(_status("Trial Balance balanced", tb.is_balanced))
    print(_status("Balance Sheet balanced (A = L + E)", bs.is_balanced))
    print(_status("Net Income = Revenue - Expenses", is_rpt.net_income == is_rpt.total_revenue - is_rpt.total_expenses))
    print(_status("Equity reconciles", eq.reconciles))
    print(_status("Cash flow reconciles", cf.cash_change_reconciles))
    print()


def show_subledger_reports(session, clock):
    """Display subledger balances and entity-level detail."""
    from decimal import Decimal
    from finance_kernel.domain.subledger_control import SubledgerType
    from finance_kernel.selectors.subledger_selector import SubledgerSelector

    selector = SubledgerSelector(session)
    as_of = clock.now().date()
    currency = "USD"
    W = 72
    print()
    print("=" * W)
    print("  SUBLEDGER REPORTS".center(W))
    print("=" * W)
    sl_types = [
        (SubledgerType.AP, "Accounts Payable"),
        (SubledgerType.AR, "Accounts Receivable"),
        (SubledgerType.INVENTORY, "Inventory"),
        (SubledgerType.BANK, "Bank / Cash"),
        (SubledgerType.WIP, "Work in Progress"),
    ]
    for sl_type, label in sl_types:
        agg = selector.get_aggregate_balance(sl_type, as_of, currency)
        entities = selector.get_entities(sl_type)
        entry_count = selector.count_entries(sl_type, as_of, currency)
        print()
        print(f"  --- {label} ({sl_type.value}) ---")
        print(f"  Aggregate balance: {currency} {agg.amount:>14,.2f}")
        print(f"  Entries: {entry_count}   Entities: {len(entities)}")
        if entities:
            print()
            print(f"    {'Entity':<20} {'Balance':>14}")
            print(f"    {'------':<20} {'-------':>14}")
            for eid in entities:
                bal_dto = selector.get_balance(eid, sl_type, as_of, currency)
                if bal_dto:
                    print(f"    {eid:<20} {currency} {bal_dto.balance:>10,.2f}")
                else:
                    print(f"    {eid:<20} {currency} {'0.00':>10}")
            for eid in entities:
                open_items = selector.get_open_items(eid, sl_type, currency)
                if open_items:
                    print(f"\n    Open items for {eid}: {len(open_items)}")
                    for item in open_items[:5]:
                        side = "Dr" if item.debit_amount else "Cr"
                        amt = item.debit_amount or item.credit_amount or Decimal("0")
                        print(f"      {item.source_document_type:<15} {side} {currency} {amt:>10,.2f}  status={item.reconciliation_status}")
    print()
