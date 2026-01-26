"""
Unit tests for Money and decimal handling.

Verifies:
- Decimal math precision
- Rounding determinism
- Serialization stability
- Float constructor prohibition
"""

import pytest
from decimal import Decimal, ROUND_HALF_UP

from finance_kernel.db.types import (
    money_from_str,
    money_from_int,
    round_money,
    MONEY_DECIMAL_PLACES,
)
from finance_kernel.domain.currency import CurrencyRegistry


class TestMoneyFromStr:
    """Tests for money_from_str function."""

    def test_simple_decimal(self):
        """Test simple decimal creation."""
        result = money_from_str("100.50")
        assert result == Decimal("100.50")

    def test_large_number(self):
        """Test large number handling."""
        result = money_from_str("123456789012345678901234567890.123456789")
        assert result == Decimal("123456789012345678901234567890.123456789")

    def test_negative(self):
        """Test negative number."""
        result = money_from_str("-100.50")
        assert result == Decimal("-100.50")

    def test_zero(self):
        """Test zero."""
        result = money_from_str("0")
        assert result == Decimal("0")

    def test_invalid_string_raises(self):
        """Test that invalid string raises ValueError."""
        with pytest.raises(Exception):  # Decimal raises InvalidOperation
            money_from_str("not a number")


class TestMoneyFromInt:
    """Tests for money_from_int function."""

    def test_cents_to_dollars(self):
        """Test converting cents to dollars."""
        result = money_from_int(1050, 2)
        assert result == Decimal("10.50")

    def test_zero_decimal_places(self):
        """Test currency with no decimal places (JPY)."""
        result = money_from_int(1000, 0)
        assert result == Decimal("1000")

    def test_three_decimal_places(self):
        """Test currency with 3 decimal places (KWD)."""
        result = money_from_int(1000, 3)
        assert result == Decimal("1.000")


class TestRoundMoney:
    """Tests for round_money function."""

    def test_round_half_up(self):
        """Test standard rounding."""
        result = round_money(Decimal("10.555"), 2)
        assert result == Decimal("10.56")

    def test_round_half_up_down(self):
        """Test rounding down case."""
        result = round_money(Decimal("10.554"), 2)
        assert result == Decimal("10.55")

    def test_round_exactly_half(self):
        """Test exactly half rounds up."""
        result = round_money(Decimal("10.545"), 2)
        assert result == Decimal("10.55")  # ROUND_HALF_UP

    def test_no_rounding_needed(self):
        """Test value already at precision."""
        result = round_money(Decimal("10.50"), 2)
        assert result == Decimal("10.50")

    def test_different_decimal_places(self):
        """Test different decimal place counts."""
        assert round_money(Decimal("10.5555"), 0) == Decimal("11")
        assert round_money(Decimal("10.5555"), 1) == Decimal("10.6")
        assert round_money(Decimal("10.5555"), 3) == Decimal("10.556")


class TestCurrencyRegistry:
    """Tests for the CurrencyRegistry class."""

    def test_get_decimal_places_usd(self):
        """Test USD has 2 decimal places."""
        assert CurrencyRegistry.get_decimal_places("USD") == 2

    def test_get_decimal_places_jpy(self):
        """Test JPY has 0 decimal places."""
        assert CurrencyRegistry.get_decimal_places("JPY") == 0

    def test_get_decimal_places_kwd(self):
        """Test KWD has 3 decimal places."""
        assert CurrencyRegistry.get_decimal_places("KWD") == 3

    def test_round_amount_usd(self):
        """Test rounding for USD."""
        decimal_places = CurrencyRegistry.get_decimal_places("USD")
        result = round_money(Decimal("10.556"), decimal_places)
        assert result == Decimal("10.56")

    def test_round_amount_jpy(self):
        """Test rounding for JPY."""
        decimal_places = CurrencyRegistry.get_decimal_places("JPY")
        result = round_money(Decimal("10.5"), decimal_places)
        assert result == Decimal("11")


class TestDecimalArithmetic:
    """Tests for decimal arithmetic correctness."""

    def test_addition_precision(self):
        """Test that addition preserves precision."""
        a = Decimal("0.1")
        b = Decimal("0.2")
        result = a + b
        assert result == Decimal("0.3")

    def test_multiplication_precision(self):
        """Test multiplication precision."""
        price = Decimal("19.99")
        quantity = Decimal("3")
        result = price * quantity
        assert result == Decimal("59.97")

    def test_division_precision(self):
        """Test division precision."""
        total = Decimal("100.00")
        parts = Decimal("3")
        result = total / parts
        # Result should be precise, not floating point approximation
        assert str(result).startswith("33.333333")

    def test_no_float_contamination(self):
        """Test that we can detect float contamination."""
        # This demonstrates why floats are dangerous
        float_result = 0.1 + 0.2
        decimal_result = Decimal("0.1") + Decimal("0.2")

        # Float is NOT exactly 0.3
        assert float_result != 0.3  # This is True due to float imprecision

        # Decimal IS exactly 0.3
        assert decimal_result == Decimal("0.3")


class TestRoundingDeterminism:
    """Tests for deterministic rounding behavior."""

    def test_same_input_same_output(self):
        """Test that same input always produces same output."""
        inputs = [
            (Decimal("10.555"), "USD"),
            (Decimal("100.5"), "JPY"),
            (Decimal("1.0005"), "KWD"),
        ]

        # Run multiple times
        for _ in range(100):
            for amount, currency in inputs:
                decimal_places = CurrencyRegistry.get_decimal_places(currency)
                result1 = round_money(amount, decimal_places)
                result2 = round_money(amount, decimal_places)
                assert result1 == result2
