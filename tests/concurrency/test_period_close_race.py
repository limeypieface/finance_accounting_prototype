"""
R23: Period Close vs Post Race Condition Test.

This test verifies that when period close and posting operations occur
concurrently on the same period, the close operation always wins -
ensuring no posts slip through to a closing period.

This is a critical compliance test. A sophisticated adversary could
attempt to post transactions to a period that is being closed, potentially
corrupting the period closure audit trail.

Expected Behavior:
- Period close should win any race against concurrent posts
- Posts that start before close completes should either:
  1. Complete before close and succeed (OK)
  2. Be rejected with ClosedPeriodError (OK)
- No post should succeed AFTER the period is closed
"""

import pytest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from finance_kernel.db.engine import get_session_factory
from finance_kernel.exceptions import PeriodAlreadyClosedError
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.period_service import PeriodService
from finance_kernel.domain.clock import SystemClock
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry, JournalLine
from finance_kernel.models.event import Event
from finance_kernel.models.audit_event import AuditEvent
from finance_kernel.services.sequence_service import SequenceCounter


pytestmark = [pytest.mark.postgres, pytest.mark.slow_locks]


def cleanup_test_data(session):
    """Clean up all test data from the database."""
    from sqlalchemy import text

    # Use TRUNCATE CASCADE which bypasses triggers and is faster
    # This is only for test cleanup - production would never do this
    session.execute(text("""
        TRUNCATE TABLE
            journal_lines,
            journal_entries,
            audit_events,
            events,
            fiscal_periods,
            accounts,
            sequence_counters
        CASCADE
    """))
    session.commit()


def setup_test_data(session, actor_id):
    """Create standard accounts and period for testing."""
    accounts = {}
    account_specs = [
        ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("4000", "Revenue", AccountType.REVENUE, NormalBalance.CREDIT, None),
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


class TestPeriodCloseVsPostRace:
    """
    R23 Compliance Test: Period close must win race against concurrent posts.

    This is a critical test for financial system integrity. The period close
    operation must be atomic and must block or reject any concurrent posts.
    """

    def test_concurrent_close_and_post_close_wins(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Test that period close wins race against concurrent posts.

        Scenario:
        - 10 threads try to post events
        - 2 threads try to close the period
        - All operations start simultaneously via barrier

        Expected:
        - Close succeeds exactly once
        - Posts either succeed (before close) or are rejected (after close)
        - No post can succeed after period is marked closed
        """
        actor_id = uuid4()
        num_post_threads = 10
        num_close_threads = 2
        total_threads = num_post_threads + num_close_threads

        # Setup test data
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            period_code = period.period_code
            effective_date = period.start_date

        barrier = Barrier(total_threads, timeout=30)

        def post_event(thread_id: int):
            """Returns the PostingOrchestrator result. Must never raise."""
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
                    producer="race_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        def close_period_task(thread_id: int):
            """Returns 'closed' or 'already_closed'. Only PeriodAlreadyClosedError is expected."""
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                period_service = PeriodService(session, clock)

                try:
                    period_service.close_period(period_code, actor_id)
                    session.commit()
                    return "closed"
                except PeriodAlreadyClosedError:
                    return "already_closed"
            finally:
                session.close()

        # Launch threads
        with ThreadPoolExecutor(max_workers=total_threads) as executor:
            post_futures = [
                executor.submit(post_event, i)
                for i in range(num_post_threads)
            ]
            close_futures = [
                executor.submit(close_period_task, i)
                for i in range(num_close_threads)
            ]

            # All must complete without unexpected exceptions
            post_results = [f.result() for f in post_futures]
            close_results = [f.result() for f in close_futures]

        # Analyze results
        successful_closes = [r for r in close_results if r == "closed"]
        successful_posts = [r for r in post_results if r.is_success]
        rejected_posts = [r for r in post_results if r.status == PostingStatus.PERIOD_CLOSED]

        # Verify: Exactly one close should succeed
        assert len(successful_closes) == 1, (
            f"Expected exactly 1 successful close, got {len(successful_closes)}. "
            f"Close results: {close_results}"
        )

        # Verify: Period is now closed
        with pg_session_factory() as verify_session:
            period_service = PeriodService(verify_session, SystemClock())
            period_info = period_service.get_period_by_code(period_code)
            assert period_info is not None
            assert period_info.status.value == "closed", (
                f"Period should be closed, but status is {period_info.status}"
            )

        # Verify: All posts are accounted for (posted before close OR rejected)
        assert len(successful_posts) + len(rejected_posts) == num_post_threads, (
            f"All posts should be posted or rejected. "
            f"Posted: {len(successful_posts)}, "
            f"Rejected: {len(rejected_posts)}, "
            f"Total: {num_post_threads}"
        )

        print(f"\n[R23] Period Close vs Post Race Results:")
        print(f"  Successful closes: {len(successful_closes)}")
        print(f"  Successful posts (before close): {len(successful_posts)}")
        print(f"  Rejected posts (after close): {len(rejected_posts)}")

    def test_post_after_close_always_rejected(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Verify that posts attempted after period close are always rejected.

        This is the non-concurrent version - ensuring basic period locking works.
        """
        actor_id = uuid4()

        # Setup test data
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            period_code = period.period_code
            effective_date = period.start_date

        # Close the period first
        with pg_session_factory() as close_session:
            clock = SystemClock()
            period_service = PeriodService(close_session, clock)
            period_service.close_period(period_code, actor_id)
            close_session.commit()

        # Now try to post - should be rejected
        with pg_session_factory() as post_session:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(post_session, clock, auto_commit=True)

            result = orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=clock.now(),
                effective_date=effective_date,
                actor_id=actor_id,
                producer="race_test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )

            assert result.status == PostingStatus.PERIOD_CLOSED, (
                f"Post to closed period should be rejected, but got {result.status}"
            )

    def test_high_contention_close_race(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Stress test: High contention scenario with many concurrent close attempts.

        50 threads try to close, 50 threads try to post.
        Only one close should succeed.
        """
        actor_id = uuid4()
        num_threads = 50  # 50 closers, 50 posters

        # Setup test data
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            period_code = period.period_code
            effective_date = period.start_date

        barrier = Barrier(num_threads * 2, timeout=30)

        def close_worker(thread_id: int):
            """Returns 'closed' or 'already_closed'. Only PeriodAlreadyClosedError is expected."""
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()

                try:
                    period_service = PeriodService(session, clock)
                    period_service.close_period(period_code, actor_id)
                    session.commit()
                    return "closed"
                except PeriodAlreadyClosedError:
                    return "already_closed"
            finally:
                session.close()

        def post_worker(thread_id: int):
            """Returns posting result. Must never raise."""
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
                    producer="stress_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "10.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "10.00", "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        # Launch threads
        with ThreadPoolExecutor(max_workers=num_threads * 2) as executor:
            close_futures = [executor.submit(close_worker, i) for i in range(num_threads)]
            post_futures = [executor.submit(post_worker, i) for i in range(num_threads)]

            # All must complete without unexpected exceptions
            close_results = [f.result() for f in close_futures]
            post_results = [f.result() for f in post_futures]

        # Analyze
        successful_closes = [r for r in close_results if r == "closed"]
        successful_posts = [r for r in post_results if r.is_success]

        # Exactly one close should succeed
        assert len(successful_closes) == 1, (
            f"Expected exactly 1 successful close, got {len(successful_closes)}"
        )

        print(f"\n[R23 Stress] High Contention Results:")
        print(f"  Close attempts: {num_threads}")
        print(f"  Post attempts: {num_threads}")
        print(f"  Successful closes: {len(successful_closes)}")
        print(f"  Successful posts: {len(successful_posts)}")
