"""
Interpretation Outcome model.

Every accepted BusinessEvent ends here. No exceptions.

This model records the terminal state of event interpretation:
- POSTED: Successfully posted to all required ledgers
- BLOCKED: Valid event, cannot process yet
- REJECTED: Invalid economic reality
- PROVISIONAL: Recorded provisionally, awaiting confirmation
- NON_POSTING: Valid, but no financial effect per policy

Invariant P15: Every accepted BusinessEvent has exactly one InterpretationOutcome.
Invariant L5: No journal rows without POSTED outcome; no POSTED outcome without all journal rows.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class OutcomeStatus(str, Enum):
    """
    Terminal status for event interpretation.

    Status transitions:
    - POSTED: Terminal (no further transitions)
    - BLOCKED: → POSTED or → REJECTED
    - REJECTED: Terminal (no further transitions)
    - PROVISIONAL: → POSTED or → REJECTED
    - NON_POSTING: Terminal (no further transitions)
    """

    POSTED = "posted"  # Successfully posted to all required ledgers
    BLOCKED = "blocked"  # Valid event, cannot process yet
    REJECTED = "rejected"  # Invalid economic reality
    PROVISIONAL = "provisional"  # Recorded provisionally, awaiting confirmation
    NON_POSTING = "non_posting"  # Valid, but no financial effect per policy


class InterpretationOutcome(Base):
    """
    Records the terminal state of event interpretation.

    Every accepted BusinessEvent must have exactly one InterpretationOutcome.
    This is the proof that "every event ends somewhere."
    """

    __tablename__ = "interpretation_outcomes"

    __table_args__ = (
        # P15: One outcome per event
        UniqueConstraint("source_event_id", name="uq_outcome_source_event"),
        # Indexes for common queries
        Index("idx_outcome_status", "status"),
        Index("idx_outcome_profile", "profile_id", "profile_version"),
        Index("idx_outcome_created", "created_at"),
    )

    # Source event reference (unique - one outcome per event)
    source_event_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
        unique=True,
    )

    # Terminal status
    status: Mapped[OutcomeStatus] = mapped_column(
        String(20),
        nullable=False,
    )

    # Economic event reference (null if REJECTED)
    econ_event_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Journal entry references (null if not POSTED)
    # Stored as JSON array of UUIDs
    journal_entry_ids: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Reason for non-POSTED outcomes
    reason_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Structured reason details
    reason_detail: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Profile that processed this event
    profile_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    profile_version: Mapped[int] = mapped_column(
        nullable=False,
    )

    # Hash of the profile for determinism verification
    profile_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Trace ID for audit trail
    trace_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Only updated for BLOCKED → POSTED transitions
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<InterpretationOutcome {self.status.value} for event {self.source_event_id}>"

    @property
    def is_terminal(self) -> bool:
        """Check if this outcome is terminal (no further transitions)."""
        return self.status in (
            OutcomeStatus.POSTED,
            OutcomeStatus.REJECTED,
            OutcomeStatus.NON_POSTING,
        )

    @property
    def can_transition(self) -> bool:
        """Check if this outcome can transition to another state."""
        return self.status in (OutcomeStatus.BLOCKED, OutcomeStatus.PROVISIONAL)
