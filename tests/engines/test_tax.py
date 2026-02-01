"""
Tests for Tax Engine.

Covers:
- Simple tax calculation (exclusive)
- Tax-inclusive (reverse) calculation
- Compound taxes
- Multiple tax codes
- Withholding tax
- Edge cases and error handling
"""

from datetime import date
from decimal import Decimal

import pytest

from finance_engines.tax import (
    TaxCalculationMethod,
    TaxCalculationResult,
    TaxCalculator,
    TaxLine,
    TaxRate,
    TaxType,
    calculate_sales_tax,
    calculate_vat,
)
from finance_kernel.domain.values import Money


class TestSimpleTaxCalculation:
    """Tests for simple (exclusive) tax calculation."""

    def setup_method(self):
        self.calculator = TaxCalculator()
        self.rates = {
            "STATE": TaxRate(
                tax_code="STATE",
                tax_name="State Sales Tax",
                rate=Decimal("0.06"),  # 6%
                tax_type=TaxType.SALES,
            ),
            "LOCAL": TaxRate(
                tax_code="LOCAL",
                tax_name="Local Sales Tax",
                rate=Decimal("0.02"),  # 2%
                tax_type=TaxType.SALES,
            ),
        }

    def test_single_tax(self):
        """Calculates single tax correctly."""
        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["STATE"],
            rates=self.rates,
        )

        assert result.net_amount == Money.of("100.00", "USD")
        assert result.tax_total == Money.of("6.00", "USD")
        assert result.gross_amount == Money.of("106.00", "USD")
        assert result.calculation_method == TaxCalculationMethod.EXCLUSIVE

    def test_multiple_taxes(self):
        """Calculates multiple taxes correctly."""
        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["STATE", "LOCAL"],
            rates=self.rates,
        )

        assert result.net_amount == Money.of("100.00", "USD")
        assert result.tax_total == Money.of("8.00", "USD")  # 6% + 2%
        assert result.gross_amount == Money.of("108.00", "USD")
        assert result.tax_count == 2

    def test_no_taxes(self):
        """Handles no tax codes gracefully."""
        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=[],
            rates=self.rates,
        )

        assert result.tax_total.is_zero
        assert result.gross_amount == result.net_amount

    def test_unknown_tax_code_raises(self):
        """Raises error for unknown tax code."""
        with pytest.raises(ValueError, match="not found"):
            self.calculator.calculate(
                amount=Money.of("100.00", "USD"),
                tax_codes=["UNKNOWN"],
                rates=self.rates,
            )

    def test_tax_line_details(self):
        """Tax lines contain correct details."""
        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["STATE"],
            rates=self.rates,
        )

        assert len(result.tax_lines) == 1
        line = result.tax_lines[0]

        assert line.tax_code == "STATE"
        assert line.tax_name == "State Sales Tax"
        assert line.tax_type == TaxType.SALES
        assert line.taxable_amount == Money.of("100.00", "USD")
        assert line.tax_amount == Money.of("6.00", "USD")
        assert line.rate_applied == Decimal("0.06")
        assert line.rate_percent == Decimal("6")


class TestTaxInclusiveCalculation:
    """Tests for tax-inclusive (reverse) calculation."""

    def setup_method(self):
        self.calculator = TaxCalculator()
        self.rates = {
            "VAT": TaxRate(
                tax_code="VAT",
                tax_name="VAT",
                rate=Decimal("0.20"),  # 20%
                tax_type=TaxType.VAT,
            ),
        }

    def test_reverse_calculate_single_tax(self):
        """Extracts tax from gross amount."""
        result = self.calculator.calculate(
            amount=Money.of("120.00", "USD"),  # Gross including 20% VAT
            tax_codes=["VAT"],
            rates=self.rates,
            is_tax_inclusive=True,
        )

        assert result.gross_amount == Money.of("120.00", "USD")
        assert result.net_amount == Money.of("100.00", "USD")
        assert result.tax_total == Money.of("20.00", "USD")
        assert result.calculation_method == TaxCalculationMethod.INCLUSIVE

    def test_reverse_calculate_convenience_method(self):
        """reverse_calculate convenience method works."""
        result = self.calculator.reverse_calculate(
            gross_amount=Money.of("120.00", "USD"),
            tax_codes=["VAT"],
            rates=self.rates,
        )

        assert result.net_amount == Money.of("100.00", "USD")
        assert result.tax_total == Money.of("20.00", "USD")

    def test_tax_line_marked_as_included(self):
        """Tax lines marked as included when tax-inclusive."""
        result = self.calculator.reverse_calculate(
            gross_amount=Money.of("120.00", "USD"),
            tax_codes=["VAT"],
            rates=self.rates,
        )

        assert result.tax_lines[0].is_included


class TestCompoundTaxes:
    """Tests for compound tax calculation."""

    def setup_method(self):
        self.calculator = TaxCalculator()

    def test_compound_tax_on_tax(self):
        """Compound tax calculated on tax-inclusive amount."""
        rates = {
            "BASE": TaxRate(
                tax_code="BASE",
                tax_name="Base Tax",
                rate=Decimal("0.10"),  # 10%
                is_compound=False,
                priority=1,
            ),
            "COMPOUND": TaxRate(
                tax_code="COMPOUND",
                tax_name="Compound Tax",
                rate=Decimal("0.05"),  # 5% on top of base
                is_compound=True,
                priority=2,
            ),
        }

        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["BASE", "COMPOUND"],
            rates=rates,
        )

        # Base: 100 * 0.10 = 10
        # Compound: 110 * 0.05 = 5.50
        # Total: 15.50
        assert result.tax_total == Money.of("15.50", "USD")
        assert result.gross_amount == Money.of("115.50", "USD")

    def test_compound_tax_line_marked(self):
        """Compound tax lines marked as compound."""
        rates = {
            "BASE": TaxRate(
                tax_code="BASE",
                tax_name="Base Tax",
                rate=Decimal("0.10"),
                is_compound=False,
            ),
            "COMPOUND": TaxRate(
                tax_code="COMPOUND",
                tax_name="Compound Tax",
                rate=Decimal("0.05"),
                is_compound=True,
            ),
        }

        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["BASE", "COMPOUND"],
            rates=rates,
        )

        base_line = next(l for l in result.tax_lines if l.tax_code == "BASE")
        compound_line = next(l for l in result.tax_lines if l.tax_code == "COMPOUND")

        assert not base_line.is_compound
        assert compound_line.is_compound


class TestWithholdingTax:
    """Tests for withholding tax calculation."""

    def setup_method(self):
        self.calculator = TaxCalculator()

    def test_withholding_deducted(self):
        """Withholding is deducted from gross."""
        rate = TaxRate(
            tax_code="WHT",
            tax_name="Withholding Tax",
            rate=Decimal("0.10"),  # 10%
            tax_type=TaxType.WITHHOLDING,
        )

        result = self.calculator.calculate_withholding(
            gross_amount=Money.of("1000.00", "USD"),
            withholding_rate=rate,
        )

        # Net = Gross - Withholding
        assert result.gross_amount == Money.of("1000.00", "USD")
        assert result.tax_total == Money.of("100.00", "USD")
        assert result.net_amount == Money.of("900.00", "USD")

    def test_withholding_requires_correct_type(self):
        """Raises error if rate is not withholding type."""
        rate = TaxRate(
            tax_code="SALES",
            tax_name="Sales Tax",
            rate=Decimal("0.10"),
            tax_type=TaxType.SALES,
        )

        with pytest.raises(ValueError, match="withholding"):
            self.calculator.calculate_withholding(
                gross_amount=Money.of("1000.00", "USD"),
                withholding_rate=rate,
            )


class TestTaxRateEffectiveness:
    """Tests for tax rate effective date handling."""

    def setup_method(self):
        self.calculator = TaxCalculator()

    def test_rate_effective(self):
        """Rate effective within date range."""
        rate = TaxRate(
            tax_code="TEST",
            tax_name="Test Tax",
            rate=Decimal("0.10"),
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 12, 31),
        )

        assert rate.is_effective(date(2024, 6, 15))
        assert not rate.is_effective(date(2023, 12, 31))
        assert not rate.is_effective(date(2025, 1, 1))

    def test_rate_no_dates_always_effective(self):
        """Rate with no dates is always effective."""
        rate = TaxRate(
            tax_code="TEST",
            tax_name="Test Tax",
            rate=Decimal("0.10"),
        )

        assert rate.is_effective(date(2020, 1, 1))
        assert rate.is_effective(date(2030, 1, 1))

    def test_calculate_checks_effectiveness(self):
        """Calculate raises error for ineffective rate."""
        rates = {
            "OLD": TaxRate(
                tax_code="OLD",
                tax_name="Old Tax",
                rate=Decimal("0.05"),
                effective_from=date(2020, 1, 1),
                effective_to=date(2022, 12, 31),
            ),
        }

        with pytest.raises(ValueError, match="not effective"):
            self.calculator.calculate(
                amount=Money.of("100.00", "USD"),
                tax_codes=["OLD"],
                rates=rates,
                calculation_date=date(2024, 1, 1),
            )


class TestTaxResultProperties:
    """Tests for TaxCalculationResult properties."""

    def setup_method(self):
        self.calculator = TaxCalculator()

    def test_effective_tax_rate(self):
        """Calculates effective tax rate."""
        rates = {
            "TAX": TaxRate(
                tax_code="TAX",
                tax_name="Tax",
                rate=Decimal("0.08"),
            ),
        }

        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["TAX"],
            rates=rates,
        )

        assert result.effective_tax_rate == Decimal("0.08")

    def test_tax_by_type(self):
        """Sums taxes by type."""
        rates = {
            "SALES1": TaxRate(
                tax_code="SALES1",
                tax_name="Sales 1",
                rate=Decimal("0.05"),
                tax_type=TaxType.SALES,
            ),
            "SALES2": TaxRate(
                tax_code="SALES2",
                tax_name="Sales 2",
                rate=Decimal("0.03"),
                tax_type=TaxType.SALES,
            ),
            "VAT": TaxRate(
                tax_code="VAT",
                tax_name="VAT",
                rate=Decimal("0.10"),
                tax_type=TaxType.VAT,
            ),
        }

        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["SALES1", "SALES2", "VAT"],
            rates=rates,
        )

        assert result.tax_by_type(TaxType.SALES) == Money.of("8.00", "USD")
        assert result.tax_by_type(TaxType.VAT) == Money.of("10.00", "USD")

    def test_tax_by_jurisdiction(self):
        """Sums taxes by jurisdiction."""
        rates = {
            "STATE": TaxRate(
                tax_code="STATE",
                tax_name="State",
                rate=Decimal("0.05"),
                jurisdiction="CA",
            ),
            "LOCAL": TaxRate(
                tax_code="LOCAL",
                tax_name="Local",
                rate=Decimal("0.02"),
                jurisdiction="SF",
            ),
        }

        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["STATE", "LOCAL"],
            rates=rates,
        )

        assert result.tax_by_jurisdiction("CA") == Money.of("5.00", "USD")
        assert result.tax_by_jurisdiction("SF") == Money.of("2.00", "USD")


class TestConvenienceFunctions:
    """Tests for convenience calculation functions."""

    def test_calculate_sales_tax(self):
        """calculate_sales_tax convenience function."""
        result = calculate_sales_tax(
            amount=Money.of("100.00", "USD"),
            rate_percent=Decimal("8.25"),
        )

        assert result.tax_total == Money.of("8.25", "USD")
        assert result.gross_amount == Money.of("108.25", "USD")

    def test_calculate_vat_exclusive(self):
        """calculate_vat with exclusive pricing."""
        result = calculate_vat(
            amount=Money.of("100.00", "USD"),
            rate_percent=Decimal("20"),
            is_inclusive=False,
        )

        assert result.tax_total == Money.of("20.00", "USD")
        assert result.gross_amount == Money.of("120.00", "USD")

    def test_calculate_vat_inclusive(self):
        """calculate_vat with inclusive pricing."""
        result = calculate_vat(
            amount=Money.of("120.00", "USD"),
            rate_percent=Decimal("20"),
            is_inclusive=True,
        )

        assert result.net_amount == Money.of("100.00", "USD")
        assert result.tax_total == Money.of("20.00", "USD")


class TestRoundingBehavior:
    """Tests for rounding in tax calculations."""

    def setup_method(self):
        self.calculator = TaxCalculator()

    def test_rounding_half_up(self):
        """Uses ROUND_HALF_UP for tax amounts."""
        rates = {
            "TAX": TaxRate(
                tax_code="TAX",
                tax_name="Tax",
                rate=Decimal("0.0725"),  # 7.25%
            ),
        }

        result = self.calculator.calculate(
            amount=Money.of("99.99", "USD"),
            tax_codes=["TAX"],
            rates=rates,
        )

        # 99.99 * 0.0725 = 7.249275 -> rounds to 7.25
        assert result.tax_total == Money.of("7.25", "USD")

    def test_multiple_taxes_sum_correctly(self):
        """Multiple taxes sum to correct total despite rounding."""
        rates = {
            "A": TaxRate(tax_code="A", tax_name="A", rate=Decimal("0.033")),
            "B": TaxRate(tax_code="B", tax_name="B", rate=Decimal("0.033")),
            "C": TaxRate(tax_code="C", tax_name="C", rate=Decimal("0.034")),
        }

        result = self.calculator.calculate(
            amount=Money.of("100.00", "USD"),
            tax_codes=["A", "B", "C"],
            rates=rates,
        )

        # Each tax rounds individually, sum should still work
        expected_total = sum(line.tax_amount.amount for line in result.tax_lines)
        assert result.tax_total.amount == expected_total


class TestTaxRateValidation:
    """Tests for TaxRate validation."""

    def test_negative_rate_raises(self):
        """Raises error for negative tax rate."""
        with pytest.raises(ValueError, match="negative"):
            TaxRate(
                tax_code="BAD",
                tax_name="Bad Tax",
                rate=Decimal("-0.05"),
            )

    def test_high_rate_allowed(self):
        """Rates over 100% are allowed (unusual but valid)."""
        rate = TaxRate(
            tax_code="HIGH",
            tax_name="High Tax",
            rate=Decimal("1.50"),  # 150%
        )

        assert rate.rate == Decimal("1.50")
        assert rate.rate_percent == Decimal("150")

    def test_immutable(self):
        """TaxRate is immutable."""
        rate = TaxRate(
            tax_code="TEST",
            tax_name="Test",
            rate=Decimal("0.10"),
        )

        with pytest.raises(AttributeError):
            rate.rate = Decimal("0.20")
