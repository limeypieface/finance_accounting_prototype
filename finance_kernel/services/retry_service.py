"""
RetryService -- financial exception retry lifecycle management.

Responsibility:
    Manages the retry lifecycle for FAILED interpretation outcomes:
    initiation, success/failure completion, and permanent abandonment.
    Enforces retry limits and valid state transitions.

Architecture position:
    Kernel > Services -- imperative shell.
    Called by operational tooling (work queues, interactive CLI) when
    a FAILED outcome is ready for retry after configuration or master
    data has been corrected.

Invariants enforced:
    MAX_RETRIES -- Safety limit (10) prevents infinite retry loops.
    P15 -- Outcome uniqueness maintained via OutcomeRecorder transitions
           (no duplicate outcomes created during retry).
    R1  -- Original event payload is immutable between retries (the
           retry contract forbids payload mutation).

Failure modes:
    - RetryNotAllowedError: Outcome is not FAILED, or MAX_RETRIES
      exceeded, or no outcome exists for the event.
    - InvalidOutcomeTransitionError (from OutcomeRecorder): Transition
      not permitted from current state.

Audit relevance:
    Every retry initiation, success, failure, and abandonment is logged
    with source_event_id, retry_actor_id, retry_count, and failure_type.

Retry contract:
  - Allowed to change between retries:
      * Policy configuration (YAML / compiled pack)
      * Reference snapshot
      * External master data (parties, contracts, items)
  - NOT allowed to change:
      * Original event payload
      * Original actor_id
      * Original event timestamp

Usage:
    retry_svc = RetryService(session, outcome_recorder, clock)

    # Initiate retry
    outcome = retry_svc.initiate_retry(
        source_event_id=event_id,
        retry_actor_id=operator_id,
    )
    assert outcome.status == OutcomeStatus.RETRYING

    # After re-running the posting pipeline:
    retry_svc.complete_retry_success(
        source_event_id=event_id,
        econ_event_id=econ_event.id,
        journal_entry_ids=[entry.id],
        engine_traces_ref=trace_id,
    )

    # Or if it failed again:
    retry_svc.complete_retry_failure(
        source_event_id=event_id,
        failure_type=FailureType.ENGINE,
        failure_message="Variance engine still cannot compute.",
        engine_traces_ref=trace_id,
    )

    # Or abandon permanently:
    retry_svc.abandon(source_event_id=event_id)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock
from finance_kernel.logging_config import get_logger
from finance_kernel.models.interpretation_outcome import (
    FailureType,
    InterpretationOutcome,
    OutcomeStatus,
)
from finance_kernel.services.outcome_recorder import (
    InvalidOutcomeTransitionError,
    OutcomeRecorder,
)

logger = get_logger("services.retry_service")


class RetryNotAllowedError(Exception):
    """Retry is not allowed for this outcome."""

    code: str = "RETRY_NOT_ALLOWED"

    def __init__(self, source_event_id: UUID, reason: str):
        self.source_event_id = source_event_id
        self.reason = reason
        super().__init__(
            f"Retry not allowed for event {source_event_id}: {reason}"
        )


class RetryService:
    """Manages the retry lifecycle for FAILED interpretation outcomes.

    Contract:
        Accepts a ``source_event_id`` for a FAILED outcome and drives it
        through the retry lifecycle: FAILED -> RETRYING -> (POSTED | FAILED).
        Alternatively, transitions FAILED -> ABANDONED (terminal).

    Guarantees:
        - Only FAILED outcomes may be retried.
        - The original event payload and actor_id are immutable (R1).
        - Each retry increments ``retry_count``.
        - MAX_RETRIES (10) is enforced as a safety limit to prevent
          infinite retry loops.
        - State transitions are validated by OutcomeRecorder.

    Non-goals:
        - Does NOT re-run the posting pipeline (caller's responsibility).
        - Does NOT call ``session.commit()`` -- caller controls boundaries.
        - Does NOT modify the original event payload.
    """

    # INVARIANT: Safety limit -- prevents infinite retry loops
    MAX_RETRIES = 10

    def __init__(
        self,
        session: Session,
        outcome_recorder: OutcomeRecorder,
        clock: Clock,
    ):
        self._session = session
        self._recorder = outcome_recorder
        self._clock = clock

    def initiate_retry(
        self,
        source_event_id: UUID,
        retry_actor_id: UUID | None = None,
    ) -> InterpretationOutcome:
        """Begin a retry attempt. Transitions FAILED -> RETRYING.

        Preconditions:
            - An ``InterpretationOutcome`` exists for ``source_event_id``.
            - Outcome status is FAILED.
            - ``retry_count < MAX_RETRIES``.

        Postconditions:
            - Outcome status is RETRYING.
            - ``retry_count`` is incremented by 1.

        Args:
            source_event_id: Event to retry.
            retry_actor_id: Actor performing the retry (for audit trail).

        Returns:
            The outcome in RETRYING state with incremented retry_count.

        Raises:
            RetryNotAllowedError: If outcome is not FAILED or max retries exceeded.
        """
        outcome = self._recorder.get_outcome(source_event_id)
        if outcome is None:
            raise RetryNotAllowedError(
                source_event_id, "No outcome found for this event"
            )

        if outcome.status_enum != OutcomeStatus.FAILED:
            raise RetryNotAllowedError(
                source_event_id,
                f"Outcome status is {outcome.status_str}, not 'failed'",
            )

        # INVARIANT: MAX_RETRIES -- Safety limit prevents infinite retry loops
        if outcome.retry_count >= self.MAX_RETRIES:
            raise RetryNotAllowedError(
                source_event_id,
                f"Maximum retry count ({self.MAX_RETRIES}) exceeded",
            )

        result = self._recorder.transition_to_retrying(source_event_id)

        logger.info(
            "retry_initiated",
            extra={
                "source_event_id": str(source_event_id),
                "retry_actor_id": str(retry_actor_id) if retry_actor_id else None,
                "retry_count": result.retry_count,
            },
        )
        return result

    def complete_retry_success(
        self,
        source_event_id: UUID,
        econ_event_id: UUID,
        journal_entry_ids: list[UUID],
        engine_traces_ref: UUID | None = None,
    ) -> InterpretationOutcome:
        """Complete a retry successfully. Transitions RETRYING → POSTED.

        Args:
            source_event_id: Event that was retried.
            econ_event_id: Economic event created during retry.
            journal_entry_ids: Journal entries created.
            engine_traces_ref: Engine trace set from this retry attempt.

        Returns:
            The outcome in POSTED state.
        """
        outcome = self._recorder.transition_to_posted(
            source_event_id=source_event_id,
            econ_event_id=econ_event_id,
            journal_entry_ids=journal_entry_ids,
        )

        if engine_traces_ref:
            outcome.engine_traces_ref = engine_traces_ref
            self._session.flush()

        logger.info(
            "retry_succeeded",
            extra={
                "source_event_id": str(source_event_id),
                "retry_count": outcome.retry_count,
            },
        )
        return outcome

    def complete_retry_failure(
        self,
        source_event_id: UUID,
        failure_type: FailureType | str,
        failure_message: str,
        reason_code: str | None = None,
        reason_detail: dict[str, Any] | None = None,
        engine_traces_ref: UUID | None = None,
    ) -> InterpretationOutcome:
        """Complete a retry with failure. Transitions RETRYING → FAILED.

        The outcome returns to FAILED state and can be retried again
        (up to MAX_RETRIES).

        Args:
            source_event_id: Event that was retried.
            failure_type: Classification of the failure.
            failure_message: Human-readable description.
            reason_code: Machine-readable reason code.
            reason_detail: Structured failure details.
            engine_traces_ref: Engine trace set from this retry attempt.

        Returns:
            The outcome in FAILED state.
        """
        outcome = self._recorder.transition_to_failed(
            source_event_id=source_event_id,
            failure_type=failure_type,
            failure_message=failure_message,
            reason_code=reason_code,
            reason_detail=reason_detail,
            engine_traces_ref=engine_traces_ref,
        )

        logger.info(
            "retry_failed",
            extra={
                "source_event_id": str(source_event_id),
                "retry_count": outcome.retry_count,
                "failure_type": outcome.failure_type,
            },
        )
        return outcome

    def abandon(
        self,
        source_event_id: UUID,
        reason_code: str | None = "MANUALLY_ABANDONED",
        reason_detail: dict[str, Any] | None = None,
    ) -> InterpretationOutcome:
        """Permanently abandon a FAILED outcome. Transitions FAILED → ABANDONED.

        ABANDONED is terminal — no further transitions are possible.

        Args:
            source_event_id: Event to abandon.
            reason_code: Why it was abandoned.
            reason_detail: Additional context.

        Returns:
            The outcome in ABANDONED state.
        """
        outcome = self._recorder.transition_to_abandoned(
            source_event_id=source_event_id,
            reason_code=reason_code,
            reason_detail=reason_detail,
        )

        logger.info(
            "outcome_abandoned",
            extra={
                "source_event_id": str(source_event_id),
                "retry_count": outcome.retry_count,
            },
        )
        return outcome
