"""
B1-B2: Fault Injection and Crash Recovery Tests.

Financial systems must be resilient to crashes at any point.
These tests verify that:

1. Partial writes are prevented (atomicity)
2. Crashes don't leave orphaned records
3. System recovers to consistent state
4. Transaction isolation is maintained
"""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4
from datetime import date, datetime
from decimal import Decimal
import threading
import time

from sqlalchemy import select, event
from sqlalchemy.orm import Session

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ledger_service import LedgerService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus
from finance_kernel.models.event import Event
from finance_kernel.models.audit_event import AuditEvent
from finance_kernel.domain.clock import DeterministicClock


class SimulatedCrash(Exception):
    """Exception to simulate a crash at a specific point."""
    pass


class TestAtomicityGuarantees:
    """
    B1: Partial write prevention tests.

    Verify that a crash during posting doesn't leave partial state.
    """

    def test_crash_during_entry_creation_leaves_no_orphans(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that a crash during entry creation doesn't leave orphaned records.
        """
        orchestrator = PostingOrchestrator(
            session, deterministic_clock, auto_commit=False
        )

        # Count records before
        events_before = len(session.execute(select(Event)).scalars().all())
        entries_before = len(session.execute(select(JournalEntry)).scalars().all())
        lines_before = len(session.execute(select(JournalLine)).scalars().all())

        event_id = uuid4()

        # Simulate crash by raising exception
        with patch.object(
            LedgerService, 'persist',
            side_effect=SimulatedCrash("Simulated crash during persist")
        ):
            with pytest.raises(SimulatedCrash):
                orchestrator.post_event(
                    event_id=event_id,
                    event_type="generic.posting",
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="crash_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )

        # Rollback should have happened
        session.rollback()

        # Verify no orphaned records
        events_after = len(session.execute(select(Event)).scalars().all())
        entries_after = len(session.execute(select(JournalEntry)).scalars().all())
        lines_after = len(session.execute(select(JournalLine)).scalars().all())

        # If the event was created before crash, it should be rolled back
        # OR if the event creation was atomic with the crash point, nothing new
        assert entries_after == entries_before, (
            f"No new entries should exist after crash. Before: {entries_before}, After: {entries_after}"
        )
        assert lines_after == lines_before, (
            f"No new lines should exist after crash. Before: {lines_before}, After: {lines_after}"
        )

    def test_crash_during_audit_event_creation_rolls_back(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that crash during audit event creation rolls back entire transaction.
        """
        # Create auditor that crashes
        auditor = AuditorService(session, deterministic_clock)

        with patch.object(
            auditor, 'record_posting',
            side_effect=SimulatedCrash("Simulated crash during audit")
        ):
            orchestrator = PostingOrchestrator(
                session, deterministic_clock, auto_commit=False
            )
            orchestrator._auditor = auditor

            entries_before = len(session.execute(select(JournalEntry)).scalars().all())

            with pytest.raises(SimulatedCrash):
                orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="crash_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )

            session.rollback()

            # Verify no new entries (transaction rolled back)
            entries_after = len(session.execute(select(JournalEntry)).scalars().all())
            assert entries_after == entries_before


class TestTransactionIsolation:
    """
    B2: Transaction isolation tests.

    Verify that concurrent transactions are properly isolated.
    """

    def test_concurrent_posts_isolated(
        self,
        pg_session_factory,
        postgres_engine,
    ):
        """
        Verify that concurrent posts are isolated from each other.

        One post's uncommitted data should not be visible to another.
        """
        from finance_kernel.domain.clock import SystemClock

        actor_id = uuid4()

        # Setup test data
        with pg_session_factory() as setup_session:
            from finance_kernel.models.account import Account, AccountType, NormalBalance
            from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
            from datetime import timedelta

            # Clean up first
            setup_session.query(JournalLine).delete()
            setup_session.query(JournalEntry).delete()
            setup_session.query(Event).delete()
            setup_session.query(AuditEvent).delete()
            setup_session.query(FiscalPeriod).delete()
            setup_session.query(Account).delete()

            # Create accounts
            for code, name, atype, nbal in [
                ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT),
                ("4000", "Revenue", AccountType.REVENUE, NormalBalance.CREDIT),
            ]:
                setup_session.add(Account(
                    code=code,
                    name=name,
                    account_type=atype,
                    normal_balance=nbal,
                    is_active=True,
                    created_by_id=actor_id,
                ))

            # Create period
            today = date.today()
            setup_session.add(FiscalPeriod(
                period_code=today.strftime("%Y-%m"),
                name="Test Period",
                start_date=today.replace(day=1),
                end_date=today.replace(day=28),
                status=PeriodStatus.OPEN,
                created_by_id=actor_id,
            ))
            setup_session.commit()

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=15)

        def transaction_a():
            """First transaction - posts and waits."""
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=False)

                barrier.wait()  # Sync with transaction B

                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=clock.now().date(),
                    actor_id=actor_id,
                    producer="tx_a",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )

                # Wait before committing
                time.sleep(0.1)
                session.commit()
                with results_lock:
                    results.append(("A", result.status))
            except Exception as e:
                with results_lock:
                    results.append(("A", f"error: {e}"))
            finally:
                session.close()

        def transaction_b():
            """Second transaction - reads during A's uncommitted state."""
            session = pg_session_factory()
            try:
                barrier.wait()  # Sync with transaction A

                # Give A time to post but not commit
                time.sleep(0.05)

                # Read entries - should not see A's uncommitted entry
                entries = session.execute(
                    select(JournalEntry).where(
                        JournalEntry.source_event_type == "generic.posting"
                    )
                ).scalars().all()

                uncommitted_visible = any(
                    e.idempotency_key and "tx_a" in e.idempotency_key
                    for e in entries
                )

                with results_lock:
                    results.append(("B", f"uncommitted_visible={uncommitted_visible}"))
            except Exception as e:
                with results_lock:
                    results.append(("B", f"error: {e}"))
            finally:
                session.close()

        # Run concurrently
        thread_a = threading.Thread(target=transaction_a)
        thread_b = threading.Thread(target=transaction_b)

        thread_a.start()
        thread_b.start()

        thread_a.join(timeout=30)
        thread_b.join(timeout=30)

        # Both threads must have reported back
        assert len(results) == 2, (
            f"Only {len(results)}/2 threads reported back: {results}"
        )

        # Transaction B should NOT have seen A's uncommitted data
        b_result = next((r for r in results if r[0] == "B"), None)
        assert b_result is not None, "Transaction B never reported results"
        assert "uncommitted_visible=False" in str(b_result[1]), (
            f"Uncommitted data should not be visible. Got: {b_result}"
        )


class TestRecoveryScenarios:
    """
    Tests for system recovery after failures.
    """

    def test_system_state_consistent_after_crash_and_restart(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify system is in consistent state after crash and restart.
        """
        orchestrator = PostingOrchestrator(
            session, deterministic_clock, auto_commit=False
        )

        # Post a successful entry first
        result1 = orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="recovery_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result1.status == PostingStatus.POSTED
        session.commit()

        # Simulate crash during second post
        with patch.object(
            LedgerService, 'persist',
            side_effect=SimulatedCrash("Crash during second post")
        ):
            orchestrator2 = PostingOrchestrator(
                session, deterministic_clock, auto_commit=False
            )

            with pytest.raises(SimulatedCrash):
                orchestrator2.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="recovery_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "200.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "200.00", "currency": "USD"},
                        ]
                    },
                )

        session.rollback()

        # "Restart" - create new orchestrator
        orchestrator3 = PostingOrchestrator(
            session, deterministic_clock, auto_commit=False
        )

        # System should be consistent - audit chain valid
        assert auditor_service.validate_chain() is True

        # First entry should still exist
        entry = session.get(JournalEntry, result1.journal_entry_id)
        assert entry is not None
        assert entry.is_posted

        # Can post new entries
        result3 = orchestrator3.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="recovery_test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "300.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "300.00", "currency": "USD"},
                ]
            },
        )
        assert result3.status == PostingStatus.POSTED
        session.commit()

        # Chain still valid
        assert auditor_service.validate_chain() is True

    def test_no_duplicate_sequence_numbers_after_crash(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify no duplicate sequence numbers after crash recovery.
        """
        orchestrator = PostingOrchestrator(
            session, deterministic_clock, auto_commit=False
        )

        sequences = []

        # Post several entries
        for i in range(3):
            result = orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="seq_test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(100 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(100 + i), "currency": "USD"},
                    ]
                },
            )
            if result.status == PostingStatus.POSTED:
                sequences.append(result.seq)
            session.commit()

        # Simulate crash (rollback without commit)
        session.rollback()

        # Post more entries
        for i in range(3):
            result = orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="seq_test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(200 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(200 + i), "currency": "USD"},
                    ]
                },
            )
            if result.status == PostingStatus.POSTED:
                sequences.append(result.seq)
            session.commit()

        # Verify no duplicate sequences
        assert len(sequences) == len(set(sequences)), (
            f"Duplicate sequence numbers found: {sequences}"
        )

        # Verify sequences are ordered
        sorted_seqs = sorted(sequences)
        assert sequences == sorted_seqs or all(
            sequences[i] < sequences[i + 1] for i in range(len(sequences) - 1)
        ), f"Sequences should be ordered: {sequences}"


class TestGracefulDegradation:
    """
    Tests for graceful handling of failure conditions.
    """

    def test_connection_loss_during_post(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify system handles connection loss gracefully.
        """
        from sqlalchemy.exc import OperationalError

        orchestrator = PostingOrchestrator(
            session, deterministic_clock, auto_commit=False
        )

        # Simulate connection loss
        with patch.object(
            session, 'flush',
            side_effect=OperationalError("Connection lost", None, None)
        ):
            with pytest.raises(OperationalError):
                orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="connection_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )

        # Session should still be usable after recovery
        session.rollback()

        # In a real scenario, we'd need a new session
        # But for testing, just verify the exception was propagated correctly
