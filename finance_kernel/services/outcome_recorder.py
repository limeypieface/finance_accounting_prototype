"""
OutcomeRecorder service for recording interpretation outcomes.

Records the state of event interpretation through its full lifecycle:
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

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock
from finance_kernel.logging_config import get_logger
from finance_kernel.models.interpretation_outcome import (
    FailureType,
    InterpretationOutcome,
    OutcomeStatus,
    VALID_TRANSITIONS,
)

logger = get_logger("services.outcome_recorder")


class OutcomeAlreadyExistsError(Exception):
    """Outcome already exists for this event (P15 violation attempt)."""

    code: str = "OUTCOME_ALREADY_EXISTS"

    def __init__(self, event_id: UUID, existing_status: OutcomeStatus):
        self.event_id = event_id
        self.existing_status = existing_status
        status_str = existing_status.value if isinstance(existing_status, OutcomeStatus) else str(existing_status)
        super().__init__(
            f"Outcome already exists for event {event_id} with status {status_str}"
        )


class InvalidOutcomeTransitionError(Exception):
    """Invalid outcome status transition."""

    code: str = "INVALID_OUTCOME_TRANSITION"

    def __init__(
        self,
        event_id: UUID,
        from_status: OutcomeStatus,
        to_status: OutcomeStatus,
    ):
        self.event_id = event_id
        self.from_status = from_status
        self.to_status = to_status
        from_str = from_status.value if isinstance(from_status, OutcomeStatus) else str(from_status)
        to_str = to_status.value if isinstance(to_status, OutcomeStatus) else str(to_status)
        super().__init__(
            f"Invalid transition for event {event_id}: {from_str} -> {to_str}"
        )


class OutcomeRecorder:
    """
    Service for recording interpretation outcomes.

    Enforces:
    - P15: One outcome per event (via unique constraint)
    - Valid status transitions

    Usage:
        recorder = OutcomeRecorder(session, clock)

        # Record a new outcome
        outcome = recorder.record_posted(
            source_event_id=event_id,
            profile_id="MyProfile",
            profile_version=1,
            econ_event_id=econ_event.id,
            journal_entry_ids=[entry.id],
        )

        # Or record rejection
        outcome = recorder.record_rejected(
            source_event_id=event_id,
            profile_id="MyProfile",
            profile_version=1,
            reason_code="INVALID_QUANTITY",
            reason_detail={"quantity": -5},
        )
    """

    def __init__(self, session: Session, clock: Clock):
        """
        Initialize the outcome recorder.

        Args:
            session: SQLAlchemy session.
            clock: Clock for timestamps.
        """
        self._session = session
        self._clock = clock

    def record_posted(
        self,
        source_event_id: UUID,
        profile_id: str,
        profile_version: int,
        econ_event_id: UUID,
        journal_entry_ids: list[UUID],
        profile_hash: str | None = None,
        trace_id: UUID | None = None,
        decision_log: list[dict] | None = None,
    ) -> InterpretationOutcome:
        """
        Record a POSTED outcome.

        L5: This must be called in the same transaction as journal writes.

        Args:
            source_event_id: The source event ID.
            profile_id: Profile that processed the event.
            profile_version: Profile version.
            econ_event_id: The economic event ID.
            journal_entry_ids: List of journal entry IDs.
            profile_hash: Optional profile hash.
            trace_id: Optional trace ID.

        Returns:
            The created InterpretationOutcome.

        Raises:
            OutcomeAlreadyExistsError: If outcome already exists.
        """
        self._check_not_exists(source_event_id)

        outcome = InterpretationOutcome(
            source_event_id=source_event_id,
            status=OutcomeStatus.POSTED,
            econ_event_id=econ_event_id,
            journal_entry_ids=[str(eid) for eid in journal_entry_ids],
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
            trace_id=trace_id,
            decision_log=decision_log,
            created_at=self._clock.now(),
        )

        self._session.add(outcome)
        self._session.flush()
        logger.info(
            "outcome_recorded",
            extra={
                "status": "posted",
                "source_event_id": str(source_event_id),
                "econ_event_id": str(econ_event_id),
                "journal_entry_ids": [str(eid) for eid in journal_entry_ids],
                "profile_id": profile_id,
                "profile_version": profile_version,
            },
        )
        return outcome

    def record_rejected(
        self,
        source_event_id: UUID,
        profile_id: str,
        profile_version: int,
        reason_code: str,
        reason_detail: dict[str, Any] | None = None,
        profile_hash: str | None = None,
        trace_id: UUID | None = None,
        decision_log: list[dict] | None = None,
    ) -> InterpretationOutcome:
        """
        Record a REJECTED outcome.

        REJECTED is terminal - event had invalid economic reality.

        Args:
            source_event_id: The source event ID.
            profile_id: Profile that processed the event.
            profile_version: Profile version.
            reason_code: Machine-readable reason code.
            reason_detail: Additional details.
            profile_hash: Optional profile hash.
            trace_id: Optional trace ID.

        Returns:
            The created InterpretationOutcome.
        """
        self._check_not_exists(source_event_id)

        outcome = InterpretationOutcome(
            source_event_id=source_event_id,
            status=OutcomeStatus.REJECTED,
            reason_code=reason_code,
            reason_detail=reason_detail,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
            trace_id=trace_id,
            decision_log=decision_log,
            created_at=self._clock.now(),
        )

        self._session.add(outcome)
        self._session.flush()
        logger.info(
            "outcome_recorded",
            extra={
                "status": "rejected",
                "source_event_id": str(source_event_id),
                "reason_code": reason_code,
            },
        )
        return outcome

    def record_blocked(
        self,
        source_event_id: UUID,
        profile_id: str,
        profile_version: int,
        reason_code: str,
        reason_detail: dict[str, Any] | None = None,
        profile_hash: str | None = None,
        trace_id: UUID | None = None,
        decision_log: list[dict] | None = None,
    ) -> InterpretationOutcome:
        """
        Record a BLOCKED outcome.

        BLOCKED is resumable - event is valid but system cannot process yet.

        Args:
            source_event_id: The source event ID.
            profile_id: Profile that processed the event.
            profile_version: Profile version.
            reason_code: Machine-readable reason code.
            reason_detail: Additional details.
            profile_hash: Optional profile hash.
            trace_id: Optional trace ID.

        Returns:
            The created InterpretationOutcome.
        """
        self._check_not_exists(source_event_id)

        outcome = InterpretationOutcome(
            source_event_id=source_event_id,
            status=OutcomeStatus.BLOCKED,
            reason_code=reason_code,
            reason_detail=reason_detail,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
            trace_id=trace_id,
            decision_log=decision_log,
            created_at=self._clock.now(),
        )

        self._session.add(outcome)
        self._session.flush()
        logger.info(
            "outcome_recorded",
            extra={
                "status": "blocked",
                "source_event_id": str(source_event_id),
                "reason_code": reason_code,
            },
        )
        return outcome

    def record_provisional(
        self,
        source_event_id: UUID,
        profile_id: str,
        profile_version: int,
        econ_event_id: UUID | None = None,
        reason_code: str | None = None,
        reason_detail: dict[str, Any] | None = None,
        profile_hash: str | None = None,
        trace_id: UUID | None = None,
        decision_log: list[dict] | None = None,
    ) -> InterpretationOutcome:
        """
        Record a PROVISIONAL outcome.

        PROVISIONAL awaits confirmation - can transition to POSTED or REJECTED.

        Args:
            source_event_id: The source event ID.
            profile_id: Profile that processed the event.
            profile_version: Profile version.
            econ_event_id: Optional economic event ID.
            reason_code: Optional reason code.
            reason_detail: Additional details.
            profile_hash: Optional profile hash.
            trace_id: Optional trace ID.

        Returns:
            The created InterpretationOutcome.
        """
        self._check_not_exists(source_event_id)

        outcome = InterpretationOutcome(
            source_event_id=source_event_id,
            status=OutcomeStatus.PROVISIONAL,
            econ_event_id=econ_event_id,
            reason_code=reason_code,
            reason_detail=reason_detail,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
            trace_id=trace_id,
            decision_log=decision_log,
            created_at=self._clock.now(),
        )

        self._session.add(outcome)
        self._session.flush()
        logger.info(
            "outcome_recorded",
            extra={
                "status": "provisional",
                "source_event_id": str(source_event_id),
            },
        )
        return outcome

    def record_non_posting(
        self,
        source_event_id: UUID,
        profile_id: str,
        profile_version: int,
        reason_code: str | None = None,
        reason_detail: dict[str, Any] | None = None,
        profile_hash: str | None = None,
        trace_id: UUID | None = None,
        decision_log: list[dict] | None = None,
    ) -> InterpretationOutcome:
        """
        Record a NON_POSTING outcome.

        NON_POSTING is terminal - event is valid but has no financial effect.

        Args:
            source_event_id: The source event ID.
            profile_id: Profile that processed the event.
            profile_version: Profile version.
            reason_code: Optional reason code.
            reason_detail: Additional details.
            profile_hash: Optional profile hash.
            trace_id: Optional trace ID.

        Returns:
            The created InterpretationOutcome.
        """
        self._check_not_exists(source_event_id)

        outcome = InterpretationOutcome(
            source_event_id=source_event_id,
            status=OutcomeStatus.NON_POSTING,
            reason_code=reason_code,
            reason_detail=reason_detail,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
            trace_id=trace_id,
            decision_log=decision_log,
            created_at=self._clock.now(),
        )

        self._session.add(outcome)
        self._session.flush()
        logger.info(
            "outcome_recorded",
            extra={
                "status": "non_posting",
                "source_event_id": str(source_event_id),
            },
        )
        return outcome

    def record_failed(
        self,
        source_event_id: UUID,
        profile_id: str,
        profile_version: int,
        failure_type: FailureType | str,
        failure_message: str,
        reason_code: str | None = None,
        reason_detail: dict[str, Any] | None = None,
        profile_hash: str | None = None,
        trace_id: UUID | None = None,
        decision_log: list[dict] | None = None,
        engine_traces_ref: UUID | None = None,
        payload_fingerprint: str | None = None,
        actor_id: UUID | None = None,
    ) -> InterpretationOutcome:
        """Record a FAILED outcome (guard/engine/system failure — retriable).

        FAILED differs from REJECTED: REJECTED means the policy determined
        the event is invalid. FAILED means the system could not process it
        due to a guard, engine, or infrastructure failure that may be resolved
        by retry.
        """
        self._check_not_exists(source_event_id)

        ft_value = failure_type.value if isinstance(failure_type, FailureType) else failure_type

        outcome = InterpretationOutcome(
            source_event_id=source_event_id,
            status=OutcomeStatus.FAILED,
            reason_code=reason_code,
            reason_detail=reason_detail,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
            trace_id=trace_id,
            decision_log=decision_log,
            failure_type=ft_value,
            failure_message=failure_message,
            engine_traces_ref=engine_traces_ref,
            payload_fingerprint=payload_fingerprint,
            actor_id=actor_id,
            retry_count=0,
            created_at=self._clock.now(),
        )

        self._session.add(outcome)
        self._session.flush()
        logger.info(
            "outcome_recorded",
            extra={
                "status": "failed",
                "source_event_id": str(source_event_id),
                "failure_type": ft_value,
                "reason_code": reason_code,
            },
        )
        return outcome

    # -----------------------------------------------------------------
    # Transitions
    # -----------------------------------------------------------------

    def _validate_and_transition(
        self,
        source_event_id: UUID,
        target_status: OutcomeStatus,
    ) -> InterpretationOutcome:
        """Validate transition and update status. Returns the outcome."""
        outcome = self._get_existing(source_event_id)
        current = outcome.status_enum
        allowed = VALID_TRANSITIONS.get(current, frozenset())
        if target_status not in allowed:
            raise InvalidOutcomeTransitionError(
                source_event_id, current, target_status,
            )
        return outcome

    def transition_to_posted(
        self,
        source_event_id: UUID,
        econ_event_id: UUID,
        journal_entry_ids: list[UUID],
    ) -> InterpretationOutcome:
        """Transition BLOCKED, PROVISIONAL, or RETRYING → POSTED."""
        outcome = self._validate_and_transition(
            source_event_id, OutcomeStatus.POSTED,
        )
        from_status = outcome.status_str
        outcome.status = OutcomeStatus.POSTED
        outcome.econ_event_id = econ_event_id
        outcome.journal_entry_ids = [str(eid) for eid in journal_entry_ids]
        outcome.updated_at = self._clock.now()

        self._session.flush()
        logger.info(
            "outcome_transitioned",
            extra={
                "from_status": from_status,
                "to_status": "posted",
                "source_event_id": str(source_event_id),
            },
        )
        return outcome

    def transition_to_rejected(
        self,
        source_event_id: UUID,
        reason_code: str,
        reason_detail: dict[str, Any] | None = None,
    ) -> InterpretationOutcome:
        """Transition BLOCKED or PROVISIONAL → REJECTED."""
        outcome = self._validate_and_transition(
            source_event_id, OutcomeStatus.REJECTED,
        )
        from_status = outcome.status_str
        outcome.status = OutcomeStatus.REJECTED
        outcome.reason_code = reason_code
        outcome.reason_detail = reason_detail
        outcome.updated_at = self._clock.now()

        self._session.flush()
        logger.info(
            "outcome_transitioned",
            extra={
                "from_status": from_status,
                "to_status": "rejected",
                "source_event_id": str(source_event_id),
            },
        )
        return outcome

    def transition_to_failed(
        self,
        source_event_id: UUID,
        failure_type: FailureType | str,
        failure_message: str,
        reason_code: str | None = None,
        reason_detail: dict[str, Any] | None = None,
        engine_traces_ref: UUID | None = None,
    ) -> InterpretationOutcome:
        """Transition BLOCKED or RETRYING → FAILED."""
        outcome = self._validate_and_transition(
            source_event_id, OutcomeStatus.FAILED,
        )
        ft_value = failure_type.value if isinstance(failure_type, FailureType) else failure_type
        from_status = outcome.status_str
        outcome.status = OutcomeStatus.FAILED
        outcome.failure_type = ft_value
        outcome.failure_message = failure_message
        outcome.reason_code = reason_code
        outcome.reason_detail = reason_detail
        outcome.engine_traces_ref = engine_traces_ref
        outcome.updated_at = self._clock.now()

        self._session.flush()
        logger.info(
            "outcome_transitioned",
            extra={
                "from_status": from_status,
                "to_status": "failed",
                "source_event_id": str(source_event_id),
                "failure_type": ft_value,
            },
        )
        return outcome

    def transition_to_retrying(
        self,
        source_event_id: UUID,
    ) -> InterpretationOutcome:
        """Transition FAILED → RETRYING. Increments retry_count."""
        outcome = self._validate_and_transition(
            source_event_id, OutcomeStatus.RETRYING,
        )
        from_status = outcome.status_str
        outcome.status = OutcomeStatus.RETRYING
        outcome.retry_count += 1
        outcome.updated_at = self._clock.now()

        self._session.flush()
        logger.info(
            "outcome_transitioned",
            extra={
                "from_status": from_status,
                "to_status": "retrying",
                "source_event_id": str(source_event_id),
                "retry_count": outcome.retry_count,
            },
        )
        return outcome

    def transition_to_abandoned(
        self,
        source_event_id: UUID,
        reason_code: str | None = None,
        reason_detail: dict[str, Any] | None = None,
    ) -> InterpretationOutcome:
        """Transition FAILED → ABANDONED (terminal)."""
        outcome = self._validate_and_transition(
            source_event_id, OutcomeStatus.ABANDONED,
        )
        from_status = outcome.status_str
        outcome.status = OutcomeStatus.ABANDONED
        if reason_code:
            outcome.reason_code = reason_code
        if reason_detail:
            outcome.reason_detail = reason_detail
        outcome.updated_at = self._clock.now()

        self._session.flush()
        logger.info(
            "outcome_transitioned",
            extra={
                "from_status": from_status,
                "to_status": "abandoned",
                "source_event_id": str(source_event_id),
            },
        )
        return outcome

    # -----------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------

    def get_outcome(self, source_event_id: UUID) -> InterpretationOutcome | None:
        """Get outcome for an event, if it exists."""
        return self._session.execute(
            select(InterpretationOutcome).where(
                InterpretationOutcome.source_event_id == source_event_id
            )
        ).scalar_one_or_none()

    def query_failed(
        self,
        failure_type: FailureType | str | None = None,
        profile_id: str | None = None,
        actor_id: UUID | None = None,
        limit: int = 100,
    ) -> list[InterpretationOutcome]:
        """Query FAILED outcomes for the work queue.

        Filters:
            failure_type: Filter by failure classification.
            profile_id: Filter by the policy that processed the event.
            actor_id: Filter by actor who initiated the event.
            limit: Maximum number of results (default 100).
        """
        stmt = (
            select(InterpretationOutcome)
            .where(InterpretationOutcome.status == OutcomeStatus.FAILED.value)
            .order_by(InterpretationOutcome.created_at.asc())
        )
        if failure_type is not None:
            ft_value = failure_type.value if isinstance(failure_type, FailureType) else failure_type
            stmt = stmt.where(InterpretationOutcome.failure_type == ft_value)
        if profile_id is not None:
            stmt = stmt.where(InterpretationOutcome.profile_id == profile_id)
        if actor_id is not None:
            stmt = stmt.where(InterpretationOutcome.actor_id == actor_id)
        stmt = stmt.limit(limit)
        return list(self._session.execute(stmt).scalars().all())

    def query_actionable(self, limit: int = 100) -> list[InterpretationOutcome]:
        """Query all actionable outcomes (FAILED or BLOCKED)."""
        stmt = (
            select(InterpretationOutcome)
            .where(
                InterpretationOutcome.status.in_([
                    OutcomeStatus.FAILED.value,
                    OutcomeStatus.BLOCKED.value,
                ])
            )
            .order_by(InterpretationOutcome.created_at.asc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars().all())

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    def _check_not_exists(self, source_event_id: UUID) -> None:
        """Check that no outcome exists for this event."""
        existing = self.get_outcome(source_event_id)
        if existing:
            raise OutcomeAlreadyExistsError(source_event_id, existing.status)

    def _get_existing(self, source_event_id: UUID) -> InterpretationOutcome:
        """Get existing outcome or raise."""
        outcome = self.get_outcome(source_event_id)
        if not outcome:
            raise ValueError(f"No outcome found for event {source_event_id}")
        return outcome
