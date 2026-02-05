"""Canonical posting entry point for all modules."""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable
from uuid import UUID
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import MeaningBuilder, MeaningBuilderResult
from finance_kernel.domain.control import evaluate_controls
from finance_kernel.domain.policy_bridge import (
    build_accounting_intent,
    build_accounting_intent_from_payload_lines,
)
from finance_kernel.domain.policy_selector import PolicyNotFoundError, PolicySelector
from finance_kernel.domain.policy_source import PolicySource
from finance_kernel.exceptions import PartyNotFoundError
from finance_kernel.logging_config import LogContext, get_logger
from finance_kernel.models.party import PartyType
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.interpretation_coordinator import (
    InterpretationCoordinator,
    InterpretationResult,
)
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.party_service import PartyService

logger = get_logger("services.module_posting")


class ModulePostingStatus(str, Enum):
    """Status of a module posting operation.

    Authority boundary:
    - Kernel layer only may assert POSTED or REJECTED (ledger truth).
    - Service layer may return only: TRANSITION_APPLIED, TRANSITION_BLOCKED,
      TRANSITION_REJECTED, GUARD_BLOCKED, GUARD_REJECTED (governance outcomes).
    """

    # Kernel-only: ledger truth (only ModulePostingService may set these)
    POSTED = "posted"
    ALREADY_POSTED = "already_posted"
    REJECTED = "rejected"  # Kernel validation / policy / period lock / invariant

    # Service-only: governance / orchestration outcomes (no ledger assertion)
    TRANSITION_APPLIED = "transition_applied"
    TRANSITION_BLOCKED = "transition_blocked"
    TRANSITION_REJECTED = "transition_rejected"
    GUARD_REJECTED = "guard_rejected"
    GUARD_BLOCKED = "guard_blocked"

    # Kernel-originated failure details (validation, profile, etc.)
    PERIOD_CLOSED = "period_closed"
    ADJUSTMENTS_NOT_ALLOWED = "adjustments_not_allowed"
    INVALID_ACTOR = "invalid_actor"
    ACTOR_FROZEN = "actor_frozen"
    INGESTION_FAILED = "ingestion_failed"
    PROFILE_NOT_FOUND = "profile_not_found"
    MEANING_FAILED = "meaning_failed"
    INTENT_FAILED = "intent_failed"
    POSTING_FAILED = "posting_failed"


@dataclass(frozen=True)
class ModulePostingResult:
    """Result of a module posting operation.

    Semantic split (R29): governance outcomes vs ledger truth.
    - is_transition: authority was exercised (transition applied/blocked/rejected).
    - is_ledger_fact: a journal entry exists (only kernel may assert).
    """

    status: ModulePostingStatus
    event_id: UUID
    journal_entry_ids: tuple[UUID, ...] = ()
    ledger_ids: tuple[str, ...] = ()  # Ledgers written (e.g. GL, AP, AR) for trace visibility
    interpretation_result: InterpretationResult | None = None
    meaning_result: MeaningBuilderResult | None = None
    profile_name: str | None = None
    message: str | None = None

    @property
    def is_transition(self) -> bool:
        """True if this is a governance outcome (authority exercised), not ledger truth."""
        return self.status in (
            ModulePostingStatus.TRANSITION_APPLIED,
            ModulePostingStatus.TRANSITION_BLOCKED,
            ModulePostingStatus.TRANSITION_REJECTED,
        )

    @property
    def is_ledger_fact(self) -> bool:
        """True iff a journal entry exists. Only kernel may set POSTED (R29)."""
        return self.status == ModulePostingStatus.POSTED

    @property
    def is_success(self) -> bool:
        """True iff posting succeeded (ledger fact created or already posted)."""
        return self.status in (
            ModulePostingStatus.POSTED,
            ModulePostingStatus.ALREADY_POSTED,
        )


class ModulePostingService:
    """Orchestrates profile-driven posting from modules."""

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
        auto_commit: bool = True,
        party_service: PartyService | None = None,
    ):
        """Legacy constructor. Prefer ``from_orchestrator`` for new code."""
        self._session = session
        self._clock = clock or SystemClock()
        self._auto_commit = auto_commit
        self._party_service_ref = party_service

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
        """Create from a PostingOrchestrator (preferred)."""
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
        instance._policy_source = getattr(orchestrator, "policy_source", None)
        instance._control_rules = getattr(orchestrator, "control_rules", ())
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
        preamble_log: list[dict] | None = None,
        account_key_to_role: Callable[[str], str | None] | None = None,
    ) -> ModulePostingResult:
        """Post an economic event through the posting pipeline.

        preamble_log: Optional list of structured log records (e.g. workflow
        transition outcomes) to prepend to the decision_log for traceability.
        Modules that call WorkflowExecutor with outcome_sink can pass the
        collected records here so workflow outcomes appear in the event trace.
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
                    preamble_log=preamble_log,
                    account_key_to_role=account_key_to_role,
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

    def _validate_import_hard_gate(
        self,
        event_id: UUID,
        event_type: str,
        producer: str,
        payload: dict[str, Any],
        actor_id: UUID,
    ) -> ModulePostingResult | None:
        """Migration-only gate for import.historical_journal. Returns result if rejected, None if passed."""
        if event_type != "import.historical_journal":
            return None
        if producer != "ingestion":
            return ModulePostingResult(
                status=ModulePostingStatus.GUARD_REJECTED,
                event_id=event_id,
                message="import.historical_journal requires producer=ingestion (migration-only)",
            )
        meta = payload.get("metadata") or {}
        if not meta.get("migration_batch_id"):
            return ModulePostingResult(
                status=ModulePostingStatus.GUARD_REJECTED,
                event_id=event_id,
                message="import.historical_journal requires payload.metadata.migration_batch_id",
            )
        party_svc = getattr(self, "_party_service_ref", None)
        if party_svc is None:
            return None
        try:
            actor_party = party_svc.get_by_id(actor_id)
            if actor_party.party_type not in (PartyType.SYSTEM, PartyType.MIGRATION_SERVICE):
                return ModulePostingResult(
                    status=ModulePostingStatus.GUARD_REJECTED,
                    event_id=event_id,
                    message="import.historical_journal requires actor party_type in (system, migration_service)",
                )
        except PartyNotFoundError:
            return ModulePostingResult(
                status=ModulePostingStatus.INVALID_ACTOR,
                event_id=event_id,
                message=f"Actor {actor_id} is not a valid party",
            )
        return None

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
        preamble_log: list[dict] | None = None,
        account_key_to_role: Callable[[str], str | None] | None = None,
    ) -> ModulePostingResult:
        """Internal posting logic (without transaction management)."""

        # INVARIANT: G14 — Actor validation is mandatory for all POSTED outcomes.
        # No POSTED may occur without validating actor_id against PartyService.
        party_svc = getattr(self, "_party_service_ref", None)
        if party_svc is None:
            return ModulePostingResult(
                status=ModulePostingStatus.REJECTED,
                event_id=event_id,
                message="Actor validation is mandatory for posting; PartyService not configured",
            )
        try:
            actor_party = party_svc.get_by_id(actor_id)
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

        # Governance preamble for trace (period check, then caller preamble e.g. workflow)
        governance_preamble: list[dict] = []
        period_info = self._period_service.get_period_for_date(effective_date)
        if period_info is not None:
            governance_preamble.append({
                "message": "period_check",
                "period_code": period_info.period_code,
                "passed": True,
                "effective_date": str(effective_date),
            })
        full_preamble = governance_preamble + (preamble_log or [])

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

        gate_result = self._validate_import_hard_gate(event_id, event_type, producer, payload, actor_id)
        if gate_result is not None:
            return gate_result

        # Config-driven controls (controls.yaml) — run after ingest, before profile
        control_rules = getattr(self, "_control_rules", ())
        if control_rules:
            control_result = evaluate_controls(payload, event_type, control_rules)
            if not control_result.passed:
                status = (
                    ModulePostingStatus.GUARD_REJECTED
                    if control_result.rejected
                    else ModulePostingStatus.GUARD_BLOCKED
                )
                return ModulePostingResult(
                    status=status,
                    event_id=event_id,
                    message=control_result.message or control_result.reason_code or "Control not satisfied",
                )

        # INVARIANT: P1 — Exactly one EconomicProfile matches any event
        # When policy_source is set (from orchestrator with pack), use config-driven profile.
        try:
            if getattr(self, "_policy_source", None) is not None:
                profile = self._policy_source.get_profile(
                    event_type, effective_date, payload=payload
                )
            else:
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

        # 5. Build accounting intent (profile_bridge or from payload.lines)
        try:
            if getattr(profile, "intent_source", None) == "payload_lines":
                if account_key_to_role is None:
                    return ModulePostingResult(
                        status=ModulePostingStatus.INTENT_FAILED,
                        event_id=event_id,
                        profile_name=profile.name,
                        message="account_key_to_role resolver required for import.historical_journal",
                    )
                intent_currency = payload.get("currency") or currency
                if not isinstance(intent_currency, str):
                    intent_currency = "USD"
                accounting_intent = build_accounting_intent_from_payload_lines(
                    profile=profile,
                    source_event_id=event_id,
                    effective_date=effective_date,
                    payload=payload,
                    account_key_to_role=account_key_to_role,
                    currency=intent_currency,
                    description=description,
                    coa_version=coa_version,
                    dimension_schema_version=dimension_schema_version,
                )
            else:
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
        pack = getattr(self, "_compiled_pack", None)
        interpretation_result = self._coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=accounting_intent,
            actor_id=actor_id,
            compiled_policy=compiled_policy,
            event_payload=payload,
            preamble_log=full_preamble,
            policy_fingerprint=getattr(pack, "canonical_fingerprint", None) if pack else None,
            profile_source="compiled_policy_pack" if pack else None,
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

        ledger_ids = tuple(li.ledger_id for li in accounting_intent.ledger_intents)
        return ModulePostingResult(
            status=ModulePostingStatus.POSTED,
            event_id=event_id,
            journal_entry_ids=journal_entry_ids,
            ledger_ids=ledger_ids,
            interpretation_result=interpretation_result,
            meaning_result=meaning_result,
            profile_name=profile.name,
            message="Event posted successfully",
        )
