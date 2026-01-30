"""
Tax Engine - Calculate taxes for transactions.

Supports sales tax, VAT, GST, withholding, and compound taxes.
Pure functions with no I/O - tax rates provided as parameters.

Usage:
    from finance_engines.tax import TaxCalculator, TaxRate, TaxType
    from finance_kernel.domain.values import Money
    from decimal import Decimal
    from datetime import date

    rates = {
        "STATE_SALES": TaxRate(
            tax_code="STATE_SALES",
            tax_name="State Sales Tax",
            rate=Decimal("0.06"),
            tax_type=TaxType.SALES,
        ),
    }

    calculator = TaxCalculator()
    result = calculator.calculate(
        amount=Money.of("100.00", "USD"),
        tax_codes=["STATE_SALES"],
        rates=rates,
    )
    print(result.tax_total)  # Money: 6.00 USD
    print(result.gross_amount)  # Money: 106.00 USD
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.tax")


class TaxType(str, Enum):
    """Type of tax."""

    SALES = "sales"  # US sales tax
    VAT = "vat"  # Value Added Tax
    GST = "gst"  # Goods and Services Tax
    HST = "hst"  # Harmonized Sales Tax (Canada)
    WITHHOLDING = "withholding"  # Withholding tax
    EXCISE = "excise"  # Excise tax
    CUSTOMS = "customs"  # Customs/import duty
    USE = "use"  # Use tax


class TaxCalculationMethod(str, Enum):
    """How to apply tax."""

    EXCLUSIVE = "exclusive"  # Tax added on top of net amount
    INCLUSIVE = "inclusive"  # Tax included in gross amount


@dataclass(frozen=True)
class TaxRate:
    """
    Tax rate definition.

    Immutable value object defining a tax rate.
    """

    tax_code: str
    tax_name: str
    rate: Decimal  # As decimal (e.g., 0.06 for 6%)
    tax_type: TaxType = TaxType.SALES

    # Compound taxes are calculated on the tax-inclusive amount
    is_compound: bool = False

    # Priority for compound calculation order (lower = calculated first)
    priority: int = 0

    # Effective dates (None = always effective)
    effective_from: date | None = None
    effective_to: date | None = None

    # Optional: Account code for GL posting
    account_code: str | None = None

    # Optional: Jurisdiction for reporting
    jurisdiction: str | None = None

    def __post_init__(self) -> None:
        if self.rate < Decimal("0"):
            raise ValueError("Tax rate cannot be negative")
        if self.rate > Decimal("1"):
            # Rates > 100% are unusual but valid in some cases
            pass

    def is_effective(self, on_date: date | None = None) -> bool:
        """Check if rate is effective on given date."""
        if on_date is None:
            on_date = date.today()

        if self.effective_from and on_date < self.effective_from:
            return False
        if self.effective_to and on_date > self.effective_to:
            return False
        return True

    @property
    def rate_percent(self) -> Decimal:
        """Rate as percentage (e.g., 6 for 6%)."""
        return self.rate * Decimal("100")


@dataclass(frozen=True)
class TaxLine:
    """
    Calculated tax for a single tax code.

    Immutable value object representing one tax in a calculation.
    """

    tax_code: str
    tax_name: str
    tax_type: TaxType
    taxable_amount: Money  # Base amount tax was calculated on
    tax_amount: Money  # Calculated tax
    rate_applied: Decimal  # Rate as decimal (e.g., 0.06)
    is_compound: bool = False
    is_included: bool = False  # True if tax was included in original amount
    account_code: str | None = None
    jurisdiction: str | None = None

    @property
    def rate_percent(self) -> Decimal:
        """Rate as percentage."""
        return self.rate_applied * Decimal("100")

    @property
    def effective_rate(self) -> Decimal:
        """Effective rate (tax / taxable)."""
        if self.taxable_amount.is_zero:
            return Decimal("0")
        return self.tax_amount.amount / self.taxable_amount.amount


@dataclass(frozen=True)
class TaxCalculationResult:
    """
    Complete tax calculation result.

    Immutable value object with all calculation details.
    """

    net_amount: Money  # Amount before tax
    tax_lines: tuple[TaxLine, ...]  # Individual tax calculations
    gross_amount: Money  # Amount after tax

    # Calculation context
    calculation_method: TaxCalculationMethod = TaxCalculationMethod.EXCLUSIVE
    calculation_date: date | None = None

    @property
    def tax_total(self) -> Money:
        """Total tax amount across all tax lines."""
        if not self.tax_lines:
            return Money.zero(self.net_amount.currency)

        total = self.tax_lines[0].tax_amount
        for line in self.tax_lines[1:]:
            total = total + line.tax_amount
        return total

    @property
    def tax_count(self) -> int:
        """Number of taxes applied."""
        return len(self.tax_lines)

    @property
    def effective_tax_rate(self) -> Decimal:
        """Overall effective tax rate (total tax / net)."""
        if self.net_amount.is_zero:
            return Decimal("0")
        return self.tax_total.amount / self.net_amount.amount

    def tax_by_type(self, tax_type: TaxType) -> Money:
        """Sum of taxes of a specific type."""
        matching = [l for l in self.tax_lines if l.tax_type == tax_type]
        if not matching:
            return Money.zero(self.net_amount.currency)

        total = matching[0].tax_amount
        for line in matching[1:]:
            total = total + line.tax_amount
        return total

    def tax_by_jurisdiction(self, jurisdiction: str) -> Money:
        """Sum of taxes for a specific jurisdiction."""
        matching = [l for l in self.tax_lines if l.jurisdiction == jurisdiction]
        if not matching:
            return Money.zero(self.net_amount.currency)

        total = matching[0].tax_amount
        for line in matching[1:]:
            total = total + line.tax_amount
        return total


class TaxCalculator:
    """
    Calculate taxes for transactions.

    Pure functions - no I/O, no database access.
    Tax rates provided as parameters.

    Handles:
        - Simple tax calculation (exclusive)
        - Tax-inclusive (reverse) calculation
        - Compound taxes
        - Multiple tax jurisdictions
    """

    def calculate(
        self,
        amount: Money,
        tax_codes: Sequence[str],
        rates: dict[str, TaxRate],
        is_tax_inclusive: bool = False,
        calculation_date: date | None = None,
    ) -> TaxCalculationResult:
        """
        Calculate tax for an amount.

        Args:
            amount: Base amount (net if exclusive, gross if inclusive)
            tax_codes: Tax codes to apply
            rates: Available tax rates (code -> TaxRate)
            is_tax_inclusive: True if amount includes tax
            calculation_date: Date for rate effectiveness check

        Returns:
            TaxCalculationResult with all tax details

        Raises:
            ValueError: If tax code not found or rate not effective
        """
        t0 = time.monotonic()
        logger.info("tax_calculation_started", extra={
            "amount": str(amount.amount),
            "currency": amount.currency.code,
            "tax_code_count": len(tax_codes),
            "tax_codes": list(tax_codes),
            "is_tax_inclusive": is_tax_inclusive,
        })

        if not tax_codes:
            logger.debug("tax_calculation_no_codes", extra={})
            # No taxes to apply
            return TaxCalculationResult(
                net_amount=amount,
                tax_lines=(),
                gross_amount=amount,
                calculation_method=TaxCalculationMethod.EXCLUSIVE,
                calculation_date=calculation_date,
            )

        # Validate and collect rates
        applicable_rates: list[TaxRate] = []
        for code in tax_codes:
            if code not in rates:
                logger.error("tax_code_not_found", extra={
                    "tax_code": code,
                    "available_codes": list(rates.keys()),
                })
                raise ValueError(f"Tax code not found: {code}")
            rate = rates[code]
            if not rate.is_effective(calculation_date):
                logger.error("tax_rate_not_effective", extra={
                    "tax_code": code,
                    "calculation_date": calculation_date.isoformat() if calculation_date else None,
                    "effective_from": rate.effective_from.isoformat() if rate.effective_from else None,
                    "effective_to": rate.effective_to.isoformat() if rate.effective_to else None,
                })
                raise ValueError(
                    f"Tax rate {code} not effective on {calculation_date}"
                )
            applicable_rates.append(rate)

        if is_tax_inclusive:
            result = self._calculate_inclusive(
                amount, applicable_rates, calculation_date
            )
        else:
            result = self._calculate_exclusive(
                amount, applicable_rates, calculation_date
            )

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("tax_calculation_completed", extra={
            "net_amount": str(result.net_amount.amount),
            "tax_total": str(result.tax_total.amount),
            "gross_amount": str(result.gross_amount.amount),
            "tax_line_count": len(result.tax_lines),
            "effective_rate": str(result.effective_tax_rate),
            "is_tax_inclusive": is_tax_inclusive,
            "duration_ms": duration_ms,
        })

        return result

    def reverse_calculate(
        self,
        gross_amount: Money,
        tax_codes: Sequence[str],
        rates: dict[str, TaxRate],
        calculation_date: date | None = None,
    ) -> TaxCalculationResult:
        """
        Reverse-calculate tax from gross (tax-inclusive) amount.

        Convenience wrapper for calculate with is_tax_inclusive=True.

        Args:
            gross_amount: Amount including tax
            tax_codes: Tax codes that are included
            rates: Available tax rates

        Returns:
            TaxCalculationResult with extracted taxes
        """
        return self.calculate(
            amount=gross_amount,
            tax_codes=tax_codes,
            rates=rates,
            is_tax_inclusive=True,
            calculation_date=calculation_date,
        )

    def calculate_withholding(
        self,
        gross_amount: Money,
        withholding_rate: TaxRate,
        calculation_date: date | None = None,
    ) -> TaxCalculationResult:
        """
        Calculate withholding tax.

        Withholding is subtracted from gross, not added.

        Args:
            gross_amount: Payment amount before withholding
            withholding_rate: Withholding tax rate

        Returns:
            TaxCalculationResult where gross < net (withholding deducted)
        """
        logger.info("withholding_calculation_started", extra={
            "gross_amount": str(gross_amount.amount),
            "withholding_rate": str(withholding_rate.rate),
            "tax_code": withholding_rate.tax_code,
        })

        if withholding_rate.tax_type != TaxType.WITHHOLDING:
            logger.error("withholding_invalid_tax_type", extra={
                "tax_type": withholding_rate.tax_type.value,
                "tax_code": withholding_rate.tax_code,
            })
            raise ValueError("Rate must be a withholding tax type")

        currency = gross_amount.currency
        decimal_places = Decimal(10) ** -currency.decimal_places

        withholding_amount = (gross_amount.amount * withholding_rate.rate).quantize(
            decimal_places, rounding=ROUND_HALF_UP
        )

        tax_line = TaxLine(
            tax_code=withholding_rate.tax_code,
            tax_name=withholding_rate.tax_name,
            tax_type=TaxType.WITHHOLDING,
            taxable_amount=gross_amount,
            tax_amount=Money.of(withholding_amount, currency),
            rate_applied=withholding_rate.rate,
            account_code=withholding_rate.account_code,
            jurisdiction=withholding_rate.jurisdiction,
        )

        # For withholding, net = gross - withholding
        net_amount = gross_amount - tax_line.tax_amount

        logger.info("withholding_calculation_completed", extra={
            "gross_amount": str(gross_amount.amount),
            "withholding_amount": str(withholding_amount),
            "net_amount": str(net_amount.amount),
        })

        return TaxCalculationResult(
            net_amount=net_amount,
            tax_lines=(tax_line,),
            gross_amount=gross_amount,
            calculation_method=TaxCalculationMethod.EXCLUSIVE,
            calculation_date=calculation_date,
        )

    def _calculate_exclusive(
        self,
        net_amount: Money,
        rates: list[TaxRate],
        calculation_date: date | None,
    ) -> TaxCalculationResult:
        """Calculate taxes to add on top of net amount."""
        logger.debug("tax_exclusive_calculation_started", extra={
            "net_amount": str(net_amount.amount),
            "rate_count": len(rates),
            "compound_count": sum(1 for r in rates if r.is_compound),
        })

        currency = net_amount.currency
        decimal_places = Decimal(10) ** -currency.decimal_places

        # Sort by priority for compound calculation
        sorted_rates = sorted(rates, key=lambda r: r.priority)

        # Separate simple and compound taxes
        simple_rates = [r for r in sorted_rates if not r.is_compound]
        compound_rates = [r for r in sorted_rates if r.is_compound]

        tax_lines: list[TaxLine] = []
        running_total = net_amount.amount

        # Calculate simple taxes (on net amount)
        for rate in simple_rates:
            tax_amount = (net_amount.amount * rate.rate).quantize(
                decimal_places, rounding=ROUND_HALF_UP
            )
            running_total += tax_amount

            tax_lines.append(
                TaxLine(
                    tax_code=rate.tax_code,
                    tax_name=rate.tax_name,
                    tax_type=rate.tax_type,
                    taxable_amount=net_amount,
                    tax_amount=Money.of(tax_amount, currency),
                    rate_applied=rate.rate,
                    is_compound=False,
                    account_code=rate.account_code,
                    jurisdiction=rate.jurisdiction,
                )
            )

        # Calculate compound taxes (on running total including previous taxes)
        for rate in compound_rates:
            base_amount = Money.of(running_total, currency)
            tax_amount = (running_total * rate.rate).quantize(
                decimal_places, rounding=ROUND_HALF_UP
            )
            running_total += tax_amount

            tax_lines.append(
                TaxLine(
                    tax_code=rate.tax_code,
                    tax_name=rate.tax_name,
                    tax_type=rate.tax_type,
                    taxable_amount=base_amount,
                    tax_amount=Money.of(tax_amount, currency),
                    rate_applied=rate.rate,
                    is_compound=True,
                    account_code=rate.account_code,
                    jurisdiction=rate.jurisdiction,
                )
            )

        gross_amount = Money.of(running_total, currency)

        return TaxCalculationResult(
            net_amount=net_amount,
            tax_lines=tuple(tax_lines),
            gross_amount=gross_amount,
            calculation_method=TaxCalculationMethod.EXCLUSIVE,
            calculation_date=calculation_date,
        )

    def _calculate_inclusive(
        self,
        gross_amount: Money,
        rates: list[TaxRate],
        calculation_date: date | None,
    ) -> TaxCalculationResult:
        """Extract taxes from tax-inclusive amount."""
        logger.debug("tax_inclusive_calculation_started", extra={
            "gross_amount": str(gross_amount.amount),
            "rate_count": len(rates),
        })

        currency = gross_amount.currency
        decimal_places = Decimal(10) ** -currency.decimal_places

        # Sort by priority (reverse for extraction)
        sorted_rates = sorted(rates, key=lambda r: r.priority, reverse=True)

        # Separate simple and compound taxes
        simple_rates = [r for r in sorted_rates if not r.is_compound]
        compound_rates = [r for r in sorted_rates if r.is_compound]

        tax_lines: list[TaxLine] = []
        running_gross = gross_amount.amount

        # Extract compound taxes first (they were added last)
        for rate in compound_rates:
            # For inclusive: tax = gross * rate / (1 + rate)
            tax_amount = (running_gross * rate.rate / (1 + rate.rate)).quantize(
                decimal_places, rounding=ROUND_HALF_UP
            )
            base_before_this_tax = running_gross - tax_amount
            running_gross = base_before_this_tax

            tax_lines.append(
                TaxLine(
                    tax_code=rate.tax_code,
                    tax_name=rate.tax_name,
                    tax_type=rate.tax_type,
                    taxable_amount=Money.of(base_before_this_tax, currency),
                    tax_amount=Money.of(tax_amount, currency),
                    rate_applied=rate.rate,
                    is_compound=True,
                    is_included=True,
                    account_code=rate.account_code,
                    jurisdiction=rate.jurisdiction,
                )
            )

        # Calculate combined rate for simple taxes
        combined_simple_rate = sum(r.rate for r in simple_rates)

        if simple_rates:
            # Net = running_gross / (1 + combined_rate)
            net_amount_value = (
                running_gross / (1 + combined_simple_rate)
            ).quantize(decimal_places, rounding=ROUND_HALF_UP)
            net_amount = Money.of(net_amount_value, currency)

            # Allocate tax to each simple rate proportionally
            total_simple_tax = running_gross - net_amount_value
            allocated_so_far = Decimal("0")

            for i, rate in enumerate(simple_rates):
                is_last = i == len(simple_rates) - 1

                if is_last:
                    # Last rate gets remainder (rounding)
                    tax_amount = total_simple_tax - allocated_so_far
                else:
                    # Proportional allocation
                    proportion = rate.rate / combined_simple_rate
                    tax_amount = (total_simple_tax * proportion).quantize(
                        decimal_places, rounding=ROUND_HALF_UP
                    )
                    allocated_so_far += tax_amount

                tax_lines.append(
                    TaxLine(
                        tax_code=rate.tax_code,
                        tax_name=rate.tax_name,
                        tax_type=rate.tax_type,
                        taxable_amount=net_amount,
                        tax_amount=Money.of(tax_amount, currency),
                        rate_applied=rate.rate,
                        is_compound=False,
                        is_included=True,
                        account_code=rate.account_code,
                        jurisdiction=rate.jurisdiction,
                    )
                )
        else:
            net_amount = Money.of(running_gross, currency)

        # Reverse the list so taxes appear in application order
        tax_lines.reverse()

        return TaxCalculationResult(
            net_amount=net_amount,
            tax_lines=tuple(tax_lines),
            gross_amount=gross_amount,
            calculation_method=TaxCalculationMethod.INCLUSIVE,
            calculation_date=calculation_date,
        )


# Convenience functions for common scenarios

def calculate_sales_tax(
    amount: Money,
    rate_percent: Decimal,
    tax_code: str = "SALES",
    tax_name: str = "Sales Tax",
) -> TaxCalculationResult:
    """
    Simple sales tax calculation.

    Args:
        amount: Net amount
        rate_percent: Tax rate as percentage (e.g., 6 for 6%)
        tax_code: Code for the tax
        tax_name: Display name

    Returns:
        TaxCalculationResult
    """
    rate = TaxRate(
        tax_code=tax_code,
        tax_name=tax_name,
        rate=rate_percent / Decimal("100"),
        tax_type=TaxType.SALES,
    )

    calculator = TaxCalculator()
    return calculator.calculate(
        amount=amount,
        tax_codes=[tax_code],
        rates={tax_code: rate},
    )


def calculate_vat(
    amount: Money,
    rate_percent: Decimal,
    is_inclusive: bool = False,
    tax_code: str = "VAT",
    tax_name: str = "VAT",
) -> TaxCalculationResult:
    """
    VAT calculation.

    Args:
        amount: Amount (net if exclusive, gross if inclusive)
        rate_percent: VAT rate as percentage
        is_inclusive: True if amount includes VAT
        tax_code: Code for the tax
        tax_name: Display name

    Returns:
        TaxCalculationResult
    """
    rate = TaxRate(
        tax_code=tax_code,
        tax_name=tax_name,
        rate=rate_percent / Decimal("100"),
        tax_type=TaxType.VAT,
    )

    calculator = TaxCalculator()
    return calculator.calculate(
        amount=amount,
        tax_codes=[tax_code],
        rates={tax_code: rate},
        is_tax_inclusive=is_inclusive,
    )
