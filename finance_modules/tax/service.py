"""
Tax Service - Orchestrates tax operations via engines + kernel.

Thin glue layer that:
1. Calls TaxCalculator for sales tax, VAT, withholding calculations (pure)
2. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
    service = TaxService(session, role_resolver, clock)
    result = service.record_tax_obligation(
        obligation_id=uuid4(),
        tax_type="sales_tax_collected",
        amount=Decimal("600.00"),
        effective_date=date.today(),
        actor_id=actor_id,
        jurisdiction="CA",
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
from finance_engines.tax import (
    TaxCalculationResult,
    TaxCalculator,
    TaxRate,
)

logger = get_logger("modules.tax.service")


class TaxService:
    """
    Orchestrates tax operations through engines and kernel.

    Engine composition:
    - TaxCalculator: sales tax, VAT, withholding calculations (pure, stateless)

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

        # Stateless engine
        self._calculator = TaxCalculator()

    # =========================================================================
    # Tax Obligation
    # =========================================================================

    def record_tax_obligation(
        self,
        obligation_id: UUID,
        tax_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        jurisdiction: str | None = None,
        currency: str = "USD",
        invoice_ref: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a tax obligation (collected or accrued).

        Profile dispatch based on tax_type:
        - "sales_tax_collected" -> tax.sales_tax_collected (SalesTaxCollected)
        - "use_tax_accrued"     -> tax.use_tax_accrued (UseTaxAccrued)
        - "vat_input"           -> tax.vat_input (VatInput)
        - "vat_output"          -> tax.vat_output (VatOutput)
        - "vat_settlement"      -> tax.vat_settlement (VatSettlement)
        - "refund_received"     -> tax.refund_received (TaxRefundReceived)
        """
        event_type = f"tax.{tax_type}"

        payload: dict[str, Any] = {
            "obligation_id": str(obligation_id),
            "tax_type": tax_type,
            "amount": str(amount),
        }
        if jurisdiction:
            payload["jurisdiction"] = jurisdiction
        if invoice_ref:
            payload["invoice_ref"] = invoice_ref

        logger.info("tax_obligation_recorded", extra={
            "obligation_id": str(obligation_id),
            "tax_type": tax_type,
            "amount": str(amount),
            "jurisdiction": jurisdiction,
        })

        try:
            result = self._poster.post_event(
                event_type=event_type,
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
    # Tax Payment
    # =========================================================================

    def record_tax_payment(
        self,
        payment_id: UUID,
        tax_type: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        jurisdiction: str | None = None,
        currency: str = "USD",
        payment_ref: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a tax payment remitted to a jurisdiction.

        Profile dispatch: tax.payment -> TaxPayment.
        """
        payload: dict[str, Any] = {
            "payment_id": str(payment_id),
            "tax_type": tax_type,
            "amount": str(amount),
        }
        if jurisdiction:
            payload["jurisdiction"] = jurisdiction
        if payment_ref:
            payload["payment_ref"] = payment_ref

        logger.info("tax_payment_recorded", extra={
            "payment_id": str(payment_id),
            "tax_type": tax_type,
            "amount": str(amount),
            "jurisdiction": jurisdiction,
        })

        try:
            result = self._poster.post_event(
                event_type="tax.payment",
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
    # VAT Settlement
    # =========================================================================

    def record_vat_settlement(
        self,
        settlement_id: UUID,
        output_vat: Decimal,
        input_vat: Decimal,
        effective_date: date,
        actor_id: UUID,
        jurisdiction: str | None = None,
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a VAT settlement (net of output and input VAT).

        Profile: tax.vat_settlement -> VatSettlement
        Debits TAX_PAYABLE (output_vat), credits TAX_RECEIVABLE (input_vat)
        and CASH (net_payment = output_vat - input_vat).
        """
        net_payment = output_vat - input_vat

        payload: dict[str, Any] = {
            "settlement_id": str(settlement_id),
            "amount": str(output_vat),
            "output_vat": str(output_vat),
            "input_vat_amount": str(input_vat),
            "net_payment": str(net_payment),
        }
        if jurisdiction:
            payload["jurisdiction"] = jurisdiction

        logger.info("vat_settlement_recorded", extra={
            "settlement_id": str(settlement_id),
            "output_vat": str(output_vat),
            "input_vat": str(input_vat),
            "net_payment": str(net_payment),
            "jurisdiction": jurisdiction,
        })

        try:
            result = self._poster.post_event(
                event_type="tax.vat_settlement",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=output_vat,
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

    def calculate_tax(
        self,
        amount: Decimal,
        tax_codes: Sequence[str],
        rates: dict[str, TaxRate],
        is_inclusive: bool = False,
        currency: str = "USD",
        calculation_date: date | None = None,
    ) -> TaxCalculationResult:
        """
        Calculate tax amounts (pure, no posting).

        Engine: TaxCalculator.calculate() computes tax lines for given
        amount, codes, and rates. No journal entries are created.

        Args:
            amount: Base amount (net if exclusive, gross if inclusive).
            tax_codes: Tax codes to apply.
            rates: Available tax rate definitions (code -> TaxRate).
            is_inclusive: True if amount already includes tax.
            currency: Currency code for the amount.
            calculation_date: Date for rate effectiveness check.

        Returns:
            TaxCalculationResult with net, gross, and individual tax lines.
        """
        money_amount = Money.of(amount, currency)
        return self._calculator.calculate(
            amount=money_amount,
            tax_codes=tax_codes,
            rates=rates,
            is_tax_inclusive=is_inclusive,
            calculation_date=calculation_date,
        )
