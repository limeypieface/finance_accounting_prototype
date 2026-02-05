"""
Expense Management Module Service (``finance_modules.expense.service``).

Responsibility
--------------
Orchestrates T&E (Travel & Entertainment) expense operations -- expense
recording, reimbursement, policy compliance checks, mileage/per-diem
calculations, cost-center allocation, receipt matching, and corporate
card reconciliation -- by delegating pure computation to ``finance_engines``
and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``ExpenseService`` is the sole public
entry point for expense operations.  It composes stateless engines
(``TaxCalculator``, ``AllocationEngine``, ``MatchingEngine``) and the
kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* R4  -- Double-entry balance enforced downstream by ``JournalWriter``.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Policy violation (e.g., over-limit expense)  -> ``ValueError`` from
  helpers before posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying expense IDs, categories, amounts, and
policy compliance outcomes.  All journal entries feed the kernel audit
chain (R11).

Usage::

    service = ExpenseService(session, role_resolver, workflow_executor, clock=clock)
    result = service.record_expense(
        expense_id=uuid4(), category="TRAVEL",
        amount=Decimal("500.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationResult,
    AllocationTarget,
)
from finance_engines.matching import (
    MatchCandidate,
    MatchingEngine,
    MatchResult,
    MatchTolerance,
    MatchType,
)
from finance_engines.tax import (
    TaxCalculationResult,
    TaxCalculator,
    TaxRate,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.values import Money
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
from finance_modules.expense.workflows import (
    EXPENSE_ALLOCATE_EXPENSE_WORKFLOW,
    EXPENSE_APPROVE_TRAVEL_AUTH_WORKFLOW,
    EXPENSE_CLEAR_ADVANCE_WORKFLOW,
    EXPENSE_ISSUE_ADVANCE_WORKFLOW,
    EXPENSE_RECORD_CARD_PAYMENT_WORKFLOW,
    EXPENSE_RECORD_CARD_STATEMENT_WORKFLOW,
    EXPENSE_RECORD_EXPENSE_REPORT_WORKFLOW,
    EXPENSE_RECORD_EXPENSE_WORKFLOW,
    EXPENSE_RECORD_RECEIPT_MATCH_WORKFLOW,
    EXPENSE_RECORD_REIMBURSEMENT_WORKFLOW,
    EXPENSE_RECORD_REPORT_WITH_GSA_CHECK_WORKFLOW,
    EXPENSE_SUBMIT_TRAVEL_AUTH_WORKFLOW,
)
from finance_engines.expense_compliance import (
    validate_gsa_compliance,
    validate_pre_travel_authorization,
)
from finance_modules.expense.dcaa_orm import (
    TravelAuthLineModel,
    TravelAuthorizationModel,
)
from finance_modules.expense.dcaa_types import (
    GSARateTable,
    TravelAuthStatus,
    TravelAuthorization,
    TravelExpenseCategory,
)
from finance_modules.expense.helpers import (
    calculate_mileage as _calculate_mileage,
)
from finance_modules.expense.helpers import (
    calculate_per_diem as _calculate_per_diem,
)
from finance_modules.expense.helpers import (
    validate_expense_against_policy,
)
from finance_modules.expense.models import (
    CardTransaction,
    ExpenseLine,
    ExpensePolicy,
    MileageRate,
    PerDiemRate,
    PolicyViolation,
)
from finance_modules.expense.orm import ExpenseReportModel, ReimbursementModel

logger = get_logger("modules.expense.service")


class ExpenseService:
    """
    Orchestrates expense-management operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``calculate_mileage``, ``calculate_per_diem``,
      etc.) return pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    * Does NOT persist expense domain models -- only journal entries are
      persisted.

    Engine composition:
    - TaxCalculator: expense tax calculations (sales tax, VAT on expenses)
    - AllocationEngine: cost center allocation of expenses

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    and journal writes share a single transaction.
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

        # Kernel posting (auto_commit=False -- we own the boundary). G14: actor validation mandatory.
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
            party_service=party_service,
        )

        # Stateless engines
        self._tax = TaxCalculator()
        self._allocation = AllocationEngine()
        self._matching = MatchingEngine()

    # =========================================================================
    # Single Expense
    # =========================================================================

    def record_expense(
        self,
        expense_id: UUID,
        category: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        employee_id: UUID | None = None,
        tax_codes: Sequence[str] | None = None,
        tax_rates: dict[str, TaxRate] | None = None,
        currency: str = "USD",
        description: str | None = None,
        cost_center: str | None = None,
        project_id: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a single expense line.

        Engine: TaxCalculator used when tax_codes are provided to compute
                the tax component of the expense.
        Profile: expense.report_approved -> ExpenseReportApproved
                 (or ExpenseReportBillable when project_id is set)
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_EXPENSE_WORKFLOW,
                "expense",
                expense_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            tax_result: TaxCalculationResult | None = None
            net_amount = amount

            # Engine: compute taxes if applicable
            if tax_codes and tax_rates:
                tax_result = self._tax.calculate(
                    amount=Money.of(amount, currency),
                    tax_codes=tax_codes,
                    rates=tax_rates,
                )
                net_amount = tax_result.net_amount.amount

            logger.info("expense_record_started", extra={
                "expense_id": str(expense_id),
                "category": category,
                "amount": str(amount),
                "net_amount": str(net_amount),
                "has_tax": tax_result is not None,
                "tax_total": str(tax_result.tax_total.amount) if tax_result else "0",
            })

            payload: dict[str, Any] = {
                "expense_id": str(expense_id),
                "category": category,
                "total_amount": str(amount),
                "description": description,
                "cost_center": cost_center,
                "expense_lines": [
                    {
                        "category": category,
                        "amount": str(net_amount),
                    },
                ],
            }
            if employee_id is not None:
                payload["employee_id"] = str(employee_id)
            if project_id is not None:
                payload["billable"] = True
                payload["project_id"] = project_id
            if tax_result is not None:
                payload["tax_total"] = str(tax_result.tax_total.amount)
                payload["tax_lines"] = [
                    {
                        "tax_code": tl.tax_code,
                        "tax_amount": str(tl.tax_amount.amount),
                        "rate_applied": str(tl.rate_applied),
                    }
                    for tl in tax_result.tax_lines
                ]

            result = self._poster.post_event(
                event_type="expense.report_approved",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Expense Report (multi-line)
    # =========================================================================

    def record_expense_report(
        self,
        report_id: UUID,
        lines: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        employee_id: UUID | None = None,
        tax_codes: Sequence[str] | None = None,
        tax_rates: dict[str, TaxRate] | None = None,
        billable: bool = False,
        project_id: str | None = None,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a multi-line expense report.

        Each line in `lines` should contain at minimum:
            {"category": str, "amount": str|Decimal}

        Engine: TaxCalculator used when tax_codes are provided.
        Profile: expense.report_approved -> ExpenseReportApproved
                 (or ExpenseReportBillable when billable=True)
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_EXPENSE_REPORT_WORKFLOW,
                "expense_report",
                report_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            total_amount = sum(
                Decimal(str(line.get("amount", "0")))
                for line in lines
            )

            tax_result: TaxCalculationResult | None = None
            if tax_codes and tax_rates:
                tax_result = self._tax.calculate(
                    amount=Money.of(total_amount, currency),
                    tax_codes=tax_codes,
                    rates=tax_rates,
                )

            logger.info("expense_report_started", extra={
                "report_id": str(report_id),
                "line_count": len(lines),
                "total_amount": str(total_amount),
                "billable": billable,
                "has_tax": tax_result is not None,
            })

            payload: dict[str, Any] = {
                "report_id": str(report_id),
                "description": description,
                "total_amount": str(total_amount),
                "line_count": len(lines),
                "expense_lines": [
                    {
                        "category": line.get("category", "GENERAL"),
                        "amount": str(line.get("amount", "0")),
                        "description": line.get("description"),
                    }
                    for line in lines
                ],
            }
            if employee_id is not None:
                payload["employee_id"] = str(employee_id)
            if billable or project_id:
                payload["billable"] = True
                payload["project_id"] = project_id
            if tax_result is not None:
                payload["tax_total"] = str(tax_result.tax_total.amount)
                payload["gross_amount"] = str(tax_result.gross_amount.amount)

            posting_amount = (
                tax_result.gross_amount.amount if tax_result else total_amount
            )

            result = self._poster.post_event(
                event_type="expense.report_approved",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            if result.is_success:
                orm_report = ExpenseReportModel(
                    id=report_id,
                    report_number=str(report_id)[:50],
                    employee_id=employee_id or actor_id,
                    report_date=effective_date,
                    purpose=description or "Expense report",
                    total_amount=posting_amount,
                    currency=currency,
                    status="approved",
                    approved_date=effective_date,
                    approved_by=actor_id,
                    project_id=None,
                    created_by_id=actor_id,
                )
                self._session.add(orm_report)
            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Cost Center Allocation
    # =========================================================================

    def allocate_expense(
        self,
        expense_id: UUID,
        cost_centers: Sequence[AllocationTarget],
        effective_date: date,
        actor_id: UUID,
        amount: Decimal | None = None,
        allocation_method: AllocationMethod = AllocationMethod.PRORATA,
        currency: str = "USD",
    ) -> tuple[AllocationResult, ModulePostingResult]:
        """
        Allocate an expense across cost centers.

        Engine: AllocationEngine distributes the expense amount across
                the provided cost center targets.
        Profile: expense.report_approved -> ExpenseReportApproved
                 (one posting per allocation, or aggregate)
        """
        try:
            if amount is None:
                # Sum eligible amounts from targets
                amount = sum(
                    (t.eligible_amount.amount for t in cost_centers if t.eligible_amount),
                    Decimal("0"),
                )

            # Engine: allocate across cost centers
            allocation_result = self._allocation.allocate(
                amount=Money.of(amount, currency),
                targets=cost_centers,
                method=allocation_method,
            )

            logger.info("expense_allocation_started", extra={
                "expense_id": str(expense_id),
                "amount": str(amount),
                "cost_center_count": len(cost_centers),
                "method": allocation_method.value,
                "total_allocated": str(allocation_result.total_allocated.amount),
                "unallocated": str(allocation_result.unallocated.amount),
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_ALLOCATE_EXPENSE_WORKFLOW,
                "expense_allocation",
                expense_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return allocation_result, failure

            # Build allocation details for the payload
            allocation_lines = [
                {
                    "cost_center": str(line.target_id),
                    "amount": str(line.allocated.amount),
                    "allocated_amount": str(line.allocated.amount),
                    "is_fully_allocated": line.is_fully_allocated,
                }
                for line in allocation_result.lines
                if not line.allocated.is_zero
            ]

            result = self._poster.post_event(
                event_type="expense.report_approved",
                payload={
                    "expense_id": str(expense_id),
                    "total_amount": str(amount),
                    "allocation_method": allocation_method.value,
                    "allocation_lines": allocation_lines,
                    "allocation_count": allocation_result.allocation_count,
                    "expense_lines": allocation_lines,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return allocation_result, result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Reimbursement
    # =========================================================================

    def record_reimbursement(
        self,
        reimbursement_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        employee_id: UUID | None = None,
        report_id: UUID | None = None,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record employee reimbursement payment.

        Profile: expense.reimbursement_paid -> ExpenseReimbursementPaid
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_REIMBURSEMENT_WORKFLOW,
                "reimbursement",
                reimbursement_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            logger.info("expense_reimbursement_started", extra={
                "reimbursement_id": str(reimbursement_id),
                "amount": str(amount),
            })

            payload: dict[str, Any] = {
                "reimbursement_id": str(reimbursement_id),
                "amount": str(amount),
            }
            if employee_id is not None:
                payload["employee_id"] = str(employee_id)
            if report_id is not None:
                payload["report_id"] = str(report_id)

            result = self._poster.post_event(
                event_type="expense.reimbursement_paid",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                orm_reimbursement = ReimbursementModel(
                    id=reimbursement_id,
                    report_id=report_id or reimbursement_id,
                    employee_id=employee_id or actor_id,
                    amount=amount,
                    currency=currency,
                    payment_date=effective_date,
                    payment_method="direct_deposit",
                    created_by_id=actor_id,
                )
                self._session.add(orm_reimbursement)
            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Corporate Card
    # =========================================================================

    def record_card_statement(
        self,
        statement_id: UUID,
        transactions: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record a corporate card statement.

        Profile: expense.card_statement -> ExpenseCardStatement
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_CARD_STATEMENT_WORKFLOW,
                "card_statement",
                statement_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            statement_total = sum(
                Decimal(str(txn.get("amount", "0")))
                for txn in transactions
            )

            logger.info("expense_card_statement_started", extra={
                "statement_id": str(statement_id),
                "transaction_count": len(transactions),
                "statement_total": str(statement_total),
            })

            result = self._poster.post_event(
                event_type="expense.card_statement",
                payload={
                    "statement_id": str(statement_id),
                    "statement_total": str(statement_total),
                    "transaction_count": len(transactions),
                    "card_transactions": [
                        {
                            "category": txn.get("category", "GENERAL"),
                            "amount": str(txn.get("amount", "0")),
                            "merchant": txn.get("merchant"),
                            "date": txn.get("date"),
                        }
                        for txn in transactions
                    ],
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=statement_total,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Corporate Card Payment
    # =========================================================================

    def record_card_payment(
        self,
        payment_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        statement_id: UUID | None = None,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record payment of a corporate card statement.

        Profile: expense.card_payment -> ExpenseCardPayment
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_CARD_PAYMENT_WORKFLOW,
                "card_payment",
                payment_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            logger.info("expense_card_payment_started", extra={
                "payment_id": str(payment_id),
                "amount": str(amount),
            })

            payload: dict[str, Any] = {
                "payment_id": str(payment_id),
                "amount": str(amount),
            }
            if statement_id is not None:
                payload["statement_id"] = str(statement_id)

            result = self._poster.post_event(
                event_type="expense.card_payment",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Travel Advance
    # =========================================================================

    def issue_advance(
        self,
        advance_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        employee_id: UUID | None = None,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Issue a travel advance to an employee.

        Profile: expense.advance_issued -> ExpenseAdvanceIssued
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_ISSUE_ADVANCE_WORKFLOW,
                "expense_advance",
                advance_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            logger.info("expense_advance_issued_started", extra={
                "advance_id": str(advance_id),
                "amount": str(amount),
            })

            payload: dict[str, Any] = {
                "advance_id": str(advance_id),
                "amount": str(amount),
            }
            if employee_id is not None:
                payload["employee_id"] = str(employee_id)

            result = self._poster.post_event(
                event_type="expense.advance_issued",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    def clear_advance(
        self,
        clearing_id: UUID,
        advance_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        employee_id: UUID | None = None,
        report_id: UUID | None = None,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Clear a travel advance against an expense report.

        Profile: expense.advance_cleared -> ExpenseAdvanceCleared
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_CLEAR_ADVANCE_WORKFLOW,
                "expense_advance_clearing",
                clearing_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            logger.info("expense_advance_cleared_started", extra={
                "clearing_id": str(clearing_id),
                "advance_id": str(advance_id),
                "amount": str(amount),
            })

            payload: dict[str, Any] = {
                "clearing_id": str(clearing_id),
                "advance_id": str(advance_id),
                "amount": str(amount),
            }
            if employee_id is not None:
                payload["employee_id"] = str(employee_id)
            if report_id is not None:
                payload["report_id"] = str(report_id)

            result = self._poster.post_event(
                event_type="expense.advance_cleared",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Policy Validation (no posting)
    # =========================================================================

    def validate_against_policy(
        self,
        lines: Sequence[ExpenseLine],
        policy_rules: dict[str, ExpensePolicy],
    ) -> list[PolicyViolation]:
        """
        Validate expense lines against category policies.

        No posting — pure validation delegated to helpers.py.

        Returns:
            List of PolicyViolation objects (empty if compliant).
        """
        violations = validate_expense_against_policy(lines, policy_rules)
        logger.info("expense_policy_validated", extra={
            "line_count": len(lines),
            "violation_count": len(violations),
        })
        return violations

    # =========================================================================
    # Card Transaction Import (no posting)
    # =========================================================================

    def import_card_transactions(
        self,
        transactions: Sequence[dict[str, Any]],
        card_id: UUID,
    ) -> list[CardTransaction]:
        """
        Parse and validate a corporate card transaction feed.

        No posting — returns validated CardTransaction domain objects.
        Caller is responsible for persistence or further processing.
        """
        from datetime import date as date_type

        results: list[CardTransaction] = []
        for txn in transactions:
            ct = CardTransaction(
                id=txn.get("id") or uuid4(),
                card_id=card_id,
                transaction_date=txn["transaction_date"] if isinstance(txn.get("transaction_date"), date_type) else date_type.fromisoformat(str(txn["transaction_date"])),
                posting_date=txn["posting_date"] if isinstance(txn.get("posting_date"), date_type) else date_type.fromisoformat(str(txn["posting_date"])),
                merchant_name=txn["merchant_name"],
                amount=Decimal(str(txn["amount"])),
                currency=txn.get("currency", "USD"),
                merchant_category_code=txn.get("merchant_category_code"),
            )
            results.append(ct)

        logger.info("card_transactions_imported", extra={
            "card_id": str(card_id),
            "transaction_count": len(results),
        })
        return results

    # =========================================================================
    # Mileage Calculation (no posting)
    # =========================================================================

    def calculate_mileage(
        self,
        miles: Decimal,
        rate: MileageRate,
    ) -> Decimal:
        """
        Calculate mileage reimbursement.

        No posting — delegates to helpers.py pure function.
        """
        return _calculate_mileage(miles, rate.rate_per_mile)

    # =========================================================================
    # Per Diem Calculation (no posting)
    # =========================================================================

    def calculate_per_diem(
        self,
        days: int,
        rates: PerDiemRate,
        include_meals: bool = True,
        include_lodging: bool = True,
        include_incidentals: bool = True,
    ) -> Decimal:
        """
        Calculate per diem allowance.

        No posting — delegates to helpers.py pure function.
        """
        return _calculate_per_diem(
            days=days,
            rates=rates,
            include_meals=include_meals,
            include_lodging=include_lodging,
            include_incidentals=include_incidentals,
        )

    # =========================================================================
    # Record Policy Violation (no posting)
    # =========================================================================

    def record_policy_violation(
        self,
        report_id: UUID,
        violations: Sequence[PolicyViolation],
        actor_id: UUID,
    ) -> list[PolicyViolation]:
        """
        Flag an expense report for policy violations.

        No journal posting — records violations for review workflow.
        Returns the violations for downstream processing.
        """
        logger.info("expense_policy_violations_recorded", extra={
            "report_id": str(report_id),
            "actor_id": str(actor_id),
            "violation_count": len(violations),
            "violation_types": list({v.violation_type for v in violations}),
        })
        return list(violations)

    # =========================================================================
    # Receipt Match (posting via MatchingEngine)
    # =========================================================================

    def record_receipt_match(
        self,
        expense_line_id: UUID,
        card_transaction_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[MatchResult, ModulePostingResult]:
        """
        Match an expense receipt to a corporate card transaction.

        Engine: MatchingEngine creates 2-way match (receipt <-> card txn).
        Profile: expense.receipt_matched -> ExpenseReceiptMatched
        """
        try:
            # Engine: create match between expense line and card transaction
            receipt_candidate = MatchCandidate(
                document_type="EXPENSE_LINE",
                document_id=expense_line_id,
                amount=Money.of(amount, currency),
            )
            card_candidate = MatchCandidate(
                document_type="CARD_TRANSACTION",
                document_id=card_transaction_id,
                amount=Money.of(amount, currency),
            )

            match_result = self._matching.create_match(
                documents=(receipt_candidate, card_candidate),
                match_type=MatchType.TWO_WAY,
                as_of_date=effective_date,
            )

            logger.info("expense_receipt_match_started", extra={
                "expense_line_id": str(expense_line_id),
                "card_transaction_id": str(card_transaction_id),
                "amount": str(amount),
                "match_status": match_result.status.value,
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_RECEIPT_MATCH_WORKFLOW,
                "expense_receipt_match",
                expense_line_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return match_result, failure

            payload: dict[str, Any] = {
                "expense_line_id": str(expense_line_id),
                "card_transaction_id": str(card_transaction_id),
                "amount": str(amount),
                "match_id": str(match_result.match_id),
                "match_status": match_result.status.value,
            }

            posting_result = self._poster.post_event(
                event_type="expense.receipt_matched",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            commit_or_rollback(self._session, posting_result)
            return match_result, posting_result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # DCAA Travel Authorization (D6 / FAR 31.205-46)
    # =========================================================================

    def submit_travel_authorization(
        self,
        authorization: TravelAuthorization,
        actor_id: UUID,
    ) -> TravelAuthorizationModel:
        """
        Submit a pre-travel authorization request (D6).

        Persists the authorization and its cost estimate lines, then posts
        the ``expense.travel_auth_submitted`` workflow event.  Does NOT
        post journal entries -- travel auths are pre-approval artifacts.

        Args:
            authorization: The travel authorization to submit.
            actor_id: The employee submitting.

        Returns:
            The persisted TravelAuthorizationModel.

        Raises:
            ValueError: If travel dates are invalid.
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_SUBMIT_TRAVEL_AUTH_WORKFLOW,
                "travel_authorization",
                authorization.authorization_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                raise ValueError(
                    failure.message or "Workflow guard blocked or rejected travel authorization submission"
                )

            logger.info("travel_auth_submission_started", extra={
                "authorization_id": str(authorization.authorization_id),
                "employee_id": str(authorization.employee_id),
                "destination": authorization.destination,
                "total_estimated": str(authorization.total_estimated),
            })

            # Persist authorization + lines
            orm_auth = TravelAuthorizationModel.from_dto(
                authorization, created_by_id=actor_id,
            )
            orm_auth.status = TravelAuthStatus.SUBMITTED.value
            self._session.add(orm_auth)

            for cost_est in authorization.estimated_costs:
                orm_line = TravelAuthLineModel.from_dto(
                    cost_est,
                    authorization_id=authorization.authorization_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_line)

            # Post workflow event
            self._poster.post_event(
                event_type="expense.travel_auth_submitted",
                payload={
                    "authorization_id": str(authorization.authorization_id),
                    "employee_id": str(authorization.employee_id),
                    "destination": authorization.destination,
                    "travel_start": str(authorization.travel_start),
                    "travel_end": str(authorization.travel_end),
                    "total_estimated": str(authorization.total_estimated),
                    "purpose": authorization.purpose,
                },
                effective_date=self._clock.now().date(),
                actor_id=actor_id,
                amount=authorization.total_estimated,
                currency=authorization.currency,
            )

            self._session.commit()

            logger.info("travel_auth_submitted", extra={
                "authorization_id": str(authorization.authorization_id),
                "status": "submitted",
            })
            return orm_auth

        except Exception:
            self._session.rollback()
            raise

    def approve_travel_authorization(
        self,
        authorization_id: UUID,
        approver_id: UUID,
    ) -> TravelAuthorizationModel:
        """
        Approve a travel authorization (D6).

        Transitions the authorization to APPROVED and posts the
        ``expense.travel_auth_approved`` workflow event.

        Args:
            authorization_id: The authorization to approve.
            approver_id: The supervisor/manager approving.

        Returns:
            The updated TravelAuthorizationModel.

        Raises:
            ValueError: If authorization not found or not in pending_approval.
        """
        try:
            orm_auth = (
                self._session.query(TravelAuthorizationModel)
                .filter_by(id=authorization_id)
                .first()
            )
            if orm_auth is None:
                raise ValueError(
                    f"Travel authorization {authorization_id} not found"
                )
            if orm_auth.status not in (
                TravelAuthStatus.SUBMITTED.value,
                TravelAuthStatus.PENDING_APPROVAL.value,
            ):
                raise ValueError(
                    f"Travel authorization {authorization_id} has status "
                    f"'{orm_auth.status}' -- must be 'submitted' or "
                    f"'pending_approval' to approve"
                )

            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_APPROVE_TRAVEL_AUTH_WORKFLOW,
                "travel_authorization",
                authorization_id,
                current_state="pending_approval",
                action="approve",
                actor_id=approver_id,
                context=None,
            )
            if failure is not None:
                raise ValueError(
                    failure.message or "Workflow guard blocked or rejected travel authorization approval"
                )

            orm_auth.status = TravelAuthStatus.APPROVED.value

            self._poster.post_event(
                event_type="expense.travel_auth_approved",
                payload={
                    "authorization_id": str(authorization_id),
                    "approver_id": str(approver_id),
                    "employee_id": str(orm_auth.employee_id),
                    "total_estimated": str(orm_auth.total_estimated),
                },
                effective_date=self._clock.now().date(),
                actor_id=approver_id,
                amount=orm_auth.total_estimated,
                currency=orm_auth.currency,
            )

            self._session.commit()

            logger.info("travel_auth_approved", extra={
                "authorization_id": str(authorization_id),
                "approver_id": str(approver_id),
            })
            return orm_auth

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # DCAA Expense Report with GSA Check (D6 + D7)
    # =========================================================================

    def record_expense_report_with_gsa_check(
        self,
        report_id: UUID,
        lines: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        employee_id: UUID | None = None,
        travel_authorization_id: UUID | None = None,
        gsa_rate_table: GSARateTable | None = None,
        travel_location: str | None = None,
        travel_start: date | None = None,
        travel_end: date | None = None,
        require_pre_auth: bool = True,
        gsa_enforcement_enabled: bool = True,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record an expense report with DCAA compliance checks (D6, D7).

        Pre-validation:
        1. D6 -- Validates pre-travel authorization if travel expenses exist.
        2. D7 -- Validates GSA rate compliance if GSA enforcement is enabled.

        If both pass, posts via the standard expense.report_approved pipeline.

        Args:
            report_id: Unique ID for the expense report.
            lines: Expense line items (each with category, amount, date).
            effective_date: Journal entry effective date.
            actor_id: Who is submitting.
            employee_id: The employee who incurred expenses.
            travel_authorization_id: Optional linked travel auth (D6).
            gsa_rate_table: GSA rate table for enforcement (D7).
            travel_location: Destination for GSA lookup.
            travel_start: First day of travel.
            travel_end: Last day of travel.
            require_pre_auth: Whether D6 pre-auth is required.
            gsa_enforcement_enabled: Whether D7 GSA caps are enforced.
            currency: ISO 4217 currency code.
            description: Optional report description.

        Returns:
            ModulePostingResult from the expense posting.

        Raises:
            ValueError: If D6 or D7 validation fails.
        """
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                EXPENSE_RECORD_REPORT_WITH_GSA_CHECK_WORKFLOW,
                "expense_report",
                report_id,
                actor_id=actor_id,
                context=None,
            )
            if failure is not None:
                return failure

            # Detect travel expense lines
            travel_categories = {
                c.value for c in TravelExpenseCategory
            }
            has_travel = any(
                line.get("category", "").lower() in travel_categories
                for line in lines
            )

            # D6: Pre-travel authorization check
            if has_travel and require_pre_auth:
                authorization: TravelAuthorization | None = None
                if travel_authorization_id is not None:
                    orm_auth = (
                        self._session.query(TravelAuthorizationModel)
                        .filter_by(id=travel_authorization_id)
                        .first()
                    )
                    if orm_auth is not None:
                        authorization = orm_auth.to_dto()

                is_valid, error = validate_pre_travel_authorization(
                    has_travel_expenses=has_travel,
                    authorization=authorization,
                    require_pre_auth=require_pre_auth,
                )
                if not is_valid:
                    raise ValueError(f"D6 violation: {error}")

            # D7: GSA compliance check
            if (
                gsa_enforcement_enabled
                and gsa_rate_table is not None
                and travel_location
                and travel_start
                and travel_end
            ):
                gsa_lines: list[tuple[UUID, TravelExpenseCategory, Decimal, date]] = []
                for line in lines:
                    cat_str = line.get("category", "other").lower()
                    try:
                        cat = TravelExpenseCategory(cat_str)
                    except ValueError:
                        continue  # non-travel category, skip GSA check
                    line_amount = Decimal(str(line.get("amount", "0")))
                    line_date = line.get("date", effective_date)
                    if isinstance(line_date, str):
                        line_date = date.fromisoformat(line_date)
                    gsa_lines.append((
                        line.get("line_id", uuid4()),
                        cat,
                        line_amount,
                        line_date,
                    ))

                if gsa_lines:
                    gsa_result = validate_gsa_compliance(
                        expense_lines=tuple(gsa_lines),
                        gsa_rate_table=gsa_rate_table,
                        travel_location=travel_location,
                        travel_start=travel_start,
                        travel_end=travel_end,
                    )

                    if not gsa_result.is_compliant:
                        violations_str = "; ".join(
                            f"{v.category.value}: ${v.claimed_amount} exceeds "
                            f"GSA limit ${v.gsa_limit} by ${v.excess}"
                            for v in gsa_result.violations
                        )
                        raise ValueError(
                            f"D7 GSA rate violation: {violations_str}"
                        )

                    # Post GSA validation event for audit trail
                    self._poster.post_event(
                        event_type="expense.report_gsa_validated",
                        payload={
                            "report_id": str(report_id),
                            "travel_location": travel_location,
                            "total_claimed": str(gsa_result.total_claimed),
                            "total_allowed": str(gsa_result.total_allowed),
                            "is_compliant": True,
                        },
                        effective_date=effective_date,
                        actor_id=actor_id,
                        amount=Decimal("0"),
                        currency=currency,
                    )

            logger.info("expense_report_gsa_validated", extra={
                "report_id": str(report_id),
                "has_travel": has_travel,
                "gsa_checked": gsa_enforcement_enabled,
            })

            # Delegate to existing multi-line report posting
            return self.record_expense_report(
                report_id=report_id,
                lines=lines,
                effective_date=effective_date,
                actor_id=actor_id,
                employee_id=employee_id,
                currency=currency,
                description=description,
            )

        except Exception:
            self._session.rollback()
            raise
