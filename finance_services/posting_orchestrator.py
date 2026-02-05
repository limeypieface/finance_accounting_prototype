"""
finance_services.posting_orchestrator -- Central DI container for kernel services.

Responsibility:
    Creates every kernel service exactly once and wires them together.
    No service may create other services internally.  The orchestrator
    is the single point of dependency injection for the posting pipeline.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    This module sits at the top of the service layer and is the only
    place where kernel services are constructed and composed.

Invariants enforced:
    - Single-instance lifecycle: no duplicate SequenceService, AuditorService,
      or PeriodService within a single posting context.
    - DI transparency: all service wiring is visible in ``__init__``.
    - SL-G1 (Subledger atomicity): subledger posting runs inside the same
      transaction as the journal write via ``post_subledger_entries``.

Failure modes:
    - Construction failure if compiled_pack or role_resolver are invalid.
    - AttributeError if a required sub-service cannot be instantiated.

Audit relevance:
    - The orchestrator's construction order defines the authoritative
      dependency graph.  Changes here affect the audit trail path and
      must be reviewed for transactional integrity.

Usage:
    from finance_services.posting_orchestrator import PostingOrchestrator

    orchestrator = PostingOrchestrator(
        session=session,
        compiled_pack=compiled_pack,
        role_resolver=role_resolver,
        clock=clock,
    )

    # All services available as properties:
    orchestrator.ingestor
    orchestrator.period_service
    orchestrator.journal_writer
    orchestrator.outcome_recorder
    orchestrator.retry_service
    orchestrator.workflow_executor
    ...

    # Module services (AP, AR, etc.) are not created here (finance_services
    # must not import finance_modules). When constructing them, pass
    # workflow_executor=orchestrator.workflow_executor so workflow guards run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from finance_kernel.domain.accounting_intent import AccountingIntent
    from finance_kernel.services.journal_writer import JournalWriteResult

from finance_config.bridges import (
    build_subledger_registry_from_defs,
    controls_from_compiled,
)
from finance_config.compiler import CompiledPolicyPack
from finance_kernel.domain.policy_source import PolicySource, SelectorPolicySource
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import MeaningBuilder
from finance_kernel.domain.policy_authority import PolicyAuthority
from finance_kernel.services.approval_service import ApprovalService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.contract_service import ContractService
from finance_kernel.services.ingestor_service import IngestorService
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.retry_service import RetryService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.reference_snapshot_service import ReferenceSnapshotService
from finance_kernel.services.reversal_service import ReversalService
from finance_services.engine_dispatcher import EngineDispatcher
from finance_services.pack_policy_source import PackPolicySource
from finance_services.workflow_executor import WorkflowExecutor
from finance_services.subledger_ap import APSubledgerService
from finance_services.subledger_ar import ARSubledgerService
from finance_services.subledger_bank import BankSubledgerService
from finance_services.subledger_contract import ContractSubledgerService
from finance_services.subledger_inventory import InventorySubledgerService
from finance_services.subledger_period_service import SubledgerPeriodService
from finance_services.subledger_posting import post_subledger_entries as _post_sl
from finance_services.subledger_service import SubledgerService


class PostingOrchestrator:
    """Central factory for kernel services.

    Contract:
        Receives a SQLAlchemy Session, CompiledPolicyPack, RoleResolver,
        and optional Clock/PolicyAuthority.  Constructs every kernel
        service exactly once, in dependency order, and exposes them as
        public attributes.

    Guarantees:
        - Single-instance lifecycle for every kernel service within this
          orchestrator's scope.
        - All services share the same Session and Clock instances.
        - Subledger services are keyed by SubledgerType for dispatch.

    Non-goals:
        - Does NOT manage transaction boundaries (caller's responsibility).
        - Does NOT own the Session lifecycle (no commit/rollback).
    """

    def __init__(
        self,
        session: Session,
        compiled_pack: CompiledPolicyPack,
        role_resolver: RoleResolver,
        policy_authority: PolicyAuthority | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or SystemClock()
        self.role_resolver = role_resolver

        # --- Singletons: created once, order matters (dependency graph) ---
        # INVARIANT: single-instance lifecycle -- no duplicate service instances.

        # Foundational services (no kernel dependencies)
        self.auditor = AuditorService(session, self._clock)
        self.period_service = PeriodService(session, self._clock)
        self.link_graph = LinkGraphService(session)
        self.snapshot_service = ReferenceSnapshotService(
            session, self._clock, compiled_pack=compiled_pack,
        )
        self.party_service = PartyService(session)
        self.contract_service = ContractService(session)

        # Ingestion (depends on auditor)
        self.ingestor = IngestorService(session, self._clock, self.auditor)

        # Build subledger control registry from compiled config (SL-Phase 7).
        # Resolves control_account_role → COA code at config time, not G9 time.
        sl_registry = None
        if compiled_pack.subledger_contracts:
            sl_registry = build_subledger_registry_from_defs(
                compiled_pack.subledger_contracts,
                role_resolver,
                default_currency=compiled_pack.scope.currency,
            )

        # Journal writing (depends on role_resolver, auditor, G9+G10 hooks)
        self.journal_writer = JournalWriter(
            session, role_resolver, self._clock, self.auditor,
            subledger_control_registry=sl_registry,
            snapshot_service=self.snapshot_service,
        )

        # Reversal service (depends on journal_writer, auditor, link_graph, period_service)
        self.reversal_service = ReversalService(
            session=session,
            journal_writer=self.journal_writer,
            auditor=self.auditor,
            link_graph=self.link_graph,
            period_service=self.period_service,
            clock=self._clock,
        )

        # Approval service (depends on auditor)
        self.approval_service = ApprovalService(session, self.auditor, self._clock)

        # Approval policies: convert compiled config to domain ApprovalPolicy objects
        approval_policy_map = _build_approval_policy_map(compiled_pack)
        self.workflow_executor = WorkflowExecutor(
            approval_service=self.approval_service,
            approval_policies=approval_policy_map,
            clock=self._clock,
        )

        # Outcome recording
        self.outcome_recorder = OutcomeRecorder(session, self._clock)
        # RetryService: constructed here for future retry flows / worker entrypoint.
        # No code path currently invokes retry_service.retry(); wire when retries are required.
        self.retry_service = RetryService(session, self.outcome_recorder, self._clock)

        # Engine dispatch (depends on compiled_pack)
        self.engine_dispatcher = EngineDispatcher(compiled_pack)

        # Policy source: from pack (config-driven) when pack present, else Selector (Python-registered)
        self.policy_source: PolicySource = PackPolicySource(compiled_pack)

        # Control rules from pack (controls.yaml) for posting-path enforcement
        self.control_rules = controls_from_compiled(compiled_pack.controls)

        # Domain components
        self.policy_authority = policy_authority
        self.meaning_builder = MeaningBuilder(policy_authority=policy_authority)

        # Interpretation coordinator (depends on journal_writer,
        # outcome_recorder, engine_dispatcher)
        self.interpretation_coordinator = InterpretationCoordinator(
            session=session,
            journal_writer=self.journal_writer,
            outcome_recorder=self.outcome_recorder,
            clock=self._clock,
            engine_dispatcher=self.engine_dispatcher,
        )

        # Subledger services (keyed by SubledgerType for dispatch)
        from finance_kernel.domain.subledger_control import SubledgerType

        _ap = APSubledgerService(session, self._clock)
        _ar = ARSubledgerService(session, self._clock)
        _bank = BankSubledgerService(session, self._clock)
        _inventory = InventorySubledgerService(session, self._clock)
        _contract = ContractSubledgerService(session, self._clock)

        self.subledger_services: dict[SubledgerType, SubledgerService] = {
            SubledgerType.AP: _ap,
            SubledgerType.AR: _ar,
            SubledgerType.BANK: _bank,
            SubledgerType.INVENTORY: _inventory,
            SubledgerType.WIP: _contract,
        }

        # Subledger period close service (SL-Phase 8, SL-G6)
        if sl_registry is not None:
            self.subledger_period_service: SubledgerPeriodService | None = (
                SubledgerPeriodService(
                    session=session,
                    clock=self._clock,
                    registry=sl_registry,
                    role_resolver=role_resolver,
                )
            )
        else:
            self.subledger_period_service = None

    @property
    def session(self) -> Session:
        """The SQLAlchemy session shared by all services."""
        return self._session

    @property
    def clock(self) -> Clock:
        """The clock shared by all services."""
        return self._clock

    def post_subledger_entries(
        self,
        accounting_intent: AccountingIntent,
        journal_result: JournalWriteResult,
        event_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        actor_id: UUID,
    ) -> None:
        """
        Post subledger entries for subledger ledger intents.

        Preconditions:
            journal_result contains successfully written entries (the
            journal write has already flushed within the current txn).

        Postconditions:
            SubledgerEntry rows are created for each subledger ledger
            intent, linked to the corresponding journal entry IDs.

        Raises:
            ValueError: If a subledger entry fails validation.

        Delegates to the subledger_posting bridge module which handles
        engine-layer imports that this orchestrator must not touch.

        INVARIANT [SL-G1]: Atomicity -- runs in the same transaction as
        the journal write.  If this fails, the entire transaction rolls back.
        """
        _post_sl(
            subledger_services=self.subledger_services,
            accounting_intent=accounting_intent,
            journal_result=journal_result,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
        )

    def make_correction_writer(
        self,
        actor_id: UUID,
        creating_event_id: UUID,
    ) -> Callable[..., UUID]:
        """Create a journal_entry_writer callback for CorrectionEngine.

        Routes CompensatingEntry instances through JournalWriter.write_reversal()
        so that correction-generated reversals get proper reversal_of_id linkage,
        idempotency keys, R9 sequences, and R21 snapshot versions.

        The CorrectionEngine handles its own CORRECTED_BY links and audit
        events, so this adapter delegates only the journal entry creation
        portion -- it does NOT call ReversalService (which would duplicate
        the link and audit overhead).

        Args:
            actor_id: Who is performing the correction.
            creating_event_id: The correction event ID (used as source_event_id
                for the reversal entries).

        Returns:
            A ``Callable[[CompensatingEntry], UUID]`` callback suitable for
            ``CorrectionEngine.execute_correction()`` or
            ``CorrectionEngine.void_document()``.
        """
        writer = self.journal_writer

        def _write_reversal(comp_entry: Any) -> UUID:
            original = writer.get_entry(comp_entry.original_entry_id)
            if original is None:
                raise ValueError(
                    f"Original entry {comp_entry.original_entry_id} not found"
                )

            reversal_entry = writer.write_reversal(
                original_entry=original,
                source_event_id=creating_event_id,
                actor_id=actor_id,
                effective_date=comp_entry.effective_date,
                reason=comp_entry.memo,
            )
            return reversal_entry.id

        return _write_reversal


def _build_approval_policy_map(
    compiled_pack: CompiledPolicyPack,
) -> dict[str, "ApprovalPolicy"]:
    """Convert compiled approval policies to domain ApprovalPolicy objects.

    Keys are ``workflow_name:action`` for action-specific policies, or
    ``workflow_name`` for workflow-level policies.
    """
    from decimal import Decimal

    from finance_kernel.domain.approval import ApprovalPolicy, ApprovalRule

    result: dict[str, ApprovalPolicy] = {}

    for cap in compiled_pack.approval_policies:
        rules = tuple(
            ApprovalRule(
                rule_name=r.rule_name,
                priority=r.priority,
                min_amount=Decimal(r.min_amount) if r.min_amount else None,
                max_amount=Decimal(r.max_amount) if r.max_amount else None,
                required_roles=r.required_roles,
                min_approvers=r.min_approvers,
                require_distinct_roles=r.require_distinct_roles,
                guard_expression=r.guard_expression,
                auto_approve_below=Decimal(r.auto_approve_below) if r.auto_approve_below else None,
                escalation_timeout_hours=r.escalation_timeout_hours,
            )
            for r in cap.rules
        )

        policy = ApprovalPolicy(
            policy_name=cap.policy_name,
            version=cap.version,
            applies_to_workflow=cap.applies_to_workflow,
            applies_to_action=cap.applies_to_action,
            rules=rules,
            policy_currency=cap.policy_currency,
            policy_hash=cap.policy_hash,
        )

        # Key by workflow:action or just workflow
        if cap.applies_to_action:
            key = f"{cap.applies_to_workflow}:{cap.applies_to_action}"
        else:
            key = cap.applies_to_workflow
        result[key] = policy

    return result


def build_posting_orchestrator(
    session: Session,
    legal_entity: str,
    as_of_date: "date",
    config_dir: "Path | None" = None,
    clock: "Clock | None" = None,
) -> PostingOrchestrator:
    """Build a PostingOrchestrator from config (single entrypoint for production).

    Loads config via get_active_config(legal_entity, as_of_date), builds
    role_resolver from the pack, and constructs the orchestrator so that
    policies, controls, approval policies, and role bindings all come
    from YAML.

    Args:
        session: SQLAlchemy session.
        legal_entity: Legal entity for config scope (e.g. "*" or "US-ENTITY").
        as_of_date: Date for effective config and policies.
        config_dir: Optional path to config sets directory.
        clock: Optional clock; default SystemClock.

    Returns:
        PostingOrchestrator with policy_source, control_rules, and approval
        policies wired from the compiled pack.
    """
    from pathlib import Path

    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_kernel.domain.clock import Clock, SystemClock

    pack = get_active_config(legal_entity, as_of_date, config_dir=config_dir)
    role_resolver = build_role_resolver(pack)
    return PostingOrchestrator(
        session=session,
        compiled_pack=pack,
        role_resolver=role_resolver,
        clock=clock or SystemClock(),
    )
