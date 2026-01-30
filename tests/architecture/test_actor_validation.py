"""
R7: Actor ID Validation Tests.

The actor_id is the audit trail's proof of WHO performed an action.
If null or invalid actor_ids are allowed, the audit trail becomes
incomplete and forensic analysis becomes impossible.

These tests verify that:
1. Null actor_id is rejected at the API boundary
2. Invalid UUID format actor_id is rejected
3. All successful postings have valid actor_id recorded
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4, UUID

from sqlalchemy import select

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.models.journal import JournalEntry, JournalLine
from finance_kernel.models.event import Event
from finance_kernel.models.audit_event import AuditEvent
from finance_kernel.domain.clock import DeterministicClock


class TestActorIdValidation:
    """
    R7 Compliance Tests: actor_id is required and must be valid.

    The actor_id is critical for audit compliance. Every action must
    be traceable to a specific actor.
    """

    def test_posting_with_valid_actor_id_succeeds(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
        deterministic_clock: DeterministicClock,
    ):
        """Verify that posting with a valid actor_id succeeds."""
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,  # Valid UUID
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        assert result.status == PostingStatus.POSTED, (
            f"Posting with valid actor_id should succeed, got {result.status}"
        )

        # Verify actor_id is stored on the journal entry
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None
        assert entry.actor_id == test_actor_id

    def test_posting_with_none_actor_id_rejected(
        self,
        session,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
        standard_accounts,
        current_period,
    ):
        """
        Verify that posting with None actor_id is rejected.

        R7: actor_id is required - null values must be rejected.
        The database NOT NULL constraint enforces this as a safety net.
        """
        from sqlalchemy.exc import IntegrityError

        # Create orchestrator without using fixture to control actor_id
        orchestrator = PostingOrchestrator(session, deterministic_clock, auto_commit=False)

        # Attempt to post with None actor_id
        # The database NOT NULL constraint will catch this
        with pytest.raises((TypeError, ValueError, IntegrityError)) as exc_info:
            orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=None,  # Invalid: None
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )

        # Rollback after the expected error
        session.rollback()

        # Verify error is related to actor_id or null constraint
        error_msg = str(exc_info.value).lower()
        assert (
            "actor" in error_msg or
            "none" in error_msg or
            "null" in error_msg or
            "uuid" in error_msg or
            "notnullviolation" in error_msg
        ), f"Error should mention actor_id or null constraint, got: {exc_info.value}"

        # Verify no journal entry was created
        entries = session.execute(select(JournalEntry)).scalars().all()
        assert len(entries) == 0, "No entry should be created with null actor_id"

    def test_posting_with_invalid_uuid_string_rejected(
        self,
        session,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
        standard_accounts,
        current_period,
    ):
        """
        Verify that posting with invalid UUID string is rejected.

        R7: actor_id must be a valid UUID.
        """
        orchestrator = PostingOrchestrator(session, deterministic_clock, auto_commit=False)

        # Attempt to post with invalid UUID string
        with pytest.raises((TypeError, ValueError, AttributeError)) as exc_info:
            orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id="not-a-valid-uuid",  # Invalid: not a UUID
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )

        # Verify no journal entry was created
        entries = session.execute(select(JournalEntry)).scalars().all()
        assert len(entries) == 0, "No entry should be created with invalid actor_id"

    def test_actor_id_recorded_in_audit_trail(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that actor_id is recorded in the audit trail.

        Every audit event should have the actor_id who performed the action.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        assert result.status == PostingStatus.POSTED

        # Check audit events have the actor_id
        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == result.journal_entry_id
            )
        ).scalars().all()

        assert len(audit_events) > 0, "Audit events should exist for posted entry"

        for event in audit_events:
            assert event.actor_id == test_actor_id, (
                f"Audit event should have correct actor_id. "
                f"Expected {test_actor_id}, got {event.actor_id}"
            )

    def test_event_ingestion_requires_actor_id(
        self,
        session,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
    ):
        """
        Verify that event ingestion also requires actor_id.

        The ingestor is the first layer of defense.
        """
        from sqlalchemy.exc import IntegrityError

        ingestor = IngestorService(session, deterministic_clock, auditor_service)

        # Attempt to ingest with None actor_id
        # The database NOT NULL constraint enforces this as a safety net
        with pytest.raises((TypeError, ValueError, IntegrityError)) as exc_info:
            ingestor.ingest(
                event_id=uuid4(),
                event_type="test.event",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=None,  # Invalid
                producer="test",
                payload={"test": "data"},
                schema_version=1,
            )

        # Rollback after the expected error
        session.rollback()

        # Verify no event was created
        events = session.execute(select(Event)).scalars().all()
        assert len(events) == 0, "No event should be created with null actor_id"


class TestActorIdConsistency:
    """
    Tests for actor_id consistency across the system.

    Every operation must consistently record the actor_id.
    """

    def test_all_journal_entries_have_actor_id(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that all journal entries have a valid actor_id.

        Post multiple entries and verify all have actor_id.
        """
        # Post several entries
        for i in range(5):
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(10 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(10 + i), "currency": "USD"},
                    ]
                },
            )
            assert result.status == PostingStatus.POSTED

        # Verify all entries have actor_id
        entries = session.execute(select(JournalEntry)).scalars().all()
        assert len(entries) == 5

        for entry in entries:
            assert entry.actor_id is not None, f"Entry {entry.id} has null actor_id"
            assert entry.actor_id == test_actor_id, (
                f"Entry {entry.id} has wrong actor_id: {entry.actor_id}"
            )

    def test_all_events_have_actor_id(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that all ingested events have a valid actor_id.
        """
        # Post several events
        for i in range(3):
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(10 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(10 + i), "currency": "USD"},
                    ]
                },
            )
            assert result.status == PostingStatus.POSTED

        # Verify all events have actor_id
        events = session.execute(select(Event)).scalars().all()
        assert len(events) == 3

        for event in events:
            assert event.actor_id is not None, f"Event {event.event_id} has null actor_id"
            assert event.actor_id == test_actor_id, (
                f"Event {event.event_id} has wrong actor_id: {event.actor_id}"
            )

    def test_all_audit_events_have_actor_id(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that all audit events have a valid actor_id.
        """
        # Post an event (which creates audit events)
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Verify all audit events have actor_id
        audit_events = session.execute(select(AuditEvent)).scalars().all()
        assert len(audit_events) > 0, "Should have audit events"

        for event in audit_events:
            assert event.actor_id is not None, f"Audit event {event.id} has null actor_id"
            assert event.actor_id == test_actor_id, (
                f"Audit event {event.id} has wrong actor_id: {event.actor_id}"
            )
