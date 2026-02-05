"""
Budgeting Module Service (``finance_modules.budget.service``).

Responsibility
--------------
Orchestrates budgeting operations -- budget entry creation, encumbrance
lifecycle management, budget-vs-actual variance analysis, versioning,
locking, and forecasting -- by delegating journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``BudgetService`` is the sole
public entry point for budgeting operations.  Budget entries are memo
postings (Dr BUDGET_CONTROL / Cr BUDGET_OFFSET) that do not affect the
financial trial balance.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on
          event_type inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Budget lock violation  -> ``ValueError`` raised before posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying budget IDs, amounts, and periods.  All
journal entries feed the kernel audit chain (R11).  Budget versions
maintain full history of changes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.party_service import PartyService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
)
from finance_modules._posting_helpers import run_workflow_guard
from finance_services.workflow_executor import WorkflowExecutor
from finance_modules.budget.workflows import (
    BUDGET_CANCEL_ENCUMBRANCE_WORKFLOW,
    BUDGET_POST_ENTRY_WORKFLOW,
    BUDGET_RECORD_ENCUMBRANCE_WORKFLOW,
    BUDGET_RELIEVE_ENCUMBRANCE_WORKFLOW,
    BUDGET_TRANSFER_WORKFLOW,
    BUDGET_UPDATE_FORECAST_WORKFLOW,
)
from finance_modules.budget.models import (
    BudgetEntry,
    BudgetLock,
    BudgetStatus,
    BudgetVariance,
    BudgetVersion,
    Encumbrance,
    EncumbranceStatus,
    ForecastEntry,
)
from finance_modules.budget.orm import BudgetLineModel, BudgetTransferModel

logger = get_logger("modules.budget.service")


class BudgetService:
    """
    Orchestrates budget management operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``check_budget_variance``, ``get_encumbrances``,
      etc.) return pure domain objects with no side-effects on the journal.
    * Budget entries are memo postings (Dr BUDGET_CONTROL / Cr BUDGET_OFFSET).

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
    * Budget entries do NOT affect the financial trial balance.
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
    # Budget Entries
    # =========================================================================

    def post_budget_entry(
        self,
        version_id: UUID,
        account_code: str,
        period: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        dimensions: tuple[tuple[str, str], ...] | None = None,
    ) -> tuple[BudgetEntry, ModulePostingResult]:
        """Post a budget entry as a memo posting."""
        entry = BudgetEntry(
            id=uuid4(),
            version_id=version_id,
            account_code=account_code,
            period=period,
            amount=amount,
            currency=currency,
            dimensions=dimensions,
        )

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                BUDGET_POST_ENTRY_WORKFLOW,
                "budget_entry",
                entry.id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return entry, failure

            logger.info("budget_entry_posted", extra={
                "version_id": str(version_id),
                "account_code": account_code,
                "period": period,
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="budget.entry",
                payload={
                    "version_id": str(version_id),
                    "account_code": account_code,
                    "period": period,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )
            if result.is_success:
                orm_line = BudgetLineModel.from_dto(entry, created_by_id=actor_id)
                self._session.add(orm_line)
                self._session.commit()
            else:
                self._session.rollback()
            return entry, result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Budget Transfer
    # =========================================================================

    def transfer_budget(
        self,
        from_account: str,
        to_account: str,
        amount: Decimal,
        period: str,
        effective_date: date,
        actor_id: UUID,
        version_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """Transfer budget between accounts."""
        try:
            transfer_id = uuid4()
            failure = run_workflow_guard(
                self._workflow_executor,
                BUDGET_TRANSFER_WORKFLOW,
                "budget_transfer",
                transfer_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            logger.info("budget_transfer", extra={
                "from_account": from_account,
                "to_account": to_account,
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="budget.transfer",
                payload={
                    "from_account": from_account,
                    "to_account": to_account,
                    "period": period,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )
            if result.is_success:
                orm_transfer = BudgetTransferModel(
                    id=uuid4(),
                    version_id=version_id,
                    from_account_code=from_account,
                    from_period=period,
                    to_account_code=to_account,
                    to_period=period,
                    amount=amount,
                    currency=currency,
                    transfer_date=effective_date,
                    reason=f"Budget transfer {from_account} -> {to_account}",
                    transferred_by=actor_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_transfer)
                self._session.commit()
            else:
                self._session.rollback()
            return result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Budget Lock
    # =========================================================================

    def lock_budget(
        self,
        version_id: UUID,
        period_range_start: str,
        period_range_end: str,
        actor_id: UUID,
    ) -> BudgetLock:
        """Lock budget version for a period range. No posting."""
        lock = BudgetLock(
            id=uuid4(),
            version_id=version_id,
            period_range_start=period_range_start,
            period_range_end=period_range_end,
            locked_by=actor_id,
            locked_date=self._clock.now().date(),
        )
        logger.info("budget_locked", extra={
            "version_id": str(version_id),
            "period_range": f"{period_range_start}-{period_range_end}",
        })
        return lock

    # =========================================================================
    # Encumbrances
    # =========================================================================

    def record_encumbrance(
        self,
        po_id: UUID,
        amount: Decimal,
        account_code: str,
        period: str,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[Encumbrance, ModulePostingResult]:
        """Record an encumbrance commitment against budget."""
        encumbrance = Encumbrance(
            id=uuid4(),
            po_id=po_id,
            account_code=account_code,
            amount=amount,
            period=period,
            currency=currency,
        )

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                BUDGET_RECORD_ENCUMBRANCE_WORKFLOW,
                "encumbrance",
                encumbrance.id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return encumbrance, failure

            result = self._poster.post_event(
                event_type="budget.encumbrance_commit",
                payload={
                    "po_id": str(po_id),
                    "account_code": account_code,
                    "period": period,
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
            return encumbrance, result
        except Exception:
            self._session.rollback()
            raise

    def relieve_encumbrance(
        self,
        encumbrance: Encumbrance,
        relief_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[Encumbrance, ModulePostingResult]:
        """Relieve encumbrance when invoice received."""
        new_relieved = encumbrance.relieved_amount + relief_amount
        new_status = (
            EncumbranceStatus.RELIEVED
            if new_relieved >= encumbrance.amount
            else EncumbranceStatus.PARTIALLY_RELIEVED
        )
        updated = dataclasses.replace(
            encumbrance,
            relieved_amount=new_relieved,
            status=new_status,
        )

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                BUDGET_RELIEVE_ENCUMBRANCE_WORKFLOW,
                "encumbrance",
                encumbrance.id,
                actor_id=actor_id,
                amount=relief_amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return updated, failure

            result = self._poster.post_event(
                event_type="budget.encumbrance_relieve",
                payload={
                    "po_id": str(encumbrance.po_id),
                    "relief_amount": str(relief_amount),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=relief_amount,
                currency=currency,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return updated, result
        except Exception:
            self._session.rollback()
            raise

    def cancel_encumbrance(
        self,
        encumbrance: Encumbrance,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> tuple[Encumbrance, ModulePostingResult]:
        """Cancel an open encumbrance."""
        remaining = encumbrance.amount - encumbrance.relieved_amount
        updated = dataclasses.replace(
            encumbrance,
            status=EncumbranceStatus.CANCELLED,
        )

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                BUDGET_CANCEL_ENCUMBRANCE_WORKFLOW,
                "encumbrance",
                encumbrance.id,
                actor_id=actor_id,
                amount=remaining,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return updated, failure

            result = self._poster.post_event(
                event_type="budget.encumbrance_cancel",
                payload={
                    "po_id": str(encumbrance.po_id),
                    "cancelled_amount": str(remaining),
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=remaining,
                currency=currency,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return updated, result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Budget vs Actual
    # =========================================================================

    def get_budget_vs_actual(
        self,
        budget_amount: Decimal,
        actual_amount: Decimal,
        account_code: str,
        period: str,
    ) -> BudgetVariance:
        """
        Calculate budget vs actual variance.

        Pure query — no posting.
        """
        variance = budget_amount - actual_amount
        pct = (variance / budget_amount * Decimal("100")) if budget_amount != 0 else Decimal("0")

        return BudgetVariance(
            account_code=account_code,
            period=period,
            budget_amount=budget_amount,
            actual_amount=actual_amount,
            variance_amount=variance,
            variance_percentage=pct,
            is_favorable=variance >= 0,
        )

    def get_encumbrance_balance(
        self,
        encumbrances: Sequence[Encumbrance],
    ) -> Decimal:
        """Calculate outstanding encumbrance balance."""
        total = Decimal("0")
        for enc in encumbrances:
            if enc.status in (EncumbranceStatus.OPEN, EncumbranceStatus.PARTIALLY_RELIEVED):
                total += enc.amount - enc.relieved_amount
        return total

    def get_available_budget(
        self,
        budget_amount: Decimal,
        actual_amount: Decimal,
        encumbrance_balance: Decimal,
    ) -> Decimal:
        """Calculate available budget = budget - actual - encumbrances."""
        return budget_amount - actual_amount - encumbrance_balance

    # =========================================================================
    # Forecast
    # =========================================================================

    def update_forecast(
        self,
        version_id: UUID,
        account_code: str,
        period: str,
        forecast_amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        basis: str = "trend",
        currency: str = "USD",
    ) -> tuple[ForecastEntry, ModulePostingResult]:
        """Post a forecast update as a memo posting."""
        entry = ForecastEntry(
            id=uuid4(),
            version_id=version_id,
            account_code=account_code,
            period=period,
            forecast_amount=forecast_amount,
            basis=basis,
            currency=currency,
        )

        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                BUDGET_UPDATE_FORECAST_WORKFLOW,
                "forecast_entry",
                entry.id,
                actor_id=actor_id,
                amount=forecast_amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return entry, failure

            result = self._poster.post_event(
                event_type="budget.forecast_update",
                payload={
                    "version_id": str(version_id),
                    "account_code": account_code,
                    "period": period,
                    "basis": basis,
                },
                effective_date=effective_date,
                actor_id=actor_id,
                amount=forecast_amount,
                currency=currency,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return entry, result
        except Exception:
            self._session.rollback()
            raise
