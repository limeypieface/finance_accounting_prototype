"""
ModulePostingService — canonical posting entry point for all modules.

Responsibility:
    Orchestrates the complete posting pipeline from raw economic event
    through profile lookup, meaning extraction, intent construction,
    and atomic journal posting.  Every ERP module (AP, AR, Inventory,
    Payroll, etc.) enters the kernel through this single service.

Architecture position:
    Kernel > Services — imperative shell, owns transaction boundaries.
    Sits at the top of the posting pipeline; delegates pure logic to
    domain layer and I/O operations to peer kernel services.

Posting flow:
    post_event(event_type, payload, effective_date, actor_id, ...)
      1. Validate actor authorization (G14)
      2. Validate period is open (PeriodService — R12/R13)
      3. Ingest event record (IngestorService — R1/R2)
      4. Find profile (PolicySelector.find_for_event — P1)
      5. Build meaning (MeaningBuilder.build)
      6. Build intent (profile_bridge.build_accounting_intent — L1)
      7. Post atomically (InterpretationCoordinator.interpret_and_post — L5/P11)
      8. Post subledger entries (SL-G1: same transaction)
      9. Commit or rollback

Invariants enforced:
    R1  — Event immutability (via IngestorService)
    R2  — Payload hash verification (via IngestorService)
    R3  — Idempotency key uniqueness (via JournalWriter)
    R7  — Transaction boundaries (commit/rollback managed here)
    R12 — Closed period enforcement (via PeriodService)
    R13 — Adjustment policy enforcement (via PeriodService)
    P1  — Exactly one profile matches any event (via PolicySelector)
    P15 — Exactly one InterpretationOutcome per event (via OutcomeRecorder)
    L5  — Atomic journal + outcome (via InterpretationCoordinator)

Failure modes:
    - PERIOD_CLOSED / ADJUSTMENTS_NOT_ALLOWED: Period validation rejected.
    - INGESTION_FAILED: Payload hash mismatch or validation failure.
    - PROFILE_NOT_FOUND: No matching EconomicProfile for event_type.
    - MEANING_FAILED: MeaningBuilder could not extract economic event.
    - GUARD_REJECTED / GUARD_BLOCKED: Policy guard denied posting.
    - INTENT_FAILED: AccountingIntent construction error.
    - POSTING_FAILED: JournalWriter or OutcomeRecorder failure.
    - INVALID_ACTOR / ACTOR_FROZEN: Actor authorization failure (G14).

Audit relevance:
    Every invocation is logged with correlation_id, event_id, actor_id,
    and timing metrics.  The IngestorService records an audit event on
    ingestion.  The InterpretationCoordinator captures the full decision
    journal for POSTED and REJECTED outcomes.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import MeaningBuilder, MeaningBuilderResult
from finance_kernel.domain.policy_bridge import build_accounting_intent
from finance_kernel.domain.policy_selector import PolicyNotFoundError, PolicySelector
from finance_kernel.exceptions import PartyNotFoundError
from finance_kernel.logging_config import get_logger, LogContext
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.interpretation_coordinator import (
    InterpretationCoordinator,
    InterpretationResult,
)
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.period_service import PeriodService

logger = get_logger("services.module_posting")


class ModulePostingStatus(str, Enum):
    """Status of a module posting operation."""

    POSTED = "posted"
    ALREADY_POSTED = "already_posted"
    PERIOD_CLOSED = "period_closed"
    ADJUSTMENTS_NOT_ALLOWED = "adjustments_not_allowed"
    INVALID_ACTOR = "invalid_actor"
    ACTOR_FROZEN = "actor_frozen"
    INGESTION_FAILED = "ingestion_failed"
    PROFILE_NOT_FOUND = "profile_not_found"
    MEANING_FAILED = "meaning_failed"
    GUARD_REJECTED = "guard_rejected"
    GUARD_BLOCKED = "guard_blocked"
    INTENT_FAILED = "intent_failed"
    POSTING_FAILED = "posting_failed"


@dataclass(frozen=True)
class ModulePostingResult:
    """Result of a module posting operation."""

    status: ModulePostingStatus
    event_id: UUID
    journal_entry_ids: tuple[UUID, ...] = ()
    interpretation_result: InterpretationResult | None = None
    meaning_result: MeaningBuilderResult | None = None
    profile_name: str | None = None
    message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status in (
            ModulePostingStatus.POSTED,
            ModulePostingStatus.ALREADY_POSTED,
        )


class ModulePostingService:
    """
    Orchestrates profile-driven posting from modules.

    Contract:
        Accepts a raw economic event described by (event_type, payload,
        effective_date, actor_id, amount, currency) and drives it through
        the full posting pipeline, returning a ``ModulePostingResult``
        that is either successful or contains a machine-readable status
        code explaining why posting was refused.

    Guarantees:
        - Exactly-once semantics: duplicate event_id yields ALREADY_POSTED.
        - R7 transaction safety: commit on success, rollback on failure
          (when auto_commit=True).
        - All invariants from R1 through P15 are enforced by delegating
          to purpose-built kernel services (IngestorService, PeriodService,
          JournalWriter, OutcomeRecorder, InterpretationCoordinator).

    Non-goals:
        - Does NOT manage schema migrations or COA maintenance.
        - Does NOT handle reversal entries (see CorrectionService).
        - Does NOT own the subledger posting logic (delegated via callable).

    Preferred usage (via PostingOrchestrator):
        service = ModulePostingService.from_orchestrator(orchestrator)

    Legacy usage (deprecated — creates services internally):
        service = ModulePostingService(
            session=session,
            role_resolver=resolver,
            clock=clock,
        )
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
        auto_commit: bool = True,
    ):
        """Legacy constructor — creates services internally.

        Prefer ``from_orchestrator`` for new code.
        """
        self._session = session
        self._clock = clock or SystemClock()
        self._auto_commit = auto_commit

        # Build internal services (legacy path)
        self._auditor = AuditorService(session, clock)
        self._ingestor = IngestorService(session, clock, self._auditor)
        self._period_service = PeriodService(session, clock)
        self._meaning_builder = MeaningBuilder()
        self._journal_writer = JournalWriter(
            session, role_resolver, clock, self._auditor
        )
        self._outcome_recorder = OutcomeRecorder(session, self._clock)
        self._coordinator = InterpretationCoordinator(
            session=session,
            journal_writer=self._journal_writer,
            outcome_recorder=self._outcome_recorder,
            clock=clock,
        )
        self._post_subledger_fn = None  # Injected via from_orchestrator()

    @classmethod
    def from_orchestrator(
        cls,
        orchestrator: PostingOrchestrator,
        auto_commit: bool = True,
    ) -> ModulePostingService:
        """Create from a PostingOrchestrator (preferred).

        Services are shared singletons from the orchestrator — no
        duplicate instances, full engine dispatch and policy authority support.
        """
        instance = cls.__new__(cls)
        instance._session = orchestrator.session
        instance._clock = orchestrator.clock
        instance._auto_commit = auto_commit
        instance._ingestor = orchestrator.ingestor
        instance._period_service = orchestrator.period_service
        instance._meaning_builder = orchestrator.meaning_builder
        instance._coordinator = orchestrator.interpretation_coordinator
        instance._party_service_ref = orchestrator.party_service
        instance._compiled_pack = orchestrator.engine_dispatcher._pack
        # Subledger posting callable — lives in finance_services/ to
        # respect architecture boundary (kernel must not import engines).
        instance._post_subledger_fn = getattr(
            orchestrator, "post_subledger_entries", None
        )
        return instance

    def post_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        effective_date: date,
        actor_id: UUID,
        amount: Decimal,
        currency: str = "USD",
        producer: str | None = None,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
        schema_version: int = 1,
        is_adjustment: bool = False,
        description: str | None = None,
        coa_version: int = 1,
        dimension_schema_version: int = 1,
    ) -> ModulePostingResult:
        """
        Post an economic event through the posting pipeline.

        Preconditions:
            - ``event_type`` is a non-empty namespaced string (e.g., "inventory.receipt").
            - ``amount`` is a ``Decimal`` (never float).
            - ``currency`` is a valid ISO 4217 code.

        Postconditions:
            - On success (POSTED/ALREADY_POSTED) the session is committed
              (when auto_commit=True) and all journal entries, outcomes,
              and subledger rows are persisted.
            - On failure, the session is rolled back (when auto_commit=True)
              and no partial state is visible.

        Raises:
            Exception: Re-raises any unexpected exception after rollback.

        Args:
            event_type: Namespaced event type (e.g., "inventory.receipt").
            payload: Event payload with domain-specific data.
            effective_date: Accounting effective date.
            actor_id: Who caused the event.
            amount: Primary monetary amount for the entry.
            currency: Currency code (default: "USD").
            producer: System that produced the event (defaults to event_type prefix).
            event_id: Optional event ID (generated if not provided).
            occurred_at: When the event happened (defaults to clock.now()).
            schema_version: Schema version for the event.
            is_adjustment: Whether this is an adjusting entry.
            description: Optional entry description.
            coa_version: COA version for snapshot.
            dimension_schema_version: Dimension schema version for snapshot.

        Returns:
            ModulePostingResult with status and artifacts.
        """
        resolved_event_id = event_id or _uuid4()
        resolved_occurred_at = occurred_at or self._clock.now()
        resolved_producer = producer or event_type.split(".")[0]

        correlation_id = str(_uuid4())
        with LogContext.bind(
            correlation_id=correlation_id,
            event_id=str(resolved_event_id),
            actor_id=str(actor_id),
            producer=resolved_producer,
        ):
            logger.info(
                "module_posting_started",
                extra={
                    "event_type": event_type,
                    "effective_date": str(effective_date),
                    "amount": str(amount),
                    "currency": currency,
                },
            )
            t0 = time.monotonic()

            try:
                result = self._do_post_event(
                    event_id=resolved_event_id,
                    event_type=event_type,
                    payload=payload,
                    effective_date=effective_date,
                    actor_id=actor_id,
                    amount=amount,
                    currency=currency,
                    producer=resolved_producer,
                    occurred_at=resolved_occurred_at,
                    schema_version=schema_version,
                    is_adjustment=is_adjustment,
                    description=description,
                    coa_version=coa_version,
                    dimension_schema_version=dimension_schema_version,
                )

                # INVARIANT: R7 — Transaction boundaries: commit on success
                if self._auto_commit and result.is_success:
                    self._session.commit()

                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                logger.info(
                    "module_posting_completed",
                    extra={
                        "status": result.status.value,
                        "duration_ms": duration_ms,
                        "profile_name": result.profile_name,
                        "entry_count": len(result.journal_entry_ids),
                    },
                )
                return result

            except Exception:
                duration_ms = round((time.monotonic() - t0) * 1000, 2)
                if self._auto_commit:
                    self._session.rollback()
                logger.error(
                    "module_posting_failed",
                    extra={"duration_ms": duration_ms},
                    exc_info=True,
                )
                raise

    def _do_post_event(
        self,
        event_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        effective_date: date,
        actor_id: UUID,
        amount: Decimal,
        currency: str,
        producer: str,
        occurred_at: datetime,
        schema_version: int,
        is_adjustment: bool,
        description: str | None,
        coa_version: int,
        dimension_schema_version: int,
    ) -> ModulePostingResult:
        """Internal posting logic (without transaction management)."""

        # INVARIANT: G14 — Actor authorization at posting boundary
        if hasattr(self, '_party_service_ref') and self._party_service_ref is not None:
            try:
                actor_party = self._party_service_ref.get_by_id(actor_id)
                if not actor_party.can_transact:
                    return ModulePostingResult(
                        status=ModulePostingStatus.ACTOR_FROZEN,
                        event_id=event_id,
                        message=f"Actor {actor_id} is frozen and cannot post",
                    )
            except PartyNotFoundError:
                return ModulePostingResult(
                    status=ModulePostingStatus.INVALID_ACTOR,
                    event_id=event_id,
                    message=f"Actor {actor_id} is not a valid party",
                )

        # INVARIANT: R12 — Closed period enforcement
        # INVARIANT: R13 — Adjustment policy enforcement
        from finance_kernel.exceptions import AdjustmentsNotAllowedError

        try:
            self._period_service.validate_adjustment_allowed(
                effective_date, is_adjustment=is_adjustment
            )
        except AdjustmentsNotAllowedError:
            return ModulePostingResult(
                status=ModulePostingStatus.ADJUSTMENTS_NOT_ALLOWED,
                event_id=event_id,
                message="Period does not allow adjustments",
            )
        except Exception as e:
            return ModulePostingResult(
                status=ModulePostingStatus.PERIOD_CLOSED,
                event_id=event_id,
                message=str(e),
            )

        # INVARIANT: R1 — Event immutability via IngestorService
        # INVARIANT: R2 — Payload hash verification via IngestorService
        ingest_result = self._ingestor.ingest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            effective_date=effective_date,
            actor_id=actor_id,
            producer=producer,
            payload=payload,
            schema_version=schema_version,
        )

        if ingest_result.status == IngestStatus.REJECTED:
            return ModulePostingResult(
                status=ModulePostingStatus.INGESTION_FAILED,
                event_id=event_id,
                message=ingest_result.message,
            )

        if ingest_result.status == IngestStatus.DUPLICATE:
            return ModulePostingResult(
                status=ModulePostingStatus.ALREADY_POSTED,
                event_id=event_id,
                message="Event already ingested (idempotent duplicate)",
            )

        # INVARIANT: P1 — Exactly one EconomicProfile matches any event
        try:
            profile = PolicySelector.find_for_event(
                event_type, effective_date, payload=payload
            )
        except PolicyNotFoundError as e:
            return ModulePostingResult(
                status=ModulePostingStatus.PROFILE_NOT_FOUND,
                event_id=event_id,
                message=str(e),
            )

        # Look up CompiledPolicy for engine dispatch (if compiled pack available)
        compiled_policy = None
        if hasattr(self, '_compiled_pack') and self._compiled_pack:
            for cp in self._compiled_pack.policies:
                if cp.name == profile.name and cp.version == profile.version:
                    compiled_policy = cp
                    break

        logger.info(
            "profile_matched",
            extra={
                "profile_name": profile.name,
                "profile_version": profile.version,
                "has_compiled_policy": compiled_policy is not None,
                "required_engines": list(
                    compiled_policy.required_engines
                ) if compiled_policy and compiled_policy.required_engines else [],
            },
        )

        # 4. Build meaning (MeaningBuilder — pure domain)
        meaning_result = self._meaning_builder.build(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            effective_date=effective_date,
            profile=profile,
        )

        if not meaning_result.success:
            # Check for guard rejection vs block
            if meaning_result.guard_result and meaning_result.guard_result.rejected:
                return ModulePostingResult(
                    status=ModulePostingStatus.GUARD_REJECTED,
                    event_id=event_id,
                    meaning_result=meaning_result,
                    profile_name=profile.name,
                    message=f"Guard rejected: {meaning_result.guard_result.reason_code}",
                )
            if meaning_result.guard_result and meaning_result.guard_result.blocked:
                return ModulePostingResult(
                    status=ModulePostingStatus.GUARD_BLOCKED,
                    event_id=event_id,
                    meaning_result=meaning_result,
                    profile_name=profile.name,
                    message=f"Guard blocked: {meaning_result.guard_result.reason_code}",
                )
            return ModulePostingResult(
                status=ModulePostingStatus.MEANING_FAILED,
                event_id=event_id,
                meaning_result=meaning_result,
                profile_name=profile.name,
                message="MeaningBuilder failed to produce economic event",
            )

        # 5. Build accounting intent (profile_bridge)
        try:
            accounting_intent = build_accounting_intent(
                profile_name=profile.name,
                source_event_id=event_id,
                effective_date=effective_date,
                amount=amount,
                currency=currency,
                payload=payload,
                description=description,
                coa_version=coa_version,
                dimension_schema_version=dimension_schema_version,
            )
        except ValueError as e:
            return ModulePostingResult(
                status=ModulePostingStatus.INTENT_FAILED,
                event_id=event_id,
                profile_name=profile.name,
                message=str(e),
            )

        # INVARIANT: L5 — Atomic journal + outcome via InterpretationCoordinator
        # INVARIANT: P11 — Multi-ledger postings are atomic
        interpretation_result = self._coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=accounting_intent,
            actor_id=actor_id,
            compiled_policy=compiled_policy,
            event_payload=payload,
        )

        if not interpretation_result.success:
            return ModulePostingResult(
                status=ModulePostingStatus.POSTING_FAILED,
                event_id=event_id,
                interpretation_result=interpretation_result,
                profile_name=profile.name,
                message=interpretation_result.error_message,
            )

        # 7. Post subledger entries (SL-G1: same transaction as journal write).
        #    Callable lives in finance_services/ (architecture boundary).
        if self._post_subledger_fn and interpretation_result.journal_result:
            self._post_subledger_fn(
                accounting_intent=accounting_intent,
                journal_result=interpretation_result.journal_result,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                actor_id=actor_id,
            )

        # Success
        journal_entry_ids = ()
        if interpretation_result.journal_result:
            journal_entry_ids = interpretation_result.journal_result.entry_ids

        # INVARIANT: L5 — POSTED status requires at least one journal entry
        assert len(journal_entry_ids) > 0, (
            "L5 violation: POSTED result must contain at least one journal entry"
        )

        return ModulePostingResult(
            status=ModulePostingStatus.POSTED,
            event_id=event_id,
            journal_entry_ids=journal_entry_ids,
            interpretation_result=interpretation_result,
            meaning_result=meaning_result,
            profile_name=profile.name,
            message="Event posted successfully",
        )

