#!/usr/bin/env python3
"""
Realistic engine & service scenarios using the REAL architecture.

Loads YAML config, registers module profiles, wires the full
PostingOrchestrator + EngineDispatcher, and posts 15 business events
through ModulePostingService.post_event().

Engine coverage:
  - variance  (4 scenarios: PPV, labor, material, overhead)
  - tax       (2 scenarios: sales tax, use tax)
  - matching  (1 scenario:  AP three-way match)
  - allocation(2 scenarios: labor distribution, contract fringe)
  - billing   (1 scenario:  CPFF provisional billing)

Usage:
    python3 scripts/demo_engines.py
    python3 scripts/demo_engines.py --trace      # seed + trace all engine entries
    python3 scripts/demo_engines.py --trace-only  # trace existing entries
"""

import argparse
import logging
import sys
import time
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4, uuid5

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"
FY_START = date(2026, 1, 1)
FY_END = date(2026, 12, 31)
EFFECTIVE = date(2026, 6, 15)
COA_UUID_NS = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _kill_orphaned_connections():
    """Kill idle connections to finance_kernel_test."""
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


def _enable_quiet_logging():
    """Enable logging so LogCapture works, but mute console output."""
    logging.disable(logging.NOTSET)
    fk_logger = logging.getLogger("finance_kernel")
    muted = []
    for h in fk_logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h, logging.FileHandler
        ):
            muted.append((h, h.level))
            h.setLevel(logging.CRITICAL + 1)
    return muted


def _restore_logging(muted):
    """Restore muted handlers and re-disable logging."""
    for h, orig_level in muted:
        h.setLevel(orig_level)
    logging.disable(logging.CRITICAL)


# ===================================================================
# Account setup — create DB rows matching config role bindings
# ===================================================================

def _account_type_for_code(code: str):
    """Derive AccountType from account code prefix."""
    from finance_kernel.models.account import AccountType

    if code.startswith("SL-"):
        # Subledger accounts — use ASSET as default
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
    """Derive NormalBalance from AccountType."""
    from finance_kernel.models.account import AccountType, NormalBalance

    if atype in (AccountType.ASSET, AccountType.EXPENSE):
        return NormalBalance.DEBIT
    return NormalBalance.CREDIT


def create_accounts_from_config(session, config, actor_id: UUID):
    """Create Account rows for every role_binding in config."""
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
            id=acct_id,
            code=code,
            name=f"{binding.role} ({code})",
            account_type=atype,
            normal_balance=nbal,
            is_active=True,
            tags=tags,
            created_by_id=actor_id,
        )
        session.add(acct)
        created += 1

    session.flush()
    return created


# ===================================================================
# Scenario definitions
# ===================================================================

ENGINE_SCENARIOS = [
    # --- variance engine (4) ---
    {
        "id": "S1",
        "label": "Inventory Receipt with PPV",
        "engine": "variance",
        "event_type": "inventory.receipt",
        "amount": Decimal("10500.00"),
        "payload": {
            "quantity": 1000,
            "has_variance": True,
            "standard_price": "10.00",
            "actual_price": "10.50",
            "standard_total": "10000.00",
            "variance_amount": "500.00",
            "variance_type": "price",
            "expected_price": "10.00",
            "actual_price": "10.50",
        },
        "business": "PO at $10/unit, invoice at $10.50/unit, 1000 units -> $500 unfavorable PPV",
    },
    {
        "id": "S2",
        "label": "WIP Labor Efficiency Variance",
        "engine": "variance",
        "event_type": "wip.labor_variance",
        "amount": Decimal("450.00"),
        "payload": {
            "quantity": 160,
            "standard_hours": 150,
            "actual_hours": 160,
            "standard_rate": "45.00",
            "actual_rate": "45.00",
            "variance_type": "quantity",
            "expected_quantity": "150",
            "actual_quantity": "160",
            "standard_price": "45.00",
        },
        "business": "Standard 150 hrs, actual 160 hrs at $45/hr -> $450 unfavorable",
    },
    {
        "id": "S3",
        "label": "WIP Material Usage Variance",
        "engine": "variance",
        "event_type": "wip.material_variance",
        "amount": Decimal("3750.00"),
        "payload": {
            "quantity": 1150,
            "standard_quantity": 1000,
            "actual_quantity": 1150,
            "standard_price": "25.00",
            "variance_type": "quantity",
            "expected_quantity": "1000",
        },
        "business": "Standard 1000 units, used 1150 at $25 -> $3,750 unfavorable",
    },
    {
        "id": "S4",
        "label": "WIP Overhead Variance",
        "engine": "variance",
        "event_type": "wip.overhead_variance",
        "amount": Decimal("4500.00"),
        "payload": {
            "quantity": 1,
            "applied_overhead": "67500.00",
            "actual_overhead": "72000.00",
            "variance_type": "standard_cost",
            "standard_cost": "67500.00",
            "actual_cost": "72000.00",
        },
        "business": "Applied $67,500, actual $72,000 -> $4,500 under-applied",
    },
    # --- tax engine (2) ---
    {
        "id": "S5",
        "label": "Sales Tax Collected (Texas 6.25%)",
        "engine": "tax",
        "event_type": "tax.sales_tax_collected",
        "amount": Decimal("6000.00"),
        "payload": {
            "amount": "6000.00",
            "jurisdiction": "TX",
            "tax_codes": ["STATE_SALES"],
            "tax_rates": {"STATE_SALES": "0.0625"},
        },
        "business": "$96,000 sale in Texas, 6.25% sales tax = $6,000 collected",
    },
    {
        "id": "S6",
        "label": "Use Tax Self-Assessment (CA 8%)",
        "engine": "tax",
        "event_type": "tax.use_tax_accrued",
        "amount": Decimal("3200.00"),
        "payload": {
            "amount": "3200.00",
            "jurisdiction": "CA",
            "purchase_amount": "40000.00",
            "use_tax_rate": "0.08",
        },
        "business": "$40,000 equipment from out-of-state, CA use tax 8%",
    },
    # --- matching engine (1) ---
    {
        "id": "S7",
        "label": "AP Invoice PO-Matched (Three-Way)",
        "engine": "matching",
        "event_type": "ap.invoice_received",
        "amount": Decimal("49500.00"),
        "payload": {
            "po_number": "PO-2026-0042",
            "gross_amount": "49500.00",
            "vendor_id": "V-100",
            "po_amount": "50000.00",
            "receipt_amount": "49000.00",
            "receipt_quantity": 490,
            "po_quantity": 500,
            "invoice_quantity": 495,
            "match_operation": "create_match",
            "match_type": "three_way",
            "match_documents": [
                {
                    "document_id": "PO-2026-0042",
                    "document_type": "purchase_order",
                    "amount": "50000.00",
                    "quantity": 500,
                },
                {
                    "document_id": "RCV-2026-0088",
                    "document_type": "receipt",
                    "amount": "49000.00",
                    "quantity": 490,
                },
                {
                    "document_id": "INV-2026-1234",
                    "document_type": "invoice",
                    "amount": "49500.00",
                    "quantity": 495,
                },
            ],
        },
        "business": "PO $50K/500u, Receipt 490u/$49K, Invoice $49,500/495u",
    },
    # --- allocation engine (2) ---
    {
        "id": "S8",
        "label": "Direct Labor Distribution to WIP",
        "engine": "allocation",
        "event_type": "labor.distribution_direct",
        "amount": Decimal("45000.00"),
        "payload": {
            "labor_type": "DIRECT",
            "amount": "45000.00",
            "project": "PRJ-001",
            "hours": 1000,
            "rate": "45.00",
            "allocation_method": "prorata",
            "allocation_targets": [
                {"target_id": "WO-001", "weight": "0.6"},
                {"target_id": "WO-002", "weight": "0.4"},
            ],
        },
        "business": "1000 hrs direct labor at $45/hr, 60/40 across 2 work orders",
    },
    {
        "id": "S9",
        "label": "Contract Fringe Allocation (DCAA)",
        "engine": "allocation",
        "event_type": "contract.indirect_allocation",
        "amount": Decimal("52500.00"),
        "payload": {
            "indirect_type": "FRINGE",
            "amount": "52500.00",
            "contract_number": "FA8750-21-C-0001",
            "direct_labor_base": "150000.00",
            "fringe_rate": "0.35",
            "allocation_method": "prorata",
            "allocation_targets": [
                {"target_id": "FA8750-21-C-0001", "weight": "1.0"},
            ],
        },
        "business": "$150K direct labor x 35% fringe -> $52,500 to contract",
    },
    # --- billing engine (1) ---
    {
        "id": "S10",
        "label": "Govt Contract Billing CPFF",
        "engine": "billing",
        "event_type": "contract.billing_provisional",
        "amount": Decimal("285000.00"),
        "payload": {
            "billing_type": "COST_REIMBURSEMENT",
            "total_billing": "285000.00",
            "cost_billing": "263889.00",
            "fee_amount": "21111.00",
            "contract_number": "FA8750-21-C-0001",
            "contract_type": "CPFF",
            "fee_rate": "0.08",
            "billing_input": {
                "contract_type": "CPFF",
                "cost_breakdown": {
                    "direct_labor": "150000.00",
                    "direct_material": "50000.00",
                    "subcontract": "0.00",
                    "travel": "5000.00",
                    "odc": "2000.00",
                },
                "indirect_rates": {
                    "fringe": "0.35",
                    "overhead": "0.45",
                    "ga": "0.10",
                },
                "fee_rate": "0.08",
                "currency": "USD",
            },
        },
        "business": "CPFF contract: $263,889 costs + $21,111 fee (8%) = $285,000",
    },
]

NON_ENGINE_SCENARIOS = [
    {
        "id": "S11",
        "label": "Standard Inventory Receipt",
        "event_type": "inventory.receipt",
        "amount": Decimal("25000.00"),
        "payload": {"quantity": 500, "has_variance": False},
        "business": "500 units at $50/unit, standard cost receipt",
    },
    {
        "id": "S12",
        "label": "Inventory Issue to Production",
        "event_type": "inventory.issue",
        "amount": Decimal("10000.00"),
        "payload": {"issue_type": "PRODUCTION", "quantity": 200},
        "business": "200 units raw material issued to production WIP",
    },
    {
        "id": "S13",
        "label": "Inventory Issue for Sale (COGS)",
        "event_type": "inventory.issue",
        "amount": Decimal("15000.00"),
        "payload": {"issue_type": "SALE", "quantity": 300},
        "business": "300 units shipped for sale, COGS recognized",
    },
    {
        "id": "S14",
        "label": "AP Direct Expense Invoice",
        "event_type": "ap.invoice_received",
        "amount": Decimal("8500.00"),
        "payload": {
            "po_number": None,
            "gross_amount": "8500.00",
            "vendor_id": "V-200",
        },
        "business": "Direct expense invoice for consulting services",
    },
    {
        "id": "S15",
        "label": "Payroll Accrual",
        "event_type": "payroll.accrual",
        "amount": Decimal("125000.00"),
        "payload": {
            "gross_pay": "125000.00",
            "federal_tax": "25000.00",
            "state_tax": "8750.00",
            "fica": "9562.50",
        },
        "business": "Monthly payroll accrual for 50 employees",
    },
]


# ===================================================================
# Trace helpers
# ===================================================================

def trace_entry(session, event_id: UUID, entry_ids: list[UUID], label: str):
    """Print trace for a single posted entry."""
    from finance_kernel.selectors.trace_selector import TraceSelector

    if not entry_ids:
        print(f"    [NO ENTRIES] {label}")
        return

    selector = TraceSelector(session)
    bundle = selector.build_trace_bundle(entry_id=entry_ids[0])

    print(f"    --- {label} ---")

    # Origin
    if bundle.origin:
        o = bundle.origin
        print(f"    Origin: event_type={o.event_type}, producer={o.producer}")
        print(f"      event_id: {o.event_id}")

    # Journal
    if bundle.journal_entries:
        for je in bundle.journal_entries:
            print(f"    Journal: entry_id={je.entry_id}, status={je.status}")
            for line in je.lines:
                side = line.side.upper() if hasattr(line.side, 'upper') else line.side
                print(f"      {side:6s} {line.account_code:12s} {line.amount:>14s} {line.currency}")

    # Interpretation
    if bundle.interpretation:
        interp = bundle.interpretation
        print(f"    Interpretation: status={interp.status}, profile={interp.profile_id}")
        dj = interp.decision_log
        if dj:
            print(f"    Decision journal: {len(dj)} records")
            engine_records = [r for r in dj if r.get("event") == "engine_dispatch_started"
                             or "engine" in r.get("event", "")]
            if engine_records:
                for rec in engine_records:
                    print(f"      ENGINE: {rec.get('event', '?')} "
                          f"engines={rec.get('required_engines', rec.get('engine_name', '?'))}")
        else:
            print("    Decision journal: (empty)")

    # Missing facts
    if bundle.missing_facts:
        print(f"    Missing facts: {len(bundle.missing_facts)}")
        for mf in bundle.missing_facts:
            print(f"      - {mf.category}: {mf.description}")

    print()


# ===================================================================
# Main
# ===================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Engine & service scenario demo")
    parser.add_argument("--trace", action="store_true",
                        help="Seed then trace all engine entries")
    parser.add_argument("--trace-only", action="store_true",
                        help="Trace existing entries (skip seeding)")
    parser.add_argument("--db-url", default=DB_URL, help="Database URL")
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)

    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_kernel.db.engine import (
        drop_tables,
        get_session,
        init_engine_from_url,
    )
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_kernel.domain.clock import DeterministicClock
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_modules import register_all_modules
    from finance_modules._orm_registry import create_all_tables
    from finance_services.invokers import register_standard_engines
    from finance_services.posting_orchestrator import PostingOrchestrator

    db_url = args.db_url

    # -----------------------------------------------------------------
    # 1. Load YAML config
    # -----------------------------------------------------------------
    print()
    print("  [1/6] Loading YAML config (US-GAAP-2026-v1)...")
    config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)
    print(f"         Config: {config.config_id} v{config.config_version}")
    print(f"         Policies: {len(config.policies)}")
    print(f"         Role bindings: {len(config.role_bindings)}")
    engine_policies = [p for p in config.policies if p.required_engines]
    print(f"         Engine-using policies: {len(engine_policies)}")

    # -----------------------------------------------------------------
    # 2. Register module profiles
    # -----------------------------------------------------------------
    print("  [2/6] Registering module profiles...")
    register_all_modules()

    # -----------------------------------------------------------------
    # 3. Connect + reset DB
    # -----------------------------------------------------------------
    print("  [3/6] Connecting to PostgreSQL and resetting schema...")
    try:
        init_engine_from_url(db_url, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    _kill_orphaned_connections()
    time.sleep(0.5)

    try:
        drop_tables()
    except Exception:
        _kill_orphaned_connections()
        time.sleep(1.0)
        try:
            drop_tables()
        except Exception:
            pass

    create_all_tables(install_triggers=True)
    register_immutability_listeners()

    session = get_session()
    clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))
    actor_id = uuid4()

    # -----------------------------------------------------------------
    # 4. Create accounts + fiscal period
    # -----------------------------------------------------------------
    print("  [4/6] Creating chart of accounts from config...")
    acct_count = create_accounts_from_config(session, config, actor_id)
    print(f"         Created {acct_count} accounts")

    period = FiscalPeriod(
        period_code="FY2026",
        name="Fiscal Year 2026",
        start_date=FY_START,
        end_date=FY_END,
        status=PeriodStatus.OPEN,
        created_by_id=actor_id,
    )
    session.add(period)

    # Create Party for the actor (required by G14 actor authorization)
    actor_party = Party(
        id=actor_id,
        party_code="SYSTEM-DEMO",
        party_type=PartyType.EMPLOYEE,
        name="Demo System Actor",
        status=PartyStatus.ACTIVE,
        is_active=True,
        created_by_id=actor_id,
    )
    session.add(actor_party)
    session.flush()

    # -----------------------------------------------------------------
    # 5. Wire PostingOrchestrator + EngineDispatcher
    # -----------------------------------------------------------------
    print("  [5/6] Wiring PostingOrchestrator + EngineDispatcher...")
    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=session,
        compiled_pack=config,
        role_resolver=role_resolver,
        clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)

    service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=False)

    # -----------------------------------------------------------------
    # 6. Post scenarios
    # -----------------------------------------------------------------
    all_scenarios = ENGINE_SCENARIOS + NON_ENGINE_SCENARIOS
    print(f"  [6/6] Posting {len(all_scenarios)} business scenarios...")
    print()

    muted = _enable_quiet_logging()

    ok_count = 0
    posted_events = []  # (scenario, event_id, entry_ids)
    engine_counts = {}

    # Engine scenarios
    print("  ENGINE SCENARIOS (5 engine types):")
    print()
    for scenario in ENGINE_SCENARIOS:
        event_id = uuid4()
        result = service.post_event(
            event_type=scenario["event_type"],
            payload=scenario["payload"],
            effective_date=EFFECTIVE,
            actor_id=actor_id,
            amount=scenario["amount"],
            currency="USD",
            producer=scenario["event_type"].split(".")[0],
            event_id=event_id,
        )
        status = result.status.value.upper()
        is_ok = result.is_success
        if is_ok:
            ok_count += 1
            entry_ids = list(result.journal_entry_ids)
            posted_events.append((scenario, event_id, entry_ids))
            eng = scenario.get("engine", "")
            engine_counts[eng] = engine_counts.get(eng, 0) + 1
        else:
            entry_ids = []
            posted_events.append((scenario, event_id, []))

        tag = f"[{status:8s}]"
        eng_tag = f"({scenario.get('engine', '')} engine)" if scenario.get("engine") else ""
        print(f"    {scenario['id']:4s} {tag} {scenario['label']} {eng_tag}")
        if not is_ok:
            print(f"           {result.message}")

    print()
    print("  NON-ENGINE SCENARIOS:")
    print()
    for scenario in NON_ENGINE_SCENARIOS:
        event_id = uuid4()
        result = service.post_event(
            event_type=scenario["event_type"],
            payload=scenario["payload"],
            effective_date=EFFECTIVE,
            actor_id=actor_id,
            amount=scenario["amount"],
            currency="USD",
            producer=scenario["event_type"].split(".")[0],
            event_id=event_id,
        )
        status = result.status.value.upper()
        is_ok = result.is_success
        if is_ok:
            ok_count += 1
            entry_ids = list(result.journal_entry_ids)
            posted_events.append((scenario, event_id, entry_ids))
        else:
            entry_ids = []
            posted_events.append((scenario, event_id, []))

        tag = f"[{status:8s}]"
        print(f"    {scenario['id']:4s} {tag} {scenario['label']}")
        if not is_ok:
            print(f"           {result.message}")

    _restore_logging(muted)

    # -----------------------------------------------------------------
    # 7. Commit
    # -----------------------------------------------------------------
    print()
    total = len(all_scenarios)
    print(f"  Committing ({ok_count}/{total} transactions)...")
    session.commit()

    # -----------------------------------------------------------------
    # 8. Engine coverage summary
    # -----------------------------------------------------------------
    coverage_parts = [f"{eng}({cnt})" for eng, cnt in sorted(engine_counts.items())]
    print(f"  Engine coverage: {' '.join(coverage_parts)}")
    print()

    # -----------------------------------------------------------------
    # 9. Traceable entry IDs
    # -----------------------------------------------------------------
    print("  Traceable entries (use with scripts/trace.py):")
    print()
    for scenario, evt_id, entry_ids in posted_events:
        if entry_ids:
            print(f"    {scenario['id']} {scenario['label']}")
            print(f"      event-id: {evt_id}")
            for eid in entry_ids:
                print(f"      entry-id: {eid}")
    print()
    print("  Quick-start:")
    first = next((p for p in posted_events if p[2]), None)
    if first:
        print(f"    python3 scripts/trace.py --event-id {first[1]}")
    print("    python3 scripts/trace.py --list")
    print()

    # -----------------------------------------------------------------
    # 10. Inline trace (if --trace)
    # -----------------------------------------------------------------
    if args.trace:
        print("  " + "=" * 60)
        print("  ENGINE SCENARIO TRACES")
        print("  " + "=" * 60)
        print()
        muted = _enable_quiet_logging()
        for scenario, evt_id, entry_ids in posted_events:
            if scenario.get("engine") and entry_ids:
                trace_entry(session, evt_id, entry_ids, f"{scenario['id']} {scenario['label']}")
        _restore_logging(muted)

    session.close()
    print("  Done.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
