"""InterpretationCoordinator -- L5 strengthened atomicity service."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.engine_types import EngineDispatchResult
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilderResult,
)
from finance_kernel.logging_config import LogContext, get_logger
from finance_kernel.models.economic_event import EconomicEvent
from finance_kernel.models.interpretation_outcome import (
    InterpretationOutcome,
    OutcomeStatus,
)
from finance_kernel.services.journal_writer import (
    JournalWriter,
    JournalWriteResult,
    WriteStatus,
)
from finance_kernel.services.log_capture import LogCapture
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.utils.hashing import canonicalize_json

logger = get_logger("services.interpretation_coordinator")


@dataclass(frozen=True)
class InterpretationResult:
    """Result of a complete interpretation and posting operation."""

    success: bool
    outcome: InterpretationOutcome | None = None
    economic_event: EconomicEvent | None = None
    journal_result: JournalWriteResult | None = None
    engine_result: EngineDispatchResult | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def posted(
        cls,
        outcome: InterpretationOutcome,
        economic_event: EconomicEvent,
        journal_result: JournalWriteResult,
        engine_result: EngineDispatchResult | None = None,
    ) -> InterpretationResult:
        """Successfully posted."""
        return cls(
            success=True,
            outcome=outcome,
            economic_event=economic_event,
            journal_result=journal_result,
            engine_result=engine_result,
        )

    @classmethod
    def rejected(
        cls,
        outcome: InterpretationOutcome,
        error_code: str,
        error_message: str,
    ) -> InterpretationResult:
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
    ) -> InterpretationResult:
        """Blocked -- valid but cannot process yet."""
        return cls(
            success=False,
            outcome=outcome,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def failure(
        cls, error_code: str, error_message: str
    ) -> InterpretationResult:
        """General failure."""
        return cls(
            success=False,
            error_code=error_code,
            error_message=error_message,
        )


class InterpretationCoordinator:
    """Coordinates interpretation and posting with L5 atomicity."""

    def __init__(
        self,
        session: Session,
        journal_writer: JournalWriter,
        outcome_recorder: OutcomeRecorder,
        clock: Clock | None = None,
        engine_dispatcher: EngineDispatcher | None = None,
    ):
        self._session = session
        self._journal_writer = journal_writer
        self._outcome_recorder = outcome_recorder
        self._clock = clock or SystemClock()
        self._engine_dispatcher = engine_dispatcher

    def interpret_and_post(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
        compiled_policy: Any | None = None,
        event_payload: dict[str, Any] | None = None,
        preamble_log: list[dict] | None = None,
        policy_fingerprint: str | None = None,
        profile_source: str | None = None,
    ) -> InterpretationResult:
        """Interpret an event and post to ledgers atomically.

        L5 Compliance: All operations happen in the current transaction.
        The caller must commit to finalize or rollback to abort.

        preamble_log: Optional records (e.g. workflow transition outcomes) to
        prepend to the decision_log for traceability and lookback.
        policy_fingerprint: Optional canonical_fingerprint of the config pack (audit).
        profile_source: Optional source of the profile (e.g. compiled_policy_pack).
        """
        correlation_id = str(_uuid4())
        capture = LogCapture()
        capture.install()
        try:
            with LogContext.bind(
                correlation_id=correlation_id,
                event_id=str(accounting_intent.source_event_id),
                trace_id=str(trace_id) if trace_id else None,
            ):
                LogContext.set_snapshot()
                t0 = time.monotonic()
                try:
                    result = self._do_interpret_and_post(
                        meaning_result=meaning_result,
                        accounting_intent=accounting_intent,
                        actor_id=actor_id,
                        trace_id=trace_id,
                        compiled_policy=compiled_policy,
                        event_payload=event_payload,
                        policy_fingerprint=policy_fingerprint,
                        profile_source=profile_source,
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

                    # Persist decision journal on the outcome (preamble + capture)
                    if result.outcome is not None:
                        result.outcome.decision_log = (
                            list(preamble_log) if preamble_log else []
                        ) + capture.take_records()
                        self._session.flush()

                    return result
                except Exception:
                    duration_ms = round((time.monotonic() - t0) * 1000, 2)
                    logger.error(
                        "interpretation_failed",
                        extra={"duration_ms": duration_ms},
                        exc_info=True,
                    )
                    raise
                finally:
                    LogContext.clear_snapshot()
        finally:
            capture.uninstall()

    def _do_interpret_and_post(
        self,
        meaning_result: MeaningBuilderResult,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
        compiled_policy: Any | None = None,
        event_payload: dict[str, Any] | None = None,
        policy_fingerprint: str | None = None,
        profile_source: str | None = None,
    ) -> InterpretationResult:
        """Internal interpret and post logic (within LogContext)."""
        logger.info(
            "interpretation_started",
            extra={
                "source_event_id": str(accounting_intent.source_event_id),
                "profile_id": accounting_intent.profile_id,
                "profile_version": accounting_intent.profile_version,
                "econ_event_id": str(accounting_intent.econ_event_id),
                "effective_date": str(accounting_intent.effective_date),
                "ledger_count": len(accounting_intent.ledger_intents),
                "profile_source": profile_source or "policy_selector",
                "policy_fingerprint": policy_fingerprint,
            },
        )

        # Log the configuration snapshot in force at interpretation time
        snapshot = accounting_intent.snapshot
        logger.info(
            "config_in_force",
            extra={
                "source_event_id": str(accounting_intent.source_event_id),
                "profile_id": accounting_intent.profile_id,
                "profile_version": accounting_intent.profile_version,
                "profile_source": profile_source or "policy_selector",
                "policy_fingerprint": policy_fingerprint,
                "coa_version": snapshot.coa_version if snapshot else None,
                "dimension_schema_version": snapshot.dimension_schema_version if snapshot else None,
                "rounding_policy_version": snapshot.rounding_policy_version if snapshot else None,
                "currency_registry_version": snapshot.currency_registry_version if snapshot else None,
            },
        )

        # --- Engine dispatch (before guard checks and journal write) ---
        engine_result: EngineDispatchResult | None = None
        if (
            self._engine_dispatcher is not None
            and compiled_policy is not None
            and event_payload is not None
            and getattr(compiled_policy, "required_engines", None)
        ):
            logger.info(
                "engine_dispatch_started",
                extra={
                    "policy_name": getattr(compiled_policy, "name", "unknown"),
                    "required_engines": list(compiled_policy.required_engines),
                },
            )
            engine_result = self._engine_dispatcher.dispatch(
                compiled_policy, event_payload,
            )

            # INVARIANT: No engine run → no success trace → no all_succeeded=True.
            # Reject results that claim success without one success trace per required engine.
            required = list(compiled_policy.required_engines)
            if required:
                traces_ok = (
                    len(engine_result.traces) == len(required)
                    and all(t.success for t in engine_result.traces)
                )
                if not traces_ok or not engine_result.all_succeeded:
                    error_msg = (
                        "; ".join(engine_result.errors)
                        if engine_result.errors
                        else (
                            f"Expected {len(required)} success trace(s), got "
                            f"{len(engine_result.traces)} (all_succeeded={engine_result.all_succeeded})"
                        )
                    )
                    logger.error(
                        "engine_dispatch_failed",
                        extra={
                            "errors": list(engine_result.errors),
                            "policy_name": getattr(compiled_policy, "name", "unknown"),
                            "required_engines": required,
                            "trace_count": len(engine_result.traces),
                        },
                    )
                    return InterpretationResult.failure(
                        error_code="ENGINE_DISPATCH_FAILED",
                        error_message=f"Engine dispatch failed: {error_msg}",
                    )

            # Log each engine trace record to the decision journal
            for trace in engine_result.traces:
                logger.info(
                    "FINANCE_ENGINE_DISPATCH",
                    extra={
                        "trace_type": "FINANCE_ENGINE_DISPATCH",
                        "engine_name": trace.engine_name,
                        "engine_version": trace.engine_version,
                        "input_fingerprint": trace.input_fingerprint,
                        "duration_ms": trace.duration_ms,
                        "success": trace.success,
                        "error": trace.error,
                        "parameters": trace.parameters_used,
                    },
                )

            logger.info(
                "engine_dispatch_completed",
                extra={
                    "engine_count": len(engine_result.engine_outputs),
                    "engines": list(engine_result.engine_outputs.keys()),
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

        # INVARIANT: P11 -- Multi-ledger postings from single intent are atomic
        journal_result = self._journal_writer.write(
            intent=accounting_intent,
            actor_id=actor_id,
            event_type=meaning_result.economic_event.economic_type,
        )

        if not journal_result.is_success:
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

        # INVARIANT: L5 -- POSTED outcome in same transaction as journal writes
        # INVARIANT: P15 -- Exactly one InterpretationOutcome per event
        outcome = self._outcome_recorder.record_posted(
            source_event_id=accounting_intent.source_event_id,
            profile_id=accounting_intent.profile_id,
            profile_version=accounting_intent.profile_version,
            econ_event_id=economic_event.id,
            journal_entry_ids=list(journal_result.entry_ids),
            trace_id=trace_id,
        )

        # INVARIANT: L5 -- Both outcome and journal entries must exist together
        assert outcome is not None, "L5 violation: POSTED requires an outcome record"
        assert journal_result.is_success, "L5 violation: POSTED requires successful journal write"

        entry_ids = [str(eid) for eid in journal_result.entry_ids]

        ledger_ids = [li.ledger_id for li in accounting_intent.ledger_intents]
        logger.info(
            "interpretation_posted",
            extra={
                "outcome_id": str(outcome.id) if hasattr(outcome, "id") else None,
                "entry_ids": entry_ids,
                "entry_count": len(entry_ids),
                "ledger_ids": ledger_ids,
            },
        )

        # Compute reproducibility proof: hash of input intent -> hash of output entries
        input_hash = hashlib.sha256(
            canonicalize_json({
                "source_event_id": str(accounting_intent.source_event_id),
                "profile_id": accounting_intent.profile_id,
                "profile_version": accounting_intent.profile_version,
                "effective_date": str(accounting_intent.effective_date),
                "ledger_intents": [
                    {
                        "ledger_id": li.ledger_id,
                        "line_count": len(li.lines),
                    }
                    for li in accounting_intent.ledger_intents
                ],
            }).encode()
        ).hexdigest()

        output_hash = hashlib.sha256(
            canonicalize_json({
                "entry_ids": entry_ids,
                "outcome_status": "POSTED",
            }).encode()
        ).hexdigest()

        logger.info(
            "reproducibility_proof",
            extra={
                "source_event_id": str(accounting_intent.source_event_id),
                "input_hash": input_hash,
                "output_hash": output_hash,
                "profile_id": accounting_intent.profile_id,
                "profile_version": accounting_intent.profile_version,
            },
        )

        # Emit FINANCE_KERNEL_TRACE
        engine_trace_summary = None
        if engine_result is not None:
            engine_trace_summary = {
                "engines_invoked": list(engine_result.engine_outputs.keys()),
                "all_succeeded": engine_result.all_succeeded,
                "traces": [
                    {
                        "engine": t.engine_name,
                        "version": t.engine_version,
                        "fingerprint": t.input_fingerprint,
                        "duration_ms": t.duration_ms,
                        "success": t.success,
                    }
                    for t in engine_result.traces
                ],
            }

        logger.info(
            "FINANCE_KERNEL_TRACE",
            extra={
                "trace_type": "FINANCE_KERNEL_TRACE",
                "source_event_id": str(accounting_intent.source_event_id),
                "policy_name": accounting_intent.profile_id,
                "policy_version": accounting_intent.profile_version,
                "journal_entry_ids": entry_ids,
                "outcome_status": "POSTED",
                "input_hash": input_hash,
                "output_hash": output_hash,
                "engine_dispatch": engine_trace_summary,
            },
        )

        return InterpretationResult.posted(
            outcome=outcome,
            economic_event=economic_event,
            journal_result=journal_result,
            engine_result=engine_result,
        )

    def post_from_intent(
        self,
        economic_event_data: EconomicEventData,
        accounting_intent: AccountingIntent,
        actor_id: UUID,
        trace_id: UUID | None = None,
    ) -> InterpretationResult:
        """Post from an accounting intent without MeaningBuilder."""
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
        """Transition a BLOCKED outcome to POSTED."""
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

        if existing_outcome.status_enum != OutcomeStatus.BLOCKED:
            return InterpretationResult.failure(
                "INVALID_TRANSITION",
                f"Cannot transition from {existing_outcome.status_str} to POSTED",
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
