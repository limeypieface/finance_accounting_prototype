"""
Government Contracts Service - Orchestrates contract operations via engines + kernel.

Thin glue layer that:
1. Calls BillingEngine for CPFF/T&M/FFP billing calculations (pure)
2. Calls AllocationCascade for DCAA indirect cost allocation (pure)
3. Calls ICEEngine for incurred cost submission schedules (pure)
4. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
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
from uuid import UUID

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_engines.billing import (
    BillingInput,
    BillingResult,
    calculate_billing,
)
from finance_engines.allocation_cascade import (
    AllocationStep,
    AllocationStepResult,
    build_dcaa_cascade,
    execute_cascade,
)
from finance_engines.ice import (
    ICEInput,
    ICESubmission,
    compile_ice_submission,
)

logger = get_logger("modules.contracts.service")


class GovernmentContractsService:
    """
    Orchestrates government contract operations through engines and kernel.

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

            payload: dict[str, Any] = {
                "contract_number": contract_id,
                "billing_period": billing_period,
                "billing_type": billing_type,
                "total_billing": str(billing_result.net_billing.amount),
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
