"""
OutcomeRecorder service for recording interpretation outcomes.

Records the terminal state of event interpretation:
- POSTED: Successfully posted to all required ledgers
- BLOCKED: Valid event, cannot process yet
- REJECTED: Invalid economic reality
- PROVISIONAL: Recorded provisionally, awaiting confirmation
- NON_POSTING: Valid, but no financial effect per policy

Invariant P15: Every accepted BusinessEvent has exactly one InterpretationOutcome.
Invariant L5: No journal rows without POSTED outcome; no POSTED outcome without all journal rows.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock
from finance_kernel.logging_config import get_logger
from finance_kernel.models.interpretation_outcome import (
    InterpretationOutcome,
    OutcomeStatus,
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

    def transition_to_posted(
        self,
        source_event_id: UUID,
        econ_event_id: UUID,
        journal_entry_ids: list[UUID],
    ) -> InterpretationOutcome:
        """
        Transition a BLOCKED or PROVISIONAL outcome to POSTED.

        Args:
            source_event_id: The source event ID.
            econ_event_id: The economic event ID.
            journal_entry_ids: List of journal entry IDs.

        Returns:
            The updated InterpretationOutcome.

        Raises:
            InvalidOutcomeTransitionError: If transition is not allowed.
        """
        outcome = self._get_existing(source_event_id)

        if outcome.status not in (OutcomeStatus.BLOCKED, OutcomeStatus.PROVISIONAL):
            raise InvalidOutcomeTransitionError(
                source_event_id,
                outcome.status,
                OutcomeStatus.POSTED,
            )

        from_status = outcome.status.value
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
        """
        Transition a BLOCKED or PROVISIONAL outcome to REJECTED.

        Args:
            source_event_id: The source event ID.
            reason_code: Machine-readable reason code.
            reason_detail: Additional details.

        Returns:
            The updated InterpretationOutcome.

        Raises:
            InvalidOutcomeTransitionError: If transition is not allowed.
        """
        outcome = self._get_existing(source_event_id)

        if outcome.status not in (OutcomeStatus.BLOCKED, OutcomeStatus.PROVISIONAL):
            raise InvalidOutcomeTransitionError(
                source_event_id,
                outcome.status,
                OutcomeStatus.REJECTED,
            )

        from_status = outcome.status.value
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

    def get_outcome(self, source_event_id: UUID) -> InterpretationOutcome | None:
        """Get outcome for an event, if it exists."""
        return self._session.execute(
            select(InterpretationOutcome).where(
                InterpretationOutcome.source_event_id == source_event_id
            )
        ).scalar_one_or_none()

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
