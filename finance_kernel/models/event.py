"""
Module: finance_kernel.models.event
Responsibility: ORM persistence for the incoming event envelope -- the trigger
    for all downstream financial processing.
Architecture position: Kernel > Models.  May import from db/base.py and
    exceptions.py only.

Invariants enforced:
    R1  -- Event immutability (ORM before_update listener + DB trigger).
    R2  -- Payload hash verification (payload_hash stored at ingestion;
           same event_id with different hash raises PayloadMismatchError).
    R3  -- event_id uniqueness (UNIQUE constraint via uq_event_id).

Failure modes:
    - IntegrityError on duplicate event_id (R3).
    - ImmutabilityViolationError on any UPDATE to an existing Event row (R1).
    - PayloadMismatchError detected upstream by IngestorService (R2).

Audit relevance:
    The Event row is the canonical, immutable source record for every financial
    posting.  Its payload_hash links to the audit event hash chain (R11).
    Replay safety (R6) depends on event immutability.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.attributes import get_history

from finance_kernel.db.base import Base, UUIDString
from finance_kernel.exceptions import ImmutabilityViolationError


class Event(Base):
    """
    Financially postable event envelope -- source of all financial facts.

    Contract:
        Once an Event row is INSERTed, it is immutable.  No column may be
        updated or deleted.  The ORM before_update listener and a DB trigger
        both enforce this (R1/R10).

    Guarantees:
        - event_id is globally unique (UNIQUE constraint).
        - payload_hash is SHA-256 of the canonical JSON payload (R2).
        - occurred_at, effective_date, and actor_id are set at ingestion
          and never change.

    Non-goals:
        - This model does NOT validate payload schema; that is the
          responsibility of IngestorService.
    """

    __tablename__ = "events"

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_id"),
        Index("idx_event_type_effective", "event_type", "effective_date"),
        Index("idx_event_effective_occurred", "effective_date", "occurred_at"),
        Index("idx_event_producer", "producer"),
    )

    # Globally unique event identifier (not the same as the row id)
    event_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
        unique=True,
    )

    # Namespaced, stable event type (e.g., "inventory.receipt", "purchasing.po_approved")
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # When the event actually happened in reality
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Accounting date for fiscal periods and reporting
    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # User or system that caused the event
    actor_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # Module or system that produced the event
    producer: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Domain-specific payload
    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    # Hash of canonicalized payload for integrity verification
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # Schema version for payload interpretation
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    # When the event was ingested into the kernel
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Event {self.event_type}:{self.event_id}>"

    @property
    def idempotency_key(self) -> str:
        """Generate the idempotency key for this event.

        Postconditions: Returns string in format producer:event_type:event_id.
            Used as the UNIQUE key on JournalEntry for R3/R8 enforcement.
        """
        return f"{self.producer}:{self.event_type}:{self.event_id}"


# =============================================================================
# ORM-Level Immutability Protection
# =============================================================================
# R10 Compliance: Events are immutable after ingestion.
#
# This event listener prevents ANY modifications to Event objects after
# they have been persisted. The database trigger provides a second layer
# of defense for raw SQL updates.
# =============================================================================

@event.listens_for(Event, "before_update")
def prevent_event_update(mapper, connection, target):
    """Prevent any updates to Event objects.

    Preconditions: Called by SQLAlchemy before any UPDATE flush on Event.
    Raises: ImmutabilityViolationError always -- INVARIANT R1/R10: Events
        are immutable once ingested.
    """
    raise ImmutabilityViolationError(
        f"R10 Violation: Events are immutable - cannot modify event {target.event_id}"
    )
