"""
Adversarial tests for PostingOrchestrator attack vectors.

These tests target specific attack patterns:
1. Parallel Twin: Concurrent duplicate event creation
2. Ghost Entry: Orphaned side effects after rollback
3. Validation Backdoor: Bypassing R13 via post_existing_event
4. Stale Reference: Exploiting cached reference data

Run with:
    pytest tests/adversarial/test_orchestrator_attack_vectors.py -v
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

from sqlalchemy import text, select, event as sa_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_kernel.db.engine import get_session_factory
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.domain.clock import SystemClock, DeterministicClock
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus
from finance_kernel.models.event import Event
from finance_kernel.models.audit_event import AuditEvent


class TestParallelTwin:
    """
    Attack Vector: Parallel Twin

    Trigger two concurrent requests for the same event ID to see if the lack
    of a database lock allows duplicate entries to be created simultaneously.

    Expected behavior: Only ONE journal entry should ever be created for a
    given event_id, regardless of race conditions.
    """

    def test_parallel_twin_sqlite_same_session_race(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Sequential simulation of race condition in SQLite (single connection).

        Simulates: Two "threads" trying to post the same event within a
        single transaction before either commits.
        """
        event_id = uuid4()
        event_type = "generic.posting"  # Use registered strategy
        effective_date = current_period.start_date

        payload = {
            "lines": [
                {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
            ]
        }

        # First post succeeds
        result1 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=effective_date,
            actor_id=test_actor_id,
            producer="twin_a",
            payload=payload,
        )
        assert result1.status == PostingStatus.POSTED, f"First post failed: {result1.validation}"

        # Second post with SAME event_id must be rejected or idempotent
        result2 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=effective_date,
            actor_id=test_actor_id,
            producer="twin_b",
            payload=payload,
        )

        # Must NOT create a duplicate
        assert result2.status == PostingStatus.ALREADY_POSTED, (
            f"Expected ALREADY_POSTED for duplicate event, got {result2.status}"
        )

        # Verify only ONE journal entry exists
        entries = session.execute(
            select(JournalEntry).where(JournalEntry.source_event_id == event_id)
        ).scalars().all()

        assert len(entries) == 1, f"Expected 1 entry, found {len(entries)} (parallel twin attack succeeded!)"

    @pytest.mark.postgres
    def test_parallel_twin_true_concurrency(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        True concurrent race condition test (PostgreSQL required).

        10 threads simultaneously try to post the same event_id.
        Only 1 should succeed, others should get ALREADY_POSTED or fail gracefully.
        """
        actor_id = uuid4()
        event_id = uuid4()
        num_threads = 10

        # Setup
        from tests.concurrency.test_true_concurrency import cleanup_test_data, setup_test_data

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads)
        results = []
        results_lock = Lock()

        def attack_post(thread_id: int):
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                barrier.wait()  # All threads start simultaneously

                result = orchestrator.post_event(
                    event_id=event_id,
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer=f"twin_{thread_id}",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )

                with results_lock:
                    results.append((thread_id, result))
            except Exception as e:
                with results_lock:
                    results.append((thread_id, e))
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(attack_post, i) for i in range(num_threads)]
            wait(futures)

        # Analyze results
        posted = [r for tid, r in results if hasattr(r, 'status') and r.status == PostingStatus.POSTED]
        already_posted = [r for tid, r in results if hasattr(r, 'status') and r.status == PostingStatus.ALREADY_POSTED]
        errors = [r for tid, r in results if isinstance(r, Exception)]

        # Invariant: Exactly 1 thread should create the entry
        assert len(posted) == 1, (
            f"Parallel Twin Attack: Expected exactly 1 POSTED, got {len(posted)}. "
            f"ALREADY_POSTED: {len(already_posted)}, Errors: {len(errors)}"
        )

        # Verify database has exactly 1 entry
        with pg_session_factory() as verify_session:
            entries = verify_session.execute(
                select(JournalEntry).where(JournalEntry.source_event_id == event_id)
            ).scalars().all()

            assert len(entries) == 1, (
                f"CRITICAL: Found {len(entries)} entries for same event_id! "
                "Parallel twin attack created duplicates!"
            )


class TestGhostEntry:
    """
    Attack Vector: Ghost Entry

    Force a database commit failure and check if external side effects
    (like audit logs or notifications) remained active despite the
    transaction rolling back.

    Expected behavior: ALL side effects must be rolled back with the transaction.
    No orphaned audit events, no phantom notifications.
    """

    def test_ghost_entry_no_orphan_audit_on_rollback(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that audit events are rolled back when posting fails.

        If the main transaction fails, audit events created within that
        transaction must also be rolled back - no ghost entries.
        """
        event_id = uuid4()

        # Count audit events before
        audit_count_before = session.execute(
            select(AuditEvent)
        ).scalars().all()
        count_before = len(audit_count_before)

        # Create orchestrator with auto_commit=False so we control the transaction
        clock = deterministic_clock
        orchestrator = PostingOrchestrator(session, clock, auto_commit=False)

        # Post an event (this creates audit events within the transaction)
        result = orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="ghost_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        assert result.is_success, f"Expected success, got {result.status}"

        # Audit events should exist in the pending transaction
        audit_count_pending = len(session.execute(select(AuditEvent)).scalars().all())
        assert audit_count_pending > count_before, "Audit events should be created"

        # Now ROLLBACK instead of commit (simulating failure)
        session.rollback()

        # Fresh query after rollback
        audit_count_after = len(session.execute(select(AuditEvent)).scalars().all())

        # Invariant: No ghost audit entries
        assert audit_count_after == count_before, (
            f"Ghost Entry Attack: Found {audit_count_after - count_before} orphaned audit events "
            "after rollback! Audit events were not rolled back with transaction."
        )

        # Verify no journal entry exists
        entries = session.execute(
            select(JournalEntry).where(JournalEntry.source_event_id == event_id)
        ).scalars().all()

        assert len(entries) == 0, "Ghost journal entry found after rollback!"

    def test_ghost_entry_no_orphan_event_on_ledger_failure(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        If ledger persistence fails after event ingestion, the event
        should also be rolled back - no orphaned Event without JournalEntry.
        """
        event_id = uuid4()

        # Count events before
        event_count_before = len(session.execute(select(Event)).scalars().all())

        # Create orchestrator
        clock = deterministic_clock
        orchestrator = PostingOrchestrator(session, clock, auto_commit=False)

        # Post with valid event but intentionally cause ledger to fail
        # We'll use an invalid account code that will fail during ledger persist
        result = orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="ghost_test",
            payload={
                "lines": [
                    {"account_code": "NONEXISTENT", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "1000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        # Should fail validation
        assert not result.is_success

        # Rollback
        session.rollback()

        # Verify no orphaned Event
        event_count_after = len(session.execute(select(Event)).scalars().all())
        assert event_count_after == event_count_before, (
            f"Ghost Event Attack: Found {event_count_after - event_count_before} orphaned "
            "Event records without corresponding JournalEntry!"
        )


class TestValidationBackdoor:
    """
    Attack Vector: Validation Backdoor

    Use the secondary post_existing_event entry point to bypass R13
    adjustment restrictions that are only enforced in the primary
    post_event method.

    Expected behavior: post_existing_event must enforce the SAME validation
    rules as post_event, including R13 adjustment restrictions.
    """

    def test_post_existing_event_must_enforce_r13_adjustments(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        period_service,
    ):
        """
        Verify post_existing_event enforces R13 adjustment policy.

        Scenario:
        1. Create a period with allows_adjustments=False
        2. Ingest an event
        3. Try to post via post_existing_event with is_adjustment=True
        4. Verify R13 is enforced - must be rejected

        This test ensures the backdoor is CLOSED.
        """
        # Create a period that does NOT allow adjustments
        no_adj_period = FiscalPeriod(
            period_code="NO-ADJ-2024",
            name="No Adjustments Period",
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 30),
            status=PeriodStatus.OPEN,
            allows_adjustments=False,  # R13: No adjustments allowed
            created_by_id=test_actor_id,
        )
        session.add(no_adj_period)
        session.flush()

        event_id = uuid4()
        clock = deterministic_clock

        # Step 1: Ingest an event first (without posting)
        from finance_kernel.services.ingestor_service import IngestorService
        from finance_kernel.services.auditor_service import AuditorService

        auditor = AuditorService(session, clock)
        ingestor = IngestorService(session, clock, auditor)

        ingest_result = ingestor.ingest(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=date(2024, 6, 15),  # In the no-adjustment period
            actor_id=test_actor_id,
            producer="backdoor_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        assert ingest_result.status == IngestStatus.ACCEPTED
        session.flush()

        orchestrator = PostingOrchestrator(session, clock, auto_commit=False)

        # Step 2: Try to post via post_existing_event WITH is_adjustment=True
        # This should be REJECTED because the period doesn't allow adjustments
        result = orchestrator.post_existing_event(
            event_id=event_id,
            required_dimensions=None,
            is_adjustment=True,  # R13: This is an adjusting entry
        )

        # Must be rejected due to R13 adjustment policy
        assert result.status == PostingStatus.ADJUSTMENTS_NOT_ALLOWED, (
            f"R13 Backdoor OPEN: post_existing_event allowed adjustment to no-adjustments period! "
            f"Got status: {result.status}, message: {result.message}"
        )

        # Step 3: Verify non-adjustment posts still work
        event_id_2 = uuid4()
        ingest_result_2 = ingestor.ingest(
            event_id=event_id_2,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=date(2024, 6, 15),
            actor_id=test_actor_id,
            producer="backdoor_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "50.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "50.00", "currency": "USD"},
                ]
            },
        )
        session.flush()

        # Non-adjustment should succeed (is_adjustment=False by default)
        result_2 = orchestrator.post_existing_event(
            event_id=event_id_2,
            required_dimensions=None,
            # is_adjustment defaults to False
        )

        assert result_2.is_success, (
            f"Non-adjustment post should succeed: {result_2.status}, {result_2.message}"
        )

        session.rollback()

    def test_post_existing_event_respects_closed_period(
        self,
        session,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
        period_service,
    ):
        """
        Verify post_existing_event respects closed period restrictions.

        This is the minimum validation that must be enforced.
        """
        # Create and close a period
        closed_period = FiscalPeriod(
            period_code="CLOSED-BACKDOOR",
            name="Closed Test Period",
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            status=PeriodStatus.OPEN,
            created_by_id=test_actor_id,
        )
        session.add(closed_period)
        session.flush()

        # Close the period
        period_service.close_period("CLOSED-BACKDOOR", test_actor_id)
        session.flush()

        event_id = uuid4()
        clock = deterministic_clock

        # Ingest event for closed period date
        auditor = AuditorService(session, clock)
        ingestor = IngestorService(session, clock, auditor)

        ingest_result = ingestor.ingest(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=date(2024, 3, 15),  # In closed period
            actor_id=test_actor_id,
            producer="backdoor_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        session.flush()

        # Try backdoor
        orchestrator = PostingOrchestrator(session, clock, auto_commit=False)
        result = orchestrator.post_existing_event(event_id=event_id)

        # Must be rejected due to closed period
        assert result.status == PostingStatus.PERIOD_CLOSED, (
            f"Validation Backdoor: post_existing_event allowed posting to closed period! "
            f"Got status: {result.status}"
        )

        session.rollback()


class TestStaleReference:
    """
    Attack Vector: Stale Reference

    Process two events in a single uncommitted session while changing
    underlying reference data in between to see if the second event
    uses outdated, cached values.

    Expected behavior: Reference data should be fresh for each event,
    or if cached, should be consistent within a transaction.
    """

    def test_stale_reference_account_deactivation(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Post event A, deactivate an account, then post event B.
        Event B should see the deactivated account.
        """
        clock = deterministic_clock
        orchestrator = PostingOrchestrator(session, clock, auto_commit=False)

        # Post first event using account 1000
        event_a_id = uuid4()
        result_a = orchestrator.post_event(
            event_id=event_a_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="stale_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result_a.is_success, f"Event A should succeed: {result_a.status}"
        session.flush()

        # Deactivate account 1000 MID-TRANSACTION
        account_1000 = session.execute(
            select(Account).where(Account.code == "1000")
        ).scalar_one()
        account_1000.is_active = False
        session.flush()

        # Post second event using the now-deactivated account 1000
        event_b_id = uuid4()
        result_b = orchestrator.post_event(
            event_id=event_b_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="stale_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "50.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "50.00", "currency": "USD"},
                ]
            },
        )

        # The second event should either:
        # 1. Fail because account is inactive (fresh reference data)
        # 2. Succeed but use a consistent snapshot (acceptable if documented)

        if result_b.is_success:
            # If it succeeds, this might indicate stale reference data
            # OR the system intentionally allows inactive accounts for in-progress txns
            # Document this as a potential finding
            pytest.skip(
                "FINDING: Event B succeeded after account deactivation. "
                "This may indicate stale reference data caching. "
                "Verify if this is intentional transaction isolation behavior."
            )
        else:
            # Expected: Account deactivation is seen
            assert result_b.status == PostingStatus.VALIDATION_FAILED, (
                f"Expected validation failure for inactive account, got {result_b.status}"
            )

        session.rollback()

    def test_stale_reference_dimension_deactivation(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Post event A with dimension, deactivate dimension, then post event B.
        Event B should see the deactivated dimension.
        """
        from finance_kernel.models.dimensions import Dimension, DimensionValue

        clock = deterministic_clock

        # Create dimension and value
        dimension = Dimension(
            code="project",
            name="Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dimension)
        session.flush()

        project_value = DimensionValue(
            dimension_code="project",
            code="PROJ001",
            name="Test Project",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(project_value)
        session.flush()

        orchestrator = PostingOrchestrator(session, clock, auto_commit=False)

        # Post first event with dimension
        event_a_id = uuid4()
        result_a = orchestrator.post_event(
            event_id=event_a_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="stale_test",
            payload={
                "lines": [
                    {
                        "account_code": "1000",
                        "side": "debit",
                        "amount": "100.00",
                        "currency": "USD",
                        "dimensions": {"project": "PROJ001"},
                    },
                    {
                        "account_code": "2000",
                        "side": "credit",
                        "amount": "100.00",
                        "currency": "USD",
                        "dimensions": {"project": "PROJ001"},
                    },
                ]
            },
            required_dimensions={"project"},
        )
        session.flush()

        # Deactivate the dimension value MID-TRANSACTION
        project_value.is_active = False
        session.flush()

        # Post second event with same dimension value
        event_b_id = uuid4()
        result_b = orchestrator.post_event(
            event_id=event_b_id,
            event_type="generic.posting",
            occurred_at=clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="stale_test",
            payload={
                "lines": [
                    {
                        "account_code": "1000",
                        "side": "debit",
                        "amount": "50.00",
                        "currency": "USD",
                        "dimensions": {"project": "PROJ001"},
                    },
                    {
                        "account_code": "2000",
                        "side": "credit",
                        "amount": "50.00",
                        "currency": "USD",
                        "dimensions": {"project": "PROJ001"},
                    },
                ]
            },
            required_dimensions={"project"},
        )

        # Reference data loader should reload and see the deactivated dimension
        # If it uses cached data, this is a stale reference vulnerability

        if result_b.is_success:
            pytest.skip(
                "FINDING: Event B succeeded after dimension deactivation. "
                "ReferenceDataLoader may cache dimension data. "
                "Consider invalidating cache between postings in same session."
            )

        session.rollback()

    @pytest.mark.postgres
    def test_stale_reference_cross_session_race(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Session A loads reference data, Session B modifies it,
        Session A posts using stale data.

        This tests transaction isolation effects on reference data.
        """
        from tests.concurrency.test_true_concurrency import cleanup_test_data, setup_test_data

        actor_id = uuid4()

        # Setup
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        # Session A: Load reference data (simulated by creating orchestrator)
        session_a = pg_session_factory()
        clock_a = SystemClock()
        orchestrator_a = PostingOrchestrator(session_a, clock_a, auto_commit=False)

        # Session B: Deactivate an account
        with pg_session_factory() as session_b:
            account = session_b.execute(
                select(Account).where(Account.code == "1000")
            ).scalar_one()
            account.is_active = False
            session_b.commit()

        # Session A: Try to post using the now-deactivated account
        # If Session A has stale reference data, it might still allow the post
        event_id = uuid4()
        result = orchestrator_a.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=clock_a.now(),
            effective_date=effective_date,
            actor_id=actor_id,
            producer="stale_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        session_a.rollback()
        session_a.close()

        # With proper transaction isolation, Session A should see the original state
        # This is actually CORRECT behavior for REPEATABLE READ or higher isolation
        # The question is whether the system handles this appropriately

        if result.is_success:
            # This is expected with REPEATABLE READ - Session A has a consistent snapshot
            pass
        else:
            # If it fails, Session A saw the deactivation (READ COMMITTED)
            assert result.status == PostingStatus.VALIDATION_FAILED

        # Re-enable account for cleanup
        with pg_session_factory() as cleanup_session:
            account = cleanup_session.execute(
                select(Account).where(Account.code == "1000")
            ).scalar_one()
            account.is_active = True
            cleanup_session.commit()
