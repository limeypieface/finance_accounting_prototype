"""
Event envelope model.

The minimum event envelope the finance kernel accepts.

Hard invariants:
- event_id is never reused
- (event_id, payload_hash) is immutable; if an event_id arrives with a
  different payload_hash, it is rejected as a protocol violation
- occurred_at and effective_date are immutable for a given event_id
- effective_date determines fiscal period inclusion, locking, and reporting
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class Event(Base):
    """
    Financially postable event envelope.

    Events are the source of all financial facts. They are immutable
    once ingested and serve as the audit trail for all postings.
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
        """
        Generate the idempotency key for this event.

        Format: producer:event_type:event_id
        """
        return f"{self.producer}:{self.event_type}:{self.event_id}"
