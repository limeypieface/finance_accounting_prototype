"""
Ingestor service - Event ingestion with boundary validation.

The Ingestor is the entry point for external events. It is responsible for:
- Detecting duplicate/conflicting events (idempotency)
- Creating immutable event records
- Transforming external events into domain EventEnvelopes

IMPORTANT: Validation is delegated to the pure domain layer (event_validator.py).
The Ingestor is part of the imperative shell (R2) - it only does I/O operations.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import EventEnvelope, ValidationResult
from finance_kernel.domain.event_validator import validate_event
from finance_kernel.models.event import Event
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.utils.hashing import hash_payload


class IngestStatus(str, Enum):
    """Status of an ingestion operation."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"  # Idempotent success
    REJECTED = "rejected"


@dataclass(frozen=True)
class IngestResult:
    """Result of an ingestion operation."""

    status: IngestStatus
    event_id: UUID
    event_envelope: EventEnvelope | None = None
    validation: ValidationResult | None = None
    message: str | None = None

    @property
    def is_success(self) -> bool:
        """Check if ingestion was successful (including duplicates)."""
        return self.status in (IngestStatus.ACCEPTED, IngestStatus.DUPLICATE)


class IngestorService:
    """
    Service for ingesting events into the finance kernel.

    The Ingestor is part of the IMPERATIVE SHELL (R2):
    - It may: persist records, emit audit events, manage transactions
    - It may NOT: validate domain rules (delegated to pure layer)

    Workflow:
    1. Delegate validation to pure domain layer (event_validator)
    2. Check for duplicates (idempotency)
    3. Create the immutable event record
    4. Return an EventEnvelope for further processing
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        auditor: AuditorService | None = None,
    ):
        """
        Initialize the Ingestor service.

        Args:
            session: SQLAlchemy session.
            clock: Clock for timestamps. Defaults to SystemClock.
            auditor: Optional Auditor for recording ingestion events.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._auditor = auditor

    def ingest(
        self,
        event_id: UUID,
        event_type: str,
        occurred_at: datetime,
        effective_date: date,
        actor_id: UUID,
        producer: str,
        payload: dict[str, Any],
        schema_version: int = 1,
    ) -> IngestResult:
        """
        Ingest an event into the finance kernel.

        This is the main entry point. It:
        1. Validates the event at the boundary
        2. Checks for duplicates
        3. Creates the event record
        4. Returns the result

        Args:
            event_id: Globally unique event identifier.
            event_type: Namespaced event type (e.g., "inventory.receipt").
            occurred_at: When the event actually happened.
            effective_date: Accounting date for fiscal periods.
            actor_id: Who/what caused the event.
            producer: Module/system that produced the event.
            payload: Domain-specific event data.
            schema_version: Schema version for payload interpretation.

        Returns:
            IngestResult with status and event envelope.
        """
        # 1. Validate at boundary (delegated to pure domain layer)
        validation = validate_event(
            event_type=event_type,
            payload=payload,
            schema_version=schema_version,
        )

        if not validation.is_valid:
            if self._auditor:
                self._auditor.record_event_rejected(
                    event_id=event_id,
                    reason="; ".join(e.message for e in validation.errors),
                    actor_id=actor_id,
                )
            return IngestResult(
                status=IngestStatus.REJECTED,
                event_id=event_id,
                validation=validation,
                message="Validation failed",
            )

        # 2. Compute payload hash
        payload_hash = hash_payload(payload)

        # 3. Check for existing event
        existing = self._get_existing_event(event_id)

        if existing is not None:
            # Check payload hash matches
            if existing.payload_hash != payload_hash:
                if self._auditor:
                    self._auditor.record_event_rejected(
                        event_id=event_id,
                        reason=f"Payload mismatch: expected {existing.payload_hash}, got {payload_hash}",
                        actor_id=actor_id,
                    )
                return IngestResult(
                    status=IngestStatus.REJECTED,
                    event_id=event_id,
                    message="Payload hash mismatch - events are immutable",
                )

            # Idempotent success - return existing
            envelope = self._to_envelope(existing)
            return IngestResult(
                status=IngestStatus.DUPLICATE,
                event_id=event_id,
                event_envelope=envelope,
                message="Event already ingested",
            )

        # 4. Create new event
        event = Event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            effective_date=effective_date,
            actor_id=actor_id,
            producer=producer,
            payload=payload,
            payload_hash=payload_hash,
            schema_version=schema_version,
            ingested_at=self._clock.now(),
        )

        self._session.add(event)
        self._session.flush()

        # 5. Record audit event
        if self._auditor:
            self._auditor.record_event_ingested(
                event_id=event_id,
                event_type=event_type,
                producer=producer,
                actor_id=actor_id,
            )

        # 6. Return success
        envelope = self._to_envelope(event)
        return IngestResult(
            status=IngestStatus.ACCEPTED,
            event_id=event_id,
            event_envelope=envelope,
            message="Event ingested successfully",
        )

    def _get_existing_event(self, event_id: UUID) -> Event | None:
        """Get existing event by ID."""
        return self._session.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one_or_none()

    def _to_envelope(self, event: Event) -> EventEnvelope:
        """Convert ORM Event to domain EventEnvelope."""
        return EventEnvelope(
            event_id=event.event_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            effective_date=event.effective_date,
            actor_id=event.actor_id,
            producer=event.producer,
            payload=event.payload,
            payload_hash=event.payload_hash,
            schema_version=event.schema_version,
        )

    def get_event(self, event_id: UUID) -> EventEnvelope | None:
        """
        Get an event by ID.

        Args:
            event_id: The event ID.

        Returns:
            EventEnvelope if found, None otherwise.
        """
        event = self._get_existing_event(event_id)
        return self._to_envelope(event) if event else None

    def get_events_by_type(
        self,
        event_type: str,
        limit: int = 100,
    ) -> list[EventEnvelope]:
        """
        Get events by type.

        Args:
            event_type: The event type to filter by.
            limit: Maximum number of events to return.

        Returns:
            List of EventEnvelopes.
        """
        events = self._session.execute(
            select(Event)
            .where(Event.event_type == event_type)
            .order_by(Event.occurred_at.desc())
            .limit(limit)
        ).scalars().all()

        return [self._to_envelope(e) for e in events]
