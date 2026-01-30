#!/usr/bin/env python3
"""
Interactive Accounting CLI.

Post business events by number, view reports and journal entries
as data accumulates. All changes are committed immediately.

Usage:
    python3 scripts/interactive.py
"""

import logging
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
ENTITY = "Acme Manufacturing Co."
FY_START = date(2025, 1, 1)
FY_END = date(2025, 12, 31)

# (description, debit_role, credit_role, amount, dr_label, cr_label)
EVENTS = [
    ("Owner investment",             "CASH",       "RETAINED_EARNINGS", Decimal("500000.00"), "Cash",              "Retained Earnings"),
    ("Inventory purchase (on acct)", "INVENTORY",  "AP",                Decimal("100000.00"), "Inventory",         "Accounts Payable"),
    ("Cash sale",                    "CASH",       "REVENUE",           Decimal("150000.00"), "Cash",              "Sales Revenue"),
    ("Credit sale (on account)",     "AR",         "REVENUE",           Decimal("75000.00"),  "Accounts Receivable","Sales Revenue"),
    ("Cost of goods sold",           "COGS",       "INVENTORY",         Decimal("60000.00"),  "COGS",              "Inventory"),
    ("Pay salaries",                 "SALARY",     "CASH",              Decimal("45000.00"),  "Salary Expense",    "Cash"),
    ("Collect receivable",           "CASH",       "AR",                Decimal("25000.00"),  "Cash",              "Accounts Receivable"),
    ("Buy equipment",                "EQUIPMENT",  "CASH",              Decimal("80000.00"),  "Equipment",         "Cash"),
    ("Pay accounts payable",         "AP",         "CASH",              Decimal("30000.00"),  "Accounts Payable",  "Cash"),
    ("Record depreciation",          "DEPRECIATION","EQUIPMENT",        Decimal("10000.00"),  "Depreciation",      "Equipment"),
]


def _fmt(v) -> str:
    d = Decimal(str(v))
    return f"${d:,.0f}"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _kill_orphaned():
    from sqlalchemy import create_engine, text
    admin_url = DB_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            conn.execute(text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = 'finance_kernel_test'
                  AND pid <> pg_backend_pid()
            """))
        eng.dispose()
    except Exception:
        pass


def _tables_exist(session) -> bool:
    from sqlalchemy import text
    try:
        r = session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='accounts')"
        ))
        return r.scalar()
    except Exception:
        session.rollback()
        return False


def _has_accounts(session) -> bool:
    from finance_kernel.models.account import Account
    try:
        return session.query(Account).count() > 0
    except Exception:
        session.rollback()
        return False


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def full_setup(session, clock):
    """Create tables, COA, fiscal period. Returns (accounts_dict, post_fn)."""
    from finance_kernel.db.engine import create_tables, drop_tables
    from finance_kernel.db.immutability import register_immutability_listeners

    # ALL models must be imported BEFORE create_tables() so Base.metadata
    # knows about every table to create.
    import finance_kernel.models.account                # noqa: F401
    import finance_kernel.models.journal                # noqa: F401
    import finance_kernel.models.event                  # noqa: F401
    import finance_kernel.models.audit_event            # noqa: F401
    import finance_kernel.models.fiscal_period          # noqa: F401
    import finance_kernel.models.economic_link          # noqa: F401
    import finance_kernel.models.economic_event         # noqa: F401
    import finance_kernel.models.interpretation_outcome # noqa: F401
    import finance_kernel.models.party                  # noqa: F401
    import finance_kernel.models.contract               # noqa: F401
    import finance_kernel.models.dimensions             # noqa: F401
    import finance_kernel.models.exchange_rate          # noqa: F401
    import finance_kernel.services.sequence_service     # noqa: F401 — SequenceCounter

    _kill_orphaned()
    time.sleep(0.3)
    try:
        drop_tables()
    except Exception:
        _kill_orphaned()
        time.sleep(0.5)
        try:
            drop_tables()
        except Exception:
            pass

    create_tables(install_triggers=True)
    register_immutability_listeners()

    # Need a fresh session after DDL
    from finance_kernel.db.engine import get_session
    new_session = get_session()

    accounts = _create_coa(new_session, clock)
    _create_period(new_session, clock)
    new_session.commit()

    post = _build_pipeline(new_session, accounts, clock)
    return new_session, accounts, post


def _create_coa(session, clock):
    from finance_kernel.models.account import Account, AccountType, NormalBalance

    actor_id = uuid4()
    specs = [
        ("1000", "Cash",                 AccountType.ASSET,     NormalBalance.DEBIT,  None,             "CASH"),
        ("1100", "Accounts Receivable",  AccountType.ASSET,     NormalBalance.DEBIT,  None,             "AR"),
        ("1200", "Inventory",            AccountType.ASSET,     NormalBalance.DEBIT,  None,             "INVENTORY"),
        ("1500", "Equipment",            AccountType.ASSET,     NormalBalance.DEBIT,  None,             "EQUIPMENT"),
        ("2000", "Accounts Payable",     AccountType.LIABILITY, NormalBalance.CREDIT, None,             "AP"),
        ("3200", "Retained Earnings",    AccountType.EQUITY,    NormalBalance.CREDIT, None,             "RETAINED_EARNINGS"),
        ("4000", "Sales Revenue",        AccountType.REVENUE,   NormalBalance.CREDIT, None,             "REVENUE"),
        ("5000", "Cost of Goods Sold",   AccountType.EXPENSE,   NormalBalance.DEBIT,  None,             "COGS"),
        ("5100", "Operating Expense",    AccountType.EXPENSE,   NormalBalance.DEBIT,  None,             "EXPENSE"),
        ("5600", "Depreciation Expense", AccountType.EXPENSE,   NormalBalance.DEBIT,  ["depreciation"], "DEPRECIATION"),
        ("5800", "Salary Expense",       AccountType.EXPENSE,   NormalBalance.DEBIT,  None,             "SALARY"),
        ("9999", "Rounding",             AccountType.EXPENSE,   NormalBalance.DEBIT,  ["rounding"],     "ROUNDING"),
    ]

    accounts = {}
    for code, name, atype, nbal, tags, role_key in specs:
        acct = Account(
            code=code, name=name, account_type=atype, normal_balance=nbal,
            is_active=True, tags=tags, created_by_id=actor_id,
        )
        session.add(acct)
        session.flush()
        accounts[role_key] = acct
    return accounts


def _create_period(session, clock):
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus

    period = FiscalPeriod(
        period_code="FY2025", name="Fiscal Year 2025",
        start_date=FY_START, end_date=FY_END,
        status=PeriodStatus.OPEN, created_by_id=uuid4(),
    )
    session.add(period)
    session.flush()


def _load_existing_accounts(session):
    """Load accounts from an already-seeded database into a role-keyed dict."""
    from finance_kernel.models.account import Account

    # Map account codes back to role keys
    code_to_role = {
        "1000": "CASH", "1100": "AR", "1200": "INVENTORY", "1500": "EQUIPMENT",
        "2000": "AP", "3200": "RETAINED_EARNINGS", "4000": "REVENUE",
        "5000": "COGS", "5100": "EXPENSE", "5600": "DEPRECIATION",
        "5800": "SALARY", "9999": "ROUNDING",
    }
    accounts = {}
    for acct in session.query(Account).all():
        role = code_to_role.get(acct.code)
        if role:
            accounts[role] = acct
    return accounts


def _build_pipeline(session, accounts, clock):
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
    from finance_kernel.services.outcome_recorder import OutcomeRecorder
    from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent, AccountingIntentSnapshot, IntentLine, LedgerIntent,
    )
    from finance_kernel.domain.meaning_builder import EconomicEventData, MeaningBuilderResult
    from finance_kernel.models.event import Event
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

        payload = {"memo": memo, "amount": str(amount)}
        evt = Event(
            event_id=source_event_id, event_type="interactive.posting",
            occurred_at=clock.now(), effective_date=effective,
            actor_id=actor_id, producer="interactive",
            payload=payload, payload_hash=hash_payload(payload),
            schema_version=1, ingested_at=clock.now(),
        )
        session.add(evt)
        session.flush()

        econ_data = EconomicEventData(
            source_event_id=source_event_id, economic_type="interactive.posting",
            effective_date=effective, profile_id="InteractiveProfile",
            profile_version=1, profile_hash=None, quantity=amount,
        )
        intent = AccountingIntent(
            econ_event_id=uuid4(), source_event_id=source_event_id,
            profile_id="InteractiveProfile", profile_version=1,
            effective_date=effective,
            ledger_intents=(
                LedgerIntent(ledger_id="GL", lines=(
                    IntentLine.debit(debit_role, amount, "USD"),
                    IntentLine.credit(credit_role, amount, "USD"),
                )),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        result = coordinator.interpret_and_post(
            meaning_result=MeaningBuilderResult.ok(econ_data),
            accounting_intent=intent,
            actor_id=actor_id,
        )
        session.flush()
        return result

    return post


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_menu():
    print()
    print("=" * 72)
    print("  INTERACTIVE ACCOUNTING CLI".center(72))
    print("=" * 72)
    print()
    print("  Post an event:")
    for i, (desc, dr, cr, amt, dr_lbl, cr_lbl) in enumerate(EVENTS, 1):
        print(f"   {i:>2}.  {desc:<34} {_fmt(amt):>10}    Dr {dr_lbl} / Cr {cr_lbl}")
    print()
    print("  View:")
    print("    R   View all reports")
    print("    J   View journal entries")
    print("    T   Trace a journal entry (full auditor decision trail)")
    print()
    print("  Other:")
    print("    X   Reset database (drop all data, start fresh)")
    print("    Q   Quit")
    print()


def show_journal(session):
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event

    acct_map = {a.id: a for a in session.query(Account).all()}
    event_map = {}
    for evt in session.query(Event).all():
        memo = ""
        if evt.payload and isinstance(evt.payload, dict):
            memo = evt.payload.get("memo", "")
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
        status_str = entry.status.value if hasattr(entry.status, 'value') else entry.status
        print(f"  Entry #{entry.seq}  |  {status_str.upper()}  |  {entry.effective_date}")
        if memo:
            print(f"  Memo: {memo}")
        print(f"  {'Account':<30} {'Debit':>14} {'Credit':>14}")
        print(f"  {'-'*30} {'-'*14} {'-'*14}")

        lines = (
            session.query(JournalLine)
            .filter(JournalLine.journal_entry_id == entry.id)
            .order_by(JournalLine.line_seq)
            .all()
        )
        for line in lines:
            acct = acct_map.get(line.account_id)
            name = f"{acct.code}  {acct.name}" if acct else "?"
            side_val = line.side.value if hasattr(line.side, 'value') else line.side
            if side_val == "debit":
                print(f"  {name:<30} {_fmt(line.amount):>14} {'':>14}")
            else:
                print(f"  {name:<30} {'':>14} {_fmt(line.amount):>14}")
        print()

    print(f"  Total: {len(entries)} journal entries")
    print()


def show_trace(session):
    """Let the user pick a journal entry by sequence number and trace it."""
    from finance_kernel.models.journal import JournalEntry, JournalLine
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome
    from finance_kernel.selectors.trace_selector import TraceSelector

    entries = session.query(JournalEntry).order_by(JournalEntry.seq).all()
    if not entries:
        print("\n  No journal entries to trace.\n")
        return

    # Build memo lookup
    event_map = {}
    for evt in session.query(Event).all():
        memo = ""
        if evt.payload and isinstance(evt.payload, dict):
            memo = evt.payload.get("memo", "")
        event_map[evt.event_id] = memo

    # Check which have decision journals
    outcomes = (
        session.query(InterpretationOutcome)
        .filter(InterpretationOutcome.decision_log.isnot(None))
        .all()
    )
    events_with_journal = {o.source_event_id for o in outcomes}

    print()
    print("=" * 72)
    print("  TRACE A JOURNAL ENTRY".center(72))
    print("=" * 72)
    print()
    print(f"  {'#':>3}  {'status':<8}  {'journal':<8}  {'memo'}")
    print(f"  {'---':>3}  {'------':<8}  {'-------':<8}  {'----'}")

    for entry in entries:
        status_val = entry.status.value if hasattr(entry.status, 'value') else str(entry.status)
        memo = event_map.get(entry.source_event_id, "")
        has_log = "YES" if entry.source_event_id in events_with_journal else "no"
        print(f"  {entry.seq:>3}  {status_val:<8}  {has_log:<8}  {memo}")

    print()
    try:
        pick = input("  Enter entry # to trace (or blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not pick:
        return

    try:
        seq_num = int(pick)
    except ValueError:
        print(f"\n  Invalid number '{pick}'.\n")
        return

    target = None
    for entry in entries:
        if entry.seq == seq_num:
            target = entry
            break

    if target is None:
        print(f"\n  No entry with seq #{seq_num}.\n")
        return

    # Run the trace
    memo = event_map.get(target.source_event_id, f"entry #{target.seq}")
    print()
    print("=" * 72)
    print(f"  AUDIT TRACE: Entry #{target.seq} — {memo}")
    print("=" * 72)

    selector = TraceSelector(session)
    bundle = selector.trace_by_event_id(target.source_event_id)

    # Origin
    print()
    print("--- ORIGIN EVENT ---")
    print()
    if bundle.origin:
        o = bundle.origin
        print(f"    event_id: {o.event_id}")
        print(f"    event_type: {o.event_type}")
        print(f"    occurred_at: {o.occurred_at}")
        print(f"    effective_date: {o.effective_date}")
        print(f"    producer: {o.producer}")
        print(f"    payload_hash: {o.payload_hash}")

    # Journal entry
    print()
    print(f"--- JOURNAL ENTRIES ({len(bundle.journal_entries)}) ---")
    print()
    for je in bundle.journal_entries:
        print(f"    entry_id: {je.entry_id}")
        print(f"    status: {je.status}  seq: {je.seq}")
        print(f"    effective_date: {je.effective_date}  posted_at: {je.posted_at}")
        print()
        print(f"      {'seq':>4}  {'side':<7} {'amount':>14}  {'curr':<4}  {'account':<12}  {'rounding'}")
        print(f"      {'---':>4}  {'----':<7} {'------':>14}  {'----':<4}  {'-------':<12}  {'--------'}")
        for line in je.lines:
            print(f"      {line.line_seq:>4}  {line.side:<7} "
                  f"{line.amount:>14}  {line.currency:<4}  "
                  f"{line.account_code:<12}  {line.is_rounding}")
        print()

    # Interpretation
    if bundle.interpretation:
        interp = bundle.interpretation
        print("--- INTERPRETATION OUTCOME ---")
        print()
        print(f"    status: {interp.status}")
        print(f"    profile: {interp.profile_id} v{interp.profile_version}")
        if interp.decision_log:
            print(f"    decision_log_records: {len(interp.decision_log)}")
        print()

    # Decision journal
    log_entries = [t for t in bundle.timeline if t.source == "structured_log"]
    audit_entries = [t for t in bundle.timeline if t.source == "audit_event"]

    print(f"--- DECISION JOURNAL ({len(bundle.timeline)} entries) ---")
    print()

    if log_entries:
        for i, te in enumerate(log_entries):
            action = te.action
            d = te.detail or {}

            if action == "interpretation_started":
                print(f"  [{i:>2}] INTERPRETATION STARTED")
                print(f"       Profile: {d.get('profile_id')} v{d.get('profile_version')}")
                print(f"       Event: {str(d.get('source_event_id', ''))[:8]}...")
            elif action == "config_in_force":
                print(f"  [{i:>2}] CONFIG SNAPSHOT (R21)")
                print(f"       COA: {d.get('coa_version')}  Dim: {d.get('dimension_schema_version')}  "
                      f"Rounding: {d.get('rounding_policy_version')}  Currency: {d.get('currency_registry_version')}")
            elif action == "journal_write_started":
                print(f"  [{i:>2}] JOURNAL WRITE STARTED — {d.get('ledger_count')} ledger(s)")
            elif action == "balance_validated":
                balanced = d.get('balanced')
                print(f"  [{i:>2}] BALANCE VALIDATED — {d.get('ledger_id')} {d.get('currency')}  "
                      f"Dr {d.get('sum_debit')} = Cr {d.get('sum_credit')}  "
                      f"{'PASS' if balanced else 'FAIL'}")
            elif action == "role_resolved":
                print(f"  [{i:>2}] ROLE RESOLVED — {d.get('role')} -> {d.get('account_code')}  "
                      f"{d.get('side')} {d.get('amount')} {d.get('currency')}")
            elif action == "line_written":
                print(f"  [{i:>2}] LINE WRITTEN — seq {d.get('line_seq')}: "
                      f"{d.get('role')} -> {d.get('account_code')}  "
                      f"{d.get('side')} {d.get('amount')} {d.get('currency')}")
            elif action == "invariant_checked":
                passed = d.get('passed')
                print(f"  [{i:>2}] INVARIANT — {d.get('invariant')}: {'PASS' if passed else 'FAIL'}")
            elif action == "journal_entry_created":
                print(f"  [{i:>2}] ENTRY CREATED — {str(d.get('entry_id', ''))[:8]}...  "
                      f"status: {d.get('status')}  seq: {d.get('seq')}")
            elif action == "journal_write_completed":
                print(f"  [{i:>2}] WRITE COMPLETED — {d.get('entry_count')} entries  {d.get('duration_ms')}ms")
            elif action == "outcome_recorded":
                print(f"  [{i:>2}] OUTCOME RECORDED — {d.get('status')}")
            elif action == "interpretation_posted":
                print(f"  [{i:>2}] INTERPRETATION POSTED — {d.get('entry_count')} entries")
            elif action == "reproducibility_proof":
                print(f"  [{i:>2}] REPRODUCIBILITY PROOF")
                print(f"       input:  {str(d.get('input_hash', ''))[:16]}...")
                print(f"       output: {str(d.get('output_hash', ''))[:16]}...")
            elif action == "FINANCE_KERNEL_TRACE":
                print(f"  [{i:>2}] KERNEL TRACE — {d.get('policy_name')} v{d.get('policy_version')}  "
                      f"outcome: {d.get('outcome_status')}")
            elif action == "interpretation_completed":
                print(f"  [{i:>2}] COMPLETED — success: {d.get('success')}  {d.get('duration_ms')}ms")
            else:
                print(f"  [{i:>2}] {action}")
                for k, v in d.items():
                    if k not in ("ts", "timestamp", "level", "logger"):
                        print(f"       {k}: {v}")
            print()

    if audit_entries:
        print(f"  Audit trail ({len(audit_entries)}):")
        for i, te in enumerate(audit_entries):
            print(f"    {i:>3}  {te.action:<30} {te.entity_type or '':<15}")
        print()

    # Integrity
    print("--- INTEGRITY ---")
    print()
    integrity = bundle.integrity
    print(f"    payload_hash_verified: {integrity.payload_hash_verified}")
    print(f"    balance_verified: {integrity.balance_verified}")
    print(f"    audit_chain_valid: {integrity.audit_chain_segment_valid}")
    all_ok = integrity.payload_hash_verified and integrity.balance_verified
    print(f"    result: {'ALL CHECKS PASSED' if all_ok else 'ISSUES DETECTED'}")

    # Missing facts
    if bundle.missing_facts:
        print()
        print(f"--- MISSING FACTS ({len(bundle.missing_facts)}) ---")
        for mf in bundle.missing_facts:
            print(f"    [{mf.fact}] {mf.expected_source}")
    else:
        print()
        print("  Trace is complete — 0 missing facts.")

    print()


def show_reports(session, clock):
    from finance_modules.reporting.service import ReportingService
    from finance_modules.reporting.models import IncomeStatementFormat
    from finance_modules.reporting.config import ReportingConfig
    from scripts.demo_reports import (
        print_trial_balance, print_balance_sheet, print_income_statement,
        print_equity_changes, print_cash_flow,
    )

    config = ReportingConfig(entity_name=ENTITY)
    svc = ReportingService(session=session, clock=clock, config=config)

    tb = svc.trial_balance(as_of_date=FY_END)
    bs = svc.balance_sheet(as_of_date=FY_END)
    is_rpt = svc.income_statement(
        period_start=FY_START, period_end=FY_END,
        format=IncomeStatementFormat.MULTI_STEP,
    )
    eq = svc.equity_changes(period_start=FY_START, period_end=FY_END)
    cf = svc.cash_flow_statement(period_start=FY_START, period_end=FY_END)

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
    print(_status("Net Income = Revenue - Expenses",
                  is_rpt.net_income == is_rpt.total_revenue - is_rpt.total_expenses))
    print(_status("Equity reconciles", eq.reconciles))
    print(_status("Cash flow reconciles", cf.cash_change_reconciles))
    print()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _enable_quiet_logging():
    """Enable logging so LogCapture works, but mute console output."""
    logging.disable(logging.NOTSET)
    fk_logger = logging.getLogger("finance_kernel")
    muted = []
    for h in fk_logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            muted.append((h, h.level))
            h.setLevel(logging.CRITICAL + 1)
    return muted


def _restore_logging(muted):
    """Restore muted handlers and re-disable logging."""
    for h, orig_level in muted:
        h.setLevel(orig_level)
    logging.disable(logging.CRITICAL)


def main() -> int:
    logging.disable(logging.CRITICAL)

    from finance_kernel.db.engine import init_engine_from_url, get_session
    from finance_kernel.domain.clock import DeterministicClock

    try:
        init_engine_from_url(DB_URL, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    clock = DeterministicClock(datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
    session = get_session()

    # Check if we need setup
    if not _tables_exist(session) or not _has_accounts(session):
        print("\n  No data found — setting up fresh database...")
        session.close()
        session, accounts, post = full_setup(session, clock)
    else:
        print("\n  Found existing data — resuming.")
        accounts = _load_existing_accounts(session)
        post = _build_pipeline(session, accounts, clock)

    # Count existing entries
    from finance_kernel.models.journal import JournalEntry
    entry_count = session.query(JournalEntry).count()
    print(f"  Journal has {entry_count} entries.")

    while True:
        print_menu()
        try:
            choice = input("  Pick: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break

        if choice == "Q":
            print("\n  Goodbye.\n")
            break

        elif choice == "R":
            show_reports(session, clock)

        elif choice == "J":
            show_journal(session)

        elif choice == "T":
            show_trace(session)

        elif choice == "X":
            print("\n  Resetting database...")
            session.close()
            session, accounts, post = full_setup(session, clock)
            print("  Done. Database is empty.\n")

        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(EVENTS):
                desc, dr, cr, amt, dr_lbl, cr_lbl = EVENTS[idx]
                muted = _enable_quiet_logging()
                result = post(dr, cr, amt, desc)
                _restore_logging(muted)
                if result.success:
                    session.commit()
                    evt_id = result.outcome.source_event_id if result.outcome else "?"
                    print(f"\n  Posted: {desc} -- {_fmt(amt)}  (Dr {dr_lbl} / Cr {cr_lbl})")
                    print(f"    Trace: python3 scripts/trace.py --event-id {evt_id}")
                else:
                    session.rollback()
                    print(f"\n  FAILED: {result.error_code}: {result.error_message}")
            else:
                print(f"\n  Invalid number. Pick 1-{len(EVENTS)}.")

        else:
            print(f"\n  Unknown command '{choice}'. Try a number, R, J, X, or Q.")

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
