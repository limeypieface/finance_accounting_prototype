"""
Audit Event model with hash chain.

Provides tamper-evident audit trail for all financial operations.

Hard invariants:
- Hash chain must validate end-to-end
- Audit records are append-only

Minimum coverage:
- Event ingested
- Event rejected
- JournalEntry posted
- JournalEntry reversed
- Period violation detected
- Protocol violation detected
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, Index, String, BigInteger
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class AuditAction(str, Enum):
    """Types of auditable actions."""

    # Event lifecycle
    EVENT_INGESTED = "event_ingested"
    EVENT_REJECTED = "event_rejected"

    # Journal lifecycle
    JOURNAL_DRAFT_CREATED = "journal_draft_created"
    JOURNAL_POSTED = "journal_posted"
    JOURNAL_REVERSED = "journal_reversed"

    # Period lifecycle
    PERIOD_OPENED = "period_opened"
    PERIOD_CLOSED = "period_closed"

    # Violations
    PERIOD_VIOLATION = "period_violation"
    PROTOCOL_VIOLATION = "protocol_violation"
    PAYLOAD_MISMATCH = "payload_mismatch"
    VALIDATION_FAILURE = "validation_failure"

    # Account lifecycle
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_DEACTIVATED = "account_deactivated"


class AuditEvent(Base):
    """
    Audit event with hash chain for tamper evidence.

    Every significant action in the finance kernel creates an audit event.
    Events are linked via hash chain - each event's hash includes the
    previous event's hash, making tampering detectable.
    """

    __tablename__ = "audit_events"

    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_action", "action"),
        Index("idx_audit_occurred", "occurred_at"),
        Index("idx_audit_seq", "seq"),
    )

    # Monotonic sequence for ordering
    seq: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
    )

    # Type of entity being audited (e.g., "Event", "JournalEntry", "FiscalPeriod")
    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # ID of the entity being audited
    entity_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # Action being recorded
    action: Mapped[AuditAction] = mapped_column(
        String(50),
        nullable=False,
    )

    # Who performed the action
    actor_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # When the action occurred
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Additional context (JSON)
    payload: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Hash of the payload
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # Hash of the previous audit event (null for first event)
    prev_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Hash of this audit event
    # hash = H(entity_type + entity_id + action + payload_hash + prev_hash)
    hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<AuditEvent {self.action.value} on {self.entity_type}:{self.entity_id}>"

    @property
    def is_genesis(self) -> bool:
        """Check if this is the first event in the chain."""
        return self.prev_hash is None
