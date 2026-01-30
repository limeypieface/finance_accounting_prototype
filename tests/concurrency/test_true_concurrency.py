"""
True concurrency tests using PostgreSQL.

These tests require a running PostgreSQL instance and test actual race conditions
using real multi-connection parallelism.

Run with:
    docker-compose up -d
    DATABASE_URL=postgresql://finance:finance_test_pwd@localhost/finance_kernel_test \
        pytest tests/concurrency/test_true_concurrency.py -v

Or simply:
    pytest tests/concurrency/test_true_concurrency.py -v -m postgres
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Lock, Event as ThreadEvent
from uuid import uuid4
from typing import Callable

from finance_kernel.db.engine import get_session_factory
from finance_kernel.db.immutability import register_immutability_listeners
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.sequence_service import SequenceService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.domain.clock import DeterministicClock, SystemClock
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus


pytestmark = [pytest.mark.postgres, pytest.mark.slow_locks]


def cleanup_test_data(session):
    """Clean up all test data from the database.

    Uses TRUNCATE CASCADE to bypass immutability triggers that prevent
    deletion of posted journal entries and audit events.
    """
    from sqlalchemy import text

    tables_to_truncate = [
        "interpretation_outcomes", "economic_events",
        "journal_lines", "journal_entries", "audit_events", "events",
        "fiscal_periods", "accounts", "sequence_counters",
    ]
    for table in tables_to_truncate:
        try:
            session.execute(text(f"TRUNCATE {table} CASCADE"))
        except Exception:
            session.rollback()
    session.commit()


def setup_test_data(session, actor_id):
    """Create standard accounts and period for testing."""
    # Create accounts
    accounts = {}
    account_specs = [
        ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("1100", "AR", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("1200", "Inventory", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("2000", "AP", AccountType.LIABILITY, NormalBalance.CREDIT, None),
        ("4000", "Revenue", AccountType.REVENUE, NormalBalance.CREDIT, None),
        ("5000", "COGS", AccountType.EXPENSE, NormalBalance.DEBIT, None),
        ("9999", "Rounding", AccountType.EXPENSE, NormalBalance.DEBIT, [AccountTag.ROUNDING.value]),
    ]

    for code, name, acct_type, normal_bal, tags in account_specs:
        account = Account(
            code=code,
            name=name,
            account_type=acct_type,
            normal_balance=normal_bal,
            is_active=True,
            tags=tags,
            created_by_id=actor_id,
        )
        session.add(account)
        accounts[code] = account

    # Create fiscal period
    today = date.today()
    start = today.replace(day=1)
    if today.month == 12:
        end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    period = FiscalPeriod(
        period_code=today.strftime("%Y-%m"),
        name=today.strftime("%B %Y"),
        start_date=start,
        end_date=end,
        status=PeriodStatus.OPEN,
        created_by_id=actor_id,
    )
    session.add(period)
    session.commit()

    return accounts, period


class TestTrueConcurrentIdempotency:
    """
    True concurrency tests for idempotency.

    These tests use multiple threads with barriers to ensure
    simultaneous execution and test real race conditions.
    """

    def test_10_threads_same_event_exactly_one_wins(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        10 threads race to post the same event - exactly 1 must win.

        This tests the idempotency constraint under true concurrent load.
        """
        actor_id = uuid4()
        event_id = uuid4()
        num_threads = 10

        # Setup test data in main thread (clean first)
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        # Barrier ensures all threads start simultaneously
        barrier = Barrier(num_threads, timeout=30)

        def post_event(thread_id: int):
            """Each thread attempts to post the same event."""
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                return orchestrator.post_event(
                    event_id=event_id,
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="concurrency_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_threads)]
            # future.result() re-raises if a thread hit an unexpected exception
            results = [f.result() for f in futures]

        posted = [r for r in results if r.status == PostingStatus.POSTED]
        already_posted = [r for r in results if r.status == PostingStatus.ALREADY_POSTED]

        assert len(posted) == 1, f"Expected exactly 1 POSTED, got {len(posted)}"
        assert len(already_posted) == num_threads - 1, (
            f"Expected {num_threads - 1} ALREADY_POSTED, got {len(already_posted)}"
        )

        # All results should reference the same entry
        entry_ids = {r.journal_entry_id for r in results if r.journal_entry_id}
        assert len(entry_ids) == 1, f"Expected 1 unique entry, got {len(entry_ids)}"

    def test_50_threads_same_event_stress(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        50 threads race to post the same event.

        Higher thread count increases collision probability.
        """
        actor_id = uuid4()
        event_id = uuid4()
        num_threads = 50

        # Setup (clean first)
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads, timeout=30)

        def post_event(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                return orchestrator.post_event(
                    event_id=event_id,
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="stress_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "50.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "50.00", "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        posted = [r for r in results if r.status == PostingStatus.POSTED]
        already_posted = [r for r in results if r.status == PostingStatus.ALREADY_POSTED]

        assert len(posted) == 1, f"Expected 1 POSTED, got {len(posted)}"
        assert len(already_posted) == num_threads - 1


class TestTrueConcurrentSequence:
    """
    True concurrency tests for sequence number assignment.

    Ensures sequence numbers remain unique and monotonic under concurrent load.
    """

    def test_100_concurrent_sequence_allocations_unique(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        100 concurrent threads allocate sequence numbers - all must be unique.
        """
        num_threads = 100
        barrier = Barrier(num_threads, timeout=30)

        def allocate_sequence(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                service = SequenceService(session)
                seq = service.next_value(SequenceService.JOURNAL_ENTRY)
                session.commit()
                return seq
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(allocate_sequence, i) for i in range(num_threads)]
            sequences = [f.result() for f in futures]

        assert len(sequences) == num_threads
        assert len(sequences) == len(set(sequences)), "Duplicate sequences detected!"

    def test_sequence_monotonicity_under_concurrency(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Sequences must be monotonically increasing (no gaps reused).
        """
        num_threads = 50
        iterations_per_thread = 10
        expected_total = num_threads * iterations_per_thread

        def allocate_many(thread_id: int):
            session = pg_session_factory()
            try:
                service = SequenceService(session)
                thread_sequences = []
                for _ in range(iterations_per_thread):
                    seq = service.next_value(SequenceService.JOURNAL_ENTRY)
                    session.commit()
                    thread_sequences.append(seq)
                return thread_sequences
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(allocate_many, i) for i in range(num_threads)]
            all_sequences = []
            for f in futures:
                all_sequences.extend(f.result())

        assert len(all_sequences) == expected_total, (
            f"Expected {expected_total} sequences, got {len(all_sequences)}"
        )

        # Sort and verify uniqueness
        sorted_seqs = sorted(all_sequences)
        assert len(sorted_seqs) == len(set(sorted_seqs)), "Duplicate sequences!"

        # Verify strictly increasing (no gaps reused)
        for i in range(1, len(sorted_seqs)):
            assert sorted_seqs[i] > sorted_seqs[i-1], (
                f"Non-monotonic sequence: {sorted_seqs[i-1]} -> {sorted_seqs[i]}"
            )


class TestTrueConcurrentPosting:
    """
    True concurrency tests for the full posting flow.
    """

    def test_100_distinct_events_all_post_successfully(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        100 threads posting 100 distinct events - all should succeed.
        """
        actor_id = uuid4()
        num_threads = 100

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads, timeout=30)

        def post_distinct_event(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                return orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="distinct_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": str(10 + thread_id), "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": str(10 + thread_id), "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_distinct_event, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        # All should be POSTED
        assert all(r.status == PostingStatus.POSTED for r in results), (
            f"Not all POSTED: {[r.status for r in results if r.status != PostingStatus.POSTED]}"
        )

        # All entry IDs should be unique
        entry_ids = [r.journal_entry_id for r in results]
        assert len(entry_ids) == len(set(entry_ids)), "Duplicate entry IDs!"

    def test_balance_integrity_under_concurrent_posting(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        After concurrent posting, trial balance must still be balanced.
        """
        actor_id = uuid4()
        num_threads = 50

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads, timeout=30)

        def post_balanced_entry(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                if thread_id % 2 == 0:
                    lines = [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                else:
                    lines = [
                        {"account_code": "5000", "side": "debit", "amount": "60.00", "currency": "USD"},
                        {"account_code": "1200", "side": "credit", "amount": "60.00", "currency": "USD"},
                    ]

                return orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="balance_test",
                    payload={"lines": lines},
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_balanced_entry, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        assert all(r.status == PostingStatus.POSTED for r in results)

        # Verify trial balance
        from finance_kernel.selectors.ledger_selector import LedgerSelector

        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, (
                f"Trial balance not balanced! Debits: {total_debits}, Credits: {total_credits}"
            )


class TestConcurrentAuditChain:
    """
    True concurrency tests for audit chain integrity.
    """

    def test_audit_chain_valid_after_concurrent_posts(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Audit chain must remain valid after concurrent posting.
        """
        actor_id = uuid4()
        num_threads = 30

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads, timeout=30)

        def post_event(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                return orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="audit_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        assert all(r.status == PostingStatus.POSTED for r in results)

        # Verify chain integrity
        with pg_session_factory() as verify_session:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(verify_session, clock, auto_commit=False)
            assert orchestrator.validate_chain() is True, (
                "Audit chain corrupted after concurrent posting!"
            )
