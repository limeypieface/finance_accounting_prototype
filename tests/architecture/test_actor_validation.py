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
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
    ):
        """Verify that posting with a valid actor_id succeeds."""
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("100.00"),
        )

        assert result.success, (
            f"Posting with valid actor_id should succeed, got error: {result.error_code}"
        )

        # Verify actor_id is stored on the journal entry
        entry_id = result.journal_result.entries[0].entry_id
        entry = session.get(JournalEntry, entry_id)
        assert entry is not None
        assert entry.actor_id == test_actor_id

    def test_posting_with_none_actor_id_rejected(
        self,
        session,
        interpretation_coordinator,
        deterministic_clock: DeterministicClock,
        standard_accounts,
        current_period,
        test_actor_id,
    ):
        """
        Verify that posting with None actor_id is rejected.

        R7: actor_id is required - null values must be rejected.
        The database NOT NULL constraint enforces this as a safety net.
        """
        from sqlalchemy.exc import IntegrityError
        from finance_kernel.domain.accounting_intent import (
            AccountingIntent, LedgerIntent, IntentLine, AccountingIntentSnapshot,
        )
        from finance_kernel.domain.meaning_builder import MeaningBuilderResult, EconomicEventData

        source_event_id = uuid4()
        econ_event_id = uuid4()
        effective_date = deterministic_clock.now().date()

        # Create source Event
        from tests.conftest import make_source_event
        make_source_event(session, source_event_id, test_actor_id, deterministic_clock, effective_date)

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="test.posting",
            effective_date=effective_date,
            profile_id="TestProfile",
            profile_version=1,
            profile_hash=None,
            quantity=Decimal("100.00"),
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id="TestProfile",
            profile_version=1,
            effective_date=effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                        IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        # Attempt to post with None actor_id
        # InterpretationCoordinator catches exceptions and returns failure result
        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=None,  # Invalid: None
        )

        # Should fail — None actor_id violates NOT NULL constraint
        assert not result.success, (
            "Posting with None actor_id should fail"
        )

    def test_posting_with_invalid_uuid_string_rejected(
        self,
        session,
        interpretation_coordinator,
        deterministic_clock: DeterministicClock,
        standard_accounts,
        current_period,
        test_actor_id,
    ):
        """
        Verify that posting with invalid UUID string is rejected.

        R7: actor_id must be a valid UUID.
        """
        from finance_kernel.domain.accounting_intent import (
            AccountingIntent, LedgerIntent, IntentLine, AccountingIntentSnapshot,
        )
        from finance_kernel.domain.meaning_builder import MeaningBuilderResult, EconomicEventData

        source_event_id = uuid4()
        econ_event_id = uuid4()
        effective_date = deterministic_clock.now().date()

        from tests.conftest import make_source_event
        make_source_event(session, source_event_id, test_actor_id, deterministic_clock, effective_date)

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="test.posting",
            effective_date=effective_date,
            profile_id="TestProfile",
            profile_version=1,
            profile_hash=None,
            quantity=Decimal("100.00"),
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id="TestProfile",
            profile_version=1,
            effective_date=effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                        IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        # Attempt to post with invalid UUID string
        # The bad UUID gets stored in the entry, causing a ValueError when
        # SQLAlchemy tries to process it during flush. The coordinator may
        # catch it and return a failure, or the ValueError may propagate
        # if the session is left in a bad state.
        try:
            result = interpretation_coordinator.interpret_and_post(
                meaning_result=meaning_result,
                accounting_intent=intent,
                actor_id="not-a-valid-uuid",  # Invalid: not a UUID
            )
            # If it returns a result, it should indicate failure
            assert not result.success, (
                "Posting with invalid UUID string should fail"
            )
        except (ValueError, TypeError, AttributeError):
            # Expected — bad UUID causes unrecoverable session state
            session.rollback()

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
        # IngestorService may accept at Python level but DB will reject on flush
        result = ingestor.ingest(
            event_id=uuid4(),
            event_type="test.event",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=None,  # Invalid
            producer="test",
            payload={"test": "data"},
            schema_version=1,
        )

        # If the ingestor accepted it at Python level, check that
        # the DB rejects it on flush (NOT NULL constraint)
        if result.status == IngestStatus.ACCEPTED:
            from sqlalchemy.exc import IntegrityError
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
        else:
            # Good — rejected at the service level
            assert result.status != IngestStatus.ACCEPTED


class TestActorIdConsistency:
    """
    Tests for actor_id consistency across the system.

    Every operation must consistently record the actor_id.
    """

    def test_all_journal_entries_have_actor_id(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
    ):
        """
        Verify that all journal entries have a valid actor_id.

        Post multiple entries and verify all have actor_id.
        """
        # Post several entries
        for i in range(5):
            result = post_via_coordinator(
                amount=Decimal(str(10 + i)),
            )
            assert result.success

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
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id: UUID,
    ):
        """
        Verify that all ingested events have a valid actor_id.
        """
        # Post several events
        for i in range(3):
            result = post_via_coordinator(
                amount=Decimal(str(10 + i)),
            )
            assert result.success

        # Verify all source events have actor_id
        events = session.execute(select(Event)).scalars().all()
        assert len(events) >= 3

        for event in events:
            assert event.actor_id is not None, f"Event {event.event_id} has null actor_id"
            assert event.actor_id == test_actor_id, (
                f"Event {event.event_id} has wrong actor_id: {event.actor_id}"
            )
