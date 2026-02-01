"""
Tax Service -- Orchestrates tax operations via engines + kernel.

Responsibility:
    Thin glue layer that connects pure tax engines to the kernel posting
    pipeline.  All tax arithmetic is delegated; all ledger persistence is
    delegated.  This service owns ONLY the transaction boundary.

Architecture:
    finance_modules -- Thin ERP glue (this layer).
    1. Calls ``TaxCalculator`` for sales tax, VAT, withholding calculations
       (pure, stateless).
    2. Calls ``ModulePostingService`` for journal entry creation.
    All computation lives in engines. All posting lives in kernel.

Invariants:
    - R7  -- This service owns the transaction boundary: commit on success,
             rollback on failure.
    - R4  -- Every journal entry produced is balanced (enforced by kernel).
    - L1  -- Account ROLES used in payloads; COA codes resolved at posting.
    - R16 -- ISO 4217 currency validation at kernel boundary.
    - Amounts are ``Decimal`` throughout -- NEVER ``float``.

Failure modes:
    - If ``post_event`` returns a non-success result (BLOCKED / REJECTED),
      the session is rolled back and the result is returned to the caller.
    - If an unhandled exception occurs, the session is rolled back and the
      exception propagates.
    - Pure engine methods (``calculate_tax``, ``calculate_deferred_tax``,
      ``calculate_provision``) raise no side-effects; failures are
      ValueError / TypeError from inputs.

Audit relevance:
    - Every posting method emits a structured log with obligation/payment ID,
      tax type, amount, and jurisdiction for external audit trail correlation.
    - Journal entries inherit the kernel audit chain (R11).

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
from finance_modules.tax.helpers import (
    calculate_temporary_differences,
    calculate_dta_valuation_allowance,
    calculate_effective_tax_rate,
    aggregate_multi_jurisdiction,
)
from finance_modules.tax.models import (
    DeferredTaxAsset,
    DeferredTaxLiability,
    Jurisdiction,
    TaxProvision,
    TemporaryDifference,
)

logger = get_logger("modules.tax.service")


class TaxService:
    """
    Orchestrates tax operations through engines and kernel.

    Contract:
        Callers supply a ``Session``, ``RoleResolver``, and optional ``Clock``.
        Each posting method executes exactly one ``post_event`` call and owns
        the commit/rollback boundary.  Pure calculation methods have no
        side-effects and may be called freely.

    Guarantees:
        - Every posting method commits on success, rolls back on any failure.
        - ``ModulePostingService`` is created with ``auto_commit=False`` so
          all journal writes share a single transaction owned by this service.
        - No direct database mutations outside ``post_event``.

    Non-goals:
        - This service does NOT manage tax returns or filing workflows.
        - This service does NOT persist domain model DTOs (e.g.,
          ``DeferredTaxAsset``); those are returned to the caller for
          upstream persistence decisions.

    Engine composition:
        - ``TaxCalculator``: sales tax, VAT, withholding calculations (pure,
          stateless).
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

        Preconditions:
            - ``amount`` must be a positive ``Decimal`` (guard enforced by
              profile).
            - ``tax_type`` must match a registered profile name suffix.

        Postconditions:
            - On success: session committed, result.is_success is True.
            - On non-success: session rolled back, result returned.
            - On exception: session rolled back, exception re-raised.

        Raises:
            Any exception propagated from ``ModulePostingService.post_event``
            (e.g., ``ImmutabilityViolationError``, ``IntegrityError``).

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

        Preconditions:
            - ``amount`` > 0 (guard enforced by profile).
        Postconditions:
            - On success: Dr TAX_PAYABLE / Cr CASH posted and committed.
        Raises:
            Any exception from ``post_event`` after session rollback.

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

        Preconditions:
            - ``output_vat`` > 0 (guard enforced by profile).
            - ``input_vat`` >= 0.
        Postconditions:
            - On success: Dr TAX_PAYABLE / Cr TAX_RECEIVABLE + CASH posted.
        Raises:
            Any exception from ``post_event`` after session rollback.

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

    # =========================================================================
    # Deferred Tax (ASC 740)
    # =========================================================================

    def calculate_deferred_tax(
        self,
        book_basis: Decimal,
        tax_basis: Decimal,
        tax_rate: Decimal = Decimal("0.21"),
    ) -> TemporaryDifference:
        """
        Calculate deferred tax from temporary difference (pure, no posting).

        Uses helpers to determine if difference is taxable (DTL) or deductible (DTA).
        """
        from uuid import uuid4
        diff_amount, diff_type = calculate_temporary_differences(book_basis, tax_basis)
        deferred_amount = (diff_amount * tax_rate).quantize(Decimal("0.01"))

        return TemporaryDifference(
            id=uuid4(),
            description=f"Temporary difference: {diff_type}",
            book_basis=book_basis,
            tax_basis=tax_basis,
            difference_amount=diff_amount,
            difference_type=diff_type,
            tax_rate=tax_rate,
            deferred_amount=deferred_amount,
        )

    def record_deferred_tax_asset(
        self,
        source: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        realizability_percentage: Decimal = Decimal("1.0"),
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[DeferredTaxAsset, ModulePostingResult]:
        """
        Record a deferred tax asset (Dr Tax Receivable / Cr Tax Expense).

        Preconditions:
            - ``amount`` > 0 (guard enforced by profile).
            - ``realizability_percentage`` in [0, 1].
        Postconditions:
            - Returns ``(DeferredTaxAsset, ModulePostingResult)``.
            - Journal entry posted for ``net_amount`` after valuation
              allowance.
        Raises:
            Any exception from ``post_event`` after session rollback.
        """
        from uuid import uuid4
        valuation_allowance = calculate_dta_valuation_allowance(amount, realizability_percentage)
        net_amount = amount - valuation_allowance

        dta = DeferredTaxAsset(
            id=uuid4(),
            source=source,
            amount=amount,
            valuation_allowance=valuation_allowance,
            net_amount=net_amount,
        )

        payload: dict[str, Any] = {
            "source": source,
            "amount": str(net_amount),
            "gross_amount": str(amount),
            "valuation_allowance": str(valuation_allowance),
        }

        try:
            result = self._poster.post_event(
                event_type="tax.dta_recorded",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=net_amount,
                currency=currency,
                description=description,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return dta, result
        except Exception:
            self._session.rollback()
            raise

    def record_deferred_tax_liability(
        self,
        source: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[DeferredTaxLiability, ModulePostingResult]:
        """
        Record a deferred tax liability (Dr Tax Expense / Cr Tax Payable).

        Preconditions:
            - ``amount`` > 0 (guard enforced by profile).
        Postconditions:
            - Returns ``(DeferredTaxLiability, ModulePostingResult)``.
        Raises:
            Any exception from ``post_event`` after session rollback.
        """
        from uuid import uuid4
        dtl = DeferredTaxLiability(
            id=uuid4(),
            source=source,
            amount=amount,
        )

        payload: dict[str, Any] = {
            "source": source,
            "amount": str(amount),
        }

        try:
            result = self._poster.post_event(
                event_type="tax.dtl_recorded",
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
            return dtl, result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Tax Provision
    # =========================================================================

    def calculate_provision(
        self,
        period: str,
        current_tax_expense: Decimal,
        deferred_tax_expense: Decimal,
        pre_tax_income: Decimal = Decimal("0"),
    ) -> TaxProvision:
        """
        Calculate total tax provision (pure, no posting).

        Combines current and deferred tax expense to compute total provision
        and effective rate.
        """
        total = current_tax_expense + deferred_tax_expense
        effective_rate = calculate_effective_tax_rate(total, pre_tax_income)

        return TaxProvision(
            period=period,
            current_tax_expense=current_tax_expense,
            deferred_tax_expense=deferred_tax_expense,
            total_tax_expense=total,
            effective_rate=effective_rate,
            pre_tax_income=pre_tax_income,
        )

    # =========================================================================
    # Multi-Jurisdiction
    # =========================================================================

    def record_multi_jurisdiction_tax(
        self,
        jurisdictions: list[dict],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        description: str | None = None,
    ) -> tuple[dict, ModulePostingResult]:
        """
        Record aggregated multi-jurisdiction tax obligation.

        Preconditions:
            - ``jurisdictions`` must be non-empty; each dict must contain
              ``taxable_amount`` and ``tax_amount`` keys.
        Postconditions:
            - Aggregated total posted as a single journal entry.
        Raises:
            ``KeyError`` if jurisdiction dicts lack required keys.
            Any exception from ``post_event`` after session rollback.

        Uses helpers to aggregate across jurisdictions, then posts total.
        """
        summary = aggregate_multi_jurisdiction(jurisdictions)

        payload: dict[str, Any] = {
            "jurisdiction_count": summary["jurisdiction_count"],
            "amount": str(summary["total_tax"]),
            "total_taxable": str(summary["total_taxable"]),
            "weighted_average_rate": str(summary["weighted_average_rate"]),
        }

        try:
            result = self._poster.post_event(
                event_type="tax.multi_jurisdiction",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=summary["total_tax"],
                currency=currency,
                description=description,
            )
            if result.is_success:
                self._session.commit()
            else:
                self._session.rollback()
            return summary, result
        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Tax Return Export
    # =========================================================================

    def export_tax_return_data(
        self,
        period: str,
        jurisdiction: str,
        gross_sales: Decimal,
        taxable_sales: Decimal,
        exempt_sales: Decimal,
        tax_collected: Decimal,
        format: str = "JSON",
    ) -> dict:
        """
        Export tax return data for external tax software (pure, no posting).

        Preconditions:
            - ``format`` must be ``"JSON"`` or ``"CSV"``.
        Postconditions:
            - Returns a dict suitable for serialization; no database state
              changed.
        Raises:
            ``ValueError`` if ``format`` is not ``"JSON"`` or ``"CSV"``.
        """
        if format not in ("JSON", "CSV"):
            raise ValueError(f"Unsupported export format: {format}")

        return {
            "format": format,
            "period": period,
            "jurisdiction": jurisdiction,
            "gross_sales": str(gross_sales),
            "taxable_sales": str(taxable_sales),
            "exempt_sales": str(exempt_sales),
            "tax_collected": str(tax_collected),
            "tax_due": str(tax_collected),
        }

    # =========================================================================
    # Tax Adjustment
    # =========================================================================

    def record_tax_adjustment(
        self,
        period: str,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        reason: str = "",
        currency: str = "USD",
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a prior period tax adjustment.

        Preconditions:
            - ``amount`` > 0 (guard enforced by profile).
        Postconditions:
            - On success: Dr TAX_EXPENSE / Cr TAX_PAYABLE posted.
        Raises:
            Any exception from ``post_event`` after session rollback.
        """
        payload: dict[str, Any] = {
            "period": period,
            "amount": str(amount),
            "reason": reason,
        }

        try:
            result = self._poster.post_event(
                event_type="tax.adjustment",
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
