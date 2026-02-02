"""
Module: finance_kernel.models.audit_event
Responsibility: ORM persistence for the tamper-evident audit hash chain.
Architecture position: Kernel > Models.  May import from db/base.py only.

Invariants enforced:
    R10 -- Audit records are append-only; no UPDATE or DELETE (ORM + DB trigger).
    R11 -- Hash chain integrity: hash = H(entity_type | entity_id | action |
           payload_hash | prev_hash).  Validated by AuditorService.
    R9  -- seq is monotonically increasing, allocated by SequenceService.

Failure modes:
    - ImmutabilityViolationError on any UPDATE/DELETE attempt (R10).
    - AuditChainBrokenError when chain validation detects a hash mismatch (R11).

Audit relevance:
    AuditEvent IS the audit trail.  Every significant financial action --
    event ingestion, journal posting, reversal, period close, protocol
    violation -- produces an AuditEvent.  The hash chain makes any
    retroactive tampering mathematically detectable.

Minimum coverage (each action type generates at least one AuditEvent):
    - EVENT_INGESTED, EVENT_REJECTED
    - JOURNAL_POSTED, JOURNAL_REVERSED
    - PERIOD_OPENED, PERIOD_CLOSED
    - PROTOCOL_VIOLATION, PAYLOAD_MISMATCH
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import JSON, BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class AuditAction(str, Enum):
    """Types of auditable actions.

    Contract: Every member represents one class of financial event that
    MUST be recorded in the audit chain.  Adding a new action type requires
    updating AuditorService to produce the corresponding AuditEvent.
    """

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

    # Close lifecycle
    CLOSE_BEGUN = "close_begun"
    SUBLEDGER_CLOSED = "subledger_closed"
    CLOSE_CERTIFIED = "close_certified"
    CLOSE_CANCELLED = "close_cancelled"

    # Account lifecycle
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_DEACTIVATED = "account_deactivated"

    # Approval lifecycle
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_ESCALATED = "approval_escalated"
    APPROVAL_AUTO_APPROVED = "approval_auto_approved"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_POLICY_DRIFT = "approval_policy_version_drift"
    APPROVAL_TAMPER_DETECTED = "approval_tamper_detected"


class AuditEvent(Base):
    """
    Audit event with hash chain for tamper evidence.

    Contract:
        AuditEvent rows are sacred -- append-only, never updated or deleted.
        Each row's hash includes the previous row's hash, creating a
        tamper-evident chain (R11).

    Guarantees:
        - seq is globally unique and monotonically increasing (R9).
        - hash = H(entity_type | entity_id | action | payload_hash | prev_hash).
        - prev_hash is None only for the genesis event.

    Non-goals:
        - This model does NOT enforce hash correctness at INSERT time;
          that is the responsibility of AuditorService.
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
        """Check if this is the first event in the hash chain (R11).

        Postconditions: Returns True iff prev_hash is None.
        """
        return self.prev_hash is None
