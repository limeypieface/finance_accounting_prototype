"""
Pytest fixtures for the finance kernel test suite.

Provides:
- PostgreSQL database sessions for all tests
- Test data generators
- Common test utilities

Environment Variables:
- DATABASE_URL: PostgreSQL connection URL (e.g., postgresql://user:pass@localhost/db)
  If not set, uses default local PostgreSQL URL.

Requirements:
- PostgreSQL must be running locally (brew services start postgresql@15)
- Database 'finance_kernel_test' must exist with user 'finance'
"""

import os
import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Generator
from uuid import uuid4, UUID

from sqlalchemy.orm import Session

from finance_kernel.db.engine import (
    init_engine_from_url,
    create_tables,
    drop_tables,
    get_session,
    get_session_factory,
    reset_engine,
    is_postgres,
)
from finance_kernel.db.base import Base
from finance_kernel.db.immutability import register_immutability_listeners, unregister_immutability_listeners
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.ingestor_service import IngestorService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.ledger_service import LedgerService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.posting_orchestrator import PostingOrchestrator
from finance_kernel.services.reference_data_loader import ReferenceDataLoader
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.domain.dtos import LineSpec, LineSide as DomainLineSide
from finance_kernel.domain.values import Money

# Import strategies to ensure they are registered
# R14/R15: Strategies self-register on import
import finance_kernel.domain.strategies.generic_strategy  # noqa: F401


# Test actor ID for all test operations
TEST_ACTOR_ID = uuid4()

# Default PostgreSQL URL for local installation
DEFAULT_POSTGRES_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "postgres: mark test as requiring PostgreSQL"
    )
    config.addinivalue_line(
        "markers", "slow_locks: mark test as potentially waiting for DB locks"
    )


def _kill_orphaned_connections():
    """
    Kill any orphaned database connections from previous test runs.

    This prevents tests from hanging when previous runs left connections
    open with uncommitted transactions holding locks.
    """
    import psycopg2
    try:
        # Connect to postgres database (not the test database) to kill connections
        conn = psycopg2.connect(
            dbname="postgres",
            user="finance",
            host="localhost",
            port=5432,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = 'finance_kernel_test'
            AND pid <> pg_backend_pid()
        """)
        terminated = cur.rowcount
        cur.close()
        conn.close()
        if terminated > 0:
            print(f"\n[conftest] Killed {terminated} orphaned DB connection(s)")
    except Exception as e:
        # Don't fail if we can't connect - DB might not be running
        print(f"\n[conftest] Could not clean orphaned connections: {e}")


@pytest.fixture(scope="session", autouse=True)
def cleanup_orphaned_connections():
    """
    Session-scoped fixture that runs once at the start of the test session
    to kill any orphaned database connections.
    """
    _kill_orphaned_connections()
    yield
    # Also clean up at the end
    _kill_orphaned_connections()


def get_database_url() -> str:
    """Get database URL from environment, or use default PostgreSQL URL."""
    return os.environ.get("DATABASE_URL", DEFAULT_POSTGRES_URL)


@pytest.fixture(scope="function")
def engine():
    """Create a PostgreSQL database engine for testing.

    Uses DATABASE_URL if set, otherwise default local PostgreSQL.
    """
    db_url = get_database_url()
    engine = init_engine_from_url(db_url, echo=False)
    yield engine
    reset_engine()


@pytest.fixture(scope="function")
def tables(engine):
    """Create all tables before test, drop after."""
    create_tables()
    # R10: Register immutability enforcement listeners
    register_immutability_listeners()
    yield
    # Clean up listeners before dropping tables
    unregister_immutability_listeners()
    drop_tables()


# =============================================================================
# Concurrency testing fixtures (higher connection pool)
# =============================================================================


@pytest.fixture(scope="module")
def postgres_engine():
    """
    Create a PostgreSQL engine with larger connection pool for concurrency testing.

    This fixture is module-scoped for efficiency - tables are created once
    per test module and shared across tests.
    """
    db_url = get_database_url()

    # Reset any existing engine first
    reset_engine()

    engine = init_engine_from_url(db_url, echo=False, pool_size=30, max_overflow=20)

    # Create tables
    create_tables()
    register_immutability_listeners()

    yield engine

    # Cleanup
    unregister_immutability_listeners()
    drop_tables()
    reset_engine()


@pytest.fixture(scope="function")
def pg_session(postgres_engine) -> Generator[Session, None, None]:
    """
    Provide a PostgreSQL session for concurrency testing.

    Each test gets a fresh session that is rolled back after the test.
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture(scope="function")
def pg_session_factory(postgres_engine):
    """
    Provide the session factory for creating sessions in concurrent threads.

    Each thread should create its own session using this factory.
    """
    return get_session_factory()


@pytest.fixture(scope="function")
def session(tables) -> Generator[Session, None, None]:
    """Provide a database session for testing."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def test_actor_id() -> UUID:
    """Provide a consistent test actor ID."""
    return TEST_ACTOR_ID


# Clock fixtures


@pytest.fixture
def deterministic_clock():
    """Provide a deterministic clock for testing."""
    return DeterministicClock()


# Service fixtures


@pytest.fixture
def auditor_service(session: Session, deterministic_clock):
    """Provide an AuditorService instance."""
    return AuditorService(session, deterministic_clock)


@pytest.fixture
def ingestor_service(session: Session, deterministic_clock, auditor_service):
    """Provide an IngestorService instance."""
    return IngestorService(session, deterministic_clock, auditor_service)


@pytest.fixture
def ledger_service(session: Session, deterministic_clock, auditor_service):
    """Provide a LedgerService instance."""
    return LedgerService(session, deterministic_clock, auditor_service)


@pytest.fixture
def period_service(session: Session, deterministic_clock) -> PeriodService:
    """Provide a PeriodService instance."""
    return PeriodService(session, deterministic_clock)


@pytest.fixture
def posting_orchestrator(session: Session, deterministic_clock):
    """Provide a PostingOrchestrator instance (with auto_commit=False for testing)."""
    return PostingOrchestrator(session, deterministic_clock, auto_commit=False)


@pytest.fixture
def reference_data_loader(session: Session):
    """Provide a ReferenceDataLoader instance."""
    return ReferenceDataLoader(session)


# Selector fixtures


@pytest.fixture
def ledger_selector(session: Session) -> LedgerSelector:
    """Provide a LedgerSelector instance."""
    return LedgerSelector(session)


@pytest.fixture
def journal_selector(session: Session) -> JournalSelector:
    """Provide a JournalSelector instance."""
    return JournalSelector(session)


# Test data generators


@pytest.fixture
def create_account(session: Session, test_actor_id: UUID):
    """Factory fixture to create test accounts."""

    def _create_account(
        code: str,
        name: str,
        account_type: AccountType = AccountType.ASSET,
        normal_balance: NormalBalance = NormalBalance.DEBIT,
        is_active: bool = True,
        tags: list[str] | None = None,
        currency: str | None = None,
    ) -> Account:
        account = Account(
            code=code,
            name=name,
            account_type=account_type,
            normal_balance=normal_balance,
            is_active=is_active,
            tags=tags,
            currency=currency,
            created_by_id=test_actor_id,
        )
        session.add(account)
        session.flush()
        return account

    return _create_account


@pytest.fixture
def create_period(session: Session, test_actor_id: UUID):
    """Factory fixture to create test fiscal periods."""

    def _create_period(
        period_code: str,
        name: str,
        start_date: date,
        end_date: date,
        status: PeriodStatus = PeriodStatus.OPEN,
    ) -> FiscalPeriod:
        period = FiscalPeriod(
            period_code=period_code,
            name=name,
            start_date=start_date,
            end_date=end_date,
            status=status,
            created_by_id=test_actor_id,
        )
        session.add(period)
        session.flush()
        return period

    return _create_period


@pytest.fixture
def create_event(session: Session, test_actor_id: UUID, deterministic_clock):
    """Factory fixture to create test events."""

    def _create_event(
        event_type: str = "test.event",
        producer: str = "test",
        payload: dict | None = None,
        effective_date: date | None = None,
        occurred_at: datetime | None = None,
    ) -> Event:
        from finance_kernel.utils.hashing import hash_payload

        event_id = uuid4()
        payload = payload or {"test": "data"}
        effective_date = effective_date or deterministic_clock.now().date()
        occurred_at = occurred_at or deterministic_clock.now()

        event = Event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            effective_date=effective_date,
            actor_id=test_actor_id,
            producer=producer,
            payload=payload,
            payload_hash=hash_payload(payload),
            schema_version=1,
            ingested_at=deterministic_clock.now(),
        )
        session.add(event)
        session.flush()
        return event

    return _create_event


@pytest.fixture
def standard_accounts(create_account):
    """Create a standard set of test accounts."""
    accounts = {
        "cash": create_account(
            code="1000",
            name="Cash",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        ),
        "ar": create_account(
            code="1100",
            name="Accounts Receivable",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        ),
        "inventory": create_account(
            code="1200",
            name="Inventory",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        ),
        "ap": create_account(
            code="2000",
            name="Accounts Payable",
            account_type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT,
        ),
        "revenue": create_account(
            code="4000",
            name="Sales Revenue",
            account_type=AccountType.REVENUE,
            normal_balance=NormalBalance.CREDIT,
        ),
        "cogs": create_account(
            code="5000",
            name="Cost of Goods Sold",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
        ),
        "rounding": create_account(
            code="9999",
            name="Rounding",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
        ),
    }
    return accounts


@pytest.fixture
def current_period(create_period, deterministic_clock):
    """Create a current open fiscal period."""
    today = deterministic_clock.now().date()
    start = today.replace(day=1)
    if today.month == 12:
        end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    return create_period(
        period_code=today.strftime("%Y-%m"),
        name=today.strftime("%B %Y"),
        start_date=start,
        end_date=end,
    )


# Reference data fixtures


@pytest.fixture
def reference_data(standard_accounts):
    """Provide reference data for the pure strategy layer."""
    from finance_kernel.domain.dtos import ReferenceData
    from finance_kernel.domain.currency import CurrencyRegistry
    from finance_kernel.domain.values import Currency

    account_ids_by_code = {
        acc.code: acc.id for acc in standard_accounts.values()
    }
    active_account_codes = frozenset(
        acc.code for acc in standard_accounts.values() if acc.is_active
    )
    rounding_account_ids = {
        "USD": standard_accounts["rounding"].id,
    }
    # Add general rounding for all currencies
    for code in CurrencyRegistry.all_codes():
        if code not in rounding_account_ids:
            rounding_account_ids[code] = standard_accounts["rounding"].id

    return ReferenceData(
        account_ids_by_code=account_ids_by_code,
        active_account_codes=active_account_codes,
        valid_currencies=frozenset(Currency(c) for c in CurrencyRegistry.all_codes()),
        rounding_account_ids=rounding_account_ids,
    )


# Utility functions


def make_balanced_lines(
    debit_account_code: str,
    credit_account_code: str,
    amount: Decimal,
    currency: str = "USD",
) -> list[LineSpec]:
    """Create a balanced pair of journal lines using account codes."""
    return [
        LineSpec(
            account_code=debit_account_code,
            side=DomainLineSide.DEBIT,
            money=Money.of(amount, currency),
        ),
        LineSpec(
            account_code=credit_account_code,
            side=DomainLineSide.CREDIT,
            money=Money.of(amount, currency),
        ),
    ]


# Export utility for tests
@pytest.fixture
def make_lines():
    """Provide the make_balanced_lines utility."""
    return make_balanced_lines
