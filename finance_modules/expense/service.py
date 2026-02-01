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

    service = ExpenseService(session, role_resolver, clock)
    result = service.record_expense(
        expense_id=uuid4(), category="TRAVEL",
        amount=Decimal("500.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
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
from finance_modules.expense.helpers import (
    calculate_mileage as _calculate_mileage,
    calculate_per_diem as _calculate_per_diem,
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            if posting_result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return match_result, posting_result

        except Exception:
            self._session.rollback()
            raise
