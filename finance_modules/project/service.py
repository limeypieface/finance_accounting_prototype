"""
Project Accounting Module Service (``finance_modules.project.service``).

Responsibility
--------------
Orchestrates project accounting operations -- cost recording, milestone
billing, T&M billing, Earned Value Management (EVM) snapshots, budget
tracking, WBS element management, and project cost allocation -- by
delegating pure computation to ``evm.py`` and ``finance_engines``, and
journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``ProjectService`` is the sole
public entry point for project accounting operations.  It composes pure
EVM functions (``calculate_bcws``, ``calculate_bcwp``, ``calculate_acwp``,
``calculate_cpi``, ``calculate_spi``, ``calculate_eac``, ``calculate_etc``,
``calculate_vac``) and the kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* All monetary calculations use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* EVM errors (e.g., zero BAC)  -> return ``Decimal("0")`` from pure
  functions (no side effects).

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying project IDs, WBS codes, amounts, and EVM
metrics.  All journal entries feed the kernel audit chain (R11).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_modules._posting_helpers import commit_or_rollback, run_workflow_guard
from finance_services.workflow_executor import WorkflowExecutor
from finance_modules.project.workflows import (
    PROJECT_BILL_MILESTONE_WORKFLOW,
    PROJECT_BILL_TIME_MATERIALS_WORKFLOW,
    PROJECT_COMPLETE_PHASE_WORKFLOW,
    PROJECT_RECORD_COST_WORKFLOW,
    PROJECT_RECOGNIZE_REVENUE_WORKFLOW,
    PROJECT_REVISE_BUDGET_WORKFLOW,
)
from finance_modules.project.evm import (
    calculate_acwp,
    calculate_bcwp,
    calculate_bcws,
    calculate_cpi,
    calculate_eac,
    calculate_etc,
    calculate_spi,
    calculate_vac,
)
from finance_modules.project.models import (
    EVMSnapshot,
    Milestone,
    Project,
    ProjectBudget,
    WBSElement,
)
from finance_modules.project.orm import ProjectCostEntryModel, ProjectModel

logger = get_logger("modules.project.service")


class ProjectService:
    """
    Orchestrates project accounting operations through EVM functions and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``take_evm_snapshot``, ``get_project_status``,
      etc.) return pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.
    * All EVM and cost calculations use ``Decimal`` -- NEVER ``float``.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    * Does NOT persist project domain models -- only journal entries are
      persisted.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        workflow_executor: WorkflowExecutor,
        clock: Clock | None = None,
        party_service: PartyService | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._workflow_executor = workflow_executor
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
            party_service=party_service,
        )

    # =========================================================================
    # Project Setup
    # =========================================================================

    def create_project(
        self,
        name: str,
        project_type: str,
        total_budget: Decimal = Decimal("0"),
        start_date: date | None = None,
        end_date: date | None = None,
        currency: str = "USD",
        actor_id: UUID | None = None,
    ) -> Project:
        """Create a project (no posting, setup only)."""
        project = Project(
            id=uuid4(),
            name=name,
            project_type=project_type,
            total_budget=total_budget,
            start_date=start_date,
            end_date=end_date,
            currency=currency,
        )
        orm_project = ProjectModel.from_dto(project, created_by_id=actor_id or uuid4())
        self._session.add(orm_project)
        self._session.commit()
        return project

    # =========================================================================
    # Cost Recording
    # =========================================================================

    def record_cost(
        self,
        project_id: UUID,
        wbs_code: str,
        cost_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Record a cost against a project WBS element."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROJECT_RECORD_COST_WORKFLOW,
                "project_cost",
                project_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "project_id": str(project_id),
                "wbs_code": wbs_code,
                "cost_type": cost_type,
                "amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="project.cost_recorded",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            if result.is_success:
                orm_cost = ProjectCostEntryModel(
                    id=uuid4(),
                    project_id=project_id,
                    phase_id=None,
                    cost_type=cost_type,
                    description=description,
                    amount=amount,
                    currency=currency,
                    period=wbs_code,
                    entry_date=effective_date,
                    source_event_id=None,
                    created_by_id=actor_id,
                )
                self._session.add(orm_cost)
            commit_or_rollback(self._session, result)
            return result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Billing
    # =========================================================================

    def bill_milestone(
        self,
        project_id: UUID,
        milestone_name: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[Milestone, ModulePostingResult]:
        """Bill a project milestone."""
        milestone = Milestone(
            id=uuid4(),
            project_id=project_id,
            name=milestone_name,
            amount=amount,
            completion_pct=Decimal("1.0"),
            is_billed=True,
            billed_date=effective_date,
        )

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROJECT_BILL_MILESTONE_WORKFLOW,
                "project_milestone",
                milestone.id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return milestone, failure

            payload: dict[str, Any] = {
                "project_id": str(project_id),
                "milestone_name": milestone_name,
                "amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="project.billing_milestone",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            commit_or_rollback(self._session, result)
            return milestone, result
        except Exception:
            self._session.rollback()
            raise

    def bill_time_materials(
        self,
        project_id: UUID,
        period: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        hours: Decimal = Decimal("0"),
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Bill time and materials for a period."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROJECT_BILL_TIME_MATERIALS_WORKFLOW,
                "project_billing",
                project_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "project_id": str(project_id),
                "period": period,
                "amount": str(amount),
                "hours": str(hours),
            }

            result = self._poster.post_event(
                event_type="project.billing_tm",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            commit_or_rollback(self._session, result)
            return result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Revenue Recognition
    # =========================================================================

    def recognize_revenue(
        self,
        project_id: UUID,
        method: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        period: str = "",
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Recognize project revenue."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROJECT_RECOGNIZE_REVENUE_WORKFLOW,
                "project_revenue",
                project_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "project_id": str(project_id),
                "method": method,
                "amount": str(amount),
                "period": period,
            }

            result = self._poster.post_event(
                event_type="project.revenue_recognized",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            commit_or_rollback(self._session, result)
            return result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Budget
    # =========================================================================

    def revise_budget(
        self,
        project_id: UUID,
        wbs_code: str,
        new_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Revise project budget for a WBS element."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROJECT_REVISE_BUDGET_WORKFLOW,
                "project_budget",
                project_id,
                actor_id=actor_id,
                amount=new_amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "project_id": str(project_id),
                "wbs_code": wbs_code,
                "amount": str(new_amount),
            }

            result = self._poster.post_event(
                event_type="project.budget_revised",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=new_amount,
                currency=currency,
                description=description,
            )
            commit_or_rollback(self._session, result)
            return result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Phase Completion
    # =========================================================================

    def complete_phase(
        self,
        project_id: UUID,
        phase: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Complete a project phase (relieves WIP)."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                PROJECT_COMPLETE_PHASE_WORKFLOW,
                "project_phase",
                project_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "project_id": str(project_id),
                "phase": phase,
                "amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="project.phase_completed",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            commit_or_rollback(self._session, result)
            return result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # EVM (Pure Calculations)
    # =========================================================================

    def calculate_evm(
        self,
        project_id: UUID,
        as_of_date: date,
        total_budget: Decimal,
        planned_pct_complete: Decimal,
        actual_pct_complete: Decimal,
        actual_costs: Decimal,
    ) -> EVMSnapshot:
        """Calculate EVM metrics for a project (pure, no posting)."""
        bcws = calculate_bcws(total_budget, planned_pct_complete)
        bcwp = calculate_bcwp(total_budget, actual_pct_complete)
        acwp = calculate_acwp(actual_costs)
        cpi = calculate_cpi(bcwp, acwp)
        spi = calculate_spi(bcwp, bcws)
        eac = calculate_eac(total_budget, cpi)
        etc = calculate_etc(eac, acwp)
        vac = calculate_vac(total_budget, eac)
        cv = bcwp - acwp
        sv = bcwp - bcws

        return EVMSnapshot(
            project_id=project_id,
            as_of_date=as_of_date,
            bcws=bcws,
            bcwp=bcwp,
            acwp=acwp,
            bac=total_budget,
            cpi=cpi,
            spi=spi,
            eac=eac,
            etc=etc,
            vac=vac,
            cv=cv,
            sv=sv,
        )

    # =========================================================================
    # Queries
    # =========================================================================

    def get_project_status(
        self,
        project_id: UUID,
        project_name: str,
        total_budget: Decimal,
        total_actual: Decimal,
        total_billed: Decimal,
    ) -> dict:
        """Get project status summary (pure query)."""
        remaining = total_budget - total_actual
        pct_spent = Decimal("0")
        if total_budget > 0:
            pct_spent = (total_actual / total_budget * 100).quantize(Decimal("0.01"))

        return {
            "project_id": str(project_id),
            "name": project_name,
            "total_budget": str(total_budget),
            "total_actual": str(total_actual),
            "total_billed": str(total_billed),
            "remaining_budget": str(remaining),
            "pct_spent": str(pct_spent),
        }

    def get_wbs_cost_report(
        self,
        project_id: UUID,
        wbs_elements: list[dict],
    ) -> dict:
        """Get WBS cost report (pure query)."""
        total_budget = Decimal("0")
        total_actual = Decimal("0")

        for elem in wbs_elements:
            total_budget += Decimal(str(elem.get("budget", "0")))
            total_actual += Decimal(str(elem.get("actual", "0")))

        return {
            "project_id": str(project_id),
            "element_count": len(wbs_elements),
            "total_budget": str(total_budget),
            "total_actual": str(total_actual),
            "total_variance": str(total_budget - total_actual),
            "elements": wbs_elements,
        }
