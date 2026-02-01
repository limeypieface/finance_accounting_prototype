"""
R19: Producer Field Immutability Tests.

The producer field identifies the source system that generated an event.
Once an event is ingested, its producer cannot change. This is critical
for audit trail integrity.

These tests verify that:
1. Producer is recorded at ingestion time
2. Producer field cannot be modified after ingestion (ORM + trigger)
3. Raw SQL UPDATE of producer is blocked by trigger
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus


class TestProducerRecording:
    """
    Tests for producer field recording.
    """

    def test_producer_recorded_on_event(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id,
    ):
        """
        Verify that the producer field is recorded on ingested events.

        The posting pipeline creates source Event records via make_source_event,
        which uses a default producer. We verify the event exists with a producer.
        """
        source_event_id = uuid4()
        result = post_via_coordinator(
            source_event_id=source_event_id,
            amount=Decimal("100.00"),
        )
        assert result.success

        # Verify the source event has a producer
        event = session.execute(
            select(Event).where(Event.event_id == source_event_id)
        ).scalar_one()

        assert event.producer is not None, (
            "Event should have a producer, got None"
        )


class TestProducerImmutability:
    """
    R19 Compliance Tests: Producer field is immutable.
    """

    def test_producer_cannot_be_modified_via_orm(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id,
    ):
        """
        Verify that producer cannot be modified after ingestion via ORM.
        """
        source_event_id = uuid4()
        result = post_via_coordinator(
            source_event_id=source_event_id,
            amount=Decimal("100.00"),
        )
        assert result.success

        event = session.execute(
            select(Event).where(Event.event_id == source_event_id)
        ).scalar_one()

        original_producer = event.producer

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
            select(Event).where(Event.event_id == source_event_id)
        ).scalar_one()
        assert event.producer == original_producer

    def test_producer_cannot_be_modified_via_raw_sql(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id,
    ):
        """
        Verify that producer cannot be modified via raw SQL.

        Database triggers should prevent this.
        """
        source_event_id = uuid4()
        result = post_via_coordinator(
            source_event_id=source_event_id,
            amount=Decimal("100.00"),
        )
        assert result.success

        event = session.execute(
            select(Event).where(Event.event_id == source_event_id)
        ).scalar_one()
        original_producer = event.producer

        # Attempt raw SQL update using nested transaction (SAVEPOINT)
        with pytest.raises(Exception) as exc_info:
            with session.begin_nested():
                session.execute(
                    text("UPDATE events SET producer = :new_producer WHERE event_id = :event_id"),
                    {
                        "new_producer": "attacker_producer",
                        "event_id": str(source_event_id),
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
            select(Event).where(Event.event_id == source_event_id)
        ).scalar_one()
        assert event.producer == original_producer
