#!/usr/bin/env python3
"""
Interactive Accounting CLI.

Post business events by number, view reports and journal entries
as data accumulates. All changes are committed immediately.

Supports two event types:
  - Simple bookkeeping (1-10): Direct debit/credit via InterpretationCoordinator
  - Engine scenarios (11-25): Full posting pipeline via ModulePostingService with
    variance, tax, matching, allocation, and billing engines

Usage:
    python3 scripts/interactive.py
"""

import logging
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4, uuid5

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
ENTITY = "Acme Manufacturing Co."
FY_START = date(2026, 1, 1)
FY_END = date(2026, 12, 31)
EFFECTIVE = date(2026, 6, 15)
COA_UUID_NS = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# ---------------------------------------------------------------------------
# Simple bookkeeping events (posted via InterpretationCoordinator)
# (description, debit_role, credit_role, amount, dr_label, cr_label)
# ---------------------------------------------------------------------------
SIMPLE_EVENTS = [
    ("Owner investment",             "CASH",                  "RETAINED_EARNINGS",      Decimal("500000.00"), "Cash",              "Retained Earnings"),
    ("Inventory purchase (on acct)", "INVENTORY",             "ACCOUNTS_PAYABLE",       Decimal("100000.00"), "Inventory",         "Accounts Payable"),
    ("Cash sale",                    "CASH",                  "REVENUE",                Decimal("150000.00"), "Cash",              "Sales Revenue"),
    ("Credit sale (on account)",     "ACCOUNTS_RECEIVABLE",   "REVENUE",                Decimal("75000.00"),  "Accounts Receivable","Sales Revenue"),
    ("Cost of goods sold",           "COGS",                  "INVENTORY",              Decimal("60000.00"),  "COGS",              "Inventory"),
    ("Pay salaries",                 "SALARY_EXPENSE",        "CASH",                   Decimal("45000.00"),  "Salary Expense",    "Cash"),
    ("Collect receivable",           "CASH",                  "ACCOUNTS_RECEIVABLE",    Decimal("25000.00"),  "Cash",              "Accounts Receivable"),
    ("Buy equipment",                "FIXED_ASSET",           "CASH",                   Decimal("80000.00"),  "Equipment",         "Cash"),
    ("Pay accounts payable",         "ACCOUNTS_PAYABLE",      "CASH",                   Decimal("30000.00"),  "Accounts Payable",  "Cash"),
    ("Record depreciation",          "DEPRECIATION_EXPENSE",  "ACCUMULATED_DEPRECIATION",Decimal("10000.00"), "Depreciation",      "Accum. Depreciation"),
]

# ---------------------------------------------------------------------------
# Engine scenarios (posted via ModulePostingService.post_event)
# ---------------------------------------------------------------------------
ENGINE_SCENARIOS = [
    # --- variance engine (4) ---
    {
        "id": "V1", "label": "Inventory Receipt with PPV",
        "engine": "variance", "event_type": "inventory.receipt",
        "amount": Decimal("10500.00"),
        "payload": {
            "quantity": 1000, "has_variance": True,
            "standard_price": "10.00", "actual_price": "10.50",
            "standard_total": "10000.00", "variance_amount": "500.00",
            "variance_type": "price", "expected_price": "10.00",
        },
        "business": "PO $10/unit, invoice $10.50/unit, 1000u -> $500 PPV",
    },
    {
        "id": "V2", "label": "WIP Labor Efficiency Variance",
        "engine": "variance", "event_type": "wip.labor_variance",
        "amount": Decimal("450.00"),
        "payload": {
            "quantity": 160, "standard_hours": 150, "actual_hours": 160,
            "standard_rate": "45.00", "actual_rate": "45.00",
            "variance_type": "quantity", "expected_quantity": "150",
            "actual_quantity": "160", "standard_price": "45.00",
        },
        "business": "Std 150hrs, actual 160hrs at $45/hr -> $450 unfavorable",
    },
    {
        "id": "V3", "label": "WIP Material Usage Variance",
        "engine": "variance", "event_type": "wip.material_variance",
        "amount": Decimal("3750.00"),
        "payload": {
            "quantity": 1150, "standard_quantity": 1000,
            "actual_quantity": 1150, "standard_price": "25.00",
            "variance_type": "quantity", "expected_quantity": "1000",
        },
        "business": "Std 1000u, used 1150 at $25 -> $3,750 unfavorable",
    },
    {
        "id": "V4", "label": "WIP Overhead Variance",
        "engine": "variance", "event_type": "wip.overhead_variance",
        "amount": Decimal("4500.00"),
        "payload": {
            "quantity": 1, "applied_overhead": "67500.00",
            "actual_overhead": "72000.00", "variance_type": "standard_cost",
            "standard_cost": "67500.00", "actual_cost": "72000.00",
        },
        "business": "Applied $67,500, actual $72,000 -> $4,500 under-applied",
    },
    # --- tax engine (2) ---
    {
        "id": "T1", "label": "Use Tax Self-Assessment (CA 8%)",
        "engine": "tax", "event_type": "tax.use_tax_accrued",
        "amount": Decimal("3200.00"),
        "payload": {
            "amount": "3200.00", "jurisdiction": "CA",
            "purchase_amount": "40000.00", "use_tax_rate": "0.08",
        },
        "business": "$40K equipment from out-of-state, CA use tax 8%",
    },
    # --- matching engine (1) ---
    {
        "id": "M1", "label": "AP Invoice PO-Matched (Three-Way)",
        "engine": "matching", "event_type": "ap.invoice_received",
        "amount": Decimal("49500.00"),
        "payload": {
            "po_number": "PO-2026-0042", "gross_amount": "49500.00",
            "vendor_id": "V-100", "po_amount": "50000.00",
            "receipt_amount": "49000.00", "receipt_quantity": 490,
            "po_quantity": 500, "invoice_quantity": 495,
            "match_operation": "create_match", "match_type": "three_way",
            "match_documents": [
                {"document_id": "PO-2026-0042", "document_type": "purchase_order",
                 "amount": "50000.00", "quantity": 500},
                {"document_id": "RCV-2026-0088", "document_type": "receipt",
                 "amount": "49000.00", "quantity": 490},
                {"document_id": "INV-2026-1234", "document_type": "invoice",
                 "amount": "49500.00", "quantity": 495},
            ],
        },
        "business": "PO $50K/500u, Receipt 490u/$49K, Invoice $49,500/495u",
    },
    # --- billing engine (1) ---
    {
        "id": "B1", "label": "Govt Contract Billing CPFF",
        "engine": "billing", "event_type": "contract.billing_provisional",
        "amount": Decimal("285000.00"),
        "payload": {
            "billing_type": "COST_REIMBURSEMENT",
            "total_billing": "285000.00", "cost_billing": "263889.00",
            "fee_amount": "21111.00",
            "contract_number": "FA8750-21-C-0001",
            "contract_type": "CPFF", "fee_rate": "0.08",
            "billing_input": {
                "contract_type": "CPFF",
                "cost_breakdown": {
                    "direct_labor": "150000.00", "direct_material": "50000.00",
                    "subcontract": "0.00", "travel": "5000.00", "odc": "2000.00",
                },
                "indirect_rates": {"fringe": "0.35", "overhead": "0.45", "ga": "0.10"},
                "fee_rate": "0.08", "currency": "USD",
            },
        },
        "business": "CPFF: $263,889 costs + $21,111 fee (8%) = $285,000",
    },
]

NON_ENGINE_SCENARIOS = [
    {
        "id": "N1", "label": "Standard Inventory Receipt",
        "event_type": "inventory.receipt",
        "amount": Decimal("25000.00"),
        "payload": {"quantity": 500, "has_variance": False},
        "business": "500 units at $50/unit, standard cost receipt",
    },
    {
        "id": "N2", "label": "Inventory Issue to Production",
        "event_type": "inventory.issue",
        "amount": Decimal("10000.00"),
        "payload": {"issue_type": "PRODUCTION", "quantity": 200},
        "business": "200 units raw material issued to production WIP",
    },
    {
        "id": "N3", "label": "Inventory Issue for Sale (COGS)",
        "event_type": "inventory.issue",
        "amount": Decimal("15000.00"),
        "payload": {"issue_type": "SALE", "quantity": 300},
        "business": "300 units shipped for sale, COGS recognized",
    },
    {
        "id": "N4", "label": "AP Direct Expense Invoice",
        "event_type": "ap.invoice_received",
        "amount": Decimal("8500.00"),
        "payload": {
            "po_number": None, "gross_amount": "8500.00", "vendor_id": "V-200",
        },
        "business": "Direct expense invoice for consulting services",
    },
    {
        "id": "N5", "label": "Payroll Accrual",
        "event_type": "payroll.accrual",
        "amount": Decimal("125000.00"),
        "payload": {
            "gross_amount": "125000.00",
            "federal_tax_amount": "25000.00",
            "state_tax_amount": "8750.00",
            "fica_amount": "9562.50",
            "benefits_amount": "6250.00",
            "net_pay_amount": "75437.50",
        },
        "business": "Monthly payroll: $125K gross, $43.3K withholdings, $75.4K net",
    },
]

ALL_PIPELINE_SCENARIOS = ENGINE_SCENARIOS + NON_ENGINE_SCENARIOS

# ---------------------------------------------------------------------------
# Subledger demo scenarios (SL-Phase 9)
# Posted as GL entries + subledger entries via concrete services.
# ---------------------------------------------------------------------------
SUBLEDGER_SCENARIOS = [
    {
        "id": "SL1", "label": "AP Invoice — Vendor V-100",
        "gl_debit": "EXPENSE", "gl_credit": "ACCOUNTS_PAYABLE",
        "sl_type": "AP", "entity_id": "V-100",
        "doc_type": "INVOICE", "amount": Decimal("15000.00"),
        "memo": "AP Invoice from Vendor V-100",
    },
    {
        "id": "SL2", "label": "AP Payment — Vendor V-100",
        "gl_debit": "ACCOUNTS_PAYABLE", "gl_credit": "CASH",
        "sl_type": "AP", "entity_id": "V-100",
        "doc_type": "PAYMENT", "amount": Decimal("15000.00"),
        "memo": "AP Payment to Vendor V-100",
    },
    {
        "id": "SL3", "label": "AR Invoice — Customer C-200",
        "gl_debit": "ACCOUNTS_RECEIVABLE", "gl_credit": "REVENUE",
        "sl_type": "AR", "entity_id": "C-200",
        "doc_type": "INVOICE", "amount": Decimal("25000.00"),
        "memo": "AR Invoice to Customer C-200",
    },
    {
        "id": "SL4", "label": "AR Payment — Customer C-200",
        "gl_debit": "CASH", "gl_credit": "ACCOUNTS_RECEIVABLE",
        "sl_type": "AR", "entity_id": "C-200",
        "doc_type": "PAYMENT", "amount": Decimal("25000.00"),
        "memo": "AR Payment from Customer C-200",
    },
    {
        "id": "SL5", "label": "Inventory Receipt — SKU-A",
        "gl_debit": "INVENTORY", "gl_credit": "ACCOUNTS_PAYABLE",
        "sl_type": "INVENTORY", "entity_id": "SKU-A",
        "doc_type": "RECEIPT", "amount": Decimal("8000.00"),
        "memo": "Inventory receipt 400u @ $20",
    },
    {
        "id": "SL6", "label": "Inventory Issue (COGS) — SKU-A",
        "gl_debit": "COGS", "gl_credit": "INVENTORY",
        "sl_type": "INVENTORY", "entity_id": "SKU-A",
        "doc_type": "ISSUE", "amount": Decimal("3000.00"),
        "memo": "Issue 150u @ $20 for sale",
    },
    {
        "id": "SL7", "label": "Bank Deposit",
        "gl_debit": "CASH", "gl_credit": "REVENUE",
        "sl_type": "BANK", "entity_id": "ACCT-001",
        "doc_type": "DEPOSIT", "amount": Decimal("50000.00"),
        "memo": "Bank deposit from daily sales",
    },
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
# Account setup from YAML config
# ---------------------------------------------------------------------------

def _account_type_for_code(code: str):
    from finance_kernel.models.account import AccountType
    if code.startswith("SL-"):
        return AccountType.ASSET
    prefix = int(code[0]) if code[0].isdigit() else 0
    if prefix == 1:
        return AccountType.ASSET
    elif prefix == 2:
        return AccountType.LIABILITY
    elif prefix == 3:
        return AccountType.EQUITY
    elif prefix == 4:
        return AccountType.REVENUE
    elif prefix in (5, 6):
        return AccountType.EXPENSE
    return AccountType.EXPENSE


def _normal_balance_for_type(atype):
    from finance_kernel.models.account import AccountType, NormalBalance
    if atype in (AccountType.ASSET, AccountType.EXPENSE):
        return NormalBalance.DEBIT
    return NormalBalance.CREDIT


def _create_accounts_from_config(session, config, actor_id):
    from finance_kernel.models.account import Account

    created = 0
    seen_codes = set()
    for binding in config.role_bindings:
        code = binding.account_code
        if code in seen_codes:
            continue
        seen_codes.add(code)

        acct_id = uuid5(COA_UUID_NS, code)
        atype = _account_type_for_code(code)
        nbal = _normal_balance_for_type(atype)
        tags = ["rounding"] if binding.role == "ROUNDING" else None

        acct = Account(
            id=acct_id, code=code,
            name=f"{binding.role} ({code})",
            account_type=atype, normal_balance=nbal,
            is_active=True, tags=tags, created_by_id=actor_id,
        )
        session.add(acct)
        created += 1

    session.flush()
    return created


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def full_setup(session, clock):
    """Create tables, config-based COA, fiscal period, wire both pipelines.

    Returns (session, post_simple, post_engine).
    """
    from finance_kernel.db.engine import drop_tables, get_session
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_modules._orm_registry import create_all_tables

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

    create_all_tables(install_triggers=True)
    register_immutability_listeners()

    new_session = get_session()

    # Load YAML config
    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_services.invokers import register_standard_engines
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from finance_kernel.models.party import Party, PartyType, PartyStatus
    from finance_services.posting_orchestrator import PostingOrchestrator
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_modules import register_all_modules

    config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)
    actor_id = uuid4()

    # Create accounts from config
    acct_count = _create_accounts_from_config(new_session, config, actor_id)

    # Fiscal period
    period = FiscalPeriod(
        period_code="FY2026", name="Fiscal Year 2026",
        start_date=FY_START, end_date=FY_END,
        status=PeriodStatus.OPEN, created_by_id=actor_id,
    )
    new_session.add(period)

    # Actor party (G14 actor authorization)
    actor_party = Party(
        id=actor_id, party_code="SYSTEM-DEMO",
        party_type=PartyType.EMPLOYEE, name="Demo System Actor",
        status=PartyStatus.ACTIVE, is_active=True,
        created_by_id=actor_id,
    )
    new_session.add(actor_party)
    new_session.flush()

    # Register module profiles
    register_all_modules()

    # Wire PostingOrchestrator + engines
    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=new_session, compiled_pack=config,
        role_resolver=role_resolver, clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)

    # Build ModulePostingService for engine scenarios
    engine_service = ModulePostingService.from_orchestrator(
        orchestrator, auto_commit=False,
    )

    # Build simple post() using the same session/resolver/coordinator
    post_simple = _build_simple_pipeline(
        new_session, role_resolver, orchestrator, actor_id,
    )

    new_session.commit()

    return new_session, post_simple, engine_service, actor_id, config, orchestrator


def resume_setup(clock):
    """Reconnect to existing database without dropping tables.

    Rebuilds in-memory service wiring (orchestrator, engines, pipelines)
    while preserving all persisted data from prior runs.

    Returns the same 6-tuple as full_setup().
    """
    from finance_kernel.db.engine import get_session
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_services.invokers import register_standard_engines
    from finance_kernel.models.party import Party
    from finance_services.posting_orchestrator import PostingOrchestrator
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_modules import register_all_modules

    register_immutability_listeners()

    session = get_session()

    config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)

    # Recover actor_id from the persisted demo party
    actor_party = session.query(Party).filter_by(party_code="SYSTEM-DEMO").first()
    if actor_party is None:
        raise RuntimeError("Cannot resume: SYSTEM-DEMO actor not found. Use Reset.")
    actor_id = actor_party.id

    register_all_modules()

    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=session, compiled_pack=config,
        role_resolver=role_resolver, clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)

    engine_service = ModulePostingService.from_orchestrator(
        orchestrator, auto_commit=False,
    )

    post_simple = _build_simple_pipeline(
        session, role_resolver, orchestrator, actor_id,
    )

    return session, post_simple, engine_service, actor_id, config, orchestrator


def _build_simple_pipeline(session, role_resolver, orchestrator, actor_id):
    """Build a post() function for simple debit/credit events."""
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent, AccountingIntentSnapshot, IntentLine, LedgerIntent,
    )
    from finance_kernel.domain.meaning_builder import EconomicEventData, MeaningBuilderResult
    from finance_kernel.models.event import Event
    from finance_kernel.utils.hashing import hash_payload

    coordinator = orchestrator.interpretation_coordinator
    clock = orchestrator.clock

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
    W = 80
    print()
    print("=" * W)
    print("  INTERACTIVE ACCOUNTING CLI".center(W))
    print("=" * W)
    print()
    print("  SIMPLE BOOKKEEPING:")
    for i, (desc, dr, cr, amt, dr_lbl, cr_lbl) in enumerate(SIMPLE_EVENTS, 1):
        print(f"   {i:>2}.  {desc:<34} {_fmt(amt):>10}    Dr {dr_lbl} / Cr {cr_lbl}")

    print()
    print("  PIPELINE B — ENGINE SCENARIOS:")
    offset = len(SIMPLE_EVENTS)
    for i, s in enumerate(ENGINE_SCENARIOS):
        n = offset + i + 1
        eng = f"({s['engine']})" if s.get("engine") else ""
        print(f"   {n:>2}.  {s['label']:<40} {_fmt(s['amount']):>10}  {eng}")

    print()
    print("  PIPELINE B — MODULE SCENARIOS:")
    offset2 = offset + len(ENGINE_SCENARIOS)
    for i, s in enumerate(NON_ENGINE_SCENARIOS):
        n = offset2 + i + 1
        print(f"   {n:>2}.  {s['label']:<40} {_fmt(s['amount']):>10}")

    print()
    print("  SUBLEDGER SCENARIOS:")
    offset3 = offset2 + len(NON_ENGINE_SCENARIOS)
    for i, s in enumerate(SUBLEDGER_SCENARIOS):
        n = offset3 + i + 1
        print(f"   {n:>2}.  {s['label']:<40} {_fmt(s['amount']):>10}  [{s['sl_type']}]")

    print()
    print("  View:")
    print("    R   View all reports")
    print("    J   View journal entries")
    print("    S   Subledger reports (entity balances, open items)")
    print("    T   Trace a journal entry (full auditor decision trail)")
    print("    F   Trace a failed/rejected/blocked event")
    print()
    print("  Close:")
    print("    H   Pre-close health check (read-only diagnostic)")
    print("    C   Close a period (guided workflow)")
    print()
    print("  Other:")
    print("    A   Post ALL scenarios at once")
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
            if not memo:
                # For engine scenarios, use event_type as memo
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
            if len(name) > 30:
                name = name[:27] + "..."
            side_val = line.side.value if hasattr(line.side, 'value') else line.side
            if side_val == "debit":
                print(f"  {name:<30} {_fmt(line.amount):>14} {'':>14}")
            else:
                print(f"  {name:<30} {'':>14} {_fmt(line.amount):>14}")
        print()

    print(f"  Total: {len(entries)} journal entries")
    print()


def _render_trace(session, event_id, event_payload_map, acct_map, config):
    """Render the full audit trace for a given event_id.

    Shared renderer used by both show_trace() and show_failed_traces().
    """
    from finance_kernel.models.fiscal_period import FiscalPeriod
    from finance_kernel.models.party import Party
    from finance_kernel.selectors.trace_selector import TraceSelector

    selector = TraceSelector(session)
    bundle = selector.trace_by_event_id(event_id)

    # ---------------------------------------------------------------
    # 1. ORIGIN EVENT (enhanced — full payload + source document refs)
    # ---------------------------------------------------------------
    print()
    print("--- ORIGIN EVENT ---")
    print()
    if bundle.origin:
        o = bundle.origin
        print(f"    event_id:       {o.event_id}")
        print(f"    event_type:     {o.event_type}")
        print(f"    occurred_at:    {o.occurred_at}")
        print(f"    effective_date: {o.effective_date}")
        print(f"    producer:       {o.producer}")
        print(f"    schema_version: {o.schema_version}")
        print(f"    payload_hash:   {o.payload_hash}")

        # Full payload
        payload = event_payload_map.get(o.event_id) or {}
        if payload:
            print()
            print("    payload:")
            for k, v in payload.items():
                val_str = str(v)
                if len(val_str) > 60:
                    val_str = val_str[:57] + "..."
                print(f"      {k}: {val_str}")

            # Extract source document references
            doc_keys = [
                ("po_number", "Purchase Order"),
                ("contract_number", "Contract"),
                ("vendor_id", "Vendor"),
                ("invoice_number", "Invoice"),
                ("receipt_number", "Receipt"),
            ]
            doc_refs = [(label, payload[k]) for k, label in doc_keys
                        if k in payload and payload[k]]
            qty_keys = [
                ("quantity", "Quantity"),
                ("po_quantity", "PO Qty"),
                ("receipt_quantity", "Receipt Qty"),
                ("invoice_quantity", "Invoice Qty"),
            ]
            qty_refs = [(label, payload[k]) for k, label in qty_keys
                        if k in payload and payload[k]]
            if doc_refs or qty_refs:
                print()
                print("    source documents:")
                for label, val in doc_refs:
                    print(f"      {label}: {val}")
                for label, val in qty_refs:
                    print(f"      {label}: {val}")

    # ---------------------------------------------------------------
    # 2. POSTING CONTEXT (actor, period, config)
    # ---------------------------------------------------------------
    print()
    print("--- POSTING CONTEXT ---")
    print()

    # Actor identity
    actor_id = bundle.origin.actor_id if bundle.origin else None
    if actor_id:
        try:
            party = session.query(Party).filter(Party.id == actor_id).first()
            if party:
                p_type = party.party_type.value if hasattr(party.party_type, 'value') else str(party.party_type)
                p_status = party.status.value if hasattr(party.status, 'value') else str(party.status)
                print(f"    actor_id:     {actor_id}")
                print(f"    actor_code:   {party.party_code}")
                print(f"    actor_name:   {party.name}")
                print(f"    actor_type:   {p_type}")
                print(f"    actor_status: {p_status}")
                print(f"    can_transact: {party.can_transact}")
            else:
                print(f"    actor_id:     {actor_id}")
                print(f"    actor_code:   (not found in party table)")
        except Exception:
            print(f"    actor_id:     {actor_id}")

    # Period status
    eff_date = bundle.origin.effective_date if bundle.origin else None
    if eff_date:
        try:
            period = (
                session.query(FiscalPeriod)
                .filter(
                    FiscalPeriod.start_date <= eff_date,
                    FiscalPeriod.end_date >= eff_date,
                )
                .first()
            )
            if period:
                p_status = period.status.value if hasattr(period.status, 'value') else str(period.status)
                print()
                print(f"    period_code:        {period.period_code}")
                print(f"    period_status:      {p_status}")
                print(f"    allows_adjustments: {period.allows_adjustments}")
                print(f"    period_range:       {period.start_date} .. {period.end_date}")
        except Exception:
            pass

    # Config identity
    if config:
        print()
        print(f"    config_id:          {config.config_id}")
        print(f"    config_version:     {config.config_version}")
        print(f"    config_checksum:    {config.checksum[:16]}...")
        if hasattr(config, 'canonical_fingerprint') and config.canonical_fingerprint:
            print(f"    config_fingerprint: {config.canonical_fingerprint[:16]}...")
        if hasattr(config, 'scope') and config.scope:
            s = config.scope
            print(f"    scope:              entity={getattr(s, 'legal_entity', '*')}  "
                  f"jurisdiction={getattr(s, 'jurisdiction', '*')}")

    # ---------------------------------------------------------------
    # 3. JOURNAL ENTRIES (enhanced — account identity, idempotency, dims)
    # ---------------------------------------------------------------
    print()
    print(f"--- JOURNAL ENTRIES ({len(bundle.journal_entries)}) ---")
    print()
    if not bundle.journal_entries:
        print("    (none — event did not produce journal entries)")
        print()
    for je in bundle.journal_entries:
        print(f"    entry_id:       {je.entry_id}")
        print(f"    status:         {je.status}  seq: {je.seq if je.seq is not None else '-'}")
        print(f"    effective_date: {je.effective_date}  posted_at: {je.posted_at}")
        print(f"    idempotency:    {je.idempotency_key}")
        if je.description:
            print(f"    description:    {je.description}")
        if je.reversal_of_id:
            print(f"    reversal_of:    {je.reversal_of_id}")
        print()
        print(f"      {'seq':>4}  {'side':<7} {'amount':>14}  {'curr':<4}  {'code':<8} {'name':<22} {'type':<10} {'nbal':<7} {'rnd'}")
        print(f"      {'---':>4}  {'----':<7} {'------':>14}  {'----':<4}  {'----':<8} {'----':<22} {'----':<10} {'---':<7} {'---'}")
        for line in je.lines:
            acct = acct_map.get(line.account_id)
            a_name = acct.name[:20] if acct else "?"
            a_type = (acct.account_type.value if acct and hasattr(acct.account_type, 'value')
                      else str(acct.account_type) if acct else "?")
            a_nbal = (acct.normal_balance.value if acct and hasattr(acct.normal_balance, 'value')
                      else str(acct.normal_balance) if acct else "?")
            print(f"      {line.line_seq:>4}  {line.side:<7} "
                  f"{line.amount:>14}  {line.currency:<4}  "
                  f"{line.account_code:<8} {a_name:<22} {a_type:<10} {a_nbal:<7} {line.is_rounding}")

            # Dimensions
            if line.dimensions:
                dims = line.dimensions if isinstance(line.dimensions, dict) else {}
                if dims:
                    dim_str = "  ".join(f"{k}={v}" for k, v in dims.items())
                    print(f"             dims: {dim_str}")
        print()

    # ---------------------------------------------------------------
    # 4. INTERPRETATION OUTCOME
    # ---------------------------------------------------------------
    if bundle.interpretation:
        interp = bundle.interpretation
        print("--- INTERPRETATION OUTCOME ---")
        print()
        print(f"    status:            {interp.status}")
        print(f"    profile:           {interp.profile_id} v{interp.profile_version}")
        if interp.profile_hash:
            print(f"    profile_hash:      {interp.profile_hash[:16]}...")
        if interp.reason_code:
            print(f"    reason_code:       {interp.reason_code}")
        if hasattr(interp, 'reason_detail') and interp.reason_detail:
            print(f"    reason_detail:     {interp.reason_detail}")
        if hasattr(interp, 'failure_type') and interp.failure_type:
            print(f"    failure_type:      {interp.failure_type}")
        if hasattr(interp, 'failure_message') and interp.failure_message:
            print(f"    failure_message:   {interp.failure_message}")
        if interp.decision_log:
            print(f"    decision_log_size: {len(interp.decision_log)} records")
        print()

    # ---------------------------------------------------------------
    # 5. DECISION JOURNAL
    # ---------------------------------------------------------------
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
            elif action == "engine_dispatch_started":
                engines = d.get('required_engines', [])
                print(f"  [{i:>2}] ENGINE DISPATCH STARTED — {engines}")
            elif action in ("engine_invoked", "engine_completed"):
                eng_name = d.get('engine_name', '?')
                print(f"  [{i:>2}] {action.upper()} — {eng_name}")
                for k, v in d.items():
                    if k not in ("ts", "timestamp", "level", "logger", "engine_name", "event"):
                        print(f"       {k}: {v}")
            elif action == "journal_write_started":
                print(f"  [{i:>2}] JOURNAL WRITE STARTED — {d.get('ledger_count')} ledger(s)")
            elif action == "balance_validated":
                balanced = d.get('balanced')
                print(f"  [{i:>2}] BALANCE VALIDATED — {d.get('ledger_id')} {d.get('currency')}  "
                      f"Dr {d.get('sum_debit')} = Cr {d.get('sum_credit')}  "
                      f"{'PASS' if balanced else 'FAIL'}")
            elif action == "role_resolved":
                acct_name = d.get('account_name', '')
                acct_type = d.get('account_type', '')
                nbal = d.get('normal_balance', '')
                cfg_id = d.get('config_id', '')
                cfg_ver = d.get('config_version', '')
                eff_from = d.get('binding_effective_from', '')
                eff_to = d.get('binding_effective_to', 'open')
                print(f"  [{i:>2}] ROLE RESOLVED — {d.get('role')} -> {d.get('account_code')}  "
                      f"{d.get('side')} {d.get('amount')} {d.get('currency')}")
                if acct_name:
                    print(f"       account: {acct_name}  type={acct_type}  normal={nbal}")
                if cfg_id:
                    print(f"       binding: config={cfg_id} v{cfg_ver}  "
                          f"effective {eff_from}..{eff_to}")
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
                if d.get('idempotency_key'):
                    print(f"       idempotency: {d.get('idempotency_key')}")
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

    # ---------------------------------------------------------------
    # 6. ECONOMIC LINKS
    # ---------------------------------------------------------------
    if bundle.lifecycle_links:
        print(f"--- ECONOMIC LINKS ({len(bundle.lifecycle_links)}) ---")
        print()
        for link in bundle.lifecycle_links:
            print(f"    {link.link_type}:")
            print(f"      parent: {link.parent_artifact_type} {link.parent_artifact_id}")
            print(f"      child:  {link.child_artifact_type} {link.child_artifact_id}")
            print(f"      created_by_event: {link.creating_event_id}")
            if link.link_metadata:
                print(f"      metadata: {link.link_metadata}")
            print()

    # ---------------------------------------------------------------
    # 7. REPRODUCIBILITY (R21 snapshot)
    # ---------------------------------------------------------------
    if bundle.reproducibility:
        r = bundle.reproducibility
        print("--- REPRODUCIBILITY (R21 SNAPSHOT) ---")
        print()
        print(f"    coa_version:              {r.coa_version}")
        print(f"    dimension_schema_version: {r.dimension_schema_version}")
        print(f"    rounding_policy_version:  {r.rounding_policy_version}")
        print(f"    currency_registry_version:{r.currency_registry_version}")
        if hasattr(r, 'fx_policy_version') and r.fx_policy_version:
            print(f"    fx_policy_version:        {r.fx_policy_version}")
        if hasattr(r, 'posting_rule_version') and r.posting_rule_version:
            print(f"    posting_rule_version:     {r.posting_rule_version}")
        print()

    # ---------------------------------------------------------------
    # 8. INTEGRITY
    # ---------------------------------------------------------------
    print("--- INTEGRITY ---")
    print()
    integrity = bundle.integrity
    print(f"    payload_hash_verified: {integrity.payload_hash_verified}")
    print(f"    balance_verified:      {integrity.balance_verified}")
    print(f"    audit_chain_valid:     {integrity.audit_chain_segment_valid}")
    all_ok = integrity.payload_hash_verified and integrity.balance_verified
    print(f"    result:                {'ALL CHECKS PASSED' if all_ok else 'ISSUES DETECTED'}")

    # ---------------------------------------------------------------
    # 9. MISSING FACTS
    # ---------------------------------------------------------------
    if bundle.missing_facts:
        print()
        print(f"--- MISSING FACTS ({len(bundle.missing_facts)}) ---")
        for mf in bundle.missing_facts:
            print(f"    [{mf.fact}] {mf.expected_source}")
    else:
        print()
        print("  Trace is complete — 0 missing facts.")

    print()


def show_trace(session, config=None):
    """Let the user pick a journal entry by sequence number and trace it."""
    from finance_kernel.models.journal import JournalEntry
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome

    entries = session.query(JournalEntry).order_by(JournalEntry.seq).all()
    if not entries:
        print("\n  No journal entries to trace.\n")
        return

    # Build memo lookup
    event_map = {}
    event_payload_map = {}
    for evt in session.query(Event).all():
        memo = ""
        if evt.payload and isinstance(evt.payload, dict):
            memo = evt.payload.get("memo", "")
            if not memo:
                memo = evt.event_type or ""
            event_payload_map[evt.event_id] = evt.payload
        event_map[evt.event_id] = memo

    # Account lookup
    acct_map = {a.id: a for a in session.query(Account).all()}

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
        seq_str = f"{entry.seq:>3}" if entry.seq is not None else "  -"
        print(f"  {seq_str}  {status_val:<8}  {has_log:<8}  {memo}")

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
    W = 72
    print("=" * W)
    print(f"  AUDIT TRACE: Entry #{target.seq} — {memo}")
    print("=" * W)

    _render_trace(session, target.source_event_id, event_payload_map, acct_map, config)


def show_failed_traces(session, config=None):
    """List rejected/blocked/failed events and let the user trace one."""
    from finance_kernel.models.account import Account
    from finance_kernel.models.event import Event
    from finance_kernel.models.interpretation_outcome import (
        InterpretationOutcome,
        OutcomeStatus,
    )

    non_posted = (
        session.query(InterpretationOutcome)
        .filter(
            InterpretationOutcome.status.notin_([
                OutcomeStatus.POSTED.value,
                OutcomeStatus.POSTED,
            ])
        )
        .order_by(InterpretationOutcome.created_at.desc())
        .all()
    )

    if not non_posted:
        print("\n  No failed/rejected/blocked events found.\n")
        return

    # Build event lookup for memos
    event_payload_map = {}
    event_type_map = {}
    for evt in session.query(Event).all():
        if evt.payload and isinstance(evt.payload, dict):
            event_payload_map[evt.event_id] = evt.payload
        event_type_map[evt.event_id] = evt.event_type or ""

    acct_map = {a.id: a for a in session.query(Account).all()}

    W = 72
    print()
    print("=" * W)
    print("  FAILED / REJECTED / BLOCKED EVENTS".center(W))
    print("=" * W)
    print()
    print(f"  {'#':>3}  {'status':<12}  {'reason':<20}  {'event_type'}")
    print(f"  {'---':>3}  {'------':<12}  {'------':<20}  {'----------'}")

    for i, outcome in enumerate(non_posted):
        status_val = outcome.status_str
        reason = outcome.reason_code or ""
        if len(reason) > 18:
            reason = reason[:15] + "..."
        evt_type = event_type_map.get(outcome.source_event_id, "")
        print(f"  {i + 1:>3}  {status_val:<12}  {reason:<20}  {evt_type}")

    print()
    try:
        pick = input("  Enter # to trace (or blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not pick:
        return

    try:
        idx = int(pick) - 1
    except ValueError:
        print(f"\n  Invalid number '{pick}'.\n")
        return

    if idx < 0 or idx >= len(non_posted):
        print(f"\n  Number out of range.\n")
        return

    outcome = non_posted[idx]
    evt_type = event_type_map.get(outcome.source_event_id, "unknown")

    print()
    print("=" * W)
    print(f"  AUDIT TRACE: {outcome.status_str.upper()} — {evt_type}")
    print("=" * W)

    _render_trace(session, outcome.source_event_id, event_payload_map, acct_map, config)


def _build_close_orchestrator(session, orchestrator, clock, config):
    """Build a PeriodCloseOrchestrator from the existing PostingOrchestrator."""
    from finance_modules.reporting.service import ReportingService
    from finance_modules.reporting.config import ReportingConfig
    from finance_modules.gl.service import GeneralLedgerService
    from finance_config.bridges import build_role_resolver
    from finance_services.period_close_orchestrator import PeriodCloseOrchestrator

    reporting_config = ReportingConfig(entity_name=ENTITY)
    reporting_service = ReportingService(session=session, clock=clock, config=reporting_config)

    role_resolver = build_role_resolver(config)
    gl_service = GeneralLedgerService(session, role_resolver, clock)

    return PeriodCloseOrchestrator.from_posting_orchestrator(
        orchestrator, reporting_service, gl_service,
    )


def handle_health_check(session, orchestrator, clock, config):
    """Handle 'H' — Pre-close health check (read-only diagnostic)."""
    close_orch = _build_close_orchestrator(session, orchestrator, clock, config)

    W = 72
    print()
    try:
        period_code = input("  Period code (e.g., FY2026): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not period_code:
        return

    period_info = orchestrator.period_service.get_period_by_code(period_code)
    if period_info is None:
        print(f"\n  Period '{period_code}' not found.\n")
        return

    result = close_orch.health_check(
        period_code=period_code,
        period_end_date=period_info.end_date,
    )

    print()
    print("=" * W)
    print(f"  PRE-CLOSE HEALTH CHECK: {period_code}".center(W))
    print("=" * W)
    print()

    # Subledger reconciliation
    print("  Subledger Reconciliation:")
    if result.sl_reconciliation:
        for sl_type, info in result.sl_reconciliation.items():
            sl_bal = info.get("sl_balance", Decimal("0"))
            gl_bal = info.get("gl_balance", Decimal("0"))
            var = info.get("variance", Decimal("0"))
            status = info.get("status", "?")
            print(f"    {sl_type:<12} SL ${sl_bal:>12,.2f}  GL ${gl_bal:>12,.2f}  "
                  f"variance: ${var:>10,.2f}  {status}")
    else:
        print("    (no subledgers configured)")

    print()

    # Suspense/clearing accounts
    print("  Suspense / Clearing Accounts:")
    if result.suspense_balances:
        for acct in result.suspense_balances:
            code = acct.get("account_code", "?")
            bal = acct.get("balance", Decimal("0"))
            status = acct.get("status", "?")
            print(f"    {code:<20} ${bal:>12,.2f}  {status}")
    else:
        print("    (none found)")

    print()

    # Trial balance
    print("  Trial Balance:")
    print(f"    Debits:  ${result.total_debits:>14,.2f}")
    print(f"    Credits: ${result.total_credits:>14,.2f}")
    print(f"    Balanced: {'YES' if result.trial_balance_ok else 'NO'}")

    print()

    # Activity
    print(f"  Period Activity:")
    print(f"    Entries: {result.period_entry_count}   "
          f"Rejected: {result.period_rejection_count}")

    print()

    # Verdict
    n_blocking = len(result.blocking_issues)
    n_warnings = len(result.warnings)
    if n_blocking == 0 and n_warnings == 0:
        print("  RESULT: No issues found. Period is ready to close.")
    else:
        print(f"  RESULT: {n_blocking} blocking, {n_warnings} warning")
        for issue in result.blocking_issues:
            print(f"    [BLOCKING] {issue.description}")
        for issue in result.warnings:
            print(f"    [WARNING]  {issue.description}")
        if n_blocking > 0:
            print()
            print("  Fix blocking issues before starting close.")

    print()


def handle_close_workflow(session, orchestrator, clock, config, actor_id):
    """Handle 'C' — Close a period (guided workflow)."""
    close_orch = _build_close_orchestrator(session, orchestrator, clock, config)

    W = 72
    print()
    try:
        period_code = input("  Period code (e.g., FY2026): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not period_code:
        return

    period_info = orchestrator.period_service.get_period_by_code(period_code)
    if period_info is None:
        print(f"\n  Period '{period_code}' not found.\n")
        return

    # Check current status
    status = close_orch.get_status(period_code)
    if status and status.get("is_closed"):
        p_status = status.get("status", "closed")
        print(f"\n  Period {period_code} is already {p_status.upper()}.\n")
        return
    if status and status.get("is_closing"):
        print(f"\n  Period {period_code} is already in CLOSING state "
              f"(run_id: {status.get('closing_run_id')}).")
        try:
            choice = input("  Cancel existing close? [y/N]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "Y":
            close_orch.cancel_close(period_code, actor_id, "User cancelled from CLI")
            session.commit()
            print("  Close cancelled. Period is OPEN again.\n")
        else:
            return

    print()
    print("=" * W)
    print(f"  PERIOD CLOSE WORKFLOW: {period_code}".center(W))
    print("=" * W)
    print()

    # Run health check first
    print("  Running health check...")
    health = close_orch.health_check(period_code, period_info.end_date)

    print(f"  TB: Dr ${health.total_debits:>12,.2f} = Cr ${health.total_credits:>12,.2f}  "
          f"{'balanced' if health.trial_balance_ok else 'IMBALANCED'}")
    print(f"  Entries: {health.period_entry_count}   "
          f"Issues: {len(health.blocking_issues)} blocking, {len(health.warnings)} warning")

    if health.blocking_issues:
        print()
        for issue in health.blocking_issues:
            print(f"    [BLOCKING] {issue.description}")
        print()
        try:
            choice = input("  Blocking issues detected. Proceed anyway? [y/N]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice != "Y":
            return

    print()
    try:
        is_ye_input = input("  Year-end close? [y/N]: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    is_year_end = is_ye_input == "Y"

    print()
    print("  Acquiring close lock and executing phases...")
    print()

    muted = _enable_quiet_logging()
    try:
        result = close_orch.close_period_full(
            period_code=period_code,
            actor_id=actor_id,
            is_year_end=is_year_end,
        )
        session.commit()
    except Exception as e:
        session.rollback()
        _restore_logging(muted)
        print(f"  ERROR: {e}\n")
        return
    _restore_logging(muted)

    # Display phase results
    for pr in result.phase_results:
        if pr.message and "Skipped" in pr.message:
            tag = "SKIP"
        elif pr.success:
            tag = "DONE"
        else:
            tag = "FAIL"
        guard_str = f"  [{pr.guard}: {'PASS' if pr.success else 'FAIL'}]" if pr.guard else ""
        print(f"  [{pr.phase}] {pr.phase_name:<24} {tag}  {pr.message or ''}{guard_str}")

    print()

    if result.certificate:
        cert = result.certificate
        print("=" * W)
        print(f"  PERIOD {period_code} CLOSED SUCCESSFULLY".center(W))
        print("=" * W)
        print()
        print(f"  Close Certificate ID:  {cert.id}")
        print(f"  Closed by:             {cert.closed_by}")
        print(f"  Closed at:             {cert.closed_at}")
        print(f"  Correlation ID:        {cert.correlation_id}")
        print()
        print(f"  Trial Balance:")
        print(f"    Debits:  ${cert.trial_balance_debits:>14,.2f}")
        print(f"    Credits: ${cert.trial_balance_credits:>14,.2f}")
        print()
        print(f"  Phases completed: {cert.phases_completed}   skipped: {cert.phases_skipped}")
        print(f"  Adjustments:      {cert.adjustments_posted}")
        print(f"  Closing entries:  {cert.closing_entries_posted}")
        print(f"  Subledgers:       {', '.join(cert.subledgers_closed) if cert.subledgers_closed else 'none'}")
        print()
        print(f"  Ledger hash (R24): {cert.ledger_hash}")
        if cert.audit_event_id:
            print(f"  Audit event:       {cert.audit_event_id}")
        print()
        print("  Close events are traceable via 'T'. Rejected events via 'F'.")
        print(f"  Full log: logs/interactive.log (grep {cert.correlation_id[:8]})")
    else:
        print(f"  Close FAILED: {result.message}")
        print()
        if result.phase_results:
            last = result.phase_results[-1]
            if last.exceptions:
                print("  Exception detail:")
                for exc in last.exceptions:
                    print(f"    [{exc.severity.upper()}] {exc.description}")
        print()
        print("  Period remains in CLOSING state. Use 'C' again to retry or cancel.")

    print()


def show_subledger_reports(session, clock):
    """Display subledger balances and entity-level detail."""
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

            # Open items summary
            for eid in entities:
                open_items = selector.get_open_items(eid, sl_type, currency)
                if open_items:
                    print(f"\n    Open items for {eid}: {len(open_items)}")
                    for item in open_items[:5]:  # Show up to 5
                        side = "Dr" if item.debit_amount else "Cr"
                        amt = item.debit_amount or item.credit_amount or Decimal("0")
                        print(f"      {item.source_document_type:<15} {side} {currency} {amt:>10,.2f}  "
                              f"status={item.reconciliation_status}")

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
# Posting helpers
# ---------------------------------------------------------------------------

def post_subledger_scenario(session, post_simple, orchestrator, scenario, actor_id, clock):
    """Post a subledger scenario: GL entry + subledger entry."""
    from finance_kernel.domain.subledger_control import SubledgerType
    from finance_engines.subledger import SubledgerEntry
    from finance_kernel.domain.values import Money

    # 1. Post the GL entry
    result = post_simple(
        scenario["gl_debit"], scenario["gl_credit"],
        scenario["amount"], scenario["memo"],
    )
    if not result.success:
        return result, None

    # 2. Create the subledger entry via the concrete service
    sl_type = SubledgerType(scenario["sl_type"])
    service = orchestrator.subledger_services.get(sl_type)
    if service is None:
        return result, None

    # Get the journal entry ID from the result
    je_id = None
    if result.outcome and result.outcome.journal_entry_ids:
        je_id = result.outcome.journal_entry_ids[0]

    # Determine debit/credit based on GL debit role vs SL type
    # For AP: GL credit to AP = SL credit (liability increases)
    # For AR: GL debit to AR = SL debit (asset increases)
    # For Inventory: GL debit to Inventory = SL debit
    # For Bank: GL debit to Cash = SL debit
    money = Money.of(scenario["amount"], "USD")

    # Credit-normal subledgers: invoice = credit, payment = debit
    credit_normal = sl_type in (SubledgerType.AP, SubledgerType.PAYROLL)
    if scenario["doc_type"] in ("INVOICE", "RECEIPT", "DEPOSIT"):
        if credit_normal:
            debit, credit = None, money
        else:
            debit, credit = money, None
    else:  # PAYMENT, ISSUE, WITHDRAWAL
        if credit_normal:
            debit, credit = money, None
        else:
            debit, credit = None, money

    entry = SubledgerEntry(
        subledger_type=sl_type.value,
        entity_id=scenario["entity_id"],
        source_document_type=scenario["doc_type"],
        source_document_id=str(uuid4()),
        source_line_id="0",
        debit=debit,
        credit=credit,
        effective_date=clock.now().date(),
        memo=scenario["memo"],
        dimensions={},
    )

    service.post(entry, gl_entry_id=je_id, actor_id=actor_id)
    session.flush()
    return result, sl_type


def post_engine_scenario(engine_service, scenario, actor_id):
    """Post a single engine/module scenario via ModulePostingService."""
    event_id = uuid4()
    result = engine_service.post_event(
        event_type=scenario["event_type"],
        payload=scenario["payload"],
        effective_date=EFFECTIVE,
        actor_id=actor_id,
        amount=scenario["amount"],
        currency="USD",
        producer=scenario["event_type"].split(".")[0],
        event_id=event_id,
    )
    return result, event_id


# ---------------------------------------------------------------------------
# Logging helpers
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
    """Restore muted handlers after a quiet-logging section.

    Console handlers are restored to their original levels. The global
    logging gate is NOT re-disabled so the persistent file handler
    (logs/interactive.log) continues to receive all records.
    """
    for h, orig_level in muted:
        h.setLevel(orig_level)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    # Persistent file log — captures everything at DEBUG level even when
    # console output is suppressed.  Check logs/interactive.log for diagnostics.
    import os
    os.makedirs("logs", exist_ok=True)
    from finance_kernel.logging_config import StructuredFormatter
    _file_handler = logging.FileHandler("logs/interactive.log", mode="a")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(StructuredFormatter())
    fk_logger = logging.getLogger("finance_kernel")
    fk_logger.addHandler(_file_handler)
    fk_logger.setLevel(logging.DEBUG)
    # Mute console handlers so CLI output stays clean, but file handler
    # continues to receive all records.
    for h in fk_logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.CRITICAL + 1)

    from finance_kernel.db.engine import init_engine_from_url, get_session
    from finance_kernel.domain.clock import DeterministicClock

    try:
        init_engine_from_url(DB_URL, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc))

    # Check for existing data before deciding whether to reset or resume
    from finance_kernel.db.engine import get_session as _get_session
    from finance_kernel.models.journal import JournalEntry

    _tmp_session = _get_session()
    has_data = _tables_exist(_tmp_session) and _has_accounts(_tmp_session)

    if has_data:
        entry_count = _tmp_session.query(JournalEntry).count()
        _tmp_session.close()
        print(f"\n  Existing data found ({entry_count} journal entries).")
        try:
            choice = input("  Resume or Reset? [R/x]: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "X":
            print("  Resetting database...")
            session, post_simple, engine_service, actor_id, config, orchestrator = full_setup(None, clock)
        else:
            print("  Resuming with existing data...")
            session, post_simple, engine_service, actor_id, config, orchestrator = resume_setup(clock)
    else:
        _tmp_session.close()
        print("\n  Setting up database with YAML config (US-GAAP-2026-v1)...")
        session, post_simple, engine_service, actor_id, config, orchestrator = full_setup(None, clock)

    print(f"  Config: {config.config_id} v{config.config_version}")
    print(f"  Policies: {len(config.policies)}  Role bindings: {len(config.role_bindings)}")

    entry_count = session.query(JournalEntry).count()
    print(f"  Journal has {entry_count} entries.")

    total_items = len(SIMPLE_EVENTS) + len(ALL_PIPELINE_SCENARIOS) + len(SUBLEDGER_SCENARIOS)

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

        elif choice == "S":
            show_subledger_reports(session, clock)

        elif choice == "T":
            show_trace(session, config)

        elif choice == "F":
            show_failed_traces(session, config)

        elif choice == "H":
            handle_health_check(session, orchestrator, clock, config)

        elif choice == "C":
            handle_close_workflow(session, orchestrator, clock, config, actor_id)

        elif choice == "A":
            # Post ALL scenarios
            print("\n  Posting all scenarios...")
            muted = _enable_quiet_logging()
            ok = 0
            fail = 0

            # Simple events
            for desc, dr, cr, amt, dr_lbl, cr_lbl in SIMPLE_EVENTS:
                result = post_simple(dr, cr, amt, desc)
                if result.success:
                    ok += 1
                    session.commit()
                else:
                    fail += 1
                    session.rollback()
                    err = getattr(result, 'error_message', None) or getattr(result, 'error_code', '?')
                    print(f"    FAIL: {desc} — {err}")

            # Engine + module scenarios
            for scenario in ALL_PIPELINE_SCENARIOS:
                result, evt_id = post_engine_scenario(engine_service, scenario, actor_id)
                status = result.status.value.upper()
                if result.is_success:
                    ok += 1
                    session.commit()
                else:
                    fail += 1
                    session.rollback()
                    print(f"    FAIL: {scenario['label']} — {result.message}")

            # Subledger scenarios
            for scenario in SUBLEDGER_SCENARIOS:
                result, sl_type = post_subledger_scenario(
                    session, post_simple, orchestrator, scenario, actor_id, clock,
                )
                if result.success:
                    ok += 1
                    session.commit()
                else:
                    fail += 1
                    session.rollback()
                    err = getattr(result, 'error_message', None) or getattr(result, 'error_code', '?')
                    print(f"    FAIL: {scenario['label']} — {err}")

            _restore_logging(muted)
            print(f"\n  Done: {ok} posted, {fail} failed (of {total_items} total).\n")

        elif choice == "X":
            print("\n  Resetting database...")
            session.close()
            session, post_simple, engine_service, actor_id, config, orchestrator = full_setup(None, clock)
            print("  Done. Database is empty.\n")

        elif choice.isdigit():
            idx = int(choice) - 1
            n_simple = len(SIMPLE_EVENTS)
            n_pipeline = len(ALL_PIPELINE_SCENARIOS)
            n_subledger = len(SUBLEDGER_SCENARIOS)

            if 0 <= idx < n_simple:
                # Simple bookkeeping event
                desc, dr, cr, amt, dr_lbl, cr_lbl = SIMPLE_EVENTS[idx]
                muted = _enable_quiet_logging()
                result = post_simple(dr, cr, amt, desc)
                _restore_logging(muted)
                if result.success:
                    session.commit()
                    evt_id = result.outcome.source_event_id if result.outcome else "?"
                    print(f"\n  Posted: {desc} -- {_fmt(amt)}  (Dr {dr_lbl} / Cr {cr_lbl})")
                    print(f"    Use T to trace this entry.")
                else:
                    session.rollback()
                    err = getattr(result, 'error_message', None) or getattr(result, 'error_code', '?')
                    print(f"\n  FAILED: {err}")

            elif n_simple <= idx < n_simple + n_pipeline:
                # Engine or module scenario
                scenario = ALL_PIPELINE_SCENARIOS[idx - n_simple]
                muted = _enable_quiet_logging()
                result, evt_id = post_engine_scenario(engine_service, scenario, actor_id)
                _restore_logging(muted)
                status = result.status.value.upper()
                eng_tag = f" ({scenario.get('engine', '')} engine)" if scenario.get("engine") else ""
                if result.is_success:
                    session.commit()
                    entry_ids = list(result.journal_entry_ids)
                    print(f"\n  [{status}] {scenario['label']}{eng_tag}")
                    print(f"    {scenario['business']}")
                    print(f"    {len(entry_ids)} journal entries created. Use T to trace.")
                else:
                    session.rollback()
                    print(f"\n  [{status}] {scenario['label']}{eng_tag}")
                    print(f"    {result.message}")

            elif n_simple + n_pipeline <= idx < n_simple + n_pipeline + n_subledger:
                # Subledger scenario
                scenario = SUBLEDGER_SCENARIOS[idx - n_simple - n_pipeline]
                muted = _enable_quiet_logging()
                result, sl_type = post_subledger_scenario(
                    session, post_simple, orchestrator, scenario, actor_id, clock,
                )
                _restore_logging(muted)
                if result.success:
                    session.commit()
                    print(f"\n  Posted: {scenario['label']} -- {_fmt(scenario['amount'])}  [{scenario['sl_type']}]")
                    print(f"    GL: Dr {scenario['gl_debit']} / Cr {scenario['gl_credit']}")
                    print(f"    SL: {scenario['sl_type']} entity={scenario['entity_id']}  "
                          f"doc={scenario['doc_type']}")
                    print(f"    Use S to view subledger reports, T to trace the GL entry.")
                else:
                    session.rollback()
                    err = getattr(result, 'error_message', None) or getattr(result, 'error_code', '?')
                    print(f"\n  FAILED: {err}")
            else:
                print(f"\n  Invalid number. Pick 1-{total_items}.")

        else:
            print(f"\n  Unknown command '{choice}'. Try a number, R, J, S, T, F, H, C, A, X, or Q.")

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
