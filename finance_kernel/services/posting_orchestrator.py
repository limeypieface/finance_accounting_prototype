"""
PostingOrchestrator — Central factory for kernel services.

Every kernel service is created once and injected. No service may create
other services internally. This ensures:
- Single-instance lifecycle (no duplicate SequenceService)
- Testability (inject test doubles at one point)
- Dependency transparency (all service wiring visible in one place)

Usage:
    from finance_kernel.services.posting_orchestrator import PostingOrchestrator

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

from sqlalchemy.orm import Session

from finance_config.compiler import CompiledPolicyPack
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.meaning_builder import MeaningBuilder
from finance_kernel.domain.policy_authority import PolicyAuthority
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.contract_service import ContractService
from finance_kernel.services.engine_dispatcher import EngineDispatcher
from finance_kernel.services.ingestor_service import IngestorService
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.ledger_service import LedgerService
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.reference_snapshot_service import ReferenceSnapshotService
from finance_kernel.services.sequence_service import SequenceService


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
        self.sequence_service = SequenceService(session)
        self.auditor = AuditorService(session, self._clock)
        self.period_service = PeriodService(session, self._clock)
        self.link_graph = LinkGraphService(session)
        self.snapshot_service = ReferenceSnapshotService(
            session, self._clock, compiled_pack=compiled_pack,
        )
        self.party_service = PartyService(session)
        self.contract_service = ContractService(session)

        # Ledger persistence (depends on auditor)
        self.ledger_service = LedgerService(session, self._clock, self.auditor)

        # Ingestion (depends on auditor)
        self.ingestor = IngestorService(session, self._clock, self.auditor)

        # Journal writing (depends on role_resolver, auditor, G9+G10 hooks)
        self.journal_writer = JournalWriter(
            session, role_resolver, self._clock, self.auditor,
            subledger_control_registry=None,  # Injected per-tenant via set_subledger_registry()
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

    @property
    def session(self) -> Session:
        """The SQLAlchemy session shared by all services."""
        return self._session

    @property
    def clock(self) -> Clock:
        """The clock shared by all services."""
        return self._clock
