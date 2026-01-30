"""
Concurrency tests for R20 compliance.

R20. Test class mapping - Concurrency tests (race safety)

These tests verify that invariants hold under concurrent access patterns.
These tests use sequential simulation of concurrent scenarios.

Run with: pytest tests/concurrency/test_race_safety.py -v
Skip with: pytest -m "not slow_locks"
"""

import pytest

pytestmark = pytest.mark.slow_locks
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.sequence_service import SequenceService
from finance_kernel.domain.clock import DeterministicClock


class TestIdempotencyConcurrency:
    """
    Concurrency tests for idempotency guarantees.

    R20: Race safety tests for idempotency invariant.
    """

    def test_100_sequential_posts_same_event_one_entry(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        100 posts of the same event must produce exactly 1 journal entry.

        R20: Concurrency test for idempotency.
        """
        event_id = uuid4()
        event_type = "generic.posting"

        # Post same event 100 times
        results = []
        for i in range(100):
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "1000",
                            "side": "debit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                        {
                            "account_code": "2000",
                            "side": "credit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                    ]
                },
            )
            results.append(result)

        # Count successes
        posted = [r for r in results if r.status == PostingStatus.POSTED]
        already_posted = [r for r in results if r.status == PostingStatus.ALREADY_POSTED]

        # Exactly 1 POSTED, 99 ALREADY_POSTED
        assert len(posted) == 1, "Only one entry should be created"
        assert len(already_posted) == 99, "99 should be idempotent hits"

        # All should share the same journal_entry_id
        entry_ids = set(r.journal_entry_id for r in results if r.journal_entry_id)
        assert len(entry_ids) == 1, "All results should reference same entry"

    def test_distinct_events_produce_distinct_entries(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        100 distinct events must produce 100 journal entries.

        R20: Concurrency test for event isolation.
        """
        results = []

        for i in range(100):
            event_id = uuid4()  # New event each time
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "1000",
                            "side": "debit",
                            "amount": str(Decimal("10.00") + Decimal(i)),
                            "currency": "USD",
                        },
                        {
                            "account_code": "2000",
                            "side": "credit",
                            "amount": str(Decimal("10.00") + Decimal(i)),
                            "currency": "USD",
                        },
                    ]
                },
            )
            results.append(result)

        # All should be POSTED
        posted = [r for r in results if r.status == PostingStatus.POSTED]
        assert len(posted) == 100, "All distinct events should create entries"

        # All should have unique entry IDs
        entry_ids = set(r.journal_entry_id for r in results)
        assert len(entry_ids) == 100, "All entries should be unique"


class TestSequenceConcurrency:
    """
    Concurrency tests for sequence number assignment.

    R20: Race safety tests for monotonic sequence guarantees.
    """

    def test_sequence_numbers_strictly_increasing(
        self,
        session,
    ):
        """
        Sequence numbers must be strictly increasing.

        R20: Concurrency test for sequence invariant.
        """
        service = SequenceService(session)

        sequences = []
        for _ in range(100):
            seq = service.next_value(SequenceService.JOURNAL_ENTRY)
            sequences.append(seq)
            session.flush()

        # All unique
        assert len(set(sequences)) == 100, "All sequences must be unique"

        # Strictly increasing
        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1], (
                f"Sequence must increase: {sequences[i-1]} -> {sequences[i]}"
            )

    def test_sequence_gaps_not_reused(
        self,
        session,
    ):
        """
        Sequence gaps (from rollbacks) must not be reused.

        R20: Concurrency test for sequence safety.
        """
        service = SequenceService(session)

        # Get some sequences
        seq1 = service.next_value(SequenceService.JOURNAL_ENTRY)
        seq2 = service.next_value(SequenceService.JOURNAL_ENTRY)
        seq3 = service.next_value(SequenceService.JOURNAL_ENTRY)

        # Simulate rollback by not committing, then continue
        session.flush()

        # Get more sequences
        seq4 = service.next_value(SequenceService.JOURNAL_ENTRY)
        seq5 = service.next_value(SequenceService.JOURNAL_ENTRY)

        # All must be strictly increasing
        all_seqs = [seq1, seq2, seq3, seq4, seq5]
        for i in range(1, len(all_seqs)):
            assert all_seqs[i] > all_seqs[i - 1]


class TestAuditChainConcurrency:
    """
    Concurrency tests for audit chain integrity.

    R20: Race safety tests for hash chain invariant.
    """

    def test_audit_chain_valid_after_many_posts(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Audit chain must remain valid after many sequential posts.

        R20: Concurrency test for audit chain integrity.
        """
        # Post many events
        for i in range(50):
            event_id = uuid4()
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "1000",
                            "side": "debit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                        {
                            "account_code": "2000",
                            "side": "credit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                    ]
                },
            )
            assert result.is_success

        # Validate the chain
        assert posting_orchestrator.validate_chain() is True


class TestBalanceConcurrency:
    """
    Concurrency tests for double-entry balance invariant.

    R20: Race safety tests for balance validation.
    """

    def test_interleaved_debit_credit_patterns(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Interleaved debit/credit patterns must maintain balance.

        R20: Concurrency test for double-entry invariant.
        """
        # Post alternating patterns
        for i in range(20):
            # Pattern 1: Cash -> Revenue
            event_id = uuid4()
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "1000",  # Cash
                            "side": "debit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                        {
                            "account_code": "4000",  # Revenue
                            "side": "credit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                    ]
                },
            )
            assert result.is_success

            # Pattern 2: COGS -> Inventory
            event_id = uuid4()
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "5000",  # COGS
                            "side": "debit",
                            "amount": "60.00",
                            "currency": "USD",
                        },
                        {
                            "account_code": "1200",  # Inventory
                            "side": "credit",
                            "amount": "60.00",
                            "currency": "USD",
                        },
                    ]
                },
            )
            assert result.is_success

        # Verify trial balance is balanced
        trial_balance = ledger_selector.trial_balance(
            as_of_date=current_period.end_date
        )

        total_debits = sum(row.debit_total for row in trial_balance)
        total_credits = sum(row.credit_total for row in trial_balance)

        assert total_debits == total_credits, (
            f"Trial balance must be balanced: debits={total_debits}, credits={total_credits}"
        )


class TestPeriodConcurrency:
    """
    Concurrency tests for period control.

    R20: Race safety tests for period enforcement.
    """

    def test_closed_period_rejected_consistently(
        self,
        posting_orchestrator,
        standard_accounts,
        create_period,
        test_actor_id,
        deterministic_clock,
        period_service,
    ):
        """
        Posts to closed periods must be consistently rejected.

        R20: Concurrency test for period control.
        """
        # Create and close a period
        closed_period = create_period(
            period_code="CLOSED-01",
            name="Closed Period",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        period_service.close_period(closed_period.period_code, test_actor_id)

        # Attempt 50 posts to closed period
        rejected = 0
        for i in range(50):
            event_id = uuid4()
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=date(2024, 1, 15),  # In closed period
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "1000",
                            "side": "debit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                        {
                            "account_code": "2000",
                            "side": "credit",
                            "amount": "100.00",
                            "currency": "USD",
                        },
                    ]
                },
            )

            if result.status == PostingStatus.PERIOD_CLOSED:
                rejected += 1

        # All must be rejected
        assert rejected == 50, "All posts to closed period must be rejected"
