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

import json
import logging
import os
import threading
from io import StringIO

import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Generator
from uuid import uuid4, UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from finance_kernel.db.engine import (
    init_engine_from_url,
    create_tables,
    drop_tables,
    get_session,
    get_session_factory,
    reset_engine,
)
from finance_kernel.db.base import Base
from finance_kernel.db.immutability import register_immutability_listeners, unregister_immutability_listeners
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.ingestor_service import IngestorService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.domain.dtos import LineSpec, LineSide as DomainLineSide
from finance_kernel.domain.values import Money
from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilder,
    MeaningBuilderResult,
    ReferenceSnapshot,
)

# Import strategies to ensure they are registered
# R14/R15: Strategies self-register on import
import finance_kernel.domain.strategies.generic_strategy  # noqa: F401

from finance_kernel.logging_config import (
    StructuredFormatter,
    configure_logging,
    reset_logging,
    LogContext,
)


# Test actor ID for all test operations
TEST_ACTOR_ID = uuid4()


# =============================================================================
# Logging fixtures
# =============================================================================


@pytest.fixture(autouse=True, scope="session")
def _configure_test_logging():
    """Configure structured logging for the test suite."""
    reset_logging()
    configure_logging(level=logging.DEBUG)
    yield
    reset_logging()


@pytest.fixture(autouse=True)
def _clear_log_context():
    """Clear LogContext between tests to prevent cross-test contamination."""
    LogContext.clear()
    yield
    LogContext.clear()


@pytest.fixture
def captured_logs():
    """
    Capture finance_kernel logs as parsed JSON dicts.

    Usage::

        def test_something(captured_logs, post_via_coordinator):
            result = post_via_coordinator(...)
            logs = captured_logs()
            assert any(r["message"] == "posting_completed" for r in logs)
    """
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger("finance_kernel")
    root.addHandler(handler)

    def _get_records() -> list[dict]:
        lines = stream.getvalue().strip().split("\n")
        return [json.loads(line) for line in lines if line]

    yield _get_records

    root.removeHandler(handler)

# Default PostgreSQL URL for local installation
DEFAULT_POSTGRES_URL = "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test"


def pytest_sessionfinish(session, exitstatus):
    """Fallback cleanup: kill orphaned connections even if fixtures fail."""
    _kill_orphaned_connections()


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


def get_database_url() -> str:
    """Get database URL from environment, or use default PostgreSQL URL."""
    return os.environ.get("DATABASE_URL", DEFAULT_POSTGRES_URL)


# =============================================================================
# Session-scoped DB infrastructure (create engine + tables ONCE per suite)
# =============================================================================
#
# Previous architecture: function-scoped engine + tables => DROP/CREATE DDL on
# every test.  This caused pg_type_typname_nsp_index UniqueViolation when
# PostgreSQL composite types (enums) were not fully cleaned up between rapid
# drop/create cycles.
#
# New architecture: session-scoped engine + tables, per-test isolation via
# transaction rollback (regular tests) or TRUNCATE (concurrency tests).
# =============================================================================


# Advisory lock key for serializing concurrent pytest sessions.
# Prevents deadlocks when two test runs compete for DDL / trigger locks.
_TEST_QUEUE_LOCK_ID = 999_042


def _wait_for_test_lock(cur):
    """Poll for the test advisory lock, printing queue status updates.

    Shows who holds the lock, how long they have been running, how many
    others are waiting, and how long *we* have waited — so the developer
    knows the run is queued, not stuck.
    """
    import sys
    import time

    poll_interval = 2
    waited = 0

    sys.stdout.write("\n" + "=" * 64 + "\n")
    sys.stdout.write("  TEST QUEUE  —  another test session holds the database lock\n")
    sys.stdout.write("=" * 64 + "\n")
    sys.stdout.flush()

    while True:
        # Who holds the lock right now?
        cur.execute("""
            SELECT
                l.pid,
                EXTRACT(EPOCH FROM (now() - a.backend_start))::int,
                (SELECT count(*) FROM pg_locks
                 WHERE locktype = 'advisory'
                   AND objid = %s AND NOT granted)
            FROM pg_locks l
            JOIN pg_stat_activity a ON l.pid = a.pid
            WHERE l.locktype = 'advisory'
              AND l.objid = %s
              AND l.granted
            LIMIT 1
        """, (_TEST_QUEUE_LOCK_ID, _TEST_QUEUE_LOCK_ID))

        row = cur.fetchone()
        if row:
            holder_pid, holder_age, waiters = row
            msg = (
                f"  Held by PID {holder_pid} ({holder_age}s) "
                f"| {waiters} waiter(s) "
                f"| you have waited {waited}s"
            )
        else:
            msg = "  Lock released — acquiring ...             "

        sys.stdout.write(f"\r{msg}")
        sys.stdout.flush()

        time.sleep(poll_interval)
        waited += poll_interval

        # Try non-blocking acquire
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_TEST_QUEUE_LOCK_ID,))
        if cur.fetchone()[0]:
            sys.stdout.write(f"\n  Acquired after {waited}s wait.\n")
            sys.stdout.write("=" * 64 + "\n\n")
            sys.stdout.flush()
            return


@pytest.fixture(scope="session")
def _test_queue_lock():
    """Serialize test sessions with a PostgreSQL advisory lock.

    Acquired once per pytest session **before** the engine or any tables
    are touched.  If another session already holds the lock we enter a
    polling loop that prints live status so the developer knows their
    run is queued, not stuck.

    The advisory lock is automatically released when the psycopg2
    connection closes — even on crashes or SIGKILL.
    """
    import psycopg2

    try:
        lock_conn = psycopg2.connect(get_database_url())
    except Exception as exc:
        print(f"\n[test-queue] DB not reachable, skipping lock: {exc}")
        yield
        return

    lock_conn.autocommit = True
    cur = lock_conn.cursor()

    # Fast path: try non-blocking acquire
    cur.execute("SELECT pg_try_advisory_lock(%s)", (_TEST_QUEUE_LOCK_ID,))
    acquired = cur.fetchone()[0]

    if not acquired:
        _wait_for_test_lock(cur)

    yield  # --- tests run while we hold the lock ---

    try:
        cur.execute("SELECT pg_advisory_unlock(%s)", (_TEST_QUEUE_LOCK_ID,))
        cur.close()
        lock_conn.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def db_engine(_test_queue_lock):
    """Single PostgreSQL engine for the entire test session.

    Depends on ``_test_queue_lock`` so that no two pytest processes
    touch the database concurrently.

    Pool is large enough for concurrency tests (30+20 overflow).
    """
    _kill_orphaned_connections()
    db_url = get_database_url()
    eng = init_engine_from_url(
        db_url, echo=False,
        pool_size=30, max_overflow=20, pool_timeout=10,
    )
    yield eng
    reset_engine()
    _kill_orphaned_connections()


@pytest.fixture(scope="session")
def db_tables(db_engine):
    """Create all tables once per session, drop once at end.

    Immutability listeners are registered once and remain active.

    Kill orphaned connections right before DDL to prevent deadlocks
    when a prior test run left backends holding locks.
    """
    import time

    _kill_orphaned_connections()
    time.sleep(1.0)  # Let PostgreSQL reclaim locks from terminated backends

    # Retry drop_tables() — terminated backends may take time to release locks
    for attempt in range(3):
        try:
            drop_tables()
            break
        except Exception:
            if attempt < 2:
                _kill_orphaned_connections()
                time.sleep(1.0 * (attempt + 1))
            # On final attempt, swallow and proceed (create_tables is idempotent)
    create_tables()
    register_immutability_listeners()
    yield
    unregister_immutability_listeners()
    try:
        drop_tables()
    except Exception:
        pass


def _truncate_all_tables(engine):
    """TRUNCATE all tables for data cleanup (bypasses row-level triggers).

    Used by concurrency/raw-SQL tests that need real commits and therefore
    cannot rely on the rollback isolation pattern.
    """
    table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
    if table_names:
        with engine.connect() as conn:
            conn.execute(text(
                "TRUNCATE " + ", ".join(table_names) + " CASCADE"
            ))
            conn.commit()


# =============================================================================
# Backward-compatible aliases (many tests depend on 'engine' / 'tables')
# =============================================================================


@pytest.fixture(scope="function")
def engine(db_engine):
    """Backward-compatible alias — returns the session-scoped engine."""
    return db_engine


@pytest.fixture(scope="function")
def tables(db_tables):
    """Backward-compatible alias — returns the session-scoped tables sentinel."""
    return db_tables


# =============================================================================
# Per-test session with automatic rollback
# =============================================================================


@pytest.fixture(scope="function")
def session(db_tables, db_engine) -> Generator[Session, None, None]:
    """Provide a database session for testing.

    Uses the SQLAlchemy 2.0 ``join_transaction_block`` pattern:
    - Opens a dedicated connection with an outer transaction
    - Creates a session that *joins* the outer transaction
    - Any ``session.commit()`` inside the test releases a savepoint — it
      does NOT actually commit to the database
    - At teardown the outer transaction is rolled back, undoing ALL data
      changes made during the test

    This gives perfect per-test isolation with zero DDL churn.
    """
    conn = db_engine.connect()
    trans = conn.begin()
    sess = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield sess
    try:
        sess.close()
    finally:
        try:
            trans.rollback()
        finally:
            conn.close()


# =============================================================================
# Concurrency testing fixtures (real commits + TRUNCATE cleanup)
# =============================================================================


@pytest.fixture(scope="module")
def postgres_engine(db_engine, db_tables):
    """Reuse the session-scoped engine for concurrency tests.

    Module-scoped for backward compatibility — but no longer creates its own
    engine or manages its own DDL lifecycle.
    """
    yield db_engine


@pytest.fixture(scope="function")
def pg_session(db_engine, db_tables) -> Generator[Session, None, None]:
    """Provide a real PostgreSQL session for concurrency / raw-SQL tests.

    Unlike the regular ``session`` fixture, this session performs real
    commits.  Data isolation is achieved via TRUNCATE at teardown.
    """
    sess = get_session()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
        try:
            _truncate_all_tables(db_engine)
        except Exception:
            # Truncate may fail if pg_session_factory teardown already cleaned up
            # and invalidated connections (e.g. cross-session tests).
            pass


@pytest.fixture(scope="function")
def pg_session_factory(db_engine, db_tables):
    """Provide a tracked session factory for creating sessions in concurrent threads.

    Each thread should create its own session using this factory.
    The factory tracks all created sessions and on teardown:
    1. Blocks new session creation (late threads get RuntimeError)
    2. Force-closes all tracked sessions (returns connections to pool)
    3. Truncates all data
    """
    factory = get_session_factory()
    created_sessions = []
    lock = threading.Lock()
    closed = False

    def tracked_factory():
        nonlocal closed
        with lock:
            if closed:
                raise RuntimeError("pg_session_factory closed (fixture teardown)")
            s = factory()
            created_sessions.append(s)
            return s

    yield tracked_factory

    # Phase 1: Block new session creation
    with lock:
        closed = True

    # Phase 2: Rollback and close all tracked sessions
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

    # Phase 3: Truncate data
    _truncate_all_tables(db_engine)


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
def period_service(session: Session, deterministic_clock) -> PeriodService:
    """Provide a PeriodService instance."""
    return PeriodService(session, deterministic_clock)


# =============================================================================
# Interpretation pipeline fixtures
# =============================================================================


@pytest.fixture
def role_resolver(standard_accounts):
    """Provide a RoleResolver with standard account role bindings.

    Maps semantic roles to the standard test accounts so that
    AccountingIntent lines using roles can be resolved to real accounts.
    """
    resolver = RoleResolver()
    role_map = {
        "CashAsset": standard_accounts["cash"],
        "AccountsReceivable": standard_accounts["ar"],
        "InventoryAsset": standard_accounts["inventory"],
        "AccountsPayable": standard_accounts["ap"],
        "SalesRevenue": standard_accounts["revenue"],
        "COGS": standard_accounts["cogs"],
        "RoundingExpense": standard_accounts["rounding"],
    }
    for role, account in role_map.items():
        resolver.register_binding(role, account.id, account.code)
    return resolver


@pytest.fixture
def journal_writer(session, role_resolver, deterministic_clock, auditor_service):
    """Provide a JournalWriter instance."""
    return JournalWriter(session, role_resolver, deterministic_clock, auditor_service)


@pytest.fixture
def outcome_recorder(session, deterministic_clock):
    """Provide an OutcomeRecorder instance."""
    return OutcomeRecorder(session, deterministic_clock)


@pytest.fixture
def interpretation_coordinator(session, journal_writer, outcome_recorder, deterministic_clock):
    """Provide an InterpretationCoordinator instance."""
    return InterpretationCoordinator(session, journal_writer, outcome_recorder, deterministic_clock)


@pytest.fixture
def meaning_builder():
    """Provide a MeaningBuilder instance."""
    return MeaningBuilder()


def make_source_event(session, source_event_id, actor_id, clock, effective_date=None, event_type="test.event"):
    """Create an Event record in the events table for a given source_event_id.

    JournalEntry.source_event_id has FK("events.event_id"), so any test that
    posts through the interpretation pipeline must first create an Event record
    for the source_event_id used in AccountingIntent.
    """
    from finance_kernel.utils.hashing import hash_payload

    effective_date = effective_date or clock.now().date()
    payload = {"test": "data"}
    evt = Event(
        event_id=source_event_id,
        event_type=event_type,
        occurred_at=clock.now(),
        effective_date=effective_date,
        actor_id=actor_id,
        producer="test",
        payload=payload,
        payload_hash=hash_payload(payload),
        schema_version=1,
        ingested_at=clock.now(),
    )
    session.add(evt)
    session.flush()
    return evt


@pytest.fixture
def create_source_event(session, test_actor_id, deterministic_clock):
    """Factory fixture to create a source Event record.

    Wraps make_source_event with session, actor, and clock from fixtures.
    """

    def _create(source_event_id, effective_date=None, event_type="test.event"):
        return make_source_event(
            session, source_event_id, test_actor_id, deterministic_clock,
            effective_date, event_type,
        )

    return _create


@pytest.fixture
def post_via_coordinator(
    interpretation_coordinator,
    deterministic_clock,
    test_actor_id,
    session,
):
    """Helper fixture to post events through the full interpretation pipeline.

    Returns a function that creates an EconomicEventData and AccountingIntent,
    then posts via InterpretationCoordinator.

    Usage:
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("100.00"),
        )
        assert result.success
    """

    def _post(
        debit_role: str = "CashAsset",
        credit_role: str = "SalesRevenue",
        amount: Decimal = Decimal("100.00"),
        currency: str = "USD",
        event_type: str = "test.posting",
        effective_date: date | None = None,
        profile_id: str = "TestProfile",
        profile_version: int = 1,
        source_event_id: UUID | None = None,
        ledger_id: str = "GL",
        extra_lines: tuple = (),
    ):
        effective_date = effective_date or deterministic_clock.now().date()
        source_event_id = source_event_id or uuid4()
        econ_event_id = uuid4()

        # Create source Event record (FK requirement for JournalEntry)
        make_source_event(
            session, source_event_id, test_actor_id, deterministic_clock,
            effective_date, event_type,
        )

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type=event_type,
            effective_date=effective_date,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=None,
            quantity=amount,
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        lines = [
            IntentLine.debit(debit_role, amount, currency),
            IntentLine.credit(credit_role, amount, currency),
        ] + list(extra_lines)

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id=profile_id,
            profile_version=profile_version,
            effective_date=effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id=ledger_id,
                    lines=tuple(lines),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()
        return result

    return _post


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


# =============================================================================
# Module integration fixtures
# =============================================================================


@pytest.fixture(scope="session")
def register_modules():
    """Register all module profiles in kernel registries.

    Session-scoped: runs once per test session.
    Populates PolicySelector and ModulePolicyRegistry with all
    module-defined AccountingPolicys and their line mappings.
    """
    from finance_kernel.domain.policy_bridge import ModulePolicyRegistry
    from finance_kernel.domain.policy_selector import PolicySelector
    from finance_modules import register_all_modules

    PolicySelector.clear()
    ModulePolicyRegistry.clear()
    register_all_modules()
    yield
    PolicySelector.clear()
    ModulePolicyRegistry.clear()


@pytest.fixture
def module_accounts(create_account):
    """Create accounts for module profile integration testing.

    Provides accounts for common roles used by module profiles
    (inventory, AP, AR, GL, and subledger roles).
    """
    return {
        # GL asset accounts
        "cash": create_account(
            code="1000", name="Cash",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "ar": create_account(
            code="1100", name="Accounts Receivable",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "inventory": create_account(
            code="1200", name="Inventory",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "prepaid_expense": create_account(
            code="1400", name="Prepaid Expense",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # GL liability accounts
        "grni": create_account(
            code="2050", name="GRNI",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "ap": create_account(
            code="2000", name="Accounts Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "tax_payable": create_account(
            code="2100", name="Tax Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "accrued_liability": create_account(
            code="2200", name="Accrued Liability",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        # GL revenue
        "revenue": create_account(
            code="4000", name="Revenue",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        # GL expense accounts
        "cogs": create_account(
            code="5000", name="COGS",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "expense": create_account(
            code="5100", name="Operating Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "scrap_expense": create_account(
            code="5200", name="Scrap Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "rounding": create_account(
            code="9999", name="Rounding",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
        ),
        # Inventory subledger accounts
        "stock_on_hand": create_account(
            code="1201", name="Stock on Hand",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "in_transit": create_account(
            code="1202", name="In Transit",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # AP subledger accounts
        "invoice_ap": create_account(
            code="2001", name="AP Invoice",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "supplier_balance": create_account(
            code="2002", name="Supplier Balance",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "payment_ap": create_account(
            code="2003", name="AP Payment",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "reversal_ap": create_account(
            code="2004", name="AP Reversal",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        # Variance / adjustment
        "ppv": create_account(
            code="5300", name="Purchase Price Variance",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "inventory_variance": create_account(
            code="5400", name="Inventory Variance",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "inventory_revaluation": create_account(
            code="5500", name="Inventory Revaluation",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        # WIP
        "wip": create_account(
            code="1300", name="Work in Process",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # Inventory subledger — additional roles
        "sold": create_account(
            code="1203", name="Sold (Inventory Sub)",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "in_production": create_account(
            code="1204", name="In Production (Inventory Sub)",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "scrapped": create_account(
            code="1205", name="Scrapped (Inventory Sub)",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # =====================================================================
        # Additional accounts for full 12-module coverage
        # =====================================================================
        # --- Fixed Assets ---
        "fixed_asset": create_account(
            code="1500", name="Fixed Assets",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "accumulated_depreciation": create_account(
            code="1550", name="Accumulated Depreciation",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.CREDIT,
        ),
        "cip": create_account(
            code="1510", name="Construction in Progress",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Cash / Bank ---
        "bank": create_account(
            code="1020", name="Bank Account",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "undeposited_funds": create_account(
            code="1030", name="Undeposited Funds",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "cash_in_transit": create_account(
            code="1040", name="Cash in Transit",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- AR subledger ---
        "customer_balance": create_account(
            code="1101", name="Customer Balance",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "allowance_doubtful": create_account(
            code="1110", name="Allowance for Doubtful Accounts",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.CREDIT,
        ),
        "unapplied_cash": create_account(
            code="1120", name="Unapplied Cash",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Procurement ---
        "encumbrance": create_account(
            code="1600", name="Encumbrance",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- WIP sub-accounts ---
        "raw_materials": create_account(
            code="1210", name="Raw Materials",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "finished_goods": create_account(
            code="1220", name="Finished Goods",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Contracts ---
        "unbilled_ar": create_account(
            code="1150", name="Unbilled AR",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "wip_contract": create_account(
            code="1310", name="WIP Contract Costs",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Tax ---
        "tax_receivable": create_account(
            code="1160", name="Tax Receivable",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Intercompany ---
        "ic_due_from": create_account(
            code="1170", name="Intercompany Due From",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- FX ---
        "fc_balance": create_account(
            code="1180", name="Foreign Currency Balance",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Expense clearing ---
        "advance_clearing": create_account(
            code="1190", name="Advance Clearing",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "project_wip": create_account(
            code="1311", name="Project WIP",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Revenue (ASC 606) ---
        "contract_receivable": create_account(
            code="1121", name="Contract Receivable",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        "unbilled_receivable": create_account(
            code="1131", name="Unbilled Receivable",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Lease (ASC 842) ---
        "rou_asset": create_account(
            code="1701", name="Right-of-Use Asset",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Budget ---
        "budget_control": create_account(
            code="1630", name="Budget Control",
            account_type=AccountType.ASSET, normal_balance=NormalBalance.DEBIT,
        ),
        # =====================================================================
        # Liability accounts
        # =====================================================================
        "accrued_payroll": create_account(
            code="2210", name="Accrued Payroll",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "federal_tax_payable": create_account(
            code="2110", name="Federal Tax Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "state_tax_payable": create_account(
            code="2120", name="State Tax Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "fica_payable": create_account(
            code="2130", name="FICA Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "benefits_payable": create_account(
            code="2140", name="Benefits Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "deferred_revenue": create_account(
            code="2300", name="Deferred Revenue",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "deferred_fee_revenue": create_account(
            code="2310", name="Deferred Fee Revenue",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "employee_payable": create_account(
            code="2400", name="Employee Payable",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "corporate_card_liability": create_account(
            code="2410", name="Corporate Card Liability",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "ic_due_to": create_account(
            code="2500", name="Intercompany Due To",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        # --- Lease (ASC 842) ---
        "lease_liability": create_account(
            code="2800", name="Lease Liability",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        # --- Budget ---
        "budget_offset": create_account(
            code="2620", name="Budget Offset",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "reserve_for_encumbrance": create_account(
            code="2600", name="Reserve for Encumbrance",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "use_tax_accrual": create_account(
            code="2700", name="Use Tax Accrual",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        # --- Clearing / control ---
        "labor_clearing": create_account(
            code="2810", name="Labor Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "payroll_clearing": create_account(
            code="2815", name="Payroll Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "material_clearing": create_account(
            code="2820", name="Material Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "expense_clearing": create_account(
            code="2830", name="Expense Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "cost_clearing": create_account(
            code="2840", name="Cost Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "tax_clearing": create_account(
            code="2850", name="Tax Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "ap_clearing": create_account(
            code="2860", name="AP Clearing",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "overhead_pool": create_account(
            code="2870", name="Overhead Pool",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "overhead_pool_applied": create_account(
            code="2871", name="Overhead Pool Applied",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "fringe_pool_applied": create_account(
            code="2872", name="Fringe Pool Applied",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        "ga_pool_applied": create_account(
            code="2873", name="G&A Pool Applied",
            account_type=AccountType.LIABILITY, normal_balance=NormalBalance.CREDIT,
        ),
        # =====================================================================
        # Equity accounts
        # =====================================================================
        "retained_earnings": create_account(
            code="3200", name="Retained Earnings",
            account_type=AccountType.EQUITY, normal_balance=NormalBalance.CREDIT,
        ),
        "income_summary": create_account(
            code="3100", name="Income Summary",
            account_type=AccountType.EQUITY, normal_balance=NormalBalance.CREDIT,
        ),
        "dividends": create_account(
            code="3300", name="Dividends",
            account_type=AccountType.EQUITY, normal_balance=NormalBalance.DEBIT,
        ),
        "cumulative_translation_adj": create_account(
            code="3400", name="Cumulative Translation Adjustment",
            account_type=AccountType.EQUITY, normal_balance=NormalBalance.CREDIT,
        ),
        # =====================================================================
        # Revenue accounts
        # =====================================================================
        "fee_revenue_earned": create_account(
            code="4100", name="Fee Revenue Earned",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "interest_income": create_account(
            code="4200", name="Interest Income",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "gain_on_disposal": create_account(
            code="4300", name="Gain on Disposal",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "realized_fx_gain": create_account(
            code="4400", name="Realized FX Gain",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "unrealized_fx_gain": create_account(
            code="4410", name="Unrealized FX Gain",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "sales_returns": create_account(
            code="4500", name="Sales Returns",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.DEBIT,
        ),
        "sales_allowance": create_account(
            code="4510", name="Sales Allowance",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.DEBIT,
        ),
        # =====================================================================
        # Expense accounts
        # =====================================================================
        "depreciation_expense": create_account(
            code="5600", name="Depreciation Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "loss_on_disposal": create_account(
            code="5700", name="Loss on Disposal",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "impairment_loss": create_account(
            code="5710", name="Impairment Loss",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Lease (ASC 842) ---
        "rou_amortization": create_account(
            code="5720", name="ROU Amortization",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "lease_interest": create_account(
            code="5730", name="Lease Interest Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "salary_expense": create_account(
            code="5800", name="Salary Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "wage_expense": create_account(
            code="5810", name="Wage Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "overtime_expense": create_account(
            code="5820", name="Overtime Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "pto_expense": create_account(
            code="5830", name="PTO Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "payroll_tax_expense": create_account(
            code="5840", name="Payroll Tax Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "bank_fee_expense": create_account(
            code="5900", name="Bank Fee Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "bad_debt_expense": create_account(
            code="5910", name="Bad Debt Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "tax_expense": create_account(
            code="5920", name="Tax Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "overhead_expense": create_account(
            code="5930", name="Overhead Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "overhead_applied": create_account(
            code="5940", name="Overhead Applied",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "overhead_control": create_account(
            code="5950", name="Overhead Control",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "labor_variance": create_account(
            code="5960", name="Labor Variance",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "material_variance": create_account(
            code="5970", name="Material Variance",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "overhead_variance": create_account(
            code="5980", name="Overhead Variance",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "indirect_rate_variance": create_account(
            code="5990", name="Indirect Rate Variance",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "discount_expense": create_account(
            code="6000", name="Discount Expense",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "purchase_discount": create_account(
            code="4250", name="Purchase Discount",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "fx_gain_loss": create_account(
            code="6010", name="FX Gain/Loss",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "unrealized_fx_loss": create_account(
            code="6020", name="Unrealized FX Loss",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "realized_fx_loss": create_account(
            code="6030", name="Realized FX Loss",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        "contract_cost_incurred": create_account(
            code="6100", name="Contract Cost Incurred",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
        # --- Project Accounting ---
        "contract_revenue": create_account(
            code="4550", name="Contract Revenue",
            account_type=AccountType.REVENUE, normal_balance=NormalBalance.CREDIT,
        ),
        "direct_cost": create_account(
            code="5050", name="Direct Cost",
            account_type=AccountType.EXPENSE, normal_balance=NormalBalance.DEBIT,
        ),
    }


@pytest.fixture
def module_role_resolver(module_accounts):
    """RoleResolver with bindings for module profile roles.

    Maps account roles used by module AccountingPolicys to real test accounts.
    """
    resolver = RoleResolver()
    bindings = {
        # =================================================================
        # Core GL roles (existing)
        # =================================================================
        "INVENTORY": module_accounts["inventory"],
        "GRNI": module_accounts["grni"],
        "EXPENSE": module_accounts["expense"],
        "ACCOUNTS_PAYABLE": module_accounts["ap"],
        "CASH": module_accounts["cash"],
        "COGS": module_accounts["cogs"],
        "ACCOUNTS_RECEIVABLE": module_accounts["ar"],
        "REVENUE": module_accounts["revenue"],
        "TAX_PAYABLE": module_accounts["tax_payable"],
        "ACCRUED_LIABILITY": module_accounts["accrued_liability"],
        "PREPAID_EXPENSE": module_accounts["prepaid_expense"],
        "SCRAP_EXPENSE": module_accounts["scrap_expense"],
        "PPV": module_accounts["ppv"],
        "INVENTORY_VARIANCE": module_accounts["inventory_variance"],
        "RoundingExpense": module_accounts["rounding"],
        "WIP": module_accounts["wip"],
        "INVENTORY_IN_TRANSIT": module_accounts["in_transit"],  # GL role for WH transfer
        "INVENTORY_ASSET": module_accounts["inventory"],  # GL alias
        # Inventory subledger roles
        "STOCK_ON_HAND": module_accounts["stock_on_hand"],
        "IN_TRANSIT": module_accounts["in_transit"],
        "SOLD": module_accounts["sold"],
        "IN_PRODUCTION": module_accounts["in_production"],
        "SCRAPPED": module_accounts["scrapped"],
        "INVENTORY_REVALUATION": module_accounts["inventory_revaluation"],
        "ITEM_BALANCE": module_accounts["stock_on_hand"],  # subledger alias
        "ADJUSTMENT": module_accounts["inventory_variance"],  # subledger alias
        "WRITEOFF": module_accounts["scrapped"],  # subledger alias
        # AP subledger roles
        "INVOICE": module_accounts["invoice_ap"],
        "SUPPLIER_BALANCE": module_accounts["supplier_balance"],
        "PAYMENT": module_accounts["payment_ap"],
        "REVERSAL": module_accounts["reversal_ap"],
        "PURCHASE_DISCOUNT": module_accounts["purchase_discount"],
        # =================================================================
        # Fixed Assets module
        # =================================================================
        "FIXED_ASSET": module_accounts["fixed_asset"],
        "ACCUMULATED_DEPRECIATION": module_accounts["accumulated_depreciation"],
        "DEPRECIATION_EXPENSE": module_accounts["depreciation_expense"],
        "GAIN_ON_DISPOSAL": module_accounts["gain_on_disposal"],
        "LOSS_ON_DISPOSAL": module_accounts["loss_on_disposal"],
        "IMPAIRMENT_LOSS": module_accounts["impairment_loss"],
        "CIP": module_accounts["cip"],
        "AP": module_accounts["ap"],  # alias used by assets module
        # =================================================================
        # Cash / Bank module
        # =================================================================
        "BANK": module_accounts["bank"],
        "BANK_DESTINATION": module_accounts["bank"],  # same physical account
        "BANK_SOURCE": module_accounts["bank"],
        "UNDEPOSITED_FUNDS": module_accounts["undeposited_funds"],
        "CASH_IN_TRANSIT": module_accounts["cash_in_transit"],
        "BANK_FEE_EXPENSE": module_accounts["bank_fee_expense"],
        "INTEREST_INCOME": module_accounts["interest_income"],
        "RECON_VARIANCE": module_accounts["inventory_variance"],  # reuse variance acct
        "PAYROLL_CLEARING": module_accounts["payroll_clearing"],
        "AVAILABLE": module_accounts["cash"],
        "DEPOSIT": module_accounts["cash"],
        "WITHDRAWAL": module_accounts["cash"],
        "RECONCILED": module_accounts["bank"],
        "PENDING": module_accounts["bank"],
        # =================================================================
        # AR module
        # =================================================================
        "CUSTOMER_BALANCE": module_accounts["customer_balance"],
        "ALLOWANCE_DOUBTFUL": module_accounts["allowance_doubtful"],
        "BAD_DEBT_EXPENSE": module_accounts["bad_debt_expense"],
        "DEFERRED_REVENUE": module_accounts["deferred_revenue"],
        "UNAPPLIED_CASH": module_accounts["unapplied_cash"],
        "DISCOUNT_EXPENSE": module_accounts["discount_expense"],
        "SALES_RETURNS": module_accounts["sales_returns"],
        "SALES_ALLOWANCE": module_accounts["sales_allowance"],
        "CREDIT": module_accounts["ar"],  # credit memo against AR
        "REFUND": module_accounts["cash"],
        "WRITE_OFF": module_accounts["bad_debt_expense"],
        "FINANCE_CHARGE": module_accounts["interest_income"],
        "TAX_LIABILITY": module_accounts["tax_payable"],
        # --- Revenue (ASC 606) ---
        "CONTRACT_RECEIVABLE": module_accounts["contract_receivable"],
        "UNBILLED_RECEIVABLE": module_accounts["unbilled_receivable"],
        # --- Lease (ASC 842) ---
        "ROU_ASSET": module_accounts["rou_asset"],
        "LEASE_LIABILITY": module_accounts["lease_liability"],
        "ROU_AMORTIZATION": module_accounts["rou_amortization"],
        "LEASE_INTEREST": module_accounts["lease_interest"],
        # --- Budget ---
        "BUDGET_CONTROL": module_accounts["budget_control"],
        "BUDGET_OFFSET": module_accounts["budget_offset"],
        # =================================================================
        # Procurement module
        # =================================================================
        "ENCUMBRANCE": module_accounts["encumbrance"],
        "RESERVE_FOR_ENCUMBRANCE": module_accounts["reserve_for_encumbrance"],
        "PURCHASE_COMMITMENT": module_accounts["encumbrance"],
        "COMMITMENT_OFFSET": module_accounts["reserve_for_encumbrance"],
        "QUANTITY_VARIANCE": module_accounts["inventory_variance"],
        # =================================================================
        # WIP module (additional)
        # =================================================================
        "RAW_MATERIALS": module_accounts["raw_materials"],
        "FINISHED_GOODS": module_accounts["finished_goods"],
        "LABOR_CLEARING": module_accounts["labor_clearing"],
        "OVERHEAD_APPLIED": module_accounts["overhead_applied"],
        "OVERHEAD_CONTROL": module_accounts["overhead_control"],
        "LABOR_VARIANCE": module_accounts["labor_variance"],
        "MATERIAL_VARIANCE": module_accounts["material_variance"],
        "OVERHEAD_VARIANCE": module_accounts["overhead_variance"],
        # =================================================================
        # Payroll module
        # =================================================================
        "SALARY_EXPENSE": module_accounts["salary_expense"],
        "WAGE_EXPENSE": module_accounts["wage_expense"],
        "OVERTIME_EXPENSE": module_accounts["overtime_expense"],
        "PTO_EXPENSE": module_accounts["pto_expense"],
        "PAYROLL_TAX_EXPENSE": module_accounts["payroll_tax_expense"],
        "FEDERAL_TAX_PAYABLE": module_accounts["federal_tax_payable"],
        "STATE_TAX_PAYABLE": module_accounts["state_tax_payable"],
        "FICA_PAYABLE": module_accounts["fica_payable"],
        "BENEFITS_PAYABLE": module_accounts["benefits_payable"],
        "ACCRUED_PAYROLL": module_accounts["accrued_payroll"],
        "OVERHEAD_POOL": module_accounts["overhead_pool"],
        "OVERHEAD_EXPENSE": module_accounts["overhead_expense"],
        # =================================================================
        # Government Contracts module
        # =================================================================
        "WIP_DIRECT_LABOR": module_accounts["wip_contract"],
        "WIP_DIRECT_MATERIAL": module_accounts["wip_contract"],
        "WIP_SUBCONTRACT": module_accounts["wip_contract"],
        "WIP_TRAVEL": module_accounts["wip_contract"],
        "WIP_ODC": module_accounts["wip_contract"],
        "WIP_FRINGE": module_accounts["wip_contract"],
        "WIP_OVERHEAD": module_accounts["wip_contract"],
        "WIP_GA": module_accounts["wip_contract"],
        "MATERIAL_CLEARING": module_accounts["material_clearing"],
        "AP_CLEARING": module_accounts["ap_clearing"],
        "EXPENSE_CLEARING": module_accounts["expense_clearing"],
        "COST_CLEARING": module_accounts["cost_clearing"],
        "CONTRACT_COST_INCURRED": module_accounts["contract_cost_incurred"],
        "FRINGE_POOL_APPLIED": module_accounts["fringe_pool_applied"],
        "OVERHEAD_POOL_APPLIED": module_accounts["overhead_pool_applied"],
        "GA_POOL_APPLIED": module_accounts["ga_pool_applied"],
        "UNBILLED_AR": module_accounts["unbilled_ar"],
        "WIP_BILLED": module_accounts["wip_contract"],
        "DEFERRED_FEE_REVENUE": module_accounts["deferred_fee_revenue"],
        "BILLED": module_accounts["ar"],
        "COST_BILLED": module_accounts["cogs"],
        "FEE_REVENUE_EARNED": module_accounts["fee_revenue_earned"],
        "INDIRECT_RATE_VARIANCE": module_accounts["indirect_rate_variance"],
        "EXPENSE_ALLOWABLE": module_accounts["expense"],
        "EXPENSE_UNALLOWABLE": module_accounts["expense"],
        "EXPENSE_CONDITIONAL": module_accounts["expense"],
        "LABOR_ALLOWABLE": module_accounts["salary_expense"],
        "LABOR_UNALLOWABLE": module_accounts["salary_expense"],
        "OVERHEAD_POOL_ALLOWABLE": module_accounts["overhead_pool"],
        "OVERHEAD_UNALLOWABLE": module_accounts["overhead_expense"],
        "WIP_RATE_ADJUSTMENT": module_accounts["wip_contract"],
        "OBLIGATION_CONTROL": module_accounts["encumbrance"],
        # =================================================================
        # Tax module
        # =================================================================
        "TAX_EXPENSE": module_accounts["tax_expense"],
        "TAX_RECEIVABLE": module_accounts["tax_receivable"],
        "USE_TAX_ACCRUAL": module_accounts["use_tax_accrual"],
        "TAX_CLEARING": module_accounts["tax_clearing"],
        # =================================================================
        # GL module (additional)
        # =================================================================
        "RETAINED_EARNINGS": module_accounts["retained_earnings"],
        "INCOME_SUMMARY": module_accounts["income_summary"],
        "DIVIDENDS": module_accounts["dividends"],
        "FOREIGN_EXCHANGE_GAIN_LOSS": module_accounts["fx_gain_loss"],
        "INTERCOMPANY_DUE_TO": module_accounts["ic_due_to"],
        "INTERCOMPANY_DUE_FROM": module_accounts["ic_due_from"],
        "ROUNDING": module_accounts["rounding"],
        "FOREIGN_CURRENCY_BALANCE": module_accounts["fc_balance"],
        "UNREALIZED_FX_GAIN": module_accounts["unrealized_fx_gain"],
        "UNREALIZED_FX_LOSS": module_accounts["unrealized_fx_loss"],
        "REALIZED_FX_GAIN": module_accounts["realized_fx_gain"],
        "REALIZED_FX_LOSS": module_accounts["realized_fx_loss"],
        "CUMULATIVE_TRANSLATION_ADJ": module_accounts["cumulative_translation_adj"],
        # =================================================================
        # Expense module
        # =================================================================
        "EMPLOYEE_PAYABLE": module_accounts["employee_payable"],
        "CORPORATE_CARD_LIABILITY": module_accounts["corporate_card_liability"],
        "ADVANCE_CLEARING": module_accounts["advance_clearing"],
        "PROJECT_WIP": module_accounts["project_wip"],
        # =================================================================
        # Project Accounting module
        # =================================================================
        "CONTRACT_REVENUE": module_accounts["contract_revenue"],
        "DIRECT_COST": module_accounts["direct_cost"],
    }
    for role, account in bindings.items():
        resolver.register_binding(role, account.id, account.code)
    return resolver


@pytest.fixture
def module_posting_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide a ModulePostingService for integration testing.

    Depends on register_modules (session-scoped) to ensure all module
    profiles are registered before any posting is attempted.
    """
    from finance_kernel.services.module_posting_service import ModulePostingService

    return ModulePostingService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
        auto_commit=False,
    )


# =============================================================================
# Configuration-driven fixtures (Part 5 — YAML → CompiledPolicyPack → kernel)
# =============================================================================


@pytest.fixture(scope="session")
def test_config():
    """Load US-GAAP-2026-v1 configuration set and compile to CompiledPolicyPack.

    Session-scoped: compiled once and reused across all tests.
    This is the canonical way to obtain configuration at runtime.
    """
    from finance_config import get_active_config

    return get_active_config(
        legal_entity="*",
        as_of_date=date(2026, 1, 1),
    )


@pytest.fixture
def config_role_resolver(test_config):
    """Build a RoleResolver from the CompiledPolicyPack's role_bindings.

    Uses the config bridge to convert YAML-declared role bindings
    into kernel-compatible RoleResolver. Account UUIDs are deterministic
    (uuid5 from account codes).
    """
    from finance_config.bridges import build_role_resolver

    return build_role_resolver(test_config)
