"""
Tests for Variance Engine.

Covers:
- Price variance (PPV)
- Quantity variance
- FX variance
- Standard cost variance
- Variance allocation
- Edge cases and error handling
"""

from decimal import Decimal

import pytest

from finance_engines.variance import (
    VarianceAllocation,
    VarianceCalculator,
    VarianceDisposition,
    VarianceResult,
    VarianceType,
)
from finance_kernel.domain.values import Currency, Money


class TestPriceVariance:
    """Tests for price variance calculation (PPV, SPV)."""

    def setup_method(self):
        self.calculator = VarianceCalculator()

    def test_unfavorable_price_variance(self):
        """Actual price higher than expected = unfavorable."""
        result = self.calculator.price_variance(
            expected_price=Money.of("10.00", "USD"),
            actual_price=Money.of("10.50", "USD"),
            quantity=Decimal("100"),
        )

        assert result.variance_type == VarianceType.PRICE
        assert result.expected == Money.of("1000.00", "USD")
        assert result.actual == Money.of("1050.00", "USD")
        assert result.variance == Money.of("50.00", "USD")
        assert result.is_favorable is False

    def test_favorable_price_variance(self):
        """Actual price lower than expected = favorable."""
        result = self.calculator.price_variance(
            expected_price=Money.of("10.00", "USD"),
            actual_price=Money.of("9.50", "USD"),
            quantity=Decimal("100"),
        )

        assert result.variance == Money.of("-50.00", "USD")
        assert result.is_favorable is True

    def test_zero_price_variance(self):
        """No variance when prices match."""
        result = self.calculator.price_variance(
            expected_price=Money.of("10.00", "USD"),
            actual_price=Money.of("10.00", "USD"),
            quantity=Decimal("100"),
        )

        assert result.variance == Money.of("0.00", "USD")
        # Zero variance is technically favorable (not unfavorable)
        assert result.is_favorable is False  # 0 is not < 0

    def test_fractional_quantity(self):
        """Works with fractional quantities."""
        result = self.calculator.price_variance(
            expected_price=Money.of("10.00", "USD"),
            actual_price=Money.of("11.00", "USD"),
            quantity=Decimal("2.5"),
        )

        assert result.variance == Money.of("2.50", "USD")

    def test_currency_mismatch_raises(self):
        """Raises error when currencies don't match."""
        with pytest.raises(ValueError, match="Currency mismatch"):
            self.calculator.price_variance(
                expected_price=Money.of("10.00", "USD"),
                actual_price=Money.of("10.50", "EUR"),
                quantity=Decimal("100"),
            )

    def test_variance_percent(self):
        """Variance percentage calculated correctly."""
        result = self.calculator.price_variance(
            expected_price=Money.of("100.00", "USD"),
            actual_price=Money.of("110.00", "USD"),
            quantity=Decimal("1"),
        )

        assert result.variance_percent == Decimal("10")

    def test_absolute_variance(self):
        """Absolute variance is always positive."""
        result = self.calculator.price_variance(
            expected_price=Money.of("10.00", "USD"),
            actual_price=Money.of("9.00", "USD"),
            quantity=Decimal("100"),
        )

        assert result.variance.amount < Decimal("0")
        assert result.absolute_variance.amount > Decimal("0")


class TestQuantityVariance:
    """Tests for quantity/usage variance calculation."""

    def setup_method(self):
        self.calculator = VarianceCalculator()

    def test_unfavorable_quantity_variance(self):
        """Used more than expected = unfavorable."""
        result = self.calculator.quantity_variance(
            expected_quantity=Decimal("100"),
            actual_quantity=Decimal("110"),
            standard_price=Money.of("5.00", "USD"),
        )

        assert result.variance_type == VarianceType.QUANTITY
        assert result.expected == Money.of("500.00", "USD")
        assert result.actual == Money.of("550.00", "USD")
        assert result.variance == Money.of("50.00", "USD")
        assert result.is_favorable is False

    def test_favorable_quantity_variance(self):
        """Used less than expected = favorable."""
        result = self.calculator.quantity_variance(
            expected_quantity=Decimal("100"),
            actual_quantity=Decimal("90"),
            standard_price=Money.of("5.00", "USD"),
        )

        assert result.variance == Money.of("-50.00", "USD")
        assert result.is_favorable is True

    def test_zero_quantity_variance(self):
        """No variance when quantities match."""
        result = self.calculator.quantity_variance(
            expected_quantity=Decimal("100"),
            actual_quantity=Decimal("100"),
            standard_price=Money.of("5.00", "USD"),
        )

        assert result.variance.is_zero


class TestFXVariance:
    """Tests for foreign exchange variance calculation."""

    def setup_method(self):
        self.calculator = VarianceCalculator()

    def test_fx_gain(self):
        """Currency strengthened = gain."""
        result = self.calculator.fx_variance(
            original_amount=Money.of("1000.00", "EUR"),
            original_rate=Decimal("1.10"),  # 1 EUR = 1.10 USD
            current_rate=Decimal("1.15"),   # 1 EUR = 1.15 USD now
            functional_currency="USD",
        )

        assert result.variance_type == VarianceType.FX
        assert result.expected == Money.of("1100.00", "USD")
        assert result.actual == Money.of("1150.00", "USD")
        assert result.variance == Money.of("50.00", "USD")

    def test_fx_loss(self):
        """Currency weakened = loss."""
        result = self.calculator.fx_variance(
            original_amount=Money.of("1000.00", "EUR"),
            original_rate=Decimal("1.10"),
            current_rate=Decimal("1.05"),
            functional_currency="USD",
        )

        assert result.variance == Money.of("-50.00", "USD")

    def test_fx_with_currency_object(self):
        """Accepts Currency object for functional currency."""
        result = self.calculator.fx_variance(
            original_amount=Money.of("1000.00", "EUR"),
            original_rate=Decimal("1.10"),
            current_rate=Decimal("1.15"),
            functional_currency=Currency("USD"),
        )

        assert result.actual.currency.code == "USD"


class TestStandardCostVariance:
    """Tests for standard cost variance calculation."""

    def setup_method(self):
        self.calculator = VarianceCalculator()

    def test_unfavorable_cost_variance(self):
        """Actual cost higher than standard = unfavorable."""
        result = self.calculator.standard_cost_variance(
            standard_cost=Money.of("50.00", "USD"),
            actual_cost=Money.of("55.00", "USD"),
            quantity=Decimal("10"),
        )

        assert result.variance_type == VarianceType.STANDARD_COST
        assert result.variance == Money.of("50.00", "USD")
        assert result.is_favorable is False

    def test_favorable_cost_variance(self):
        """Actual cost lower than standard = favorable."""
        result = self.calculator.standard_cost_variance(
            standard_cost=Money.of("50.00", "USD"),
            actual_cost=Money.of("45.00", "USD"),
            quantity=Decimal("10"),
        )

        assert result.variance == Money.of("-50.00", "USD")
        assert result.is_favorable is True

    def test_default_quantity_one(self):
        """Default quantity is 1 for total cost comparison."""
        result = self.calculator.standard_cost_variance(
            standard_cost=Money.of("500.00", "USD"),
            actual_cost=Money.of("550.00", "USD"),
        )

        assert result.variance == Money.of("50.00", "USD")

    def test_currency_mismatch_raises(self):
        """Raises error when currencies don't match."""
        with pytest.raises(ValueError, match="Currency mismatch"):
            self.calculator.standard_cost_variance(
                standard_cost=Money.of("50.00", "USD"),
                actual_cost=Money.of("55.00", "EUR"),
            )


class TestVarianceAllocation:
    """Tests for variance allocation across targets."""

    def setup_method(self):
        self.calculator = VarianceCalculator()

    def test_allocate_variance_by_weight(self):
        """Allocates variance proportionally by weight."""
        variance = self.calculator.price_variance(
            expected_price=Money.of("10.00", "USD"),
            actual_price=Money.of("11.00", "USD"),
            quantity=Decimal("100"),
        )
        # Variance is 100.00 USD

        allocations = self.calculator.allocate_variance(
            variance=variance,
            targets=[
                ("item-1", "inventory", Decimal("30")),
                ("item-2", "inventory", Decimal("70")),
            ],
        )

        assert len(allocations) == 2
        assert allocations[0].target_id == "item-1"
        assert allocations[0].allocated_variance == Money.of("30.00", "USD")
        assert allocations[1].target_id == "item-2"
        assert allocations[1].allocated_variance == Money.of("70.00", "USD")

    def test_allocate_handles_rounding(self):
        """Last target gets rounding remainder."""
        variance = VarianceResult(
            variance_type=VarianceType.PRICE,
            expected=Money.of("100.00", "USD"),
            actual=Money.of("100.03", "USD"),
            variance=Money.of("0.03", "USD"),
            is_favorable=False,
        )

        allocations = self.calculator.allocate_variance(
            variance=variance,
            targets=[
                ("a", "inv", Decimal("1")),
                ("b", "inv", Decimal("1")),
                ("c", "inv", Decimal("1")),
            ],
        )

        # 0.03 / 3 = 0.01 each, but rounding may differ
        total = sum(a.allocated_variance.amount for a in allocations)
        assert total == Decimal("0.03")

    def test_allocate_empty_targets(self):
        """Returns empty list for empty targets."""
        variance = VarianceResult(
            variance_type=VarianceType.PRICE,
            expected=Money.of("100.00", "USD"),
            actual=Money.of("110.00", "USD"),
            variance=Money.of("10.00", "USD"),
            is_favorable=False,
        )

        allocations = self.calculator.allocate_variance(variance, [])
        assert allocations == []

    def test_allocate_zero_weight_raises(self):
        """Raises error when total weight is zero."""
        variance = VarianceResult(
            variance_type=VarianceType.PRICE,
            expected=Money.of("100.00", "USD"),
            actual=Money.of("110.00", "USD"),
            variance=Money.of("10.00", "USD"),
            is_favorable=False,
        )

        with pytest.raises(ValueError, match="zero"):
            self.calculator.allocate_variance(
                variance=variance,
                targets=[
                    ("a", "inv", Decimal("0")),
                    ("b", "inv", Decimal("0")),
                ],
            )


class TestVarianceResultProperties:
    """Tests for VarianceResult value object."""

    def test_immutable(self):
        """VarianceResult is immutable."""
        result = VarianceResult(
            variance_type=VarianceType.PRICE,
            expected=Money.of("100.00", "USD"),
            actual=Money.of("110.00", "USD"),
            variance=Money.of("10.00", "USD"),
            is_favorable=False,
        )

        with pytest.raises(AttributeError):
            result.variance = Money.of("20.00", "USD")

    def test_variance_percent_zero_expected(self):
        """Variance percent is 0 when expected is 0."""
        result = VarianceResult(
            variance_type=VarianceType.PRICE,
            expected=Money.of("0.00", "USD"),
            actual=Money.of("10.00", "USD"),
            variance=Money.of("10.00", "USD"),
            is_favorable=False,
        )

        assert result.variance_percent == Decimal("0")
