#!/usr/bin/env python3
"""
Seed the database with a realistic set of business transactions.

Drops all tables, recreates them with immutability triggers, posts 8
business transactions through the interpretation pipeline, and commits.

Usage:
    python3 scripts/seed_data.py
"""

import logging
import sys
import time
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
FY_START = date(2025, 1, 1)
FY_END = date(2025, 12, 31)


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
        pass  # best-effort


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

    from finance_kernel.db.engine import (
        drop_tables,
        get_session,
        init_engine_from_url,
    )
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent,
        AccountingIntentSnapshot,
        IntentLine,
        LedgerIntent,
    )
    from finance_kernel.domain.clock import DeterministicClock
    from finance_kernel.domain.meaning_builder import (
        EconomicEventData,
        MeaningBuilderResult,
    )
    from finance_kernel.models.account import Account, AccountType, NormalBalance
    from finance_kernel.models.event import Event
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from finance_kernel.services.auditor_service import AuditorService
    from finance_kernel.services.interpretation_coordinator import (
        InterpretationCoordinator,
    )
    from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
    from finance_kernel.services.outcome_recorder import OutcomeRecorder
    from finance_kernel.utils.hashing import hash_payload
    from finance_modules._orm_registry import create_all_tables

    # -----------------------------------------------------------------
    # 1. Connect + reset
    # -----------------------------------------------------------------
    print()
    print("  [1/5] Connecting to PostgreSQL...")
    try:
        init_engine_from_url(DB_URL, echo=False)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1

    print("  [2/5] Dropping old tables and recreating schema...")
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
            pass  # tables may not exist yet

    create_all_tables(install_triggers=True)
    register_immutability_listeners()

    session = get_session()
    clock = DeterministicClock(datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC))
    actor_id = uuid4()

    # -----------------------------------------------------------------
    # 2. Chart of accounts
    # -----------------------------------------------------------------
    print("  [3/5] Creating chart of accounts (12 accounts)...")

    specs = [
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

    # -----------------------------------------------------------------
    # 3. Fiscal period
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    # 4. Posting pipeline (logging enabled for decision journal capture)
    # -----------------------------------------------------------------
    print("  [4/5] Posting 8 business transactions...")
    muted = _enable_quiet_logging()

    auditor = AuditorService(session, clock)
    resolver = RoleResolver()
    for role_key, acct in accounts.items():
        resolver.register_binding(role_key, acct.id, acct.code)

    writer = JournalWriter(session, resolver, clock, auditor)
    recorder = OutcomeRecorder(session, clock)
    coordinator = InterpretationCoordinator(session, writer, recorder, clock)

    def post(debit_role: str, credit_role: str, amount: Decimal, memo: str = ""):
        source_event_id = uuid4()
        effective = clock.now().date()

        payload = {"memo": memo, "amount": str(amount)}
        evt = Event(
            event_id=source_event_id,
            event_type="demo.posting",
            occurred_at=clock.now(),
            effective_date=effective,
            actor_id=actor_id,
            producer="seed_data",
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
        return result

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

    ok_count = 0
    posted_events = []  # (event_id, entry_ids, memo)
    for i, (dr, cr, amt, memo) in enumerate(txns, 1):
        result = post(dr, cr, amt, memo)
        status = "OK" if result.success else "FAIL"
        if result.success:
            ok_count += 1
            entry_ids = list(result.journal_result.entry_ids) if result.journal_result else []
            event_id = result.outcome.source_event_id if result.outcome else None
            posted_events.append((event_id, entry_ids, memo))
        print(f"         {i}. [{status}] {memo}")

    _restore_logging(muted)

    # -----------------------------------------------------------------
    # 5. Commit
    # -----------------------------------------------------------------
    print(f"  [5/5] Committing ({ok_count}/{len(txns)} transactions)...")
    session.commit()
    session.close()

    print()
    print(f"  Done. Database seeded with {ok_count} journal entries.")
    print()

    # -----------------------------------------------------------------
    # 6. Print traceable IDs
    # -----------------------------------------------------------------
    if posted_events:
        print("  Traceable entries (use with scripts/trace.py):")
        print()
        for evt_id, entry_ids, memo in posted_events:
            print(f"    {memo}")
            print(f"      event-id: {evt_id}")
            for eid in entry_ids:
                print(f"      entry-id: {eid}")
            print()
        print("  Quick-start:")
        first_evt = posted_events[0][0]
        print(f"    python3 scripts/trace.py --event-id {first_evt}")
        print("    python3 scripts/trace.py --list")
        print("    python3 scripts/demo_trace.py")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
