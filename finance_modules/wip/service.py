"""
WIP (Work in Process) Module Service (``finance_modules.wip.service``).

Responsibility
--------------
Orchestrates WIP (Work in Process) manufacturing operations -- material
issues, labor charges, overhead application, scrap recording, byproduct
recognition, job completion, production cost summaries, unit cost
breakdowns, and production variance analysis -- by delegating pure
computation to ``finance_engines`` and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``WipService`` is the sole public
entry point for WIP operations.  It composes stateless engines
(``AllocationEngine``, ``VarianceCalculator``), stateful engines
(``ValuationLayer``, ``LinkGraphService``), and the kernel
``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* L5  -- Atomicity: link creation and journal posting share a single
          transaction (``auto_commit=False``).

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* ValuationLayer errors (e.g., insufficient lot quantity)  -> propagate
  before posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying job IDs, item IDs, quantities, and costs.
All journal entries feed the kernel audit chain (R11).  Cost-lot links
(``EconomicLink``) tie material issues to jobs and jobs to finished goods
for full cost-flow traceability.

Usage::

    service = WipService(session, role_resolver, clock)
    result = service.record_material_issue(
        issue_id=uuid4(), job_id="JOB-100",
        item_id="STEEL-001", quantity=Decimal("50"),
        cost=Decimal("1250.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_modules.wip.models import (
    ByproductRecord,
    ProductionCostSummary,
    UnitCostBreakdown,
)
from finance_modules.wip.orm import (
    LaborEntryModel,
    OverheadApplicationModel,
    WorkOrderModel,
    ByproductRecordModel,
)
from finance_services.valuation_service import ValuationLayer
from finance_engines.allocation import AllocationEngine, AllocationMethod, AllocationTarget
from finance_engines.variance import VarianceCalculator, VarianceResult

logger = get_logger("modules.wip.service")


class WipService:
    """
    Orchestrates Work-in-Process operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``get_production_cost_summary``,
      ``get_unit_cost_breakdown``, etc.) return pure domain objects with no
      side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine link writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).

    Engine composition:
    - ValuationLayer: WIP cost layer valuation
    - AllocationEngine: overhead allocation across jobs
    - VarianceCalculator: labor, material, and overhead variances
    - LinkGraphService: production link tracking (material -> job, job -> FG)

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    (links, allocations) and journal writes share a single transaction.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        # Kernel posting (auto_commit=False -- we own the boundary)
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

        # Stateful engines (share session for atomicity)
        self._link_graph = LinkGraphService(session)
        self._valuation = ValuationLayer(session, self._link_graph)

        # Stateless engines
        self._allocation = AllocationEngine()
        self._variance = VarianceCalculator()

    # =========================================================================
    # Material Issue
    # =========================================================================

    def record_material_issue(
        self,
        issue_id: UUID,
        job_id: str,
        item_id: str,
        quantity: Decimal,
        cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        warehouse: str | None = None,
    ) -> ModulePostingResult:
        """
        Record raw materials issued to a work order.

        Engine: LinkGraphService establishes material-to-job link.
        Profile: wip.material_issued -> WipMaterialIssued
        """
        try:
            # Engine: establish production link (material -> job)
            from finance_kernel.domain.economic_link import EconomicLink, LinkType
            material_ref = ArtifactRef(ArtifactType.COST_LOT, issue_id)
            job_ref = ArtifactRef(ArtifactType.EVENT, uuid4())

            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.CONSUMED_BY,
                parent_ref=material_ref,
                child_ref=job_ref,
                creating_event_id=issue_id,
                created_at=self._clock.now(),
                metadata={
                    "job_id": job_id,
                    "item_id": item_id,
                    "quantity": str(quantity),
                    "cost": str(cost),
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            logger.info("wip_material_issued", extra={
                "issue_id": str(issue_id),
                "job_id": job_id,
                "item_id": item_id,
                "quantity": str(quantity),
                "cost": str(cost),
            })

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="wip.material_issued",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "item_code": item_id,
                    "job_id": job_id,
                    "warehouse": warehouse,
                    "cost": str(cost),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(cost)),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Labor Charge
    # =========================================================================

    def record_labor_charge(
        self,
        charge_id: UUID,
        job_id: str,
        work_order_id: UUID,
        operation_id: UUID,
        hours: Decimal,
        rate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        employee_id: str | None = None,
        labor_code: str | None = None,
    ) -> ModulePostingResult:
        """
        Record direct labor charged to a work order.

        Profile: wip.labor_charged -> WipLaborCharged
        """
        total_cost = hours * rate
        try:
            logger.info("wip_labor_charge", extra={
                "charge_id": str(charge_id),
                "job_id": job_id,
                "hours": str(hours),
                "rate": str(rate),
                "total_cost": str(total_cost),
            })

            result = self._poster.post_event(
                event_type="wip.labor_charged",
                payload={
                    "hours": str(hours),
                    "rate": str(rate),
                    "job_id": job_id,
                    "employee_id": employee_id,
                    "labor_code": labor_code,
                    "total_cost": str(total_cost),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total_cost)),
                currency=currency,
            )

            if result.is_success:
                orm_labor = LaborEntryModel(
                    id=charge_id,
                    work_order_id=work_order_id,
                    operation_id=operation_id,
                    employee_id=UUID(employee_id) if employee_id else uuid4(),
                    work_date=effective_date,
                    hours=hours,
                    labor_rate=rate,
                    labor_cost=total_cost,
                    entry_type=labor_code or "run",
                    created_by_id=actor_id,
                )
                self._session.add(orm_labor)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Overhead Allocation
    # =========================================================================

    def record_overhead_allocation(
        self,
        job_id: str,
        work_order_id: UUID,
        allocation_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        allocation_base: str | None = None,
        rate: Decimal | None = None,
    ) -> ModulePostingResult:
        """
        Record overhead applied to a work order.

        Engine: AllocationEngine can be used upstream to compute the
        allocation_amount across multiple jobs; this method records the
        per-job result.

        Profile: wip.overhead_applied -> WipOverheadApplied
        """
        try:
            logger.info("wip_overhead_allocation", extra={
                "job_id": job_id,
                "allocation_amount": str(allocation_amount),
                "allocation_base": allocation_base,
                "rate": str(rate) if rate else None,
            })

            result = self._poster.post_event(
                event_type="wip.overhead_applied",
                payload={
                    "job_id": job_id,
                    "allocation_amount": str(allocation_amount),
                    "allocation_base": allocation_base,
                    "rate": str(rate) if rate else None,
                    "quantity": "1",
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(allocation_amount)),
                currency=currency,
            )

            if result.is_success:
                orm_overhead = OverheadApplicationModel(
                    id=uuid4(),
                    work_order_id=work_order_id,
                    application_date=effective_date,
                    overhead_type="variable",
                    basis=allocation_base or "labor_hours",
                    rate=rate or Decimal("0"),
                    quantity=Decimal("1"),
                    amount=allocation_amount,
                    created_by_id=actor_id,
                )
                self._session.add(orm_overhead)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Job Completion
    # =========================================================================

    def complete_job(
        self,
        job_id: str,
        effective_date: date,
        actor_id: UUID,
        quantity: Decimal = Decimal("1"),
        unit_cost: Decimal | None = None,
        total_cost: Decimal | None = None,
        currency: str = "USD",
        item_id: str | None = None,
    ) -> ModulePostingResult:
        """
        Complete a job and transfer WIP to finished goods.

        Engine: LinkGraphService links production job to finished goods.
        Profile: wip.completion -> WipCompletion

        Caller must supply either total_cost or unit_cost (total = qty * unit).
        """
        if total_cost is None and unit_cost is not None:
            total_cost = quantity * unit_cost
        elif total_cost is None:
            raise ValueError("Either total_cost or unit_cost must be provided")

        try:
            # Engine: establish completion link (job -> finished goods)
            from finance_kernel.domain.economic_link import EconomicLink, LinkType

            completion_id = uuid4()
            job_ref = ArtifactRef(ArtifactType.EVENT, completion_id)
            fg_ref = ArtifactRef(ArtifactType.RECEIPT, uuid4())

            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.DERIVED_FROM,
                parent_ref=job_ref,
                child_ref=fg_ref,
                creating_event_id=completion_id,
                created_at=self._clock.now(),
                metadata={
                    "job_id": job_id,
                    "quantity": str(quantity),
                    "total_cost": str(total_cost),
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            logger.info("wip_job_completion", extra={
                "job_id": job_id,
                "quantity": str(quantity),
                "total_cost": str(total_cost),
                "item_id": item_id,
            })

            result = self._poster.post_event(
                event_type="wip.completion",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "job_id": job_id,
                    "item_code": item_id,
                    "total_cost": str(total_cost),
                    "unit_cost": str(total_cost / quantity) if quantity else None,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total_cost)),
                currency=currency,
            )

            if result.is_success:
                orm_work_order = WorkOrderModel(
                    id=completion_id,
                    order_number=job_id,
                    item_id=UUID(str(item_id)) if item_id else uuid4(),
                    quantity_ordered=quantity,
                    quantity_completed=quantity,
                    quantity_scrapped=Decimal("0"),
                    planned_start_date=None,
                    planned_end_date=None,
                    actual_start_date=None,
                    actual_end_date=effective_date,
                    status="completed",
                    parent_work_order_id=None,
                    sales_order_id=None,
                    created_by_id=actor_id,
                )
                self._session.add(orm_work_order)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Scrap
    # =========================================================================

    def record_scrap(
        self,
        scrap_id: UUID,
        job_id: str,
        quantity: Decimal,
        cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        reason: str | None = None,
    ) -> ModulePostingResult:
        """
        Record scrap on a work order.

        Profile: wip.scrap -> WipScrap
        """
        try:
            logger.info("wip_scrap_recorded", extra={
                "scrap_id": str(scrap_id),
                "job_id": job_id,
                "quantity": str(quantity),
                "cost": str(cost),
                "reason": reason,
            })

            result = self._poster.post_event(
                event_type="wip.scrap",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "job_id": job_id,
                    "cost": str(cost),
                    "reason": reason,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(cost)),
                currency=currency,
            )

            if result.is_success:
                # Query for existing work order by order_number to update scrap qty
                existing_wo = (
                    self._session.query(WorkOrderModel)
                    .filter(WorkOrderModel.order_number == job_id)
                    .first()
                )
                if existing_wo is not None:
                    existing_wo.quantity_scrapped = (
                        existing_wo.quantity_scrapped + quantity
                    )
                    existing_wo.updated_by_id = actor_id
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Rework
    # =========================================================================

    def record_rework(
        self,
        rework_id: UUID,
        job_id: str,
        quantity: Decimal,
        cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        reason: str | None = None,
    ) -> ModulePostingResult:
        """
        Record rework costs charged to a work order.

        Profile: wip.rework -> WipRework
        """
        try:
            logger.info("wip_rework_recorded", extra={
                "rework_id": str(rework_id),
                "job_id": job_id,
                "quantity": str(quantity),
                "cost": str(cost),
                "reason": reason,
            })

            result = self._poster.post_event(
                event_type="wip.rework",
                payload={
                    "quantity": int(quantity) if quantity == int(quantity) else str(quantity),
                    "job_id": job_id,
                    "cost": str(cost),
                    "reason": reason,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(cost)),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Variance Recording
    # =========================================================================

    def record_labor_variance(
        self,
        job_id: str,
        standard_hours: Decimal,
        actual_hours: Decimal,
        standard_rate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[VarianceResult, ModulePostingResult]:
        """
        Record labor efficiency variance for a job.

        Engine: VarianceCalculator.quantity_variance() for hours efficiency.
        Profile: wip.labor_variance -> WipLaborVariance
        """
        try:
            variance_result = self._variance.quantity_variance(
                expected_quantity=standard_hours,
                actual_quantity=actual_hours,
                standard_price=Money.of(standard_rate, currency),
            )

            logger.info("wip_labor_variance", extra={
                "job_id": job_id,
                "variance_amount": str(variance_result.variance.amount),
                "is_favorable": variance_result.is_favorable,
            })

            result = self._poster.post_event(
                event_type="wip.labor_variance",
                payload={
                    "job_id": job_id,
                    "standard_hours": str(standard_hours),
                    "actual_hours": str(actual_hours),
                    "standard_rate": str(standard_rate),
                    "variance_amount": str(variance_result.variance.amount),
                    "is_favorable": variance_result.is_favorable,
                    "quantity": "1",
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(variance_result.variance.amount),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return variance_result, result

        except Exception:
            self._session.rollback()
            raise

    def record_material_variance(
        self,
        job_id: str,
        standard_quantity: Decimal,
        actual_quantity: Decimal,
        standard_cost: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[VarianceResult, ModulePostingResult]:
        """
        Record material usage variance for a job.

        Engine: VarianceCalculator.quantity_variance() for usage efficiency.
        Profile: wip.material_variance -> WipMaterialVariance
        """
        try:
            variance_result = self._variance.quantity_variance(
                expected_quantity=standard_quantity,
                actual_quantity=actual_quantity,
                standard_price=Money.of(standard_cost, currency),
            )

            logger.info("wip_material_variance", extra={
                "job_id": job_id,
                "variance_amount": str(variance_result.variance.amount),
                "is_favorable": variance_result.is_favorable,
            })

            result = self._poster.post_event(
                event_type="wip.material_variance",
                payload={
                    "job_id": job_id,
                    "standard_quantity": str(standard_quantity),
                    "actual_quantity": str(actual_quantity),
                    "standard_cost": str(standard_cost),
                    "variance_amount": str(variance_result.variance.amount),
                    "is_favorable": variance_result.is_favorable,
                    "quantity": "1",
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(variance_result.variance.amount),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return variance_result, result

        except Exception:
            self._session.rollback()
            raise

    def record_overhead_variance(
        self,
        applied_overhead: Decimal,
        actual_overhead: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[VarianceResult, ModulePostingResult]:
        """
        Record overhead over/under-applied variance.

        Engine: VarianceCalculator.standard_cost_variance() for applied vs actual.
        Profile: wip.overhead_variance -> WipOverheadVariance
        """
        try:
            variance_result = self._variance.standard_cost_variance(
                standard_cost=Money.of(applied_overhead, currency),
                actual_cost=Money.of(actual_overhead, currency),
            )

            logger.info("wip_overhead_variance", extra={
                "applied_overhead": str(applied_overhead),
                "actual_overhead": str(actual_overhead),
                "variance_amount": str(variance_result.variance.amount),
                "is_favorable": variance_result.is_favorable,
            })

            result = self._poster.post_event(
                event_type="wip.overhead_variance",
                payload={
                    "applied_overhead": str(applied_overhead),
                    "actual_overhead": str(actual_overhead),
                    "variance_amount": str(variance_result.variance.amount),
                    "is_favorable": variance_result.is_favorable,
                    "quantity": "1",
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(variance_result.variance.amount),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return variance_result, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Production Cost Summary
    # =========================================================================

    def calculate_production_cost(
        self,
        job_id: UUID,
        material_cost: Decimal,
        labor_cost: Decimal,
        overhead_cost: Decimal,
        units_produced: Decimal,
    ) -> ProductionCostSummary:
        """
        Calculate aggregated production costs for a job.

        Pure calculation -- no journal posting. Useful for reporting and
        feeding into unit-cost or variance analyses.
        """
        total_cost = material_cost + labor_cost + overhead_cost

        logger.info("wip_production_cost_calculated", extra={
            "job_id": str(job_id),
            "material_cost": str(material_cost),
            "labor_cost": str(labor_cost),
            "overhead_cost": str(overhead_cost),
            "total_cost": str(total_cost),
            "units_produced": str(units_produced),
        })

        return ProductionCostSummary(
            job_id=job_id,
            material_cost=material_cost,
            labor_cost=labor_cost,
            overhead_cost=overhead_cost,
            total_cost=total_cost,
            units_produced=units_produced,
        )

    # =========================================================================
    # Byproduct Recording
    # =========================================================================

    def record_byproduct(
        self,
        job_id: UUID,
        item_id: UUID,
        description: str,
        value: Decimal,
        quantity: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[ByproductRecord, ModulePostingResult]:
        """
        Record a byproduct from a production job.

        Profile: wip.byproduct -> WIPByproductRecorded
        Dr INVENTORY / Cr WIP -- recognizes recoverable value of byproduct.
        """
        byproduct_id = uuid4()
        try:
            logger.info("wip_byproduct_recorded", extra={
                "byproduct_id": str(byproduct_id),
                "job_id": str(job_id),
                "item_id": str(item_id),
                "description": description,
                "value": str(value),
                "quantity": str(quantity),
            })

            result = self._poster.post_event(
                event_type="wip.byproduct",
                payload={
                    "job_id": str(job_id),
                    "item_id": str(item_id),
                    "description": description,
                    "value": str(value),
                    "quantity": str(quantity),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(value)),
                currency=currency,
            )

            record = ByproductRecord(
                id=byproduct_id,
                job_id=job_id,
                item_id=item_id,
                description=description,
                value=value,
                quantity=quantity,
            )

            if result.is_success:
                orm_byproduct = ByproductRecordModel(
                    id=byproduct_id,
                    job_id=job_id,
                    item_id=item_id,
                    description=description,
                    value=value,
                    quantity=quantity,
                    created_by_id=actor_id,
                )
                self._session.add(orm_byproduct)
                self._session.commit()
            else:
                self._session.rollback()
            return record, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Unit Cost Breakdown
    # =========================================================================

    def calculate_unit_cost(
        self,
        job_id: UUID,
        material_cost: Decimal,
        labor_cost: Decimal,
        overhead_cost: Decimal,
        units_produced: Decimal,
    ) -> UnitCostBreakdown:
        """
        Calculate per-unit cost breakdown by component.

        Pure calculation -- no journal posting. Divides each cost component
        by units_produced to derive per-unit rates.

        Raises ValueError if units_produced is zero.
        """
        if units_produced == 0:
            raise ValueError("units_produced must be non-zero for unit cost calculation")

        material_per_unit = material_cost / units_produced
        labor_per_unit = labor_cost / units_produced
        overhead_per_unit = overhead_cost / units_produced
        total_per_unit = material_per_unit + labor_per_unit + overhead_per_unit

        logger.info("wip_unit_cost_calculated", extra={
            "job_id": str(job_id),
            "units_produced": str(units_produced),
            "material_per_unit": str(material_per_unit),
            "labor_per_unit": str(labor_per_unit),
            "overhead_per_unit": str(overhead_per_unit),
            "total_per_unit": str(total_per_unit),
        })

        return UnitCostBreakdown(
            job_id=job_id,
            units_produced=units_produced,
            material_per_unit=material_per_unit,
            labor_per_unit=labor_per_unit,
            overhead_per_unit=overhead_per_unit,
            total_per_unit=total_per_unit,
        )
