"""
Interpretation Outcome model.

Every accepted BusinessEvent ends here. No exceptions.

This model records the terminal state of event interpretation:
- POSTED: Successfully posted to all required ledgers
- BLOCKED: Valid event, cannot process yet (awaiting precondition)
- REJECTED: Invalid economic reality (policy says no)
- PROVISIONAL: Recorded provisionally, awaiting confirmation
- NON_POSTING: Valid, but no financial effect per policy
- FAILED: Guard/engine/system failure (retriable)
- RETRYING: Retry in progress after FAILED
- ABANDONED: Permanently given up after repeated failures

Invariant P15: Every accepted BusinessEvent has exactly one InterpretationOutcome.
Invariant L5: No journal rows without POSTED outcome; no POSTED outcome without all journal rows.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class FailureType(str, Enum):
    """Classification of posting failures for work queue routing."""

    GUARD = "guard"                    # Guard expression evaluation failure
    ENGINE = "engine"                  # Engine computation failure
    RECONCILIATION = "reconciliation"  # Subledger-GL reconciliation failure
    SNAPSHOT = "snapshot"              # Stale reference snapshot
    AUTHORITY = "authority"            # Policy authority / actor validation failure
    CONTRACT = "contract"              # Engine contract validation failure
    SYSTEM = "system"                  # Unexpected system error


class OutcomeStatus(str, Enum):
    """
    Status for event interpretation outcomes.

    State machine:
        BLOCKED → POSTED | REJECTED | FAILED
        PROVISIONAL → POSTED | REJECTED
        FAILED → RETRYING | ABANDONED
        RETRYING → POSTED | FAILED
        POSTED: terminal
        REJECTED: terminal
        NON_POSTING: terminal
        ABANDONED: terminal
    """

    POSTED = "posted"            # Successfully posted to all required ledgers
    BLOCKED = "blocked"          # Valid event, cannot process yet
    REJECTED = "rejected"        # Invalid economic reality (policy says no)
    PROVISIONAL = "provisional"  # Recorded provisionally, awaiting confirmation
    NON_POSTING = "non_posting"  # Valid, but no financial effect per policy
    FAILED = "failed"            # Guard/engine/system failure (retriable)
    RETRYING = "retrying"        # Retry in progress after FAILED
    ABANDONED = "abandoned"      # Permanently given up


# Allowed state transitions (from → set of valid targets)
VALID_TRANSITIONS: dict[OutcomeStatus, frozenset[OutcomeStatus]] = {
    OutcomeStatus.BLOCKED: frozenset({
        OutcomeStatus.POSTED, OutcomeStatus.REJECTED, OutcomeStatus.FAILED,
    }),
    OutcomeStatus.PROVISIONAL: frozenset({
        OutcomeStatus.POSTED, OutcomeStatus.REJECTED,
    }),
    OutcomeStatus.FAILED: frozenset({
        OutcomeStatus.RETRYING, OutcomeStatus.ABANDONED,
    }),
    OutcomeStatus.RETRYING: frozenset({
        OutcomeStatus.POSTED, OutcomeStatus.FAILED,
    }),
    # Terminal states — no transitions allowed
    OutcomeStatus.POSTED: frozenset(),
    OutcomeStatus.REJECTED: frozenset(),
    OutcomeStatus.NON_POSTING: frozenset(),
    OutcomeStatus.ABANDONED: frozenset(),
}


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
        # Work queue indexes (Phase 9)
        Index("idx_outcome_failure_type", "failure_type"),
        Index("idx_outcome_actor", "actor_id"),
        Index("idx_outcome_failure_status", "status", "failure_type"),
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

    # Decision journal — structured log records captured during interpretation.
    # Stored as a JSON array of dicts, each with ts, message, and structured fields.
    # Populated automatically by InterpretationCoordinator via LogCapture.
    decision_log: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # --- Phase 9: Financial Exception Lifecycle fields ---

    # Failure classification for work queue routing (null if POSTED/NON_POSTING)
    failure_type: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    # Human-readable failure description
    failure_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Reference to engine trace set produced during this attempt
    engine_traces_ref: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # SHA-256 hash of the original event payload (immutable across retries)
    payload_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Actor who initiated the event (immutable across retries)
    actor_id: Mapped[UUID | None] = mapped_column(
        UUIDString(),
        nullable=True,
    )

    # Number of retry attempts (0 = original attempt)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
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

    @property
    def status_enum(self) -> OutcomeStatus:
        """Return status as OutcomeStatus enum (normalizes raw DB strings)."""
        if isinstance(self.status, OutcomeStatus):
            return self.status
        return OutcomeStatus(self.status)

    @property
    def status_str(self) -> str:
        """Return status as a plain string value."""
        if isinstance(self.status, OutcomeStatus):
            return self.status.value
        return self.status

    def __repr__(self) -> str:
        return f"<InterpretationOutcome {self.status_str} for event {self.source_event_id}>"

    @property
    def is_terminal(self) -> bool:
        """Check if this outcome is terminal (no further transitions)."""
        return self.status_enum in (
            OutcomeStatus.POSTED,
            OutcomeStatus.REJECTED,
            OutcomeStatus.NON_POSTING,
            OutcomeStatus.ABANDONED,
        )

    @property
    def can_transition(self) -> bool:
        """Check if this outcome can transition to another state."""
        return len(VALID_TRANSITIONS.get(self.status_enum, frozenset())) > 0

    @property
    def is_retriable(self) -> bool:
        """Check if this outcome can be retried."""
        return self.status_enum == OutcomeStatus.FAILED

    def validate_transition(self, target: OutcomeStatus) -> None:
        """Raise ValueError if transition from current status to target is invalid."""
        allowed = VALID_TRANSITIONS.get(self.status_enum, frozenset())
        if target not in allowed:
            raise ValueError(
                f"Invalid transition: {self.status_str} → {target.value}. "
                f"Allowed: {sorted(s.value for s in allowed)}"
            )
