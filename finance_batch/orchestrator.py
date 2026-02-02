"""
BatchOrchestrator -- DI container for the batch processing system (Phase 10).

Contract:
    Wires TaskRegistry with module task implementations, creates BatchExecutor,
    and optionally creates BatchScheduler.  Single place where all batch
    dependencies are composed.

Architecture: finance_batch (top-level).  This is the canonical entry point
    for configuring and running batch jobs.

Invariants enforced:
    BT-4  -- Clock injection (all services receive the same Clock).
    BT-5  -- Audit trail (AuditorService wired into executor).
    BT-9  -- No kernel imports of finance_batch (orchestrator lives here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.sequence_service import SequenceService

from finance_batch.services.executor import BatchExecutor
from finance_batch.services.scheduler import BatchScheduler
from finance_batch.tasks.base import TaskRegistry

# Module task implementations
from finance_batch.tasks.ap_tasks import InvoiceMatchTask, PaymentRunTask
from finance_batch.tasks.ar_tasks import DunningLetterTask, SmallBalanceWriteOffTask
from finance_batch.tasks.assets_tasks import MassDepreciationTask
from finance_batch.tasks.cash_tasks import BankReconcileTask
from finance_batch.tasks.credit_loss_tasks import ECLCalculationTask
from finance_batch.tasks.gl_tasks import PeriodEndRevaluationTask, RecurringEntryTask
from finance_batch.tasks.payroll_tasks import LaborCostAllocationTask

if TYPE_CHECKING:
    from finance_config.schema import AccountingConfigurationSet

logger = get_logger("batch.orchestrator")


def _default_task_registry() -> TaskRegistry:
    """Create a TaskRegistry pre-loaded with all module task implementations."""
    registry = TaskRegistry()
    registry.register(PaymentRunTask())
    registry.register(InvoiceMatchTask())
    registry.register(DunningLetterTask())
    registry.register(SmallBalanceWriteOffTask())
    registry.register(MassDepreciationTask())
    registry.register(BankReconcileTask())
    registry.register(ECLCalculationTask())
    registry.register(RecurringEntryTask())
    registry.register(PeriodEndRevaluationTask())
    registry.register(LaborCostAllocationTask())
    return registry


class BatchOrchestrator:
    """DI container for the batch processing system.

    Contract:
        - ``from_session()`` factory creates a fully wired orchestrator.
        - ``create_executor()`` returns a BatchExecutor for ad-hoc jobs.
        - ``create_scheduler()`` returns a BatchScheduler for background use.
        - ``task_registry`` provides access to registered tasks.

    Non-goals:
        - Does NOT start the scheduler automatically -- caller decides.
        - Does NOT manage session lifecycle -- caller controls commits.
    """

    def __init__(
        self,
        session: Session,
        task_registry: TaskRegistry,
        clock: Clock | None = None,
        auditor_service: AuditorService | None = None,
        sequence_service: SequenceService | None = None,
        actor_id: UUID | None = None,
    ) -> None:
        self._session = session
        self._task_registry = task_registry
        self._clock = clock or SystemClock()
        self._auditor = auditor_service or AuditorService(
            session=session, clock=self._clock,
        )
        self._sequence = sequence_service or SequenceService(session)
        self._actor_id = actor_id or uuid4()

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    def from_session(
        cls,
        session: Session,
        clock: Clock | None = None,
        actor_id: UUID | None = None,
        task_registry: TaskRegistry | None = None,
    ) -> BatchOrchestrator:
        """Create a fully wired BatchOrchestrator from a session.

        Args:
            session: SQLAlchemy session for persistence.
            clock: Optional clock for deterministic testing (BT-4).
            actor_id: Optional actor UUID for audit attribution.
            task_registry: Optional pre-configured registry. If None,
                uses the default registry with all module tasks.
        """
        effective_clock = clock or SystemClock()
        registry = task_registry if task_registry is not None else _default_task_registry()

        return cls(
            session=session,
            task_registry=registry,
            clock=effective_clock,
            auditor_service=AuditorService(session=session, clock=effective_clock),
            sequence_service=SequenceService(session),
            actor_id=actor_id,
        )

    # -------------------------------------------------------------------------
    # Executor
    # -------------------------------------------------------------------------

    def create_executor(self, session: Session | None = None) -> BatchExecutor:
        """Create a BatchExecutor wired with the orchestrator's dependencies.

        Args:
            session: Optional session override. If None, uses the
                orchestrator's session.
        """
        target_session = session or self._session
        return BatchExecutor(
            session=target_session,
            task_registry=self._task_registry,
            clock=self._clock,
            auditor_service=(
                AuditorService(session=target_session, clock=self._clock)
                if session is not None
                else self._auditor
            ),
            sequence_service=(
                SequenceService(target_session)
                if session is not None
                else self._sequence
            ),
        )

    # -------------------------------------------------------------------------
    # Scheduler
    # -------------------------------------------------------------------------

    def create_scheduler(
        self,
        session_factory: Callable[[], Session],
        tick_interval_seconds: int = 60,
    ) -> BatchScheduler:
        """Create a BatchScheduler wired with the orchestrator's dependencies.

        Args:
            session_factory: Callable returning new sessions for each tick.
            tick_interval_seconds: Polling interval (default 60s).
        """
        clock = self._clock
        registry = self._task_registry

        def executor_factory(session: Session) -> BatchExecutor:
            return BatchExecutor(
                session=session,
                task_registry=registry,
                clock=clock,
                auditor_service=AuditorService(session=session, clock=clock),
                sequence_service=SequenceService(session),
            )

        return BatchScheduler(
            session_factory=session_factory,
            executor_factory=executor_factory,
            clock=clock,
            actor_id=self._actor_id,
            tick_interval_seconds=tick_interval_seconds,
        )

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def session(self) -> Session:
        return self._session

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def task_registry(self) -> TaskRegistry:
        return self._task_registry

    @property
    def actor_id(self) -> UUID:
        return self._actor_id
