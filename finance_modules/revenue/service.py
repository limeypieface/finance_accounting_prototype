"""
Module: finance_modules.revenue.service
Responsibility:
    Thin orchestration glue for the ASC 606 five-step revenue recognition
    model.  Coordinates engine calculations and kernel posting -- contains
    NO accounting logic of its own.

    1. Calls AllocationEngine for SSP allocation (Step 4).
    2. Calls ModulePostingService for journal entry creation.
    3. Calls helpers for domain-specific pure calculations.

Architecture:
    finance_modules layer -- stateful only insofar as it holds a Session
    and commits/rolls back.  All financial truth is produced by the kernel
    posting pipeline.  This service owns the transaction boundary per R7.

    Dependency direction (strict):
        service.py  -->  finance_engines.allocation (AllocationEngine)
        service.py  -->  finance_kernel.services    (ModulePostingService)
        service.py  -->  finance_modules.revenue.helpers (pure math)
        service.py  -X-> finance_services           (FORBIDDEN)
        service.py  -X-> finance_config             (FORBIDDEN)

Invariants:
    - R7:  Each public method owns its transaction boundary (commit/rollback).
    - R4:  Every posted entry satisfies DOUBLE_ENTRY_BALANCE.
    - R14: Event-type-to-profile mapping via profiles.py; no if/switch here.
    - L1:  Account ROLES in profiles resolve to COA codes at posting time.
    - R16: ISO 4217 currency codes enforced at the kernel boundary.

Failure modes:
    - AllocationEngine raises on zero-length target list.
    - ModulePostingService raises on guard violations (e.g., amount <= 0).
    - Session rollback on any unhandled exception.

Audit relevance:
    - Every recognition, allocation, and modification event produces an
      immutable, auditable journal entry via the kernel pipeline.
    - ASC 606 Step 4 allocation uses AllocationEngine for deterministic,
      reproducible SSP proportional allocation.
    - Contract modifications are classified per ASC 606-10-25-12 using
      pure helper assessment -- no manual override path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_engines.allocation import AllocationEngine
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.validation import require_decimal
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_modules._posting_helpers import run_workflow_guard
from finance_services.workflow_executor import WorkflowExecutor
from finance_modules.revenue.workflows import (
    REVENUE_ALLOCATE_PRICE_WORKFLOW,
    REVENUE_MODIFY_CONTRACT_WORKFLOW,
    REVENUE_RECOGNIZE_REVENUE_WORKFLOW,
    REVENUE_UPDATE_VARIABLE_CONSIDERATION_WORKFLOW,
)
from finance_modules.revenue.helpers import (
    assess_modification_type,
    calculate_ssp,
    estimate_variable_consideration,
    measure_progress_input,
    measure_progress_output,
)
from finance_modules.revenue.models import (
    ContractModification,
    ContractStatus,
    ModificationType,
    PerformanceObligation,
    RecognitionMethod,
    RecognitionSchedule,
    RevenueContract,
    SSPAllocation,
    TransactionPrice,
)
from finance_modules.revenue.orm import (
    ContractModificationModel,
    PerformanceObligationModel,
    RevenueContractModel,
    SSPAllocationModel,
    TransactionPriceModel,
)

logger = get_logger("modules.revenue.service")


class RevenueRecognitionService:
    """
    Orchestrates ASC 606 five-step revenue recognition model.

    Contract:
        Callers supply a live SQLAlchemy Session, a RoleResolver for L1
        account role resolution, and an optional Clock.  Each public
        method owns its own transaction boundary (commit on success,
        rollback on failure).

    Guarantees:
        - Every posting method produces exactly one journal entry via
          ModulePostingService (R4, L5).
        - AllocationEngine is used for SSP allocation (Step 4); no
          manual arithmetic in this service.
        - All returned DTOs are frozen dataclasses.

    Non-goals:
        - This service does NOT persist revenue domain models (contracts,
          obligations) -- only journal entries are persisted.
        - This service does NOT enforce contract-level business rules
          beyond what the kernel guards provide.

    Engine composition:
        - AllocationEngine: SSP allocation (Step 4)

    Transaction boundary: this service commits on success, rolls back on
    failure (R7).
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

        self._allocation = AllocationEngine()

    # =========================================================================
    # Step 1: Identify the Contract
    # =========================================================================

    def identify_contract(
        self,
        contract_id: UUID,
        customer_id: UUID,
        contract_number: str,
        start_date: date,
        total_consideration: Decimal,
        end_date: date | None = None,
        variable_consideration: Decimal = Decimal("0"),
        currency: str = "USD",
    ) -> RevenueContract:
        """
        Step 1: Identify the contract with a customer (ASC 606-10-25-1).

        Preconditions:
            - ``total_consideration`` is a Decimal (never float).
            - ``currency`` is a valid ISO 4217 code.

        Postconditions:
            - Returns a frozen RevenueContract with status IDENTIFIED.
            - No journal entry is posted (pure domain record creation).

        Raises:
            TypeError: If total_consideration is not Decimal.
        """
        # INVARIANT: Monetary amounts must be Decimal, never float (R16).
        require_decimal(total_consideration, "total_consideration")
        logger.info("revenue_contract_identified", extra={
            "contract_id": str(contract_id),
            "customer_id": str(customer_id),
            "total_consideration": str(total_consideration),
        })

        contract = RevenueContract(
            id=contract_id,
            customer_id=customer_id,
            contract_number=contract_number,
            start_date=start_date,
            end_date=end_date,
            total_consideration=total_consideration,
            variable_consideration=variable_consideration,
            status=ContractStatus.IDENTIFIED,
            currency=currency,
        )

        orm_contract = RevenueContractModel.from_dto(contract, created_by_id=customer_id)
        self._session.add(orm_contract)
        self._session.commit()

        return contract

    # =========================================================================
    # Step 2: Identify Performance Obligations
    # =========================================================================

    def identify_performance_obligations(
        self,
        contract_id: UUID,
        deliverables: Sequence[dict],
    ) -> tuple[PerformanceObligation, ...]:
        """
        Step 2: Identify performance obligations in the contract
        (ASC 606-10-25-14).

        Each deliverable dict should have:
        - description: str
        - is_distinct: bool (default True)
        - standalone_selling_price: Decimal (optional)
        - recognition_method: str (optional, default "point_in_time")

        Preconditions:
            - ``deliverables`` is non-empty.
            - Each deliverable has a "description" key.

        Postconditions:
            - Returns a tuple of frozen PerformanceObligation DTOs.
            - No journal entry is posted (pure domain record creation).

        Raises:
            KeyError: If a deliverable is missing "description".
            ValueError: If recognition_method is not a valid enum value.
        """
        # INVARIANT: At least one deliverable required for obligation identification.
        assert len(deliverables) > 0, "deliverables must not be empty"
        obligations = []
        for d in deliverables:
            method_str = d.get("recognition_method", "point_in_time")
            method = RecognitionMethod(method_str)
            obligations.append(PerformanceObligation(
                id=uuid4(),
                contract_id=contract_id,
                description=d["description"],
                is_distinct=d.get("is_distinct", True),
                standalone_selling_price=Decimal(str(d.get("standalone_selling_price", "0"))),
                recognition_method=method,
            ))

        logger.info("revenue_obligations_identified", extra={
            "contract_id": str(contract_id),
            "obligation_count": len(obligations),
        })

        for ob in obligations:
            orm_ob = PerformanceObligationModel.from_dto(ob, created_by_id=contract_id)
            self._session.add(orm_ob)
        self._session.commit()

        return tuple(obligations)

    # =========================================================================
    # Step 3: Determine Transaction Price
    # =========================================================================

    def determine_transaction_price(
        self,
        contract_id: UUID,
        base_price: Decimal,
        variable_scenarios: list[dict] | None = None,
        variable_method: str = "expected_value",
        financing_component: Decimal = Decimal("0"),
        noncash_consideration: Decimal = Decimal("0"),
        consideration_payable: Decimal = Decimal("0"),
    ) -> TransactionPrice:
        """
        Step 3: Determine the transaction price (ASC 606-10-32-2).

        Uses helpers.estimate_variable_consideration for variable amounts.
        Pure calculation -- no posting.

        Preconditions:
            - ``base_price`` is Decimal >= 0.
            - ``variable_method`` is "expected_value" or "most_likely_amount".

        Postconditions:
            - Returns a frozen TransactionPrice with computed total.
            - total = base + variable + financing + noncash - payable.

        Raises:
            No exceptions under normal conditions.
        """
        # INVARIANT: Monetary amounts must be Decimal (R16).
        require_decimal(base_price, "base_price")
        variable = Decimal("0")
        if variable_scenarios:
            variable = estimate_variable_consideration(
                base_amount=base_price,
                scenarios=variable_scenarios,
                method=variable_method,
            )

        total = base_price + variable + financing_component + noncash_consideration - consideration_payable

        logger.info("revenue_price_determined", extra={
            "contract_id": str(contract_id),
            "base_price": str(base_price),
            "variable_consideration": str(variable),
            "total_transaction_price": str(total),
        })

        txn_price = TransactionPrice(
            id=uuid4(),
            contract_id=contract_id,
            base_price=base_price,
            variable_consideration=variable,
            financing_component=financing_component,
            noncash_consideration=noncash_consideration,
            consideration_payable=consideration_payable,
            total_transaction_price=total,
        )

        orm_price = TransactionPriceModel.from_dto(txn_price, created_by_id=contract_id)
        self._session.add(orm_price)
        self._session.commit()

        return txn_price

    # =========================================================================
    # Step 4: Allocate Transaction Price
    # =========================================================================

    def allocate_transaction_price(
        self,
        contract_id: UUID,
        total_price: Decimal,
        obligations: Sequence[PerformanceObligation],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[tuple[SSPAllocation, ...], ModulePostingResult]:
        """
        Step 4: Allocate transaction price to performance obligations
        (ASC 606-10-32-28).

        Uses AllocationEngine for proportional SSP allocation.
        Posts the allocation via ``revenue.price_allocation`` event type.

        Preconditions:
            - ``obligations`` is non-empty.
            - ``total_price`` is Decimal > 0.
            - ``currency`` is a valid ISO 4217 code.

        Postconditions:
            - Returns tuple of (SSPAllocation tuple, ModulePostingResult).
            - On success: session is committed and result.is_success is True.
            - On failure: session is rolled back.
            - Sum of allocated_amounts equals total_price (within rounding).

        Raises:
            Exception: Propagated from AllocationEngine or posting pipeline;
                session is rolled back before re-raise.
        """
        # INVARIANT: At least one obligation required for allocation.
        assert len(obligations) > 0, "obligations must not be empty"
        # INVARIANT: Monetary amounts must be Decimal (R16).
        require_decimal(total_price, "total_price")

        failure = run_workflow_guard(
            self._workflow_executor,
            REVENUE_ALLOCATE_PRICE_WORKFLOW,
            "revenue_contract",
            contract_id,
            actor_id=actor_id,
            amount=total_price,
            currency=currency,
            context=None,
        )
        if failure is not None:
            return (), failure

        # Engine: AllocationEngine for proportional SSP allocation
        from finance_engines.allocation import AllocationMethod, AllocationTarget
        from finance_kernel.domain.values import Money

        total_ssp = sum(o.standalone_selling_price for o in obligations)

        if total_ssp > 0:
            targets = [
                AllocationTarget(
                    target_id=str(o.id),
                    target_type="obligation",
                    eligible_amount=Money.of(str(o.standalone_selling_price), currency),
                )
                for o in obligations
            ]
            alloc_result = self._allocation.allocate(
                amount=Money.of(str(total_price), currency),
                targets=targets,
                method=AllocationMethod.PRORATA,
            )
            allocations = []
            for line, obligation in zip(alloc_result.lines, obligations, strict=False):
                pct = obligation.standalone_selling_price / total_ssp
                allocations.append(SSPAllocation(
                    id=uuid4(),
                    contract_id=contract_id,
                    obligation_id=obligation.id,
                    standalone_selling_price=obligation.standalone_selling_price,
                    allocated_amount=line.allocated.amount,
                    allocation_percentage=pct,
                ))
        else:
            # Equal allocation when no SSP data available
            targets = [
                AllocationTarget(
                    target_id=str(o.id),
                    target_type="obligation",
                )
                for o in obligations
            ]
            alloc_result = self._allocation.allocate(
                amount=Money.of(str(total_price), currency),
                targets=targets,
                method=AllocationMethod.EQUAL,
            )
            allocations = []
            for line, obligation in zip(alloc_result.lines, obligations, strict=False):
                pct = Decimal("1") / Decimal(str(len(obligations)))
                allocations.append(SSPAllocation(
                    id=uuid4(),
                    contract_id=contract_id,
                    obligation_id=obligation.id,
                    standalone_selling_price=obligation.standalone_selling_price,
                    allocated_amount=line.allocated.amount,
                    allocation_percentage=pct,
                ))

        logger.info("revenue_price_allocated", extra={
            "contract_id": str(contract_id),
            "total_price": str(total_price),
            "obligation_count": len(obligations),
        })

        try:
            result = self._poster.post_event(
                event_type="revenue.price_allocation",
                payload={
                    "contract_id": str(contract_id),
                    "total_price": str(total_price),
                    "obligation_count": len(obligations),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=total_price,
                currency=currency,
            )

            if result.is_success:
                for alloc in allocations:
                    orm_alloc = SSPAllocationModel.from_dto(alloc, created_by_id=actor_id)
                    self._session.add(orm_alloc)
                self._session.commit()
            else:
                self._session.rollback()
            return tuple(allocations), result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Step 5: Recognize Revenue
    # =========================================================================

    def recognize_revenue(
        self,
        contract_id: UUID,
        obligation_id: UUID,
        amount: Decimal,
        method: RecognitionMethod,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        progress: Decimal | None = None,
    ) -> ModulePostingResult:
        """
        Step 5: Recognize revenue when (or as) obligations are satisfied
        (ASC 606-10-25-23).

        Dispatches to the appropriate profile based on recognition method.

        Preconditions:
            - ``amount`` is Decimal > 0.
            - ``method`` is a valid RecognitionMethod enum member.

        Postconditions:
            - On success: session is committed; result.is_success is True.
            - On failure: session is rolled back.

        Raises:
            KeyError: If method is not in the event_type_map.
            Exception: Propagated from posting pipeline; session rolled back.
        """
        # INVARIANT: Recognition amount must be Decimal (R16).
        require_decimal(amount)

        failure = run_workflow_guard(
            self._workflow_executor,
            REVENUE_RECOGNIZE_REVENUE_WORKFLOW,
            "revenue_obligation",
            obligation_id,
            actor_id=actor_id,
            amount=amount,
            currency=currency,
            context=None,
        )
        if failure is not None:
            return failure

        event_type_map = {
            RecognitionMethod.POINT_IN_TIME: "revenue.recognize_point_in_time",
            RecognitionMethod.OVER_TIME_INPUT: "revenue.recognize_over_time_input",
            RecognitionMethod.OVER_TIME_OUTPUT: "revenue.recognize_over_time_output",
        }
        event_type = event_type_map[method]

        payload: dict[str, Any] = {
            "contract_id": str(contract_id),
            "obligation_id": str(obligation_id),
            "method": method.value,
        }
        if progress is not None:
            payload["progress"] = str(progress)

        logger.info("revenue_recognition_started", extra={
            "contract_id": str(contract_id),
            "obligation_id": str(obligation_id),
            "amount": str(amount),
            "method": method.value,
        })

        try:
            result = self._poster.post_event(
                event_type=event_type,
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
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
    # Contract Modification
    # =========================================================================

    def modify_contract(
        self,
        contract_id: UUID,
        modification_date: date,
        price_change: Decimal,
        adds_distinct_goods: bool,
        price_reflects_ssp: bool,
        remaining_goods_distinct: bool,
        actor_id: UUID,
        description: str = "",
        currency: str = "USD",
    ) -> tuple[ContractModification, ModulePostingResult]:
        """
        Record a contract modification per ASC 606-10-25-12.

        Assesses modification type via ``helpers.assess_modification_type``
        and posts the appropriate journal entry.

        Preconditions:
            - ``price_change`` is Decimal.
            - ``modification_date`` is a valid date.

        Postconditions:
            - Returns (ContractModification, ModulePostingResult).
            - On success: session is committed.
            - On failure: session is rolled back.

        Raises:
            ValueError: If modification type assessment produces invalid enum.
            Exception: Propagated from posting pipeline; session rolled back.
        """
        # INVARIANT: Monetary amounts must be Decimal (R16).
        require_decimal(price_change, "price_change")

        failure = run_workflow_guard(
            self._workflow_executor,
            REVENUE_MODIFY_CONTRACT_WORKFLOW,
            "revenue_contract",
            contract_id,
            actor_id=actor_id,
            amount=abs(price_change),
            currency=currency,
            context=None,
        )
        if failure is not None:
            placeholder = ContractModification(
                id=uuid4(),
                contract_id=contract_id,
                modification_date=modification_date,
                modification_type=ModificationType.CUMULATIVE_CATCH_UP,
                description=description,
                price_change=price_change,
                actor_id=actor_id,
            )
            return placeholder, failure

        mod_type_str = assess_modification_type(
            adds_distinct_goods=adds_distinct_goods,
            price_reflects_ssp=price_reflects_ssp,
            remaining_goods_distinct=remaining_goods_distinct,
        )
        mod_type = ModificationType(mod_type_str)

        event_type_map = {
            ModificationType.CUMULATIVE_CATCH_UP: "revenue.modification_cumulative",
            ModificationType.PROSPECTIVE: "revenue.modification_prospective",
            ModificationType.SEPARATE_CONTRACT: "revenue.modification_cumulative",
            ModificationType.TERMINATION: "revenue.modification_cumulative",
        }
        event_type = event_type_map[mod_type]

        modification = ContractModification(
            id=uuid4(),
            contract_id=contract_id,
            modification_date=modification_date,
            modification_type=mod_type,
            description=description,
            price_change=price_change,
            actor_id=actor_id,
        )

        logger.info("revenue_contract_modification", extra={
            "contract_id": str(contract_id),
            "modification_type": mod_type.value,
            "price_change": str(price_change),
        })

        posting_amount = abs(price_change)

        try:
            result = self._poster.post_event(
                event_type=event_type,
                payload={
                    "contract_id": str(contract_id),
                    "modification_type": mod_type.value,
                    "price_change": str(price_change),
                    "description": description,
                },
                effective_date=modification_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            if result.is_success:
                orm_mod = ContractModificationModel.from_dto(modification, created_by_id=actor_id)
                self._session.add(orm_mod)
                self._session.commit()
            else:
                self._session.rollback()
            return modification, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Variable Consideration Update
    # =========================================================================

    def update_variable_consideration(
        self,
        contract_id: UUID,
        new_estimate: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Update variable consideration estimate (ASC 606-10-32-14).

        Posts adjustment via ``revenue.variable_consideration_update``.

        Preconditions:
            - ``new_estimate`` is Decimal.

        Postconditions:
            - On success: session is committed; result.is_success is True.
            - On failure: session is rolled back.

        Raises:
            Exception: Propagated from posting pipeline; session rolled back.
        """
        # INVARIANT: Monetary amounts must be Decimal (R16).
        require_decimal(new_estimate, "new_estimate")
        posting_amount = abs(new_estimate)

        failure = run_workflow_guard(
            self._workflow_executor,
            REVENUE_UPDATE_VARIABLE_CONSIDERATION_WORKFLOW,
            "revenue_contract",
            contract_id,
            actor_id=actor_id,
            amount=posting_amount,
            currency=currency,
            context=None,
        )
        if failure is not None:
            return failure

        logger.info("revenue_variable_consideration_update", extra={
            "contract_id": str(contract_id),
            "new_estimate": str(new_estimate),
        })

        try:
            result = self._poster.post_event(
                event_type="revenue.variable_consideration_update",
                payload={
                    "contract_id": str(contract_id),
                    "new_estimate": str(new_estimate),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
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
    # Queries
    # =========================================================================

    def get_contract_status(
        self,
        contract: RevenueContract,
        obligations: Sequence[PerformanceObligation],
    ) -> dict:
        """
        Query contract status summary.

        Preconditions:
            - ``contract`` is a valid RevenueContract.
        Postconditions:
            - Returns a dict with contract_id, status, counts.
            - Pure query -- no posting, no session interaction.
        """
        satisfied = sum(1 for o in obligations if o.satisfied)
        return {
            "contract_id": str(contract.id),
            "status": contract.status.value,
            "total_consideration": str(contract.total_consideration),
            "obligations_total": len(obligations),
            "obligations_satisfied": satisfied,
            "obligations_remaining": len(obligations) - satisfied,
        }

    def get_unbilled_revenue(
        self,
        contracts: Sequence[RevenueContract],
        obligations: Sequence[PerformanceObligation],
    ) -> Decimal:
        """
        Calculate total unbilled revenue across contracts.

        Preconditions:
            - All obligations have Decimal allocated_price values.
        Postconditions:
            - Returns Decimal >= 0 representing satisfied but unrecognized revenue.
            - Pure calculation -- no posting, no session interaction.
        """
        total = Decimal("0")
        for o in obligations:
            if o.satisfied and o.allocated_price > 0:
                total += o.allocated_price
        return total

    def get_deferred_revenue(
        self,
        contracts: Sequence[RevenueContract],
        obligations: Sequence[PerformanceObligation],
    ) -> Decimal:
        """
        Calculate total deferred revenue across contracts.

        Preconditions:
            - All obligations have Decimal allocated_price values.
        Postconditions:
            - Returns Decimal >= 0 representing unsatisfied obligation value.
            - Pure calculation -- no posting, no session interaction.
        """
        total = Decimal("0")
        for o in obligations:
            if not o.satisfied and o.allocated_price > 0:
                total += o.allocated_price
        return total
