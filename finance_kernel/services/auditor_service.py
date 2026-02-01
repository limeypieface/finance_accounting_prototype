"""
AuditorService -- tamper-evident audit trail and hash chain maintenance.

Responsibility:
    Creates immutable, hash-chained audit events for every significant
    state change in the kernel.  Provides chain validation for tamper
    detection and trace queries for forensic review.

Architecture position:
    Kernel > Services -- imperative shell, called by IngestorService,
    JournalWriter, PeriodService, and CorrectionService.

Invariants enforced:
    R9  -- Sequence monotonicity via SequenceService (never raw SQL max+1).
    R11 -- Audit chain integrity: ``hash = H(payload_hash + prev_hash)``.
           Every audit event carries a cryptographic link to its predecessor.
    R1  -- Append-only: audit events are never modified or deleted (ORM +
           DB trigger enforced on the AuditEvent model).

Failure modes:
    - AuditChainBrokenError: Recomputed hash does not match stored hash,
      or prev_hash does not match the predecessor's hash.
    - IntegrityError: Concurrent insert race on sequence counter.

Audit relevance:
    This IS the audit service.  All audit events emitted by peer services
    flow through ``_create_audit_event()`` which enforces R11 hash chain
    linkage before persisting.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.exceptions import AuditChainBrokenError
from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.services.sequence_service import SequenceService
from finance_kernel.logging_config import get_logger
from finance_kernel.utils.hashing import hash_audit_event, hash_payload

logger = get_logger("services.auditor")


@dataclass(frozen=True)
class AuditTraceEntry:
    """A single entry in an audit trace."""

    seq: int
    action: AuditAction
    occurred_at: datetime
    actor_id: UUID
    payload: dict[str, Any]
    hash: str


@dataclass(frozen=True)
class AuditTrace:
    """
    Complete audit trace for an entity.

    Contains all audit events in chronological order.
    """

    entity_type: str
    entity_id: UUID
    entries: tuple[AuditTraceEntry, ...]

    @property
    def is_empty(self) -> bool:
        return len(self.entries) == 0

    @property
    def first_action(self) -> AuditAction | None:
        return self.entries[0].action if self.entries else None

    @property
    def last_action(self) -> AuditAction | None:
        return self.entries[-1].action if self.entries else None


class AuditorService:
    """
    Service for creating and validating tamper-evident audit events.

    Contract:
        Accepts domain-specific recording requests (event ingested,
        journal posted, period closed, etc.) and creates append-only
        ``AuditEvent`` rows with cryptographic hash chain linkage.

    Guarantees:
        - R11: Every audit event's ``hash`` is a deterministic function
          of ``(entity_type, entity_id, action, payload_hash, prev_hash)``.
          Tampering with any field is detectable by ``validate_chain()``.
        - R9: Sequence numbers are allocated via ``SequenceService``
          (locked counter row), never via the aggregate-max-plus-one
          anti-pattern.
        - R1: Audit events are append-only.  The ``AuditEvent`` model
          is protected by ORM listeners and database triggers.

    Non-goals:
        - Does NOT call ``session.commit()`` -- caller controls boundaries.
        - Does NOT interpret or act on audit events (that is forensic tooling).
    """

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
    ):
        """
        Initialize the Auditor service.

        Args:
            session: SQLAlchemy session.
            clock: Clock for timestamps. Defaults to SystemClock.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._sequence_service = SequenceService(session)

    def _get_last_hash(self) -> str | None:
        """Get the hash of the most recent audit event."""
        last_event = self._session.execute(
            select(AuditEvent)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        ).scalar_one_or_none()

        return last_event.hash if last_event else None

    def _create_audit_event(
        self,
        entity_type: str,
        entity_id: UUID,
        action: AuditAction,
        actor_id: UUID,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """
        Create a new audit event with hash chain linkage.

        This is the internal method that actually creates the event.
        Public methods should use the domain-specific recording methods.

        Preconditions:
            - ``entity_type`` is a non-empty string.
            - ``entity_id`` and ``actor_id`` are valid UUIDs.
            - ``action`` is a valid ``AuditAction`` enum member.

        Postconditions:
            - A new ``AuditEvent`` row is flushed to the session with
              a monotonically increasing ``seq`` (R9) and a valid
              hash chain link (R11).
            - ``event.hash == H(entity_type, entity_id, action,
              payload_hash, prev_hash)`` (R11).

        Raises:
            IntegrityError: On concurrent sequence counter race.

        Args:
            entity_type: Type of entity being audited.
            entity_id: ID of the entity.
            action: The action being recorded.
            actor_id: Who performed the action.
            payload: Additional context data.

        Returns:
            The created AuditEvent.
        """
        # INVARIANT: R9 -- Sequence monotonicity via locked counter row
        seq = self._sequence_service.next_value(SequenceService.AUDIT_EVENT)
        assert seq > 0, "R9 violation: audit sequence must be strictly positive"

        prev_hash = self._get_last_hash()

        # Hash the payload
        payload_data = payload or {}
        computed_payload_hash = hash_payload(payload_data)

        # INVARIANT: R11 -- hash = H(payload_hash + prev_hash)
        event_hash = hash_audit_event(
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action.value,
            payload_hash=computed_payload_hash,
            prev_hash=prev_hash,
        )
        assert event_hash, "R11 violation: event hash must be non-empty"

        # Create the audit event
        audit_event = AuditEvent(
            seq=seq,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_id=actor_id,
            occurred_at=self._clock.now(),
            payload=payload_data,
            payload_hash=computed_payload_hash,
            prev_hash=prev_hash,
            hash=event_hash,
        )

        # INVARIANT: R1 -- Append-only: audit events are immutable once flushed
        self._session.add(audit_event)
        self._session.flush()

        logger.info(
            "audit_event_created",
            extra={
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "action": action.value,
                "seq": seq,
            },
        )

        return audit_event

    # Domain-specific recording methods

    def record_event_ingested(
        self,
        event_id: UUID,
        event_type: str,
        producer: str,
        actor_id: UUID,
    ) -> AuditEvent:
        """
        Record that an event was ingested.

        Preconditions:
            - ``event_id`` corresponds to a persisted Event row (or will
              be persisted in the same transaction).
            - ``event_type`` is a non-empty namespaced string.
        """
        return self._create_audit_event(
            entity_type="Event",
            entity_id=event_id,
            action=AuditAction.EVENT_INGESTED,
            actor_id=actor_id,
            payload={
                "event_type": event_type,
                "producer": producer,
            },
        )

    def record_event_rejected(
        self,
        event_id: UUID,
        reason: str,
        actor_id: UUID,
    ) -> AuditEvent:
        """
        Record that an event was rejected.

        Preconditions:
            - ``event_id`` is the UUID of the rejected event.
            - ``reason`` is a non-empty human-readable rejection reason.
        """
        return self._create_audit_event(
            entity_type="Event",
            entity_id=event_id,
            action=AuditAction.EVENT_REJECTED,
            actor_id=actor_id,
            payload={"reason": reason},
        )

    def record_posting(
        self,
        entry_id: UUID,
        event_id: UUID,
        event_type: str,
        effective_date: date,
        actor_id: UUID,
        line_count: int,
    ) -> AuditEvent:
        """
        Record that a journal entry was posted.

        Preconditions:
            - ``entry_id`` corresponds to a POSTED JournalEntry in the
              same transaction.
            - ``line_count`` is > 0 (a posted entry must have lines).
        """
        return self._create_audit_event(
            entity_type="JournalEntry",
            entity_id=entry_id,
            action=AuditAction.JOURNAL_POSTED,
            actor_id=actor_id,
            payload={
                "event_id": str(event_id),
                "event_type": event_type,
                "effective_date": str(effective_date),
                "line_count": line_count,
            },
        )

    def record_reversal(
        self,
        entry_id: UUID,
        original_entry_id: UUID,
        reason: str,
        actor_id: UUID,
    ) -> AuditEvent:
        """
        Record that a journal entry was reversed.

        Preconditions:
            - ``entry_id`` is the new reversal entry (POSTED).
            - ``original_entry_id`` is the entry being reversed (now REVERSED).
            - ``reason`` is non-empty.
        """
        return self._create_audit_event(
            entity_type="JournalEntry",
            entity_id=entry_id,
            action=AuditAction.JOURNAL_REVERSED,
            actor_id=actor_id,
            payload={
                "original_entry_id": str(original_entry_id),
                "reason": reason,
            },
        )

    def record_period_closed(
        self,
        period_id: UUID,
        period_code: str,
        actor_id: UUID,
    ) -> AuditEvent:
        """Record that a period was closed."""
        return self._create_audit_event(
            entity_type="FiscalPeriod",
            entity_id=period_id,
            action=AuditAction.PERIOD_CLOSED,
            actor_id=actor_id,
            payload={"period_code": period_code},
        )

    def record_period_violation(
        self,
        period_id: UUID,
        period_code: str,
        effective_date: date,
        actor_id: UUID,
    ) -> AuditEvent:
        """Record an attempted violation of period control."""
        return self._create_audit_event(
            entity_type="FiscalPeriod",
            entity_id=period_id,
            action=AuditAction.PERIOD_VIOLATION,
            actor_id=actor_id,
            payload={
                "period_code": period_code,
                "effective_date": str(effective_date),
            },
        )

    # Close lifecycle events

    def record_close_begun(
        self,
        period_id: UUID,
        period_code: str,
        actor_id: UUID,
        correlation_id: str,
    ) -> AuditEvent:
        """Record that a period close was initiated."""
        return self._create_audit_event(
            entity_type="FiscalPeriod",
            entity_id=period_id,
            action=AuditAction.CLOSE_BEGUN,
            actor_id=actor_id,
            payload={
                "period_code": period_code,
                "correlation_id": correlation_id,
            },
        )

    def record_subledger_closed(
        self,
        period_id: UUID,
        period_code: str,
        subledger_type: str,
        actor_id: UUID,
    ) -> AuditEvent:
        """Record that a subledger was closed for a period."""
        return self._create_audit_event(
            entity_type="SubledgerClose",
            entity_id=period_id,
            action=AuditAction.SUBLEDGER_CLOSED,
            actor_id=actor_id,
            payload={
                "period_code": period_code,
                "subledger_type": subledger_type,
            },
        )

    def record_close_certified(
        self,
        period_id: UUID,
        period_code: str,
        actor_id: UUID,
        certificate_data: dict[str, Any],
    ) -> AuditEvent:
        """Record the close certificate. Certificate data in payload."""
        return self._create_audit_event(
            entity_type="FiscalPeriod",
            entity_id=period_id,
            action=AuditAction.CLOSE_CERTIFIED,
            actor_id=actor_id,
            payload={
                "period_code": period_code,
                **certificate_data,
            },
        )

    def record_close_cancelled(
        self,
        period_id: UUID,
        period_code: str,
        actor_id: UUID,
        reason: str,
    ) -> AuditEvent:
        """Record that a close was cancelled."""
        return self._create_audit_event(
            entity_type="FiscalPeriod",
            entity_id=period_id,
            action=AuditAction.CLOSE_CANCELLED,
            actor_id=actor_id,
            payload={
                "period_code": period_code,
                "reason": reason,
            },
        )

    # Chain validation

    def validate_chain(self) -> bool:
        """
        Validate the entire audit chain.

        Postconditions:
            - Returns ``True`` only if every event's stored ``hash``
              matches the recomputed value (R11) and every event's
              ``prev_hash`` matches its predecessor's ``hash``.

        Raises:
            AuditChainBrokenError: If chain validation fails at any point.
        """
        events = self._session.execute(
            select(AuditEvent).order_by(AuditEvent.seq)
        ).scalars().all()

        if not events:
            return True

        # INVARIANT: R11 -- First event should have no prev_hash (chain genesis)
        if events[0].prev_hash is not None:
            logger.critical("audit_chain_broken", exc_info=True)
            raise AuditChainBrokenError(
                str(events[0].id),
                "None",
                events[0].prev_hash,
            )

        # INVARIANT: R11 -- Validate each event's hash and chain linkage
        for i, event in enumerate(events):
            # Handle action which may be an AuditAction enum or a string
            action_value = (
                event.action.value if isinstance(event.action, AuditAction) else event.action
            )

            # Recompute the hash
            expected_hash = hash_audit_event(
                entity_type=event.entity_type,
                entity_id=str(event.entity_id),
                action=action_value,
                payload_hash=event.payload_hash,
                prev_hash=event.prev_hash,
            )

            if event.hash != expected_hash:
                logger.critical("audit_chain_broken", exc_info=True)
                raise AuditChainBrokenError(
                    str(event.id),
                    expected_hash,
                    event.hash,
                )

            # INVARIANT: R11 -- Validate chain linkage (except for first event)
            if i > 0:
                expected_prev = events[i - 1].hash
                if event.prev_hash != expected_prev:
                    logger.critical("audit_chain_broken", exc_info=True)
                    raise AuditChainBrokenError(
                        str(event.id),
                        expected_prev,
                        event.prev_hash or "None",
                    )

        logger.info(
            "audit_chain_valid",
            extra={"event_count": len(events)},
        )
        return True

    # Trace and query methods

    def get_trace(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> AuditTrace:
        """
        Get the complete audit trace for an entity.

        Args:
            entity_type: Type of entity.
            entity_id: Entity ID.

        Returns:
            AuditTrace with all events in chronological order.
        """
        events = self._session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == entity_type,
                AuditEvent.entity_id == entity_id,
            )
            .order_by(AuditEvent.seq)
        ).scalars().all()

        entries = tuple(
            AuditTraceEntry(
                seq=event.seq,
                action=event.action,
                occurred_at=event.occurred_at,
                actor_id=event.actor_id,
                payload=event.payload or {},
                hash=event.hash,
            )
            for event in events
        )

        return AuditTrace(
            entity_type=entity_type,
            entity_id=entity_id,
            entries=entries,
        )

    def get_recent_events(self, limit: int = 100) -> list[AuditEvent]:
        """
        Get the most recent audit events.

        Args:
            limit: Maximum number of events to return.

        Returns:
            List of AuditEvents, most recent first.
        """
        result = self._session.execute(
            select(AuditEvent)
            .order_by(AuditEvent.seq.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
