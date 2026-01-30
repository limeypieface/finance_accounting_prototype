"""
InterpretationCoordinator - L5 Strengthened Atomicity Service.

Coordinates MeaningBuilder, JournalWriter, and OutcomeRecorder to ensure:
- L5: No journal rows without a matching POSTED outcome
- L5: No POSTED outcome without all journal rows
- P11: Multi-ledger postings are atomic

This is the integration point between the economic and finance layers.
It enforces that the InterpretationOutcome=POSTED is committed in the
same transaction as all JournalEntry writes.

Usage:
    coordinator = InterpretationCoordinator(
        session=session,
        journal_writer=writer,
        outcome_recorder=recorder,
        clock=clock,
    )

    result = coordinator.interpret_and_post(
        meaning_result=meaning_result,
        accounting_intent=intent,
        actor_id=actor_id,
    )

    if result.success:
        session.commit()  # L5: All or nothing
    else:
        session.rollback()
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from finance_kernel.logging_config import get_logger, LogContext

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilderResult,
)
from finance_kernel.models.economic_event import EconomicEvent
from finance_kernel.models.interpretation_outcome import (
    InterpretationOutcome,
    OutcomeStatus,
)
from finance_kernel.services.journal_writer import (
    JournalWriteResult,
    JournalWriter,
    WriteStatus,
)
from finance_kernel.services.outcome_recorder import OutcomeRecorder

logger = get_logger("services.interpretation_coordinator")


@dataclass(frozen=True)
class InterpretationResult:
    """
    Result of a complete interpretation and posting operation.

    Contains the final outcome status and all created artifacts.
    """

    success: bool
    outcome: InterpretationOutcome | None = None
    economic_event: EconomicEvent | None = None
    journal_result: JournalWriteResult | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def posted(
        cls,
        outcome: InterpretationOutcome,
        economic_event: EconomicEvent,
        journal_result: JournalWriteResult,
    ) -> "InterpretationResult":
        """Successfully posted."""
        return cls(
            success=True,
            outcome=outcome,
            economic_event=economic_event,
            journal_result=journal_result,
        )

    @classmethod
    def rejected(
        cls,
        outcome: InterpretationOutcome,
        error_code: str,
        error_message: str,
    ) -> "InterpretationResult":
        """Rejected by guard or validation."""
        return cls(
            success=False,
            outcome=outcome,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def blocked(
        cls,
        outcome: InterpretationOutcome,
        error_code: str,
        error_message: str,
    ) -> "InterpretationResult":
        """Blocked - valid but cannot process yet."""
        return cls(
            success=False,
            outcome=outcome,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def failure(
        cls, error_code: str, error_message: str
    ) -> "InterpretationResult":
        """General failure."""
        return cls(
            success=False,
            error_code=error_code,
            error_message=error_message,
        )


class InterpretationCoordinator:
    """
    Coordinates interpretation and posting with L5 atomicity.

    This service ensures that:
    1. EconomicEvent is created
    2. JournalEntry records are created for all ledgers
    3. InterpretationOutcome=POSTED is recorded
    4. All three happen in the same transaction (L5)

    If any step fails, the entire operation fails and nothing is persisted.
    The caller is responsible for commit/rollback.
    """

    def __init__(
        self,
        session: Session,
        journal_writer: JournalWriter,
        outcome_recorder: OutcomeRecorder,
        clock: Clock | None = None,
    ):
        """
        Initialize the coordinator.

        Args:
            session: SQLAlchemy session.
            journal_writer: JournalWriter for creating entries.
            outcome_recorder: OutcomeRecorder for recording outcomes.
            clock: Clock for timestamps.
        """
        self._session = session
        self._journal_writer = journal_writer
        self._outcome_recorder = outcome_recorder
        self._clock = clock or SystemClock()

    def interpret_and_post(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
    ) -> InterpretationResult:
        """
        Interpret an event and post to ledgers atomically.

        L5 Compliance: All operations happen in the current transaction.
        The caller must commit to finalize or rollback to abort.

        Args:
            meaning_result: Result from MeaningBuilder.
            accounting_intent: Intent to post.
            actor_id: Who is performing the operation.
            trace_id: Optional trace ID.

        Returns:
            InterpretationResult with outcome and artifacts.
        """
        correlation_id = str(_uuid4())
        with LogContext.bind(
            correlation_id=correlation_id,
            event_id=str(accounting_intent.source_event_id),
            trace_id=str(trace_id) if trace_id else None,
        ):
            t0 = time.monotonic()
            try:
                result = self._do_interpret_and_post(
                    meaning_result=meaning_result,
                    accounting_intent=accounting_intent,
                    actor_id=actor_id,
                    trace_id=trace_id,
                )
                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                logger.info(
                    "interpretation_completed",
                    extra={
                        "success": result.success,
                        "duration_ms": duration_ms,
                        "error_code": result.error_code,
                    },
                )
                return result
            except Exception:
                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                logger.error(
                    "interpretation_failed",
                    extra={"duration_ms": duration_ms},
                    exc_info=True,
                )
                raise

    def _do_interpret_and_post(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
    ) -> InterpretationResult:
        """Internal interpret and post logic (within LogContext)."""
        logger.info(
            "interpretation_started",
            extra={
                "source_event_id": str(accounting_intent.source_event_id),
                "profile_id": accounting_intent.profile_id,
            },
        )

        # Handle guard results first
        if meaning_result.guard_result and meaning_result.guard_result.rejected:
            return self._handle_rejection(
                meaning_result=meaning_result,
                accounting_intent=accounting_intent,
                trace_id=trace_id,
            )

        if meaning_result.guard_result and meaning_result.guard_result.blocked:
            return self._handle_block(
                meaning_result=meaning_result,
                accounting_intent=accounting_intent,
                trace_id=trace_id,
            )

        # Proceed with posting
        if not meaning_result.success or not meaning_result.economic_event:
            return InterpretationResult.failure(
                "INTERPRETATION_FAILED",
                "MeaningBuilder did not produce economic event",
            )

        # 1. Create EconomicEvent
        economic_event = self._create_economic_event(
            meaning_result.economic_event, trace_id
        )

        # 2. Write journal entries (P11: atomic multi-ledger)
        journal_result = self._journal_writer.write(
            intent=accounting_intent,
            actor_id=actor_id,
            event_type=meaning_result.economic_event.economic_type,
        )

        if not journal_result.is_success:
            # Journal write failed - record rejection or block
            if journal_result.status == WriteStatus.ROLE_RESOLUTION_FAILED:
                return self._record_block_for_resolution(
                    meaning_result=meaning_result,
                    accounting_intent=accounting_intent,
                    journal_result=journal_result,
                    trace_id=trace_id,
                )
            else:
                return self._record_rejection_for_write_failure(
                    meaning_result=meaning_result,
                    accounting_intent=accounting_intent,
                    journal_result=journal_result,
                    trace_id=trace_id,
                )

        # 3. Record POSTED outcome (L5: same transaction as journal writes)
        outcome = self._outcome_recorder.record_posted(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            econ_event_id=economic_event.id,
            journal_entry_ids=list(journal_result.entry_ids),
            trace_id=trace_id,
        )

        entry_ids = [str(eid) for eid in journal_result.entry_ids]

        logger.info(
            "interpretation_posted",
            extra={
                "outcome_id": str(outcome.id) if hasattr(outcome, "id") else None,
                "entry_ids": entry_ids,
            },
        )

        # Emit FINANCE_KERNEL_TRACE
        logger.info(
            "FINANCE_KERNEL_TRACE",
            extra={
                "trace_type": "FINANCE_KERNEL_TRACE",
                "source_event_id": str(accounting_intent.source_event_id),
                "policy_name": accounting_intent.profile_id,
                "policy_version": accounting_intent.profile_version,
                "journal_entry_ids": entry_ids,
                "outcome_status": "POSTED",
            },
        )

        return InterpretationResult.posted(
            outcome=outcome,
            economic_event=economic_event,
            journal_result=journal_result,
        )

    def post_from_intent(
        self,
        economic_event_data: EconomicEventData,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
    ) -> InterpretationResult:
        """
        Post from an accounting intent without MeaningBuilder.

        Used when the economic event data is already prepared.

        Args:
            economic_event_data: Prepared economic event data.
            accounting_intent: Intent to post.
            actor_id: Who is performing the operation.
            trace_id: Optional trace ID.

        Returns:
            InterpretationResult with outcome and artifacts.
        """
        # 1. Create EconomicEvent
        economic_event = self._create_economic_event(
            economic_event_data, trace_id
        )

        # 2. Write journal entries
        journal_result = self._journal_writer.write(
            intent=accounting_intent,
            actor_id=actor_id,
            event_type=economic_event_data.economic_type,
        )

        if not journal_result.is_success:
            if journal_result.status == WriteStatus.ROLE_RESOLUTION_FAILED:
                # Block for missing role bindings
                outcome = self._outcome_recorder.record_blocked(
                    source_event_id=accounting_intent.source_event_id,
                    profile_id=accounting_intent.profile_id,
                    profile_version=accounting_intent.profile_version,
                    reason_code="ROLE_RESOLUTION_BLOCKED",
                    reason_detail={
                        "unresolved_roles": journal_result.unresolved_roles,
                        "message": journal_result.error_message,
                    },
                    trace_id=trace_id,
                )
                return InterpretationResult.blocked(
                    outcome=outcome,
                    error_code=journal_result.error_code or "UNKNOWN",
                    error_message=journal_result.error_message or "Unknown error",
                )
            else:
                # Reject for validation/other failures
                outcome = self._outcome_recorder.record_rejected(
                    source_event_id=accounting_intent.source_event_id,
                    profile_id=accounting_intent.profile_id,
                    profile_version=accounting_intent.profile_version,
                    reason_code=journal_result.error_code or "WRITE_FAILED",
                    reason_detail={"message": journal_result.error_message},
                    trace_id=trace_id,
                )
                return InterpretationResult.rejected(
                    outcome=outcome,
                    error_code=journal_result.error_code or "UNKNOWN",
                    error_message=journal_result.error_message or "Unknown error",
                )

        # 3. Record POSTED outcome
        outcome = self._outcome_recorder.record_posted(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            econ_event_id=economic_event.id,
            journal_entry_ids=list(journal_result.entry_ids),
            trace_id=trace_id,
        )

        return InterpretationResult.posted(
            outcome=outcome,
            economic_event=economic_event,
            journal_result=journal_result,
        )

    def _create_economic_event(
        self,
        data: EconomicEventData,
        trace_id: UUID | None,
    ) -> EconomicEvent:
        """Create and persist an EconomicEvent."""
        event = EconomicEvent(
            id=uuid4(),
            source_event_id=data.source_event_id,
            economic_type=data.economic_type,
            quantity=data.quantity,
            dimensions=data.dimensions,
            effective_date=data.effective_date,
            profile_id=data.profile_id,
            profile_version=data.profile_version,
            profile_hash=data.profile_hash,
            trace_id=trace_id or data.trace_id,
            created_at=self._clock.now(),
        )

        # Copy snapshot fields if present
        if data.snapshot:
            event.coa_version = data.snapshot.coa_version
            event.dimension_schema_version = data.snapshot.dimension_schema_version
            event.currency_registry_version = data.snapshot.currency_registry_version
            event.fx_policy_version = data.snapshot.fx_policy_version

        self._session.add(event)
        self._session.flush()
        return event

    def _handle_rejection(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        trace_id: UUID | None,
    ) -> InterpretationResult:
        """Handle guard rejection."""
        guard_result = meaning_result.guard_result
        assert guard_result is not None

        logger.warning(
            "interpretation_guard_rejected",
            extra={"reason_code": guard_result.reason_code or "GUARD_REJECTED"},
        )

        outcome = self._outcome_recorder.record_rejected(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            reason_code=guard_result.reason_code or "GUARD_REJECTED",
            reason_detail=guard_result.reason_detail,
            trace_id=trace_id,
        )

        # Emit FINANCE_KERNEL_TRACE for rejection
        logger.info(
            "FINANCE_KERNEL_TRACE",
            extra={
                "trace_type": "FINANCE_KERNEL_TRACE",
                "source_event_id": str(accounting_intent.source_event_id),
                "policy_name": accounting_intent.profile_id,
                "policy_version": accounting_intent.profile_version,
                "outcome_status": "REJECTED",
                "reason_code": guard_result.reason_code or "GUARD_REJECTED",
            },
        )

        return InterpretationResult.rejected(
            outcome=outcome,
            error_code=guard_result.reason_code or "GUARD_REJECTED",
            error_message=guard_result.triggered_guard.message
            if guard_result.triggered_guard
            else "Guard rejected",
        )

    def _handle_block(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        trace_id: UUID | None,
    ) -> InterpretationResult:
        """Handle guard block."""
        guard_result = meaning_result.guard_result
        assert guard_result is not None

        logger.warning(
            "interpretation_guard_blocked",
            extra={"reason_code": guard_result.reason_code or "GUARD_BLOCKED"},
        )

        outcome = self._outcome_recorder.record_blocked(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            reason_code=guard_result.reason_code or "GUARD_BLOCKED",
            reason_detail=guard_result.reason_detail,
            trace_id=trace_id,
        )

        # Emit FINANCE_KERNEL_TRACE for block
        logger.info(
            "FINANCE_KERNEL_TRACE",
            extra={
                "trace_type": "FINANCE_KERNEL_TRACE",
                "source_event_id": str(accounting_intent.source_event_id),
                "policy_name": accounting_intent.profile_id,
                "policy_version": accounting_intent.profile_version,
                "outcome_status": "BLOCKED",
                "reason_code": guard_result.reason_code or "GUARD_BLOCKED",
            },
        )

        return InterpretationResult.blocked(
            outcome=outcome,
            error_code=guard_result.reason_code or "GUARD_BLOCKED",
            error_message=guard_result.triggered_guard.message
            if guard_result.triggered_guard
            else "Guard blocked",
        )

    def _record_block_for_resolution(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        journal_result: JournalWriteResult,
        trace_id: UUID | None,
    ) -> InterpretationResult:
        """Record BLOCKED for role resolution failure."""
        outcome = self._outcome_recorder.record_blocked(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            reason_code="ROLE_RESOLUTION_BLOCKED",
            reason_detail={
                "unresolved_roles": journal_result.unresolved_roles,
                "message": journal_result.error_message,
            },
            trace_id=trace_id,
        )

        return InterpretationResult.blocked(
            outcome=outcome,
            error_code="ROLE_RESOLUTION_BLOCKED",
            error_message=journal_result.error_message or "Role resolution failed",
        )

    def _record_rejection_for_write_failure(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        journal_result: JournalWriteResult,
        trace_id: UUID | None,
    ) -> InterpretationResult:
        """Record REJECTED for journal write failure."""
        logger.warning(
            "interpretation_write_failed",
            extra={"error_code": journal_result.error_code or "WRITE_FAILED"},
        )
        outcome = self._outcome_recorder.record_rejected(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            reason_code=journal_result.error_code or "WRITE_FAILED",
            reason_detail={"message": journal_result.error_message},
            trace_id=trace_id,
        )

        return InterpretationResult.rejected(
            outcome=outcome,
            error_code=journal_result.error_code or "WRITE_FAILED",
            error_message=journal_result.error_message or "Journal write failed",
        )

    def transition_blocked_to_posted(
        self,
        source_event_id: UUID,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
    ) -> InterpretationResult:
        """
        Transition a BLOCKED outcome to POSTED.

        Used when the blocking condition is resolved (e.g., role binding added).

        Args:
            source_event_id: The blocked event's source ID.
            accounting_intent: The updated intent.
            actor_id: Who is performing the operation.
            trace_id: Optional trace ID.

        Returns:
            InterpretationResult with updated outcome.
        """
        logger.info(
            "blocked_to_posted_transition",
            extra={"source_event_id": str(source_event_id)},
        )

        # Get existing outcome
        existing_outcome = self._outcome_recorder.get_outcome(source_event_id)
        if not existing_outcome:
            return InterpretationResult.failure(
                "NO_OUTCOME",
                f"No outcome found for event {source_event_id}",
            )

        if existing_outcome.status != OutcomeStatus.BLOCKED:
            return InterpretationResult.failure(
                "INVALID_TRANSITION",
                f"Cannot transition from {existing_outcome.status.value} to POSTED",
            )

        # Create economic event
        event = EconomicEvent(
            id=uuid4(),
            source_event_id=source_event_id,
            economic_type="unknown",  # Would need to be passed in
            effective_date=accounting_intent.effective_date,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            trace_id=trace_id,
            created_at=self._clock.now(),
        )
        self._session.add(event)
        self._session.flush()

        # Write journal entries
        journal_result = self._journal_writer.write(
            intent=accounting_intent,
            actor_id=actor_id,
        )

        if not journal_result.is_success:
            return InterpretationResult.failure(
                journal_result.error_code or "WRITE_FAILED",
                journal_result.error_message or "Journal write failed",
            )

        # Transition to POSTED
        outcome = self._outcome_recorder.transition_to_posted(
            source_event_id=source_event_id,
            econ_event_id=event.id,
            journal_entry_ids=list(journal_result.entry_ids),
        )

        return InterpretationResult.posted(
            outcome=outcome,
            economic_event=event,
            journal_result=journal_result,
        )
