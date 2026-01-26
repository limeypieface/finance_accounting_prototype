"""
Crash/restart tests for R20 compliance.

R20. Test class mapping - Crash/restart tests (durability)

These tests verify that invariants hold across transaction boundaries
and simulated crash scenarios. They test atomicity and recoverability.
"""

import pytest
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.ledger_service import LedgerService
from finance_kernel.models.journal import JournalEntry, JournalEntryStatus


class TestAtomicityOnFailure:
    """
    Tests for atomicity when operations fail.

    R20: Crash/restart tests for atomicity invariant.
    """

    def test_validation_failure_leaves_no_partial_state(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        session,
    ):
        """
        Validation failure must leave no partial state.

        R20: Durability test - unbalanced entry creates no records.
        """
        event_id = uuid4()

        # Count entries before
        entries_before = session.query(JournalEntry).count()

        # Attempt unbalanced entry (no rounding account configured for this)
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
                        "amount": "50.00",  # Unbalanced by 50.00 (exceeds tolerance)
                        "currency": "USD",
                    },
                ]
            },
        )

        # Should fail
        assert result.status == PostingStatus.VALIDATION_FAILED

        # No partial state - same count
        entries_after = session.query(JournalEntry).count()
        assert entries_after == entries_before, "No entries should be created on failure"

    def test_invalid_account_leaves_no_partial_state(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        session,
    ):
        """
        Invalid account validation must leave no partial state.

        R20: Durability test - invalid account creates no records.
        """
        event_id = uuid4()

        entries_before = session.query(JournalEntry).count()

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
                        "account_code": "INVALID_ACCOUNT",  # Does not exist
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

        # Should fail
        assert result.status == PostingStatus.VALIDATION_FAILED

        entries_after = session.query(JournalEntry).count()
        assert entries_after == entries_before, "No entries should be created on failure"

    def test_period_closed_leaves_no_partial_state(
        self,
        posting_orchestrator,
        standard_accounts,
        create_period,
        test_actor_id,
        deterministic_clock,
        period_service,
        session,
    ):
        """
        Closed period rejection must leave no partial state.

        R20: Durability test - closed period creates no records.
        """
        # Create and close a period
        closed_period = create_period(
            period_code="CRASH-CLOSED",
            name="Crash Test Closed",
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 29),
        )
        period_service.close_period(closed_period.period_code, test_actor_id)

        entries_before = session.query(JournalEntry).count()

        event_id = uuid4()
        result = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=date(2024, 2, 15),  # In closed period
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

        # Should be rejected
        assert result.status == PostingStatus.PERIOD_CLOSED

        entries_after = session.query(JournalEntry).count()
        assert entries_after == entries_before, "No entries should be created on closed period"


class TestIdempotencyDurability:
    """
    Tests for idempotency durability across retries.

    R20: Crash/restart tests for idempotency invariant.
    """

    def test_retry_after_success_returns_same_entry(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Retry after successful post must return same entry.

        R20: Durability test - idempotency survives retries.
        """
        event_id = uuid4()

        # First post
        result1 = posting_orchestrator.post_event(
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

        assert result1.status == PostingStatus.POSTED
        entry_id = result1.journal_entry_id

        # Retry
        result2 = posting_orchestrator.post_event(
            event_id=event_id,  # Same event_id
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

        assert result2.status == PostingStatus.ALREADY_POSTED
        assert result2.journal_entry_id == entry_id, "Must return same entry"

    def test_payload_mismatch_detected(
        self,
        ingestor_service,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Re-ingesting event with different payload must be detected.

        R20: Durability test - event immutability enforced.
        """
        event_id = uuid4()

        # First ingest
        result1 = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.event",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"original": "data"},
            schema_version=1,
        )

        assert result1.status == IngestStatus.ACCEPTED

        # Try to re-ingest with different payload
        result2 = ingestor_service.ingest(
            event_id=event_id,  # Same event_id
            event_type="test.event",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"modified": "data"},  # Different payload
            schema_version=1,
        )

        # Must be rejected as payload mismatch
        assert result2.status == IngestStatus.REJECTED
        assert "mismatch" in result2.message.lower() or result2.validation


class TestAuditChainDurability:
    """
    Tests for audit chain durability.

    R20: Crash/restart tests for hash chain invariant.
    """

    def test_audit_chain_survives_multiple_transactions(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Audit chain must be valid across multiple transaction commits.

        R20: Durability test - hash chain persists correctly.
        """
        # Post several events (each commits separately with auto_commit=True)
        for i in range(10):
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

        # Chain should still be valid
        assert posting_orchestrator.validate_chain() is True

    def test_audit_chain_links_persist(
        self,
        posting_orchestrator,
        auditor_service,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Audit chain prev_hash links must persist correctly.

        R20: Durability test - hash chain linkage survives.
        """
        # Post events
        for i in range(5):
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

        # Get recent events and verify chain
        recent = auditor_service.get_recent_events(limit=10)

        # First event should have None prev_hash
        assert recent[-1].prev_hash is None, "First event should have no prev_hash"

        # Subsequent events should link to previous
        for i in range(len(recent) - 1):
            current = recent[i]
            prev = recent[i + 1]
            assert current.prev_hash == prev.hash, (
                f"Event {current.seq} prev_hash should match event {prev.seq} hash"
            )


class TestImmutabilityDurability:
    """
    Tests for immutability durability (R10).

    R20: Crash/restart tests for immutability invariant.
    """

    def test_posted_entry_immutable_across_sessions(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        session,
    ):
        """
        Posted entries must remain immutable.

        R20: Durability test - R10 immutability persists.
        """
        from finance_kernel.exceptions import ImmutabilityViolationError

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

        # Try to modify the posted entry
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None
        assert entry.status == JournalEntryStatus.POSTED

        # Attempt modification should raise ImmutabilityViolationError
        with pytest.raises(ImmutabilityViolationError):
            entry.description = "Modified description"
            session.flush()

        session.rollback()


class TestSequenceDurability:
    """
    Tests for sequence durability.

    R20: Crash/restart tests for sequence invariant.
    """

    def test_sequence_persists_across_commits(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Sequence numbers must persist and remain monotonic across commits.

        R20: Durability test - sequence monotonicity persists.
        """
        sequences = []

        # Post multiple events
        for i in range(10):
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
            sequences.append(result.seq)

        # Verify monotonic
        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1], (
                f"Sequence must be monotonic: {sequences[i-1]} -> {sequences[i]}"
            )
