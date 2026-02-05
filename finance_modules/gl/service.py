"""
General Ledger Module Service (``finance_modules.gl.service``).

Responsibility
--------------
Orchestrates GL operations -- manual journal entries, accruals, reversals,
recurring entries, intercompany eliminations, currency revaluation and
translation, account reconciliation, and period close tasks -- by
delegating pure computation to ``finance_engines`` and journal persistence
to ``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``GeneralLedgerService`` is the sole
public entry point for GL operations.  It composes stateless engines
(``VarianceCalculator``) and the kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* R4  -- Double-entry balance enforced downstream by ``JournalWriter``.
* R12 -- Closed-period enforcement via kernel ``PeriodService``.
* R25 -- Kernel primitives only: Money from finance_kernel; no parallel
          financial types in this module.
* R26 -- Journal + link graph are the system of record; GL module ORM
          (e.g. RecurringEntryModel, AccountReconciliationModel) is an
          operational projection, persisted in the same transaction where used.
* R27 -- Ledger impact is defined by kernel policy (profiles); this
          module does not branch on operational results to choose accounts.
* Workflow executor required; every financial action calls
  ``execute_transition`` (guards enforced, no bypass).

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Period lock violation  -> kernel rejects posting (R12/R13).

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying entry IDs, amounts, and descriptions.  All
journal entries feed the kernel audit chain (R11).

Usage::

    # workflow_executor is required — guards are always enforced (no bypass).
    service = GeneralLedgerService(session, role_resolver, workflow_executor, clock=clock)
    result = service.record_journal_entry(
        entry_id=uuid4(), description="Monthly accrual",
        lines=[{"account": "5100", "debit": "1000.00"}],
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

from finance_engines.variance import VarianceCalculator, VarianceResult
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
from finance_modules.gl.workflows import (
    GL_ADJUSTMENT_WORKFLOW,
    GL_CLOSING_ENTRY_WORKFLOW,
    GL_CTA_WORKFLOW,
    GL_DEFERRED_EXPENSE_RECOGNITION_WORKFLOW,
    GL_DEFERRED_REVENUE_RECOGNITION_WORKFLOW,
    GL_DIVIDEND_DECLARED_WORKFLOW,
    GL_FX_REALIZED_GAIN_WORKFLOW,
    GL_FX_REALIZED_LOSS_WORKFLOW,
    GL_FX_UNREALIZED_GAIN_WORKFLOW,
    GL_FX_UNREALIZED_LOSS_WORKFLOW,
    GL_INTERCOMPANY_TRANSFER_WORKFLOW,
    GL_JOURNAL_ENTRY_WORKFLOW,
    GL_RECURRING_ENTRY_WORKFLOW,
    GL_RETAINED_EARNINGS_ROLL_WORKFLOW,
)
from finance_modules.gl.models import (
    AccountReconciliation,
    PeriodCloseTask,
    ReconciliationStatus,
    RecurringEntry,
    RevaluationResult,
    TranslationMethod,
    TranslationResult,
)
from finance_modules.gl.orm import (
    AccountReconciliationModel,
    RecurringEntryModel,
)
from finance_services.workflow_executor import WorkflowExecutor

logger = get_logger("modules.gl.service")


class GeneralLedgerService:
    """
    Orchestrates general-ledger operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``calculate_variance``, ``reconcile_account``,
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

    Engine composition:
    - VarianceCalculator: budget vs actual variance analysis

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
        self._workflow_executor = workflow_executor  # Required: guards always enforced

        # Kernel posting (auto_commit=False -- we own the boundary). G14: actor validation mandatory.
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
            party_service=party_service,
        )

        # Stateless engines
        self._variance = VarianceCalculator()

    # =========================================================================
    # Journal Entries
    # =========================================================================

    def record_journal_entry(
        self,
        entry_id: UUID,
        description: str,
        lines: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        amount: Decimal | None = None,
        currency: str = "USD",
        reference: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a manual journal entry.

        Lines should contain account/debit/credit information for profile
        dispatch. The total debit amount is used as the posting amount.

        Profile: gl.journal_entry (dispatched via profile selector)
        """
        try:
            # Derive posting amount from lines if not provided
            posting_amount = amount
            if posting_amount is None:
                posting_amount = sum(
                    Decimal(str(line.get("debit", "0")))
                    for line in lines
                )

            logger.info("gl_journal_entry_started", extra={
                "entry_id": str(entry_id),
                "line_count": len(lines),
                "amount": str(posting_amount),
                "description": description[:80],
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                GL_JOURNAL_ENTRY_WORKFLOW,
                "gl_journal_entry",
                entry_id,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.journal_entry",
                payload={
                    "entry_id": str(entry_id),
                    "description": description,
                    "lines": list(lines),
                    "reference": reference,
                    "line_count": len(lines),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Adjustments
    # =========================================================================

    def record_adjustment(
        self,
        entry_id: UUID,
        description: str,
        lines: Sequence[dict[str, Any]],
        effective_date: date,
        actor_id: UUID,
        adjustment_type: str = "ACCRUAL",
        amount: Decimal | None = None,
        currency: str = "USD",
        original_entry_id: UUID | None = None,
    ) -> ModulePostingResult:
        """
        Record an adjusting journal entry.

        Supports accruals, deferrals, reclassifications, and corrections.

        Profile: gl.adjustment (dispatched via profile selector)
        """
        try:
            posting_amount = amount
            if posting_amount is None:
                posting_amount = sum(
                    Decimal(str(line.get("debit", "0")))
                    for line in lines
                )

            logger.info("gl_adjustment_started", extra={
                "entry_id": str(entry_id),
                "adjustment_type": adjustment_type,
                "line_count": len(lines),
                "amount": str(posting_amount),
            })

            payload: dict[str, Any] = {
                "entry_id": str(entry_id),
                "description": description,
                "lines": list(lines),
                "adjustment_type": adjustment_type,
                "line_count": len(lines),
            }
            if original_entry_id is not None:
                payload["original_entry_id"] = str(original_entry_id)

            failure = run_workflow_guard(
                self._workflow_executor,
                GL_ADJUSTMENT_WORKFLOW,
                "gl_adjustment",
                entry_id,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
                context={"adjustment_type": adjustment_type},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.adjustment",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Period Close
    # =========================================================================

    def record_closing_entry(
        self,
        period_id: str,
        effective_date: date,
        actor_id: UUID,
        net_income: Decimal | None = None,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record year-end/period closing entry.

        Closes revenue and expense accounts to retained earnings
        via the income summary account.

        Profile: gl.year_end_close -> YearEndClose
        """
        try:
            posting_amount = abs(net_income) if net_income is not None else Decimal("0")

            logger.info("gl_closing_entry_started", extra={
                "period_id": period_id,
                "net_income": str(net_income) if net_income is not None else None,
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                GL_CLOSING_ENTRY_WORKFLOW,
                "gl_closing_entry",
                uuid4(),
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
                context={"period_id": period_id},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.year_end_close",
                payload={
                    "period_id": period_id,
                    "net_income": str(net_income) if net_income is not None else None,
                    "closing_type": "YEAR_END",
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Budget Variance Analysis
    # =========================================================================

    def compute_budget_variance(
        self,
        budget_amount: Decimal,
        actual_amount: Decimal,
        currency: str = "USD",
    ) -> VarianceResult:
        """
        Compute budget vs actual variance.

        Engine: VarianceCalculator.standard_cost_variance()
        Pure computation -- no posting, no transaction side effects.
        """
        logger.info("gl_budget_variance_started", extra={
            "budget_amount": str(budget_amount),
            "actual_amount": str(actual_amount),
        })

        return self._variance.standard_cost_variance(
            standard_cost=Money.of(budget_amount, currency),
            actual_cost=Money.of(actual_amount, currency),
        )

    # =========================================================================
    # Intercompany
    # =========================================================================

    def record_intercompany_transfer(
        self,
        transfer_id: UUID,
        from_entity: str,
        to_entity: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record an intercompany transfer.

        Profile: gl.intercompany_transfer -> IntercompanyTransfer
        """
        try:
            logger.info("gl_intercompany_transfer_started", extra={
                "transfer_id": str(transfer_id),
                "from_entity": from_entity,
                "to_entity": to_entity,
                "amount": str(amount),
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                GL_INTERCOMPANY_TRANSFER_WORKFLOW,
                "gl_intercompany_transfer",
                transfer_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={"from_entity": from_entity, "to_entity": to_entity},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.intercompany_transfer",
                payload={
                    "transfer_id": str(transfer_id),
                    "from_entity": from_entity,
                    "to_entity": to_entity,
                    "description": description,
                },
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
    # Dividend
    # =========================================================================

    def record_dividend_declared(
        self,
        dividend_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a dividend declaration.

        Profile: gl.dividend_declared -> DividendDeclared
        """
        try:
            logger.info("gl_dividend_declared_started", extra={
                "dividend_id": str(dividend_id),
                "amount": str(amount),
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                GL_DIVIDEND_DECLARED_WORKFLOW,
                "gl_dividend_declared",
                dividend_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.dividend_declared",
                payload={
                    "dividend_id": str(dividend_id),
                    "description": description,
                },
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
    # Deferred Revenue Recognition
    # =========================================================================

    def recognize_deferred_revenue(
        self,
        recognition_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        remaining_deferred: Decimal,
        currency: str = "USD",
        org_unit: str | None = None,
        cost_center: str | None = None,
        project: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Recognize deferred revenue over service period.

        Profile: deferred.revenue_recognition -> DeferredRevenueRecognition
        Guards: amount > 0, remaining_deferred >= 0
        """
        payload: dict[str, Any] = {
            "amount": str(amount),
            "remaining_deferred": str(remaining_deferred),
        }
        if org_unit:
            payload["org_unit"] = org_unit
        if cost_center:
            payload["cost_center"] = cost_center
        if project:
            payload["project"] = project

        logger.info("gl_deferred_revenue_recognition", extra={
            "recognition_id": str(recognition_id),
            "amount": str(amount),
            "remaining_deferred": str(remaining_deferred),
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_DEFERRED_REVENUE_RECOGNITION_WORKFLOW,
                "gl_deferred_revenue_recognition",
                recognition_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="deferred.revenue_recognition",
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
    # Deferred Expense Recognition (Prepaid)
    # =========================================================================

    def recognize_deferred_expense(
        self,
        recognition_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        remaining_deferred: Decimal,
        currency: str = "USD",
        org_unit: str | None = None,
        cost_center: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Recognize prepaid expense over benefit period.

        Profile: deferred.expense_recognition -> DeferredExpenseRecognition
        Guards: amount > 0, remaining_deferred >= 0
        """
        payload: dict[str, Any] = {
            "amount": str(amount),
            "remaining_deferred": str(remaining_deferred),
        }
        if org_unit:
            payload["org_unit"] = org_unit
        if cost_center:
            payload["cost_center"] = cost_center

        logger.info("gl_deferred_expense_recognition", extra={
            "recognition_id": str(recognition_id),
            "amount": str(amount),
            "remaining_deferred": str(remaining_deferred),
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_DEFERRED_EXPENSE_RECOGNITION_WORKFLOW,
                "gl_deferred_expense_recognition",
                recognition_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="deferred.expense_recognition",
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
    # FX — Unrealized Gain/Loss
    # =========================================================================

    def record_fx_unrealized_gain(
        self,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        original_currency: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record unrealized FX gain from period-end revaluation.

        Profile: fx.unrealized_gain -> FXUnrealizedGain
        """
        payload: dict[str, Any] = {"amount": str(amount)}
        if original_currency:
            payload["original_currency"] = original_currency

        logger.info("gl_fx_unrealized_gain", extra={
            "amount": str(amount),
            "original_currency": original_currency,
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_FX_UNREALIZED_GAIN_WORKFLOW,
                "gl_fx_unrealized_gain",
                uuid4(),
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={"original_currency": original_currency},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="fx.unrealized_gain",
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

    def record_fx_unrealized_loss(
        self,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        original_currency: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record unrealized FX loss from period-end revaluation.

        Profile: fx.unrealized_loss -> FXUnrealizedLoss
        """
        payload: dict[str, Any] = {"amount": str(amount)}
        if original_currency:
            payload["original_currency"] = original_currency

        logger.info("gl_fx_unrealized_loss", extra={
            "amount": str(amount),
            "original_currency": original_currency,
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_FX_UNREALIZED_LOSS_WORKFLOW,
                "gl_fx_unrealized_loss",
                uuid4(),
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={"original_currency": original_currency},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="fx.unrealized_loss",
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
    # FX — Realized Gain/Loss
    # =========================================================================

    def record_fx_realized_gain(
        self,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        original_currency: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record realized FX gain from settled transaction.

        Profile: fx.realized_gain -> FXRealizedGain
        """
        payload: dict[str, Any] = {"amount": str(amount)}
        if original_currency:
            payload["original_currency"] = original_currency

        logger.info("gl_fx_realized_gain", extra={
            "amount": str(amount),
            "original_currency": original_currency,
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_FX_REALIZED_GAIN_WORKFLOW,
                "gl_fx_realized_gain",
                uuid4(),
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={"original_currency": original_currency},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="fx.realized_gain",
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

    def record_fx_realized_loss(
        self,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        original_currency: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record realized FX loss from settled transaction.

        Profile: fx.realized_loss -> FXRealizedLoss
        """
        payload: dict[str, Any] = {"amount": str(amount)}
        if original_currency:
            payload["original_currency"] = original_currency

        logger.info("gl_fx_realized_loss", extra={
            "amount": str(amount),
            "original_currency": original_currency,
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_FX_REALIZED_LOSS_WORKFLOW,
                "gl_fx_realized_loss",
                uuid4(),
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context={"original_currency": original_currency},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="fx.realized_loss",
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
    # Recurring Entries
    # =========================================================================

    def generate_recurring_entry(
        self,
        template: RecurringEntry,
        effective_date: date,
        actor_id: UUID,
        amount: Decimal | None = None,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Generate a journal entry from a recurring entry template.

        Reads the RecurringEntry template and posts via gl.recurring_entry
        profile. The template defines the description and frequency;
        the amount is passed explicitly for each period generation.

        Profile: gl.recurring_entry -> GLRecurringEntry
        """
        from uuid import uuid4 as _uuid4

        if not template.is_active:
            return ModulePostingResult(
                status=ModulePostingStatus.GUARD_REJECTED,
                event_id=_uuid4(),
                journal_entry_ids=(),
                message="Recurring entry template is inactive",
            )

        if template.end_date and effective_date > template.end_date:
            return ModulePostingResult(
                status=ModulePostingStatus.GUARD_REJECTED,
                event_id=_uuid4(),
                journal_entry_ids=(),
                message="Effective date is past template end date",
            )

        posting_amount = amount if amount is not None else Decimal("0")

        logger.info("gl_recurring_entry_started", extra={
            "template_id": str(template.id),
            "template_name": template.name,
            "frequency": template.frequency,
            "amount": str(posting_amount),
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_RECURRING_ENTRY_WORKFLOW,
                "gl_recurring_entry",
                template.id,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
                context={"template_name": template.name},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.recurring_entry",
                payload={
                    "template_id": str(template.id),
                    "template_name": template.name,
                    "frequency": template.frequency,
                    "description": template.description,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            if result.is_success:
                orm_entry = RecurringEntryModel.from_dto(template, created_by_id=actor_id)
                orm_entry.last_generated_date = effective_date
                self._session.add(orm_entry)
            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Retained Earnings Roll
    # =========================================================================

    def record_retained_earnings_roll(
        self,
        fiscal_year: int,
        net_income: Decimal,
        actor_id: UUID,
        effective_date: date,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Roll prior year P&L to retained earnings.

        Posts Dr Income Summary / Cr Retained Earnings for the net income
        of the specified fiscal year. Distinct from year-end close which
        handles the period close process; this specifically records the
        retained earnings transfer.

        Profile: gl.retained_earnings_roll -> GLRetainedEarningsRoll
        """
        posting_amount = abs(net_income)

        logger.info("gl_retained_earnings_roll_started", extra={
            "fiscal_year": fiscal_year,
            "net_income": str(net_income),
            "amount": str(posting_amount),
        })

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                GL_RETAINED_EARNINGS_ROLL_WORKFLOW,
                "gl_retained_earnings_roll",
                uuid4(),
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
                context={"fiscal_year": fiscal_year},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="gl.retained_earnings_roll",
                payload={
                    "fiscal_year": fiscal_year,
                    "net_income": str(net_income),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Account Reconciliation
    # =========================================================================

    def reconcile_account(
        self,
        reconciliation_id: UUID,
        account_id: UUID,
        period: str,
        balance_confirmed: Decimal,
        actor_id: UUID,
        reconciled_date: date,
        notes: str | None = None,
    ) -> AccountReconciliation:
        """
        Record a period-end account reconciliation sign-off.

        No posting — pure domain record. Creates an AccountReconciliation
        confirming that the specified account balance has been verified
        for the given period.
        """
        logger.info("gl_account_reconciliation", extra={
            "account_id": str(account_id),
            "period": period,
            "balance_confirmed": str(balance_confirmed),
            "reconciled_by": str(actor_id),
        })

        recon = AccountReconciliation(
            id=reconciliation_id,
            account_id=account_id,
            period=period,
            reconciled_date=reconciled_date,
            reconciled_by=actor_id,
            status=ReconciliationStatus.RECONCILED,
            notes=notes,
            balance_confirmed=balance_confirmed,
        )

        orm_recon = AccountReconciliationModel.from_dto(recon, created_by_id=actor_id)
        self._session.add(orm_recon)
        self._session.commit()

        return recon

    # =========================================================================
    # Multi-Currency: Translation
    # =========================================================================

    def translate_balances(
        self,
        entity_id: str,
        period: str,
        source_currency: str,
        target_currency: str,
        balance_amount: Decimal,
        exchange_rate: Decimal,
        method: TranslationMethod = TranslationMethod.CURRENT_RATE,
    ) -> TranslationResult:
        """
        Translate account balances from source to target currency.

        Pure calculation — no posting. Returns TranslationResult with
        the translated amount and computed CTA.

        For current rate method: amount * rate = translated, CTA = difference.
        """
        from uuid import uuid4 as _uuid4

        translated = balance_amount * exchange_rate
        cta = translated - balance_amount

        logger.info("gl_translate_balances", extra={
            "entity_id": entity_id,
            "period": period,
            "source_currency": source_currency,
            "target_currency": target_currency,
            "balance_amount": str(balance_amount),
            "exchange_rate": str(exchange_rate),
            "translated_amount": str(translated),
            "cta_amount": str(cta),
            "method": method.value,
        })

        return TranslationResult(
            id=_uuid4(),
            entity_id=entity_id,
            period=period,
            source_currency=source_currency,
            target_currency=target_currency,
            method=method,
            translated_amount=translated,
            cta_amount=cta,
            exchange_rate=exchange_rate,
        )

    def record_cta(
        self,
        entity_id: str,
        period: str,
        cta_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        source_currency: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a Cumulative Translation Adjustment (CTA) to equity.

        Posts via fx.translation_adjustment profile (Dr Unrealized FX Loss /
        Cr CTA equity account). Typically called after translate_balances().

        Profile: fx.translation_adjustment -> FXTranslationAdjustment
        """
        try:
            posting_amount = abs(cta_amount)

            logger.info("gl_record_cta_started", extra={
                "entity_id": entity_id,
                "period": period,
                "cta_amount": str(cta_amount),
                "posting_amount": str(posting_amount),
                "source_currency": source_currency,
            })

            failure = run_workflow_guard(
                self._workflow_executor,
                GL_CTA_WORKFLOW,
                "gl_cta",
                uuid4(),
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
                context={"entity_id": entity_id, "period": period},
            )
            if failure is not None:
                return failure

            result = self._poster.post_event(
                event_type="fx.translation_adjustment",
                payload={
                    "entity_id": entity_id,
                    "period": period,
                    "cta_amount": str(cta_amount),
                    "source_currency": source_currency,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            commit_or_rollback(self._session, result)
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Multi-Currency: Period-End Revaluation
    # =========================================================================

    def run_period_end_revaluation(
        self,
        revaluation_entries: Sequence[dict],
        effective_date: date,
        actor_id: UUID,
        period: str | None = None,
        currency: str = "USD",
    ) -> RevaluationResult:
        """
        Run period-end FX revaluation for multiple currencies.

        Loops existing record_fx_unrealized_gain/loss methods for each entry.
        Each entry dict should have: original_currency, amount, is_gain (bool).

        Returns RevaluationResult summarizing the run.
        """
        from uuid import uuid4 as _uuid4

        total_gain = Decimal("0")
        total_loss = Decimal("0")
        entries_posted = 0

        logger.info("gl_period_end_revaluation_started", extra={
            "entry_count": len(revaluation_entries),
            "effective_date": effective_date.isoformat(),
            "period": period,
        })

        for entry in revaluation_entries:
            amount = Decimal(str(entry["amount"]))
            original_currency = entry.get("original_currency")
            is_gain = entry.get("is_gain", True)

            if is_gain:
                result = self.record_fx_unrealized_gain(
                    amount=amount,
                    effective_date=effective_date,
                    actor_id=actor_id,
                    currency=currency,
                    original_currency=original_currency,
                )
                if result.is_success:
                    total_gain += amount
                    entries_posted += 1
            else:
                result = self.record_fx_unrealized_loss(
                    amount=amount,
                    effective_date=effective_date,
                    actor_id=actor_id,
                    currency=currency,
                    original_currency=original_currency,
                )
                if result.is_success:
                    total_loss += amount
                    entries_posted += 1

        logger.info("gl_period_end_revaluation_completed", extra={
            "total_gain": str(total_gain),
            "total_loss": str(total_loss),
            "entries_posted": entries_posted,
        })

        return RevaluationResult(
            id=_uuid4(),
            period=period or "",
            revaluation_date=effective_date,
            currencies_processed=len(revaluation_entries),
            total_gain=total_gain,
            total_loss=total_loss,
            entries_posted=entries_posted,
        )
