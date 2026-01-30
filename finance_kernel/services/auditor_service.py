"""
Auditor service - Audit trail creation and validation.

The Auditor is responsible for:
- Creating tamper-evident audit events
- Maintaining the hash chain
- Validating audit chain integrity
- Providing audit trail queries

All audit events are created within the same transaction as
the operation being audited.
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
    Service for creating and validating audit events.

    The Auditor maintains a hash chain linking all audit events.
    Each event includes:
    - A hash of its payload
    - The hash of the previous event
    - A computed hash of all the above

    This makes any tampering detectable.
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

        Args:
            entity_type: Type of entity being audited.
            entity_id: ID of the entity.
            action: The action being recorded.
            actor_id: Who performed the action.
            payload: Additional context data.

        Returns:
            The created AuditEvent.
        """
        # Get sequence and previous hash
        seq = self._sequence_service.next_value(SequenceService.AUDIT_EVENT)
        prev_hash = self._get_last_hash()

        # Hash the payload
        payload_data = payload or {}
        computed_payload_hash = hash_payload(payload_data)

        # Compute the event hash
        event_hash = hash_audit_event(
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action.value,
            payload_hash=computed_payload_hash,
            prev_hash=prev_hash,
        )

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
        """Record that an event was ingested."""
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
        """Record that an event was rejected."""
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
        """Record that a journal entry was posted."""
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
        """Record that a journal entry was reversed."""
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

    # Chain validation

    def validate_chain(self) -> bool:
        """
        Validate the entire audit chain.

        Returns:
            True if chain is valid.

        Raises:
            AuditChainBrokenError: If chain validation fails.
        """
        events = self._session.execute(
            select(AuditEvent).order_by(AuditEvent.seq)
        ).scalars().all()

        if not events:
            return True

        # First event should have no prev_hash
        if events[0].prev_hash is not None:
            logger.critical("audit_chain_broken", exc_info=True)
            raise AuditChainBrokenError(
                str(events[0].id),
                "None",
                events[0].prev_hash,
            )

        # Validate each event's hash and chain linkage
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

            # Validate chain linkage (except for first event)
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
