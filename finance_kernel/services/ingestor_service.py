"""
IngestorService -- event ingestion with boundary validation and idempotency.

Responsibility:
    Entry point for external events into the finance kernel.  Validates
    payloads at the boundary, detects duplicate/conflicting events via
    payload hash comparison, creates immutable event records, and returns
    domain ``EventEnvelope`` objects for downstream processing.

Architecture position:
    Kernel > Services -- imperative shell, called by ModulePostingService
    at the start of every posting pipeline invocation.

Invariants enforced:
    R1  -- Event immutability: once an Event row is flushed, its payload
           and payload_hash are protected by ORM listeners and DB triggers.
    R2  -- Payload hash verification: same event_id with different payload
           is a protocol violation, resulting in REJECTED status.
    R3  -- Idempotency: duplicate event_id with matching payload_hash
           returns DUPLICATE (idempotent success).
    R8  -- Idempotency locking: UNIQUE constraint on ``event_id`` column
           plus ``SELECT ... FOR UPDATE`` semantics via IntegrityError
           handling.

Failure modes:
    - REJECTED (validation): Pure-domain ``validate_event()`` found errors
      in the payload schema or required fields.
    - REJECTED (hash mismatch): Same event_id re-submitted with different
      payload (R2 protocol violation).
    - REJECTED (concurrent conflict): IntegrityError on insert and
      subsequent lookup fails.
    - DUPLICATE: Idempotent re-delivery of an already-ingested event.

Audit relevance:
    Ingestion and rejection are recorded via AuditorService (when provided).
    Structured log entries include event_type, payload_hash, and error counts.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import EventEnvelope, ValidationResult
from finance_kernel.domain.event_validator import validate_event
from finance_kernel.logging_config import get_logger
from finance_kernel.models.event import Event
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.utils.hashing import hash_payload

logger = get_logger("services.ingestor")


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

    Contract:
        Accepts raw event data (event_id, event_type, payload, etc.)
        and returns an ``IngestResult`` indicating acceptance, idempotent
        duplicate, or rejection.

    Guarantees:
        - R1: Event records are immutable once flushed.  The ORM model and
          DB triggers prevent modification or deletion.
        - R2: Payload hash is computed at ingestion time and stored.  Any
          re-submission with a different payload for the same event_id is
          detected and rejected.
        - R3/R8: Idempotency via UNIQUE constraint on ``event_id`` plus
          IntegrityError handling for concurrent inserts.
        - Boundary validation is delegated to the pure domain layer
          (``event_validator.py``).

    Non-goals:
        - Does NOT call ``session.commit()`` -- caller controls boundaries.
        - Does NOT validate domain-level business rules beyond schema
          validation (that is the policy layer).
        - Does NOT manage the posting pipeline (that is ModulePostingService).
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

        Preconditions:
            - ``event_id`` is a globally unique UUID.
            - ``event_type`` is a non-empty namespaced string.
            - ``payload`` is a JSON-serializable dict.

        Postconditions:
            - On ACCEPTED: An immutable ``Event`` row is flushed (R1) with
              a computed ``payload_hash`` (R2), and an audit event is
              recorded (if auditor is configured).
            - On DUPLICATE: No new row is created; existing envelope returned.
            - On REJECTED: No Event row is created; rejection audit event
              is recorded (if auditor is configured).

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
            logger.warning(
                "event_rejected_validation",
                extra={"error_count": len(validation.errors)},
            )
            return IngestResult(
                status=IngestStatus.REJECTED,
                event_id=event_id,
                validation=validation,
                message="Validation failed",
            )

        # INVARIANT: R2 -- Compute payload hash for verification
        payload_hash = hash_payload(payload)

        # INVARIANT: R3/R8 -- Check for existing event (idempotency)
        existing = self._get_existing_event(event_id)

        if existing is not None:
            # INVARIANT: R2 -- Payload hash verification: same event_id +
            # different payload = protocol violation
            if existing.payload_hash != payload_hash:
                if self._auditor:
                    self._auditor.record_event_rejected(
                        event_id=event_id,
                        reason=f"Payload mismatch: expected {existing.payload_hash}, got {payload_hash}",
                        actor_id=actor_id,
                    )
                logger.warning("event_rejected_hash_mismatch")
                return IngestResult(
                    status=IngestStatus.REJECTED,
                    event_id=event_id,
                    message="Payload hash mismatch - events are immutable",
                )

            # Idempotent success - return existing
            envelope = self._to_envelope(existing)
            logger.info(
                "event_duplicate",
                extra={"event_type": event_type},
            )
            return IngestResult(
                status=IngestStatus.DUPLICATE,
                event_id=event_id,
                event_envelope=envelope,
                message="Event already ingested",
            )

        # INVARIANT: R1 -- Create new immutable event record
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
        try:
            self._session.flush()
        except IntegrityError:
            # Concurrent insert — another thread inserted the same event_id
            self._session.rollback()
            logger.warning(
                "concurrent_event_insert_conflict",
                extra={"event_id": str(event_id)},
            )
            existing = self._get_existing_event(event_id)
            if existing is not None:
                envelope = self._to_envelope(existing)
                return IngestResult(
                    status=IngestStatus.DUPLICATE,
                    event_id=event_id,
                    event_envelope=envelope,
                    message="Event already ingested (concurrent)",
                )
            return IngestResult(
                status=IngestStatus.REJECTED,
                event_id=event_id,
                message="Concurrent insert conflict — event not found after retry",
            )

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
        logger.info(
            "event_ingested",
            extra={"event_type": event_type, "payload_hash": payload_hash},
        )
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
