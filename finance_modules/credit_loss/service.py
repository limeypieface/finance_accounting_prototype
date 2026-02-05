"""
Credit Loss Module Service (``finance_modules.credit_loss.service``).

Responsibility
--------------
Orchestrates ASC 326 (CECL) credit loss operations -- expected credit loss
estimation via loss-rate and PD/LGD methods, forward-looking adjustments,
vintage analysis, allowance provisioning, and write-off recording -- by
delegating pure computation to ``calculations.py`` and journal persistence
to ``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``CreditLossService`` is the sole
public entry point for credit-loss operations.  It composes pure calculation
functions (``calculate_ecl_loss_rate``, ``calculate_ecl_pd_lgd``,
``calculate_vintage_loss_curve``, ``apply_forward_looking_adjustment``)
and the kernel ``ModulePostingService``.

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
* Calculation errors (e.g., invalid loss rate)  -> propagate before posting.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying portfolio IDs, loss rates, and allowance
amounts.  All journal entries feed the kernel audit chain (R11).
ASC 326 compliance requires full traceability of loss estimation methodology.
"""

from __future__ import annotations

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
from finance_modules.credit_loss.workflows import (
    CREDIT_LOSS_ADJUST_PROVISION_WORKFLOW,
    CREDIT_LOSS_RECORD_PROVISION_WORKFLOW,
    CREDIT_LOSS_RECORD_RECOVERY_WORKFLOW,
    CREDIT_LOSS_RECORD_WRITE_OFF_WORKFLOW,
)
from finance_modules.credit_loss.calculations import (
    apply_forward_looking_adjustment,
    calculate_ecl_loss_rate,
    calculate_ecl_pd_lgd,
    calculate_provision_change,
    calculate_vintage_loss_curve,
)
from finance_modules.credit_loss.models import (
    ECLEstimate,
    ForwardLookingAdjustment,
    VintageAnalysis,
)

logger = get_logger("modules.credit_loss.service")


class CreditLossService:
    """
    Orchestrates ASC 326 (CECL) credit loss operations through calculations
    and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``estimate_ecl``, ``analyze_vintage``, etc.)
      return pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.
    * All loss-rate calculations use ``Decimal`` -- NEVER ``float``.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).
    * Does NOT persist credit-loss domain models -- only journal entries are
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
    # ECL Calculation (Pure)
    # =========================================================================

    def calculate_ecl(
        self,
        segment: str,
        gross_balance: Decimal,
        historical_loss_rate: Decimal,
        method: str = "loss_rate",
        probability_of_default: Decimal = Decimal("0"),
        loss_given_default: Decimal = Decimal("0"),
    ) -> ECLEstimate:
        """
        Calculate Expected Credit Loss (pure, no posting).
        """
        if method == "pd_lgd":
            ecl_amount = calculate_ecl_pd_lgd(
                gross_balance, probability_of_default, loss_given_default,
            )
            loss_rate = probability_of_default * loss_given_default
        else:
            ecl_amount = calculate_ecl_loss_rate(gross_balance, historical_loss_rate)
            loss_rate = historical_loss_rate

        return ECLEstimate(
            id=uuid4(),
            segment=segment,
            as_of_date=self._clock.now().date(),
            gross_receivable=gross_balance,
            loss_rate=loss_rate,
            ecl_amount=ecl_amount,
            method=method,
        )

    # =========================================================================
    # Provision Posting
    # =========================================================================

    def record_provision(
        self,
        segment: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Record credit loss provision (Dr Bad Debt Expense / Cr Allowance)."""
        try:
            entity_id = uuid4()
            failure = run_workflow_guard(
                self._workflow_executor,
                CREDIT_LOSS_RECORD_PROVISION_WORKFLOW,
                "credit_loss_provision",
                entity_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "segment": segment,
                "amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="credit_loss.provision",
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

    def adjust_provision(
        self,
        segment: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Adjust credit loss provision."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                CREDIT_LOSS_ADJUST_PROVISION_WORKFLOW,
                "credit_loss_adjustment",
                uuid4(),
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "segment": segment,
                "amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="credit_loss.adjustment",
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
    # Write-Off & Recovery
    # =========================================================================

    def record_write_off(
        self,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        segment: str = "general",
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Write off against allowance (Dr Allowance / Cr AR)."""
        failure = run_workflow_guard(
            self._workflow_executor,
            CREDIT_LOSS_RECORD_WRITE_OFF_WORKFLOW,
            "credit_loss_write_off",
            customer_id,
            actor_id=actor_id,
            amount=amount,
            currency=currency,
            context=None,
        )
        if failure is not None:
            return failure

        payload: dict[str, Any] = {
            "customer_id": str(customer_id),
            "segment": segment,
            "amount": str(amount),
        }

        try:
            result = self._poster.post_event(
                event_type="credit_loss.write_off",
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

    def record_recovery(
        self,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        segment: str = "general",
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """Record recovery of previously written-off amount (Dr AR / Cr Allowance)."""
        try:
            failure = run_workflow_guard(
                self._workflow_executor,
                CREDIT_LOSS_RECORD_RECOVERY_WORKFLOW,
                "credit_loss_recovery",
                customer_id,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
                context=None,
            )
            if failure is not None:
                return failure

            payload: dict[str, Any] = {
                "customer_id": str(customer_id),
                "segment": segment,
                "amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="credit_loss.recovery",
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
    # Vintage Analysis (Pure)
    # =========================================================================

    def run_vintage_analysis(
        self,
        segment: str,
        origination_period: str,
        original_balance: Decimal,
        current_balance: Decimal,
        cumulative_losses: Decimal,
        periods_aged: int = 0,
    ) -> VintageAnalysis:
        """Run vintage analysis for a cohort (pure, no posting)."""
        loss_rate = calculate_vintage_loss_curve(original_balance, cumulative_losses)

        return VintageAnalysis(
            segment=segment,
            origination_period=origination_period,
            original_balance=original_balance,
            current_balance=current_balance,
            cumulative_losses=cumulative_losses,
            loss_rate=loss_rate,
            periods_aged=periods_aged,
        )

    # =========================================================================
    # Forward-Looking Adjustment (Pure)
    # =========================================================================

    def apply_forward_looking(
        self,
        base_rate: Decimal,
        adjustment_pct: Decimal,
        factor_name: str = "",
        rationale: str = "",
    ) -> ForwardLookingAdjustment:
        """Apply forward-looking adjustment to base loss rate (pure, no posting)."""
        adjusted_rate = apply_forward_looking_adjustment(base_rate, adjustment_pct)

        return ForwardLookingAdjustment(
            factor_name=factor_name,
            base_rate=base_rate,
            adjustment_pct=adjustment_pct,
            adjusted_rate=adjusted_rate,
            rationale=rationale,
        )

    # =========================================================================
    # Disclosure (Pure Query)
    # =========================================================================

    def get_disclosure_data(
        self,
        as_of_date: date,
        segments: list[dict],
    ) -> dict:
        """Get ASC 326 disclosure data (pure query)."""
        total_gross = Decimal("0")
        total_allowance = Decimal("0")

        for seg in segments:
            total_gross += Decimal(str(seg.get("gross_balance", "0")))
            total_allowance += Decimal(str(seg.get("allowance", "0")))

        return {
            "as_of_date": str(as_of_date),
            "segment_count": len(segments),
            "total_gross_receivable": str(total_gross),
            "total_allowance": str(total_allowance),
            "net_receivable": str(total_gross - total_allowance),
            "segments": segments,
        }
