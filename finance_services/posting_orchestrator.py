"""
PostingOrchestrator — Central DI container for kernel services.

Every kernel service is created once and injected. No service may create
other services internally. This ensures:
- Single-instance lifecycle (no duplicate SequenceService)
- Testability (inject test doubles at one point)
- Dependency transparency (all service wiring visible in one place)

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
    orchestrator.interpretation_coordinator
    orchestrator.engine_dispatcher
    ...
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from finance_kernel.domain.accounting_intent import AccountingIntent
    from finance_kernel.services.journal_writer import JournalWriteResult

from finance_services.subledger_posting import post_subledger_entries as _post_sl

from finance_config.bridges import build_subledger_registry_from_defs
from finance_config.compiler import CompiledPolicyPack
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import MeaningBuilder
from finance_kernel.domain.policy_authority import PolicyAuthority
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.contract_service import ContractService
from finance_services.engine_dispatcher import EngineDispatcher
from finance_kernel.services.ingestor_service import IngestorService
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.reference_snapshot_service import ReferenceSnapshotService
from finance_services.subledger_period_service import SubledgerPeriodService
from finance_services.subledger_service import SubledgerService
from finance_services.subledger_ap import APSubledgerService
from finance_services.subledger_ar import ARSubledgerService
from finance_services.subledger_bank import BankSubledgerService
from finance_services.subledger_contract import ContractSubledgerService
from finance_services.subledger_inventory import InventorySubledgerService


class PostingOrchestrator:
    """Central factory for kernel services.

    Every kernel service is created once and injected.
    No service may create other services internally.

    This is the single point of DI for the posting pipeline. Modules
    receive this object and access services through it — they never
    construct kernel services themselves.
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

        # --- Singletons: created once, order matters (dependency graph) ---

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

        # Outcome recording
        self.outcome_recorder = OutcomeRecorder(session, self._clock)

        # Engine dispatch (depends on compiled_pack)
        self.engine_dispatcher = EngineDispatcher(compiled_pack)

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

        Delegates to the subledger_posting bridge module which handles
        engine-layer imports that this orchestrator must not touch.

        SL-G1: Atomicity — runs in the same transaction as the journal write.
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
