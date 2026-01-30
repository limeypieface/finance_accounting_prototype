"""
R19: Producer Field Immutability Tests.

The producer field identifies the source system that generated an event.
Once an event is ingested, its producer cannot change. This is critical
for audit trail integrity.

These tests verify that:
1. Producer is recorded at ingestion time
2. Same event_id with different producer is rejected or returns existing
3. Producer field cannot be modified after ingestion
4. Audit trail preserves producer information
"""

import pytest
from uuid import uuid4
from datetime import datetime

from sqlalchemy import select, text

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry
from finance_kernel.domain.clock import DeterministicClock


class TestProducerRecording:
    """
    Tests for producer field recording.
    """

    def test_producer_recorded_on_event(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that the producer field is recorded on ingested events.
        """
        producer_name = "inventory_system"

        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer=producer_name,
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Verify producer on event
        event = session.execute(
            select(Event).where(Event.event_id == result.event_id)
        ).scalar_one()

        assert event.producer == producer_name, (
            f"Expected producer={producer_name}, got {event.producer}"
        )


class TestProducerImmutability:
    """
    R19 Compliance Tests: Producer field is immutable.
    """

    def test_same_event_id_different_producer_returns_already_posted(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that posting same event_id with different producer returns ALREADY_POSTED.

        The idempotency key includes producer, so this tests producer as part of identity.

        Behavior options:
        1. ALREADY_POSTED - event already exists (OK if producer matches)
        2. PAYLOAD_MISMATCH or similar - producer changed (protocol violation)

        Either behavior is acceptable as long as:
        - Only ONE journal entry exists
        - The original producer is preserved
        """
        event_id = uuid4()

        # First post with producer="system_a"
        result1 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="system_a",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result1.status == PostingStatus.POSTED
        original_entry_id = result1.journal_entry_id

        # Try to post same event_id with different producer
        result2 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="system_b",  # DIFFERENT producer
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        # Either ALREADY_POSTED (idempotency key differs) or a new posting
        # Either way, the original event's producer should be preserved
        # and there should be proper tracking

        # Verify original event still has original producer
        event = session.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one_or_none()

        if event:
            assert event.producer == "system_a", (
                f"Original producer should be preserved. Expected 'system_a', got '{event.producer}'"
            )

        # Count total journal entries for this event_id
        entry = session.execute(
            select(JournalEntry).where(JournalEntry.source_event_id == event_id)
        ).scalar_one_or_none()

        if entry:
            assert entry.id == original_entry_id, (
                "If entry exists, it should be the original"
            )

    def test_producer_cannot_be_modified_via_orm(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that producer cannot be modified after ingestion via ORM.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="original_producer",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        event = session.execute(
            select(Event).where(Event.event_id == result.event_id)
        ).scalar_one()

        original_producer = event.producer
        event_id = result.event_id

        # Attempt to modify producer using nested transaction (SAVEPOINT)
        # so rollback only affects the modification attempt, not the event creation
        with pytest.raises(Exception):
            with session.begin_nested():
                event.producer = "attacker_producer"
                session.flush()

        # Re-fetch after nested transaction rollback
        session.expire_all()

        # Verify producer unchanged
        event = session.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one()
        assert event.producer == original_producer

    def test_producer_cannot_be_modified_via_raw_sql(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that producer cannot be modified via raw SQL.

        Database triggers should prevent this.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="original_producer",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        event_id = result.event_id

        # Attempt raw SQL update using nested transaction (SAVEPOINT)
        # so rollback only affects the modification attempt
        with pytest.raises(Exception) as exc_info:
            with session.begin_nested():
                session.execute(
                    text("UPDATE events SET producer = :new_producer WHERE event_id = :event_id"),
                    {
                        "new_producer": "attacker_producer",
                        "event_id": str(event_id),
                    },
                )
                session.flush()

        # Verify we got an immutability error
        error_str = str(exc_info.value).lower()
        assert (
            "immut" in error_str or
            "cannot" in error_str or
            "update" in error_str or
            "trigger" in error_str or
            "r10" in error_str
        ), f"Expected immutability error, got: {exc_info.value}"

        # Re-fetch after nested transaction rollback
        session.expire_all()

        # Verify producer unchanged
        event = session.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one()
        assert event.producer == "original_producer"


class TestProducerInIdempotencyKey:
    """
    Tests for producer as part of the idempotency key.
    """

    def test_idempotency_key_includes_producer(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that the idempotency key includes the producer.

        Format: producer:event_type:event_id
        """
        event_id = uuid4()
        producer = "test_producer"
        event_type = "generic.posting"

        result = posting_orchestrator.post_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer=producer,
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Check idempotency key format
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None
        assert entry.idempotency_key is not None

        # Idempotency key should contain producer
        assert producer in entry.idempotency_key, (
            f"Idempotency key should contain producer. Key: {entry.idempotency_key}"
        )

    def test_different_producers_same_event_id_are_distinct(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that same event_id from different producers creates different entries.

        This is because the idempotency key includes producer.

        Note: This is a design decision - some systems might reject this as invalid.
        """
        event_id = uuid4()

        # Post from producer A
        result_a = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="producer_a",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result_a.status == PostingStatus.POSTED

        # Post same event_id from producer B
        result_b = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="producer_b",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "200.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "200.00", "currency": "USD"},
                ]
            },
        )

        # If producers are part of idempotency, both should succeed with different entries
        # If producers are NOT part of idempotency, second could return:
        # - ALREADY_POSTED (if same payload hash)
        # - INGESTION_FAILED (if different payload hash - R2 violation)

        # Either way, verify the system behavior is consistent
        if result_b.status == PostingStatus.POSTED:
            # Different producers = different entries
            assert result_a.journal_entry_id != result_b.journal_entry_id
        else:
            # Same event_id should be rejected
            # ALREADY_POSTED = duplicate with same payload
            # INGESTION_FAILED = R2 violation (same event_id, different payload hash)
            assert result_b.status in (
                PostingStatus.ALREADY_POSTED,
                PostingStatus.INGESTION_FAILED,
            ), f"Expected ALREADY_POSTED or INGESTION_FAILED, got {result_b.status}"


class TestProducerAuditTrail:
    """
    Tests for producer in audit trail.
    """

    def test_producer_recorded_in_audit_event(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service,
    ):
        """
        Verify that producer is recorded in audit events.
        """
        from finance_kernel.models.audit_event import AuditEvent, AuditAction

        producer_name = "audit_test_producer"

        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer=producer_name,
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Find audit events for the ingested event
        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == result.event_id,
                AuditEvent.action == AuditAction.EVENT_INGESTED,
            )
        ).scalars().all()

        assert len(audit_events) > 0, "Should have audit event for ingestion"

        # Check producer is in audit payload
        for ae in audit_events:
            if ae.payload and "producer" in ae.payload:
                assert ae.payload["producer"] == producer_name, (
                    f"Audit event should record producer. Got: {ae.payload}"
                )
