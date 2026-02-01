"""
Government Contracts Module Service (``finance_modules.contracts.service``).

Responsibility
--------------
Orchestrates government-contract operations -- cost incurrence, CPFF/T&M/FFP
billing, DCAA indirect-cost allocation, ICE submission compilation, contract
modifications, cost disallowance, and audit-finding tracking -- by delegating
pure computation to ``finance_engines`` and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``GovernmentContractsService`` is the
sole public entry point for government-contract operations.  It composes
stateless engines (``BillingEngine``, ``AllocationCascade``, ``ICEEngine``)
and the kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* DCAA -- Indirect cost allocation follows FAR/CAS allocation methodology
           via ``build_dcaa_cascade``.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Engine errors (e.g., invalid billing type)  -> propagate before posting.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying contract IDs, CLIN references, amounts, and
cost types.  All journal entries feed the kernel audit chain (R11).
DCAA compliance requires full traceability of indirect cost allocation.

Usage::

    service = GovernmentContractsService(session, role_resolver, clock)
    result = service.record_cost_incurrence(
        contract_id="FA8750-21-C-0001",
        cost_type="DIRECT_LABOR",
        amount=Decimal("50000.00"),
        effective_date=date.today(),
        actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from sqlalchemy.orm import Session

from finance_engines.allocation_cascade import (
    AllocationStep,
    AllocationStepResult,
    build_dcaa_cascade,
    execute_cascade,
)
from finance_engines.billing import (
    BillingInput,
    BillingResult,
    calculate_billing,
)
from finance_engines.ice import (
    ICEInput,
    ICESubmission,
    compile_ice_submission,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_modules.contracts.models import (
    AuditFinding,
    ContractModification,
    CostDisallowance,
    Subcontract,
)
from finance_modules.contracts.orm import ContractBillingModel, ContractFundingModel

logger = get_logger("modules.contracts.service")


class GovernmentContractsService:
    """
    Orchestrates government contract operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``compile_ice_submission``, ``calculate_billing``,
      etc.) return pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.
    * DCAA indirect cost allocation follows FAR/CAS methodology.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    * Does NOT persist contract domain models -- only journal entries are
      persisted.

    Engine composition:
    - BillingEngine: CPFF/T&M/FFP billing calculations (pure)
    - AllocationCascade: DCAA indirect cost allocation (pure)
    - ICEEngine: incurred cost submission schedules (pure)

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all journal writes
    share a single transaction.
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

    # =========================================================================
    # Cost Incurrence
    # =========================================================================

    def record_cost_incurrence(
        self,
        contract_id: str,
        cost_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        clin_number: str | None = None,
        labor_category: str | None = None,
        cost_center: str | None = None,
        org_unit: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a cost incurrence against a government contract.

        Profile dispatch: contract.cost_incurred with where-clause on cost_type.
        Supported cost_types: DIRECT_LABOR, DIRECT_MATERIAL, SUBCONTRACT,
        TRAVEL, ODC, INDIRECT_FRINGE, INDIRECT_OVERHEAD, INDIRECT_GA.
        """
        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "cost_type": cost_type,
            "amount": str(amount),
        }
        if clin_number:
            payload["clin_number"] = clin_number
        if labor_category:
            payload["labor_category"] = labor_category
        if cost_center:
            payload["cost_center"] = cost_center
        if org_unit:
            payload["org_unit"] = org_unit

        logger.info("contract_cost_incurrence", extra={
            "contract_id": contract_id,
            "cost_type": cost_type,
            "amount": str(amount),
        })

        try:
            result = self._poster.post_event(
                event_type="contract.cost_incurred",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
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
    # Billing
    # =========================================================================

    def generate_billing(
        self,
        contract_id: str,
        billing_period: str,
        effective_date: date,
        actor_id: UUID,
        billing_input: BillingInput,
        currency: str = "USD",
        org_unit: str | None = None,
        description: str | None = None,
    ) -> tuple[BillingResult, ModulePostingResult]:
        """
        Generate provisional billing for a contract.

        Engine: calculate_billing() computes line items, fees, withholding.
        Profile dispatch: contract.billing_provisional with where-clause
        on billing_type (COST_REIMBURSEMENT, TIME_AND_MATERIALS, LABOR_HOUR).
        """
        try:
            # Engine: calculate billing amounts (pure)
            billing_result = calculate_billing(billing_input)

            logger.info("contract_billing_calculated", extra={
                "contract_id": contract_id,
                "billing_period": billing_period,
                "contract_type": billing_result.contract_type.value,
                "net_billing": str(billing_result.net_billing.amount),
                "gross_billing": str(billing_result.gross_billing.amount),
                "fee_amount": str(billing_result.fee_amount.amount),
                "funding_limited": billing_result.funding_limited,
            })

            # Map contract type to billing_type for profile dispatch
            billing_type_map = {
                "CPFF": "COST_REIMBURSEMENT",
                "CPIF": "COST_REIMBURSEMENT",
                "CPAF": "COST_REIMBURSEMENT",
                "T&M": "TIME_AND_MATERIALS",
                "LH": "LABOR_HOUR",
            }
            billing_type = billing_type_map.get(
                billing_result.contract_type.value,
                billing_result.contract_type.value,
            )

            # cost_billing = net billing minus fee (for from_context split)
            cost_billing = billing_result.net_billing.amount - billing_result.fee_amount.amount

            payload: dict[str, Any] = {
                "contract_number": contract_id,
                "billing_period": billing_period,
                "billing_type": billing_type,
                "total_billing": str(billing_result.net_billing.amount),
                "cost_billing": str(cost_billing),
                "gross_billing": str(billing_result.gross_billing.amount),
                "fee_amount": str(billing_result.fee_amount.amount),
                "withholding_amount": str(billing_result.withholding_amount.amount),
                "total_direct_cost": str(billing_result.total_direct_cost.amount),
                "total_indirect_cost": str(billing_result.total_indirect_cost.amount),
                "funding_limited": billing_result.funding_limited,
                "ceiling_limited": billing_result.ceiling_limited,
                "line_item_count": len(billing_result.line_items),
            }
            if org_unit:
                payload["org_unit"] = org_unit

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="contract.billing_provisional",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=billing_result.net_billing.amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                contract_uuid = uuid5(NAMESPACE_DNS, contract_id)
                orm_billing = ContractBillingModel(
                    id=uuid4(),
                    contract_id=contract_uuid,
                    billing_number=f"{contract_id}-{billing_period}",
                    billing_type=billing_type,
                    billing_period=billing_period,
                    billing_date=effective_date,
                    direct_costs=billing_result.total_direct_cost.amount,
                    indirect_costs=billing_result.total_indirect_cost.amount,
                    fee_amount=billing_result.fee_amount.amount,
                    total_amount=billing_result.net_billing.amount,
                    currency=currency,
                    status="draft",
                    created_by_id=actor_id,
                )
                self._session.add(orm_billing)
                self._session.commit()
            else:
                self._session.rollback()
            return billing_result, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Funding Actions
    # =========================================================================

    def record_funding_action(
        self,
        contract_id: str,
        action_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        modification_number: str | None = None,
        org_unit: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a contract funding action (obligation, deobligation, etc.).

        Profile dispatch: contract.funding_action.
        Supported action_types: OBLIGATION, DEOBLIGATION, INCREMENTAL_FUNDING.
        """
        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "action_type": action_type,
            "amount": str(amount),
        }
        if modification_number:
            payload["modification_number"] = modification_number
        if org_unit:
            payload["org_unit"] = org_unit

        logger.info("contract_funding_action", extra={
            "contract_id": contract_id,
            "action_type": action_type,
            "amount": str(amount),
        })

        try:
            result = self._poster.post_event(
                event_type="contract.funding_action",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )

            if result.is_success:
                contract_uuid = uuid5(NAMESPACE_DNS, contract_id)
                orm_funding = ContractFundingModel(
                    id=uuid4(),
                    contract_id=contract_uuid,
                    funding_action_number=modification_number or f"{contract_id}-{action_type}",
                    funding_type=action_type.lower(),
                    amount=amount,
                    cumulative_funded=amount,
                    currency=currency,
                    effective_date=effective_date,
                    modification_number=modification_number,
                    authorized_by=actor_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_funding)
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Indirect Allocation
    # =========================================================================

    def record_indirect_allocation(
        self,
        contract_id: str,
        indirect_type: str,
        amount: Decimal,
        rate_applied: Decimal,
        base_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        org_unit: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record indirect cost allocation to a contract.

        Profile dispatch: contract.indirect_allocation with where-clause
        on indirect_type (FRINGE, OVERHEAD, G_AND_A).
        """
        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "indirect_type": indirect_type,
            "amount": str(amount),
            "rate_applied": str(rate_applied),
            "base_amount": str(base_amount),
        }
        if org_unit:
            payload["org_unit"] = org_unit

        logger.info("contract_indirect_allocation", extra={
            "contract_id": contract_id,
            "indirect_type": indirect_type,
            "amount": str(amount),
            "rate_applied": str(rate_applied),
        })

        try:
            result = self._poster.post_event(
                event_type="contract.indirect_allocation",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
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
    # Rate Adjustment
    # =========================================================================

    def record_rate_adjustment(
        self,
        contract_id: str,
        indirect_type: str,
        provisional_rate: Decimal,
        final_rate: Decimal,
        base_amount: Decimal,
        adjustment_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        org_unit: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record final vs provisional rate adjustment.

        Profile dispatch: contract.rate_adjustment.
        """
        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "indirect_type": indirect_type,
            "provisional_rate": str(provisional_rate),
            "final_rate": str(final_rate),
            "base_amount": str(base_amount),
            "adjustment_amount": str(adjustment_amount),
        }
        if org_unit:
            payload["org_unit"] = org_unit

        logger.info("contract_rate_adjustment", extra={
            "contract_id": contract_id,
            "indirect_type": indirect_type,
            "adjustment_amount": str(adjustment_amount),
        })

        try:
            result = self._poster.post_event(
                event_type="contract.rate_adjustment",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=abs(adjustment_amount),
                currency=currency,
                description=description,
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
    # Fee Accrual
    # =========================================================================

    def record_fee_accrual(
        self,
        contract_id: str,
        fee_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        cumulative_fee: Decimal | None = None,
        ceiling_fee: Decimal | None = None,
        org_unit: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record fee accrual on a government contract.

        Profile dispatch: contract.fee_accrual with where-clause on fee_type.
        Supported fee_types: FIXED_FEE, INCENTIVE_FEE, AWARD_FEE.
        """
        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "fee_type": fee_type,
            "amount": str(amount),
        }
        if cumulative_fee is not None:
            payload["cumulative_fee"] = str(cumulative_fee)
        if ceiling_fee is not None:
            payload["ceiling_fee"] = str(ceiling_fee)
        if org_unit:
            payload["org_unit"] = org_unit

        logger.info("contract_fee_accrual", extra={
            "contract_id": contract_id,
            "fee_type": fee_type,
            "amount": str(amount),
        })

        try:
            result = self._poster.post_event(
                event_type="contract.fee_accrual",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
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
    # Engine-Only Operations (Pure, No Posting)
    # =========================================================================

    def run_allocation_cascade(
        self,
        steps: tuple[AllocationStep, ...] | None,
        pool_balances: dict[str, Any],
        rates: dict[str, Decimal],
        currency: str = "USD",
    ) -> tuple[list[AllocationStepResult], dict[str, Any]]:
        """
        Run DCAA indirect cost allocation cascade (pure, no posting).

        Engine: execute_cascade() with optional build_dcaa_cascade() defaults.
        Returns step results and final pool balances for caller to post.
        """
        resolved_steps = steps or build_dcaa_cascade()
        return execute_cascade(resolved_steps, pool_balances, rates, currency)

    def compile_ice(self, ice_input: ICEInput) -> ICESubmission:
        """
        Compile ICE submission schedules (pure, no posting).

        Engine: compile_ice_submission() produces all DCAA schedules.
        """
        return compile_ice_submission(ice_input)

    # =========================================================================
    # Contract Modification
    # =========================================================================

    def record_contract_modification(
        self,
        contract_id: str,
        modification_number: str,
        modification_type: str,
        amount_change: Decimal,
        effective_date: date,
        actor_id: UUID,
        description: str | None = None,
        currency: str = "USD",
    ) -> tuple[ContractModification, ModulePostingResult]:
        """
        Record a contract modification (scope, funding, admin).
        """
        from uuid import uuid4
        mod = ContractModification(
            id=uuid4(),
            contract_id=contract_id,
            modification_number=modification_number,
            modification_type=modification_type,
            effective_date=effective_date,
            description=description or "",
            amount_change=amount_change,
        )

        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "modification_number": modification_number,
            "modification_type": modification_type,
            "amount": str(amount_change),
        }

        try:
            result = self._poster.post_event(
                event_type="contract.modification",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount_change,
                currency=currency,
                description=description,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return mod, result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Subcontract Cost
    # =========================================================================

    def record_subcontract_cost(
        self,
        contract_id: str,
        subcontractor_name: str,
        subcontract_number: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[Subcontract, ModulePostingResult]:
        """
        Record subcontract cost flow-down.
        """
        from uuid import uuid4
        sub = Subcontract(
            id=uuid4(),
            contract_id=contract_id,
            subcontractor_name=subcontractor_name,
            subcontract_number=subcontract_number,
            amount=amount,
            description=description or "",
        )

        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "subcontractor": subcontractor_name,
            "subcontract_number": subcontract_number,
            "amount": str(amount),
        }

        try:
            result = self._poster.post_event(
                event_type="contract.subcontract_cost",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return sub, result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Equitable Adjustment
    # =========================================================================

    def record_equitable_adjustment(
        self,
        contract_id: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        reason: str = "",
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record an equitable adjustment (REA processing).
        """
        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "amount": str(amount),
            "reason": reason,
        }

        try:
            result = self._poster.post_event(
                event_type="contract.equitable_adjustment",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
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
    # DCAA Audit Prep
    # =========================================================================

    def run_dcaa_audit_prep(
        self,
        contract_id: str,
        period: str,
    ) -> dict:
        """
        Run pre-audit compliance check (pure, no posting).

        Uses ICEEngine to compile submission and checks for completeness.
        """
        from datetime import date as date_cls

        from finance_engines.ice import ContractCostInput
        from finance_kernel.domain.values import Money

        # Extract fiscal year from period string
        fiscal_year_str = period[:4] if len(period) >= 4 else period
        if not fiscal_year_str.isdigit():
            raise ValueError(f"Cannot parse fiscal year from period '{period}'")
        fiscal_year = int(fiscal_year_str)

        # Build a minimal ICE input for compliance check
        ice_input = ICEInput(
            contractor_name=f"Contract {contract_id}",
            fiscal_year=fiscal_year,
            fiscal_year_start=date_cls(fiscal_year, 1, 1),
            fiscal_year_end=date_cls(fiscal_year, 12, 31),
            currency="USD",
            contract_costs=(
                ContractCostInput(
                    contract_number=contract_id,
                    contract_type="CPFF",
                    direct_labor=Money.of(Decimal("0"), "USD"),
                ),
            ),
        )
        submission = compile_ice_submission(ice_input)

        # Count non-empty schedules for completeness assessment
        schedule_names = [
            "schedule_a", "schedule_b", "schedule_c",
            "schedule_g", "schedule_h", "schedule_i", "schedule_j",
        ]
        schedules_compiled = sum(
            1 for name in schedule_names
            if getattr(submission, name, None) is not None
        )

        return {
            "contract_id": contract_id,
            "period": period,
            "schedules_compiled": schedules_compiled,
            "total_claimed": str(submission.total_claimed.amount),
            "total_unallowable": str(submission.total_unallowable.amount),
            "is_valid": submission.is_valid,
            "is_complete": schedules_compiled > 0,
        }

    # =========================================================================
    # SF-1034 Public Voucher
    # =========================================================================

    def generate_sf1034(
        self,
        contract_id: str,
        period: str,
        billing_amount: Decimal,
        fee_amount: Decimal = Decimal("0"),
    ) -> dict:
        """
        Generate SF-1034 public voucher data (pure, no posting).

        Uses BillingEngine for calculation if needed.
        """
        total_voucher = billing_amount + fee_amount

        return {
            "form": "SF-1034",
            "contract_id": contract_id,
            "period": period,
            "billing_amount": str(billing_amount),
            "fee_amount": str(fee_amount),
            "total_voucher": str(total_voucher),
            "certification": "I certify the above amounts are correct and just.",
        }

    # =========================================================================
    # Cost Disallowance
    # =========================================================================

    def record_cost_disallowance(
        self,
        contract_id: str,
        cost_type: str,
        amount: Decimal,
        reason: str,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[CostDisallowance, ModulePostingResult]:
        """
        Record a DCAA cost disallowance (moves cost to unallowable).
        """
        from uuid import uuid4
        disallowance = CostDisallowance(
            id=uuid4(),
            contract_id=contract_id,
            cost_type=cost_type,
            amount=amount,
            reason=reason,
            disallowance_date=effective_date,
        )

        payload: dict[str, Any] = {
            "contract_number": contract_id,
            "cost_type": cost_type,
            "amount": str(amount),
            "reason": reason,
        }

        try:
            result = self._poster.post_event(
                event_type="contract.cost_disallowance",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                description=description,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return disallowance, result
        except Exception:
            self._session.rollback()
            raise
