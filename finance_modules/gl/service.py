"""
General Ledger Module Service - Orchestrates GL operations via engines + kernel.

Thin glue layer that:
1. Calls VarianceCalculator for budget vs actual analysis
2. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
    service = GeneralLedgerService(session, role_resolver, clock)
    result = service.record_journal_entry(
        entry_id=uuid4(), description="Monthly accrual",
        lines=[{"account": "5100", "debit": "1000.00"}],
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Sequence
from uuid import UUID

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
from finance_engines.variance import VarianceCalculator, VarianceResult

logger = get_logger("modules.gl.service")


class GeneralLedgerService:
    """
    Orchestrates general-ledger operations through engines and kernel.

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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            result = self._poster.post_event(
                event_type="gl.adjustment",
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
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

            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
