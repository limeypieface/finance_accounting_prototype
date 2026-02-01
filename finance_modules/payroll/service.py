"""
Payroll Module Service (``finance_modules.payroll.service``).

Responsibility
--------------
Orchestrates payroll operations -- payroll runs, tax withholding (federal,
state, FICA), employer tax accruals, benefit deductions, cost-center
allocation, DCAA fringe/overhead/G&A cascade, variance analysis, and ACH
batch generation -- by delegating pure computation to ``finance_engines``
and ``helpers.py``, and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``PayrollService`` is the sole public
entry point for payroll operations.  It composes stateless engines
(``AllocationEngine``, ``AllocationCascade``, ``VarianceCalculator``) and
pure helper functions (``calculate_federal_withholding``, ``calculate_fica``,
``calculate_state_withholding``, ``generate_nacha_batch``), plus the kernel
``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* DCAA -- Indirect cost allocation follows FAR/CAS methodology via
           ``build_dcaa_cascade``.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Engine errors (e.g., zero-length allocation targets)  -> propagate before
  posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying run IDs, employee IDs, gross pay amounts,
and withholding breakdowns.  All journal entries feed the kernel audit
chain (R11).  Payroll is SOX-critical and requires full withholding
traceability.

Usage::

    service = PayrollService(session, role_resolver, clock)
    result = service.record_payroll_run(
        run_id=uuid4(), employee_id="EMP-001",
        gross_pay=Decimal("5000.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence
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
from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationResult,
    AllocationTarget,
)
from finance_engines.allocation_cascade import (
    AllocationStep,
    AllocationStepResult,
    execute_cascade,
    build_dcaa_cascade,
)
from finance_engines.variance import VarianceCalculator, VarianceResult
from finance_modules.payroll.helpers import (
    calculate_federal_withholding,
    calculate_fica,
    calculate_state_withholding,
    generate_nacha_batch,
)
from finance_modules.payroll.models import (
    BenefitsDeduction,
    EmployerContribution,
    WithholdingResult,
)
from finance_modules.payroll.orm import (
    BenefitsDeductionModel,
    EmployerContributionModel,
    PayrollRunModel,
)

logger = get_logger("modules.payroll.service")


class PayrollService:
    """
    Orchestrates payroll operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``calculate_withholding_breakdown``,
      ``generate_nacha_batch``, etc.) return pure domain objects with no
      side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.
    * DCAA fringe/overhead/G&A cascade follows FAR/CAS methodology.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    * Does NOT persist payroll domain models -- only journal entries are
      persisted.

    Engine composition:
    - AllocationEngine: distributes payroll costs across cost centers/projects
    - AllocationCascade: fringe/overhead/G&A indirect cost cascade (DCAA)
    - VarianceCalculator: budget vs actual payroll variances

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    and journal writes share a single transaction.
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

        # Stateless engines
        self._allocation = AllocationEngine()
        self._variance = VarianceCalculator()

    # =========================================================================
    # Payroll Run (Accrual)
    # =========================================================================

    def record_payroll_run(
        self,
        run_id: UUID,
        employee_id: str,
        gross_pay: Decimal,
        effective_date: date,
        actor_id: UUID,
        pay_period_id: UUID | None = None,
        currency: str = "USD",
        department: str | None = None,
        federal_tax: Decimal | None = None,
        state_tax: Decimal | None = None,
        fica: Decimal | None = None,
        benefits: Decimal | None = None,
    ) -> ModulePostingResult:
        """
        Record a payroll run (expense accrual with withholding breakdowns).

        Profile: payroll.accrual -> PayrollAccrual
        """
        try:
            logger.info("payroll_run_started", extra={
                "run_id": str(run_id),
                "employee_id": employee_id,
                "gross_pay": str(gross_pay),
                "department": department,
            })

            # Compute net pay: gross minus all withholdings
            withholdings = sum(
                w for w in (federal_tax, state_tax, fica, benefits) if w is not None
            )
            net_pay = gross_pay - withholdings

            payload: dict = {
                "employee_id": employee_id,
                "gross_amount": str(gross_pay),
                "net_pay_amount": str(net_pay),
                "department": department,
            }
            if federal_tax is not None:
                payload["federal_tax_amount"] = str(federal_tax)
            if state_tax is not None:
                payload["state_tax_amount"] = str(state_tax)
            if fica is not None:
                payload["fica_amount"] = str(fica)
            if benefits is not None:
                payload["benefits_amount"] = str(benefits)

            result = self._poster.post_event(
                event_type="payroll.accrual",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(gross_pay)),
                currency=currency,
                event_id=run_id,
            )

            if result.is_success:
                withholdings_total = sum(
                    w for w in (federal_tax, state_tax, fica, benefits) if w is not None
                )
                orm_model = PayrollRunModel(
                    id=run_id,
                    pay_period_id=pay_period_id or run_id,
                    run_date=effective_date,
                    total_gross=gross_pay,
                    total_taxes=Decimal(str(
                        sum(w for w in (federal_tax, state_tax, fica) if w is not None)
                    )),
                    total_deductions=Decimal(str(withholdings_total)),
                    total_net=gross_pay - withholdings_total,
                    employee_count=1,
                    status="draft",
                    created_by_id=actor_id,
                )
                self._session.add(orm_model)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Payroll Tax
    # =========================================================================

    def record_payroll_tax(
        self,
        run_id: UUID,
        tax_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        deposit_reference: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a payroll tax deposit/remittance.

        Links the tax deposit back to the originating payroll run via
        LinkGraphService.

        Profile: payroll.tax_deposit -> PayrollTaxDeposit
        """
        try:
            # Engine: link tax deposit to payroll run
            from finance_kernel.domain.economic_link import EconomicLink, LinkType
            tax_event_id = uuid4()
            run_ref = ArtifactRef(ArtifactType.EVENT, run_id)
            tax_ref = ArtifactRef(ArtifactType.PAYMENT, tax_event_id)

            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.DERIVED_FROM,
                parent_ref=run_ref,
                child_ref=tax_ref,
                creating_event_id=tax_event_id,
                created_at=self._clock.now(),
                metadata={
                    "tax_type": tax_type,
                    "amount": str(amount),
                    "deposit_reference": deposit_reference,
                },
            )
            self._link_graph.establish_link(link, allow_duplicate=True)

            logger.info("payroll_tax_recorded", extra={
                "run_id": str(run_id),
                "tax_type": tax_type,
                "amount": str(amount),
            })

            # Build payload with tax-type-specific amounts
            payload: dict = {
                "tax_type": tax_type,
                "deposit_amount": str(amount),
                "deposit_reference": deposit_reference,
                "run_id": str(run_id),
            }
            # Map tax type to specific payload fields for profile where-clause dispatch
            tax_field_map = {
                "FEDERAL": "federal_tax_amount",
                "STATE": "state_tax_amount",
                "FICA": "fica_amount",
            }
            field_key = tax_field_map.get(tax_type.upper())
            if field_key:
                payload[field_key] = str(amount)

            result = self._poster.post_event(
                event_type="payroll.tax_deposit",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(amount)),
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
    # Payroll Payment (Net Pay)
    # =========================================================================

    def record_payroll_payment(
        self,
        payment_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record net payroll payment to employees.

        Profile: payroll.payment -> PayrollPayment
        """
        try:
            logger.info("payroll_payment_recorded", extra={
                "payment_id": str(payment_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="payroll.payment",
                payload={
                    "net_amount": str(amount),
                    "payment_id": str(payment_id),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(amount)),
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
    # Benefits Payment
    # =========================================================================

    def record_benefits_payment(
        self,
        payment_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        provider: str | None = None,
    ) -> ModulePostingResult:
        """
        Record benefits payment to providers.

        Profile: payroll.benefits_payment -> PayrollBenefitsPayment
        """
        try:
            logger.info("benefits_payment_recorded", extra={
                "payment_id": str(payment_id),
                "amount": str(amount),
                "provider": provider,
            })

            result = self._poster.post_event(
                event_type="payroll.benefits_payment",
                payload={
                    "amount": str(amount),
                    "provider": provider,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(amount)),
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
    # Timesheet Recording
    # =========================================================================

    def record_regular_hours(
        self,
        timesheet_id: UUID,
        employee_id: str,
        hours: Decimal,
        rate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        department: str | None = None,
    ) -> ModulePostingResult:
        """
        Record regular hourly wages.

        Profile: timesheet.regular -> TimesheetRegular
        """
        total = hours * rate
        try:
            logger.info("timesheet_regular_recorded", extra={
                "timesheet_id": str(timesheet_id),
                "employee_id": employee_id,
                "hours": str(hours),
                "rate": str(rate),
            })

            result = self._poster.post_event(
                event_type="timesheet.regular",
                payload={
                    "hours": str(hours),
                    "rate": str(rate),
                    "pay_code": "REGULAR",
                    "employee_id": employee_id,
                    "department": department,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total)),
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

    def record_overtime(
        self,
        timesheet_id: UUID,
        employee_id: str,
        hours: Decimal,
        rate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        department: str | None = None,
    ) -> ModulePostingResult:
        """
        Record overtime wages.

        Profile: timesheet.overtime -> TimesheetOvertime
        """
        total = hours * rate
        try:
            logger.info("timesheet_overtime_recorded", extra={
                "timesheet_id": str(timesheet_id),
                "employee_id": employee_id,
                "hours": str(hours),
                "rate": str(rate),
            })

            result = self._poster.post_event(
                event_type="timesheet.overtime",
                payload={
                    "hours": str(hours),
                    "rate": str(rate),
                    "pay_code": "OVERTIME",
                    "employee_id": employee_id,
                    "department": department,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total)),
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

    def record_pto(
        self,
        timesheet_id: UUID,
        employee_id: str,
        hours: Decimal,
        rate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        department: str | None = None,
    ) -> ModulePostingResult:
        """
        Record paid time off.

        Profile: timesheet.pto -> TimesheetPTO
        """
        total = hours * rate
        try:
            logger.info("timesheet_pto_recorded", extra={
                "timesheet_id": str(timesheet_id),
                "employee_id": employee_id,
                "hours": str(hours),
                "rate": str(rate),
            })

            result = self._poster.post_event(
                event_type="timesheet.pto",
                payload={
                    "hours": str(hours),
                    "rate": str(rate),
                    "employee_id": employee_id,
                    "department": department,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(total)),
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
    # Labor Cost Allocation
    # =========================================================================

    def allocate_labor_costs(
        self,
        run_id: UUID,
        allocations: Sequence[dict],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Allocate labor costs from a payroll run across cost centers/projects.

        Each allocation dict must contain:
            - target_id: str (cost center or project code)
            - amount: Decimal (labor cost allocated)
            - labor_type: str ("DIRECT", "INDIRECT", or "OVERHEAD")

        Engine: AllocationEngine distributes total across targets.
        Profile: labor.distribution_direct / labor.distribution_indirect /
                 labor.distribution_overhead (dispatched per allocation line)

        Posts one journal entry per allocation line for proper cost center
        tracking.
        """
        if not allocations:
            raise ValueError("At least one allocation target is required")

        total_amount = sum(Decimal(str(a["amount"])) for a in allocations)
        last_result: ModulePostingResult | None = None

        try:
            logger.info("labor_cost_allocation_started", extra={
                "run_id": str(run_id),
                "allocation_count": len(allocations),
                "total_amount": str(total_amount),
            })

            for alloc in allocations:
                target_id = alloc["target_id"]
                alloc_amount = Decimal(str(alloc["amount"]))
                labor_type = alloc.get("labor_type", "DIRECT").upper()

                # Select event type based on labor type
                event_type_map = {
                    "DIRECT": "labor.distribution_direct",
                    "INDIRECT": "labor.distribution_indirect",
                    "OVERHEAD": "labor.distribution_overhead",
                }
                event_type = event_type_map.get(labor_type, "labor.distribution_direct")

                logger.info("labor_allocation_posting", extra={
                    "target_id": target_id,
                    "amount": str(alloc_amount),
                    "labor_type": labor_type,
                    "event_type": event_type,
                })

                last_result = self._poster.post_event(
                    event_type=event_type,
                    payload={
                        "run_id": str(run_id),
                        "target_id": target_id,
                        "amount": str(alloc_amount),
                        "labor_type": labor_type,
                        "cost_center": alloc.get("cost_center"),
                        "project": alloc.get("project"),
                    },
                    effective_date=effective_date,
                    actor_id=actor_id,
                    amount=alloc_amount,
                    currency=currency,
                )

                if not last_result.is_success:
                    self._session.rollback()
                    return last_result

            # All allocations posted successfully
            self._session.commit()
            return last_result  # type: ignore[return-value]

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # DCAA Indirect Cost Cascade
    # =========================================================================

    def run_dcaa_cascade(
        self,
        pool_balances: dict[str, Decimal],
        rates: dict[str, Decimal],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        steps: Sequence[AllocationStep] | None = None,
    ) -> tuple[list[AllocationStepResult], dict[str, Money]]:
        """
        Execute DCAA indirect cost allocation cascade.

        Engine: execute_cascade() for fringe -> overhead -> G&A computation.

        This is a computation-only method that returns the cascade results.
        Callers should use the results to post individual allocation entries
        via record_overhead_allocation or allocate_labor_costs.

        Args:
            pool_balances: Pool balances by pool name (e.g., {"DIRECT_LABOR": Decimal("100000")}).
            rates: Indirect cost rates (e.g., {"fringe": Decimal("0.35")}).
            effective_date: Not used for posting (computation only).
            actor_id: Not used for posting (computation only).
            currency: Currency code for the cascade.
            steps: Optional custom cascade steps (defaults to standard DCAA cascade).

        Returns:
            Tuple of (step results, final pool balances as Money).
        """
        money_balances = {
            pool: Money.of(amount, currency)
            for pool, amount in pool_balances.items()
        }
        cascade_steps = steps or build_dcaa_cascade()

        logger.info("dcaa_cascade_started", extra={
            "pool_count": len(pool_balances),
            "rate_count": len(rates),
            "step_count": len(cascade_steps),
        })

        results, final_balances = execute_cascade(
            steps=cascade_steps,
            pool_balances=money_balances,
            rates=rates,
            currency=currency,
        )

        logger.info("dcaa_cascade_completed", extra={
            "step_results": len(results),
            "final_pool_count": len(final_balances),
        })

        return results, final_balances

    # =========================================================================
    # Budget Variance
    # =========================================================================

    def compute_payroll_variance(
        self,
        budget_amount: Decimal,
        actual_amount: Decimal,
        currency: str = "USD",
    ) -> VarianceResult:
        """
        Compute budget vs actual payroll variance.

        Engine: VarianceCalculator.standard_cost_variance() for budget comparison.

        This is a computation-only method. The caller decides whether to post
        the variance as a journal entry.
        """
        variance_result = self._variance.standard_cost_variance(
            standard_cost=Money.of(budget_amount, currency),
            actual_cost=Money.of(actual_amount, currency),
        )

        logger.info("payroll_variance_computed", extra={
            "budget": str(budget_amount),
            "actual": str(actual_amount),
            "variance": str(variance_result.variance.amount),
            "is_favorable": variance_result.is_favorable,
        })

        return variance_result

    # =========================================================================
    # Gross-to-Net Calculation (Pure)
    # =========================================================================

    def calculate_gross_to_net(
        self,
        employee_id: UUID,
        gross_pay: Decimal,
        filing_status: str = "single",
        allowances: int = 0,
        state_rate: Decimal = Decimal("0.05"),
        ytd_earnings: Decimal = Decimal("0"),
    ) -> WithholdingResult:
        """
        Calculate gross-to-net payroll breakdown using helper functions.

        This is a pure computation method -- no posting or side effects.
        Uses helpers for federal withholding, state withholding, and FICA.

        Returns:
            WithholdingResult with all tax breakdowns and net pay.
        """
        federal = calculate_federal_withholding(gross_pay, filing_status, allowances)
        state = calculate_state_withholding(gross_pay, state_rate)
        ss_tax, medicare_tax = calculate_fica(gross_pay, ytd_earnings)

        total_deductions = federal + state + ss_tax + medicare_tax
        net_pay = gross_pay - total_deductions

        logger.info("gross_to_net_calculated", extra={
            "employee_id": str(employee_id),
            "gross_pay": str(gross_pay),
            "federal": str(federal),
            "state": str(state),
            "ss_tax": str(ss_tax),
            "medicare": str(medicare_tax),
            "net_pay": str(net_pay),
        })

        return WithholdingResult(
            id=uuid4(),
            employee_id=employee_id,
            gross_pay=gross_pay,
            federal_withholding=federal,
            state_withholding=state,
            social_security=ss_tax,
            medicare=medicare_tax,
            total_deductions=total_deductions,
            net_pay=net_pay,
        )

    # =========================================================================
    # Benefits Deduction
    # =========================================================================

    def record_benefits_deduction(
        self,
        employee_id: UUID,
        plan_name: str,
        employee_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        employer_amount: Decimal = Decimal("0"),
        currency: str = "USD",
    ) -> tuple[BenefitsDeduction, ModulePostingResult]:
        """
        Record an employee benefits deduction from paycheck.

        Creates a BenefitsDeduction model and posts via payroll.benefits_deduction.

        Returns:
            Tuple of (BenefitsDeduction, ModulePostingResult).
        """
        deduction = BenefitsDeduction(
            id=uuid4(),
            employee_id=employee_id,
            plan_name=plan_name,
            employee_amount=employee_amount,
            employer_amount=employer_amount,
        )

        try:
            logger.info("benefits_deduction_started", extra={
                "employee_id": str(employee_id),
                "plan_name": plan_name,
                "employee_amount": str(employee_amount),
                "employer_amount": str(employer_amount),
            })

            result = self._poster.post_event(
                event_type="payroll.benefits_deduction",
                payload={
                    "employee_id": str(employee_id),
                    "plan_name": plan_name,
                    "employee_amount": str(employee_amount),
                    "employer_amount": str(employer_amount),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(employee_amount)),
                currency=currency,
            )

            if result.is_success:
                orm_model = BenefitsDeductionModel.from_dto(deduction, created_by_id=actor_id)
                self._session.add(orm_model)
                self._session.commit()
            else:
                self._session.rollback()
            return deduction, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # NACHA File Generation (Pure)
    # =========================================================================

    def generate_nacha_file(
        self,
        payments: list[dict],
        company_name: str,
        company_id: str,
        effective_date: str,
    ) -> str:
        """
        Generate a NACHA/ACH batch for payroll direct deposits.

        This is a pure computation method -- no posting or side effects.
        Delegates to generate_nacha_batch helper.

        Args:
            payments: List of dicts with name, account, routing, amount.
            company_name: Company name for ACH header.
            company_id: Company EIN/ID for ACH header.
            effective_date: Settlement date string.

        Returns:
            Pipe-delimited representation of ACH batch.
        """
        logger.info("nacha_file_generated", extra={
            "company_name": company_name,
            "payment_count": len(payments),
        })

        return generate_nacha_batch(
            payments=payments,
            company_name=company_name,
            company_id=company_id,
            effective_date=effective_date,
        )

    # =========================================================================
    # Employer Contribution
    # =========================================================================

    def record_employer_contribution(
        self,
        employee_id: UUID,
        plan_name: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[EmployerContribution, ModulePostingResult]:
        """
        Record an employer contribution to a benefits plan.

        Creates an EmployerContribution model and posts via
        payroll.employer_contribution.

        Returns:
            Tuple of (EmployerContribution, ModulePostingResult).
        """
        contribution = EmployerContribution(
            id=uuid4(),
            employee_id=employee_id,
            plan_name=plan_name,
            amount=amount,
        )

        try:
            logger.info("employer_contribution_started", extra={
                "employee_id": str(employee_id),
                "plan_name": plan_name,
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="payroll.employer_contribution",
                payload={
                    "employee_id": str(employee_id),
                    "plan_name": plan_name,
                    "amount": str(amount),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=Decimal(str(amount)),
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return contribution, result

        except Exception:
            self._session.rollback()
            raise
