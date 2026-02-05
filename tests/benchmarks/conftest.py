"""
Benchmark fixtures — full pipeline wiring for performance measurement.

Provides:
- bench_posting_service: Module-scoped fixture wiring the complete pipeline
  (PostingOrchestrator + EngineDispatcher + ModulePostingService)
- Scenario factories for simple, complex, and engine-requiring events
- bench_session_factory: For concurrent benchmark tests

Mirrors the wiring pattern from scripts/demo_engines.py but is designed
for repeatable benchmark runs with TRUNCATE cleanup between modules.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy.orm import Session

from tests.benchmarks.helpers import BenchTimer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FY_START = date(2026, 1, 1)
FY_END = date(2026, 12, 31)
EFFECTIVE = date(2026, 6, 15)
COA_UUID_NS = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


# ---------------------------------------------------------------------------
# Account creation (from demo_engines.py)
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


def create_accounts_from_config(session, config, actor_id: UUID) -> int:
    """Create Account rows for every role_binding in config."""
    from finance_kernel.models.account import Account

    created = 0
    seen_codes: set[str] = set()
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


# ---------------------------------------------------------------------------
# Scenario factories
# ---------------------------------------------------------------------------

def make_simple_event(*, iteration: int = 0) -> dict:
    """Simple 2-line inventory receipt (no engine)."""
    return {
        "event_type": "inventory.receipt",
        "amount": Decimal("25000.00"),
        "currency": "USD",
        "payload": {"quantity": 500, "has_variance": False},
        "producer": "inventory",
    }


def make_complex_event(*, iteration: int = 0) -> dict:
    """Multi-line payroll accrual (5+ journal lines).

    The PayrollAccrual profile reads from_context fields:
      federal_tax_amount, state_tax_amount, fica_amount, net_pay_amount
    and generates one line per non-zero field. Combined with the base
    SALARY_EXPENSE debit, this produces 5 journal lines.

    Balance: Dr SALARY_EXPENSE $125,000
             Cr FEDERAL_TAX_PAYABLE $25,000
             Cr STATE_TAX_PAYABLE $8,750
             Cr FICA_PAYABLE $9,562.50
             Cr ACCRUED_PAYROLL $81,687.50
    """
    return {
        "event_type": "payroll.accrual",
        "amount": Decimal("125000.00"),
        "currency": "USD",
        "payload": {
            "gross_pay": "125000.00",
            "federal_tax_amount": "25000.00",
            "state_tax_amount": "8750.00",
            "fica_amount": "9562.50",
            "net_pay_amount": "81687.50",
        },
        "producer": "payroll",
    }


def make_engine_event(*, iteration: int = 0) -> dict:
    """Inventory receipt with PPV variance engine dispatch."""
    return {
        "event_type": "inventory.receipt",
        "amount": Decimal("10500.00"),
        "currency": "USD",
        "payload": {
            "quantity": 1000,
            "has_variance": True,
            "standard_price": "10.00",
            "actual_price": "10.50",
            "standard_total": "10000.00",
            "variance_amount": "500.00",
            "variance_type": "price",
            "expected_price": "10.00",
        },
        "producer": "inventory",
    }


SCENARIO_FACTORIES = {
    "simple_2_line": make_simple_event,
    "complex_multi_line": make_complex_event,
    "engine_requiring": make_engine_event,
}


# ---------------------------------------------------------------------------
# Module-scoped benchmark posting service
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bench_posting_service(db_engine, db_tables):
    """Full-pipeline ModulePostingService for benchmarks.

    Module-scoped: created once per benchmark test file, torn down
    with TRUNCATE at the end.

    Uses real commits (not savepoint rollback) so timing includes
    actual DB flushes and WAL writes.
    """
    from sqlalchemy import text

    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_kernel.db.base import Base
    from finance_kernel.db.engine import get_session
    from finance_kernel.domain.clock import DeterministicClock
    from finance_kernel.domain.policy_bridge import ModulePolicyRegistry
    from finance_kernel.domain.policy_selector import PolicySelector
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_modules import register_all_modules
    from finance_services.invokers import register_standard_engines
    from finance_services.posting_orchestrator import PostingOrchestrator

    # Mute console logging during benchmarks
    logging.disable(logging.CRITICAL)

    # 1. Reset policy registries then load config + register profiles (avoid PolicyAlreadyRegisteredError when run after other tests)
    PolicySelector.clear()
    ModulePolicyRegistry.clear()
    config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)
    register_all_modules()

    # 2. TRUNCATE for a clean slate
    table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
    if table_names:
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
            conn.commit()

    # 3. Create session + seed data
    session = get_session()
    clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))
    actor_id = uuid4()

    create_accounts_from_config(session, config, actor_id)

    period = FiscalPeriod(
        period_code="FY2026",
        name="Fiscal Year 2026",
        start_date=FY_START,
        end_date=FY_END,
        status=PeriodStatus.OPEN,
        created_by_id=actor_id,
    )
    session.add(period)

    actor_party = Party(
        id=actor_id,
        party_code="BENCH-ACTOR",
        party_type=PartyType.EMPLOYEE,
        name="Benchmark Actor",
        status=PartyStatus.ACTIVE,
        is_active=True,
        created_by_id=actor_id,
    )
    session.add(actor_party)
    session.flush()
    session.commit()

    # 4. Wire PostingOrchestrator + engines
    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=session,
        compiled_pack=config,
        role_resolver=role_resolver,
        clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)

    service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=True)

    yield {
        "service": service,
        "session": session,
        "actor_id": actor_id,
        "config": config,
        "clock": clock,
        "orchestrator": orchestrator,
        "db_engine": db_engine,
    }

    # Teardown
    try:
        session.close()
    except Exception:
        pass
    if table_names:
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
            conn.commit()
    logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def bench_session_factory(db_engine, db_tables):
    """Tracked session factory for concurrent benchmark tests.

    Module-scoped. Each thread creates its own session via this factory.
    Teardown force-closes all sessions and truncates data.
    """
    from sqlalchemy import text

    from finance_kernel.db.base import Base
    from finance_kernel.db.engine import get_session_factory

    factory = get_session_factory()
    created_sessions: list[Session] = []
    lock = threading.Lock()
    closed = False

    def tracked_factory() -> Session:
        nonlocal closed
        with lock:
            if closed:
                raise RuntimeError("bench_session_factory closed (teardown)")
            s = factory()
            created_sessions.append(s)
            return s

    yield tracked_factory

    with lock:
        closed = True

    for s in created_sessions:
        try:
            if s.is_active:
                s.rollback()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass

    table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
    if table_names:
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
            conn.commit()


@pytest.fixture
def bench_timer():
    """Provide a fresh BenchTimer instance."""
    return BenchTimer()
