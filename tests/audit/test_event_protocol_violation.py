"""
Event protocol violation tests.

Verifies the hard invariant from event.py docstring:
- (event_id, payload_hash) is immutable; if an event_id arrives with a
  different payload_hash, it is rejected as a protocol violation

This is distinct from duplicate detection (idempotent success).
"""

from uuid import uuid4

import pytest

from finance_kernel.exceptions import PayloadMismatchError
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus


class TestEventProtocolViolation:
    """Tests for event protocol violation detection (payload hash mismatch)."""

    def test_same_event_id_different_payload_is_protocol_violation(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Scenario:
        1. Insert an event with event_id="A" and payload that hashes to HASH_1
        2. Attempt to insert with same event_id="A" but different payload (HASH_2)
        3. System must recognize this as a Protocol Violation, not just a Duplicate ID
        """
        event_id = uuid4()  # Our "event_id=A"
        event_type = "test.protocol_violation"

        # Step 1: Insert first event with payload_hash="HASH_1" (via payload content)
        payload_1 = {"data": "original_content", "version": 1}  # Will generate HASH_1

        result_1 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=payload_1,
        )

        # Verify first insert succeeded
        assert result_1.status == IngestStatus.ACCEPTED
        assert result_1.is_success is True
        session.flush()

        # Step 2: Attempt to insert with SAME event_id but DIFFERENT payload (HASH_2)
        payload_2 = {"data": "modified_content", "version": 2}  # Will generate HASH_2

        result_2 = ingestor_service.ingest(
            event_id=event_id,  # Same event_id as before
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=payload_2,  # Different payload -> different hash
        )

        # Step 3: Verify this is recognized as a Protocol Violation
        # NOT a duplicate (which would return DUPLICATE status)
        assert result_2.status == IngestStatus.REJECTED, (
            f"Expected REJECTED for protocol violation, got {result_2.status}"
        )
        assert result_2.is_success is False

        # Verify the message indicates payload mismatch (protocol violation)
        assert result_2.message is not None
        assert "payload" in result_2.message.lower() or "hash" in result_2.message.lower(), (
            f"Expected payload/hash mismatch message, got: {result_2.message}"
        )

    def test_same_event_id_same_payload_is_idempotent_duplicate(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that same event_id with SAME payload is an idempotent duplicate.
        This is the expected success case, NOT a protocol violation.
        """
        event_id = uuid4()
        event_type = "test.idempotent"
        payload = {"data": "same_content"}

        # Insert first time
        result_1 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=payload,
        )
        assert result_1.status == IngestStatus.ACCEPTED
        session.flush()

        # Insert second time with SAME payload
        result_2 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=payload,  # Exact same payload
        )

        # Should be DUPLICATE (idempotent success), NOT REJECTED
        assert result_2.status == IngestStatus.DUPLICATE
        assert result_2.is_success is True  # Duplicate is still a success
        assert result_2.event_envelope is not None

    def test_protocol_violation_vs_duplicate_distinction(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Explicitly verify the distinction between:
        - Protocol Violation: same event_id, different payload_hash -> REJECTED
        - Duplicate: same event_id, same payload_hash -> DUPLICATE (success)
        """
        event_id = uuid4()
        event_type = "test.distinction"
        original_payload = {"key": "value_A"}

        # Insert original
        original_result = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=original_payload,
        )
        assert original_result.status == IngestStatus.ACCEPTED
        original_hash = original_result.event_envelope.payload_hash
        session.flush()

        # Re-insert with SAME payload -> should be DUPLICATE
        same_payload_result = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=original_payload,
        )
        assert same_payload_result.status == IngestStatus.DUPLICATE, (
            "Same payload should result in DUPLICATE status"
        )

        # Re-insert with DIFFERENT payload -> should be REJECTED (protocol violation)
        different_payload = {"key": "value_B"}  # Different from original
        different_payload_result = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=different_payload,
        )
        assert different_payload_result.status == IngestStatus.REJECTED, (
            "Different payload should result in REJECTED status (protocol violation)"
        )

    def test_payload_mismatch_exception_attributes(self):
        """
        Verify PayloadMismatchError has correct attributes for protocol violation.
        This exception class is available for explicit error handling.
        """
        event_id = "test-event-123"
        expected_hash = "HASH_1_abc123"
        received_hash = "HASH_2_def456"

        error = PayloadMismatchError(event_id, expected_hash, received_hash)

        # Verify it's the right exception type
        assert error.code == "PAYLOAD_MISMATCH"

        # Verify attributes are preserved for programmatic access
        assert error.event_id == event_id
        assert error.expected_hash == expected_hash
        assert error.received_hash == received_hash

        # Verify message indicates this is about payload mismatch
        assert "mismatch" in str(error).lower()
        assert event_id in str(error)
        assert expected_hash in str(error)
        assert received_hash in str(error)

    def test_protocol_violation_audit_trail(
        self,
        session,
        ingestor_service: IngestorService,
        auditor_service,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that protocol violations are recorded in the audit trail.
        """
        event_id = uuid4()
        event_type = "test.audit_protocol_violation"

        # Insert original event
        result_1 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"original": True},
        )
        assert result_1.status == IngestStatus.ACCEPTED
        session.flush()

        # Attempt protocol violation
        result_2 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"original": False, "tampered": True},  # Different payload
        )
        assert result_2.status == IngestStatus.REJECTED
        session.flush()

        # Verify the rejection was audited (if auditor records rejections)
        # The audit trail should contain evidence of the rejection attempt
        from sqlalchemy import select

        from finance_kernel.models.audit_event import AuditAction, AuditEvent

        # Check if there's a rejection audit event for this event_id
        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == event_id,
                AuditEvent.action == AuditAction.EVENT_REJECTED,
            )
        ).scalars().all()

        # There should be an audit record of the rejection
        assert len(audit_events) >= 1, (
            "Protocol violation should be recorded in audit trail"
        )

        # Verify the rejection reason mentions payload mismatch
        rejection_audit = audit_events[0]
        assert "payload" in str(rejection_audit.payload).lower() or \
               "mismatch" in str(rejection_audit.payload).lower(), (
            f"Audit payload should mention payload mismatch: {rejection_audit.payload}"
        )


class TestSameEventIdDifferentProducer:
    """
    Tests for behavior when same event_id is submitted with different producer values.

    The event_id is globally unique. The producer field identifies the source system,
    but uniqueness is determined by event_id alone. The system behavior depends on
    whether the payload_hash matches.
    """

    def test_same_event_id_different_producer_same_payload_is_duplicate(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Same event_id + same payload + different producer = DUPLICATE (idempotent).

        The producer field is not part of the uniqueness key.
        If the payload matches, it's treated as a duplicate delivery.
        """
        event_id = uuid4()
        event_type = "test.producer.same_payload"
        payload = {"order_id": "12345", "amount": 100}

        # First insert from producer "system_a"
        result_1 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="system_a",
            payload=payload,
        )
        assert result_1.status == IngestStatus.ACCEPTED
        session.flush()

        # Second insert with SAME event_id, SAME payload, but DIFFERENT producer
        result_2 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="system_b",  # Different producer
            payload=payload,  # Same payload
        )

        # Should be DUPLICATE because payload_hash matches
        assert result_2.status == IngestStatus.DUPLICATE, (
            "Same event_id with same payload should be DUPLICATE regardless of producer"
        )
        assert result_2.is_success is True

        # The returned envelope should reflect the ORIGINAL event's data
        assert result_2.event_envelope is not None
        assert result_2.event_envelope.producer == "system_a"  # Original producer preserved

    def test_same_event_id_different_producer_different_payload_is_protocol_violation(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Same event_id + different payload + different producer = REJECTED (protocol violation).

        Even with a different producer, if the event_id exists with a different
        payload_hash, it's a protocol violation. The producer cannot "override"
        an existing event.
        """
        event_id = uuid4()
        event_type = "test.producer.diff_payload"

        # First insert from producer "system_a"
        result_1 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="system_a",
            payload={"version": 1, "data": "original"},
        )
        assert result_1.status == IngestStatus.ACCEPTED
        session.flush()

        # Second insert with SAME event_id, DIFFERENT payload, DIFFERENT producer
        result_2 = ingestor_service.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="system_b",  # Different producer
            payload={"version": 2, "data": "modified"},  # Different payload
        )

        # Should be REJECTED - protocol violation
        assert result_2.status == IngestStatus.REJECTED, (
            "Same event_id with different payload is protocol violation, "
            "regardless of producer"
        )
        assert result_2.is_success is False
        assert "payload" in result_2.message.lower() or "hash" in result_2.message.lower()

    def test_event_id_uniqueness_across_producers(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that event_id is globally unique, not unique per-producer.

        Two different producers cannot both claim the same event_id with
        different payloads. The first one wins; subsequent attempts are
        either duplicates (same payload) or violations (different payload).
        """
        event_id = uuid4()

        # Producer A creates the event
        result_a = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.global_uniqueness",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="producer_a",
            payload={"source": "A", "value": 100},
        )
        assert result_a.status == IngestStatus.ACCEPTED
        session.flush()

        # Producer B tries to use the SAME event_id with different payload
        result_b = ingestor_service.ingest(
            event_id=event_id,  # Same event_id!
            event_type="test.global_uniqueness",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="producer_b",
            payload={"source": "B", "value": 200},  # Different payload
        )

        # Producer B should be REJECTED - event_id already belongs to producer A's event
        assert result_b.status == IngestStatus.REJECTED, (
            "event_id is globally unique; producer B cannot claim it with different payload"
        )

        # Verify only one event exists in the database
        from sqlalchemy import select

        from finance_kernel.models.event import Event

        events = session.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalars().all()

        assert len(events) == 1, "Only one event should exist for this event_id"
        assert events[0].producer == "producer_a", "Original producer should be preserved"

    def test_producer_preserved_on_duplicate_detection(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        deterministic_clock,
    ):
        """
        When a duplicate is detected, the original event's producer is preserved.

        The system returns the existing event's envelope, not a new one with
        the duplicate request's producer.
        """
        event_id = uuid4()
        payload = {"immutable": "data"}

        # Original from "original_producer"
        result_1 = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.producer_preserved",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="original_producer",
            payload=payload,
        )
        assert result_1.status == IngestStatus.ACCEPTED
        original_envelope = result_1.event_envelope
        session.flush()

        # Duplicate attempt from "different_producer"
        result_2 = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.producer_preserved",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="different_producer",  # Trying with different producer
            payload=payload,  # Same payload
        )

        assert result_2.status == IngestStatus.DUPLICATE
        duplicate_envelope = result_2.event_envelope

        # The envelope returned should be the ORIGINAL, not a new one
        assert duplicate_envelope.producer == "original_producer", (
            "Duplicate detection should return original event's producer, "
            f"got {duplicate_envelope.producer}"
        )
        assert duplicate_envelope.event_id == original_envelope.event_id
        assert duplicate_envelope.payload_hash == original_envelope.payload_hash
