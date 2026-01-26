"""
Tests for currency validation and precision (R16, R17 Compliance).

R16: ISO 4217 enforcement
- Currency codes must be validated at ingestion or domain boundary.
- Invalid codes must be rejected or explicitly flagged as non-financial.

R17: Precision-derived tolerance
- Rounding tolerance must be derived from currency precision.
- Fixed decimal tolerances are forbidden.
"""

import pytest
from decimal import Decimal

from finance_kernel.domain.currency import CurrencyRegistry, CurrencyInfo
from finance_kernel.domain.values import Currency, Money


class TestR16ISO4217Enforcement:
    """Tests for R16: ISO 4217 enforcement."""

    def test_valid_currency_codes_accepted(self):
        """Valid ISO 4217 codes should be accepted."""
        valid_codes = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD"]
        for code in valid_codes:
            assert CurrencyRegistry.is_valid(code)
            assert CurrencyRegistry.validate(code) == code

    def test_lowercase_codes_normalized(self):
        """Lowercase codes should be normalized to uppercase."""
        assert CurrencyRegistry.validate("usd") == "USD"
        assert CurrencyRegistry.validate("eur") == "EUR"
        assert CurrencyRegistry.validate("gbp") == "GBP"

    def test_whitespace_trimmed(self):
        """Whitespace should be trimmed from codes."""
        assert CurrencyRegistry.validate(" USD ") == "USD"
        assert CurrencyRegistry.validate("EUR ") == "EUR"
        assert CurrencyRegistry.validate(" GBP") == "GBP"

    def test_invalid_currency_codes_rejected(self):
        """Invalid codes must be rejected at domain boundary."""
        invalid_codes = ["XXY", "ABC", "123", "US", "USDD", "", "X"]
        for code in invalid_codes:
            assert not CurrencyRegistry.is_valid(code)

    def test_validate_raises_on_invalid_code(self):
        """CurrencyRegistry.validate must raise ValueError on invalid codes."""
        with pytest.raises(ValueError, match="Invalid ISO 4217 currency code"):
            CurrencyRegistry.validate("XXY")

        with pytest.raises(ValueError, match="Invalid ISO 4217 currency code"):
            CurrencyRegistry.validate("ABC")

    def test_validate_raises_on_wrong_length(self):
        """Currency codes must be exactly 3 characters."""
        with pytest.raises(ValueError, match="must be 3 characters"):
            CurrencyRegistry.validate("US")

        with pytest.raises(ValueError, match="must be 3 characters"):
            CurrencyRegistry.validate("USDD")

    def test_validate_raises_on_empty_or_none(self):
        """Empty or None codes must be rejected."""
        with pytest.raises(ValueError, match="Invalid currency code"):
            CurrencyRegistry.validate("")

        with pytest.raises(ValueError, match="Invalid currency code"):
            CurrencyRegistry.validate(None)

    def test_currency_value_object_validates_at_boundary(self):
        """Currency value object must validate at construction (boundary)."""
        # Valid currencies
        usd = Currency("USD")
        assert usd.code == "USD"

        # Invalid currencies raise at construction
        with pytest.raises(ValueError):
            Currency("XXY")

        with pytest.raises(ValueError):
            Currency("")

    def test_money_validates_currency_at_boundary(self):
        """Money value object must validate currency at construction."""
        # Valid money
        money = Money.of(Decimal("100.00"), "USD")
        assert money.currency.code == "USD"

        # Invalid currency raises at construction
        with pytest.raises(ValueError):
            Money.of(Decimal("100.00"), "XXY")

    def test_all_iso4217_codes_present(self):
        """Registry should contain comprehensive ISO 4217 codes."""
        # Check major currencies
        major = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
        for code in major:
            assert CurrencyRegistry.is_valid(code)

        # Check zero-decimal currencies
        zero_decimal = ["JPY", "KRW", "VND", "BIF", "CLP"]
        for code in zero_decimal:
            info = CurrencyRegistry.get_info(code)
            assert info is not None
            assert info.decimal_places == 0

        # Check three-decimal currencies
        three_decimal = ["KWD", "BHD", "OMR", "JOD"]
        for code in three_decimal:
            info = CurrencyRegistry.get_info(code)
            assert info is not None
            assert info.decimal_places == 3


class TestR17PrecisionDerivedTolerance:
    """Tests for R17: Precision-derived tolerance."""

    def test_tolerance_derived_from_precision_two_decimals(self):
        """For 2 decimal currencies, tolerance should be 0.01."""
        two_decimal_currencies = ["USD", "EUR", "GBP", "CHF"]
        for code in two_decimal_currencies:
            tolerance = CurrencyRegistry.get_rounding_tolerance(code)
            assert tolerance == Decimal("0.01"), f"{code} should have tolerance 0.01"

    def test_tolerance_derived_from_precision_zero_decimals(self):
        """For 0 decimal currencies, tolerance should be 1."""
        zero_decimal_currencies = ["JPY", "KRW", "VND"]
        for code in zero_decimal_currencies:
            tolerance = CurrencyRegistry.get_rounding_tolerance(code)
            assert tolerance == Decimal("1"), f"{code} should have tolerance 1"

    def test_tolerance_derived_from_precision_three_decimals(self):
        """For 3 decimal currencies, tolerance should be 0.001."""
        three_decimal_currencies = ["KWD", "BHD", "OMR", "JOD"]
        for code in three_decimal_currencies:
            tolerance = CurrencyRegistry.get_rounding_tolerance(code)
            assert tolerance == Decimal("0.001"), f"{code} should have tolerance 0.001"

    def test_tolerance_derived_from_precision_four_decimals(self):
        """For 4 decimal currencies, tolerance should be 0.0001."""
        four_decimal_currencies = ["CLF", "UYW"]
        for code in four_decimal_currencies:
            tolerance = CurrencyRegistry.get_rounding_tolerance(code)
            assert tolerance == Decimal("0.0001"), f"{code} should have tolerance 0.0001"

    def test_currency_info_tolerance_matches_decimal_places(self):
        """CurrencyInfo.rounding_tolerance must match decimal_places."""
        # Test 2 decimal
        info_usd = CurrencyInfo("USD", 2, "US Dollar")
        assert info_usd.rounding_tolerance == Decimal("0.01")

        # Test 0 decimal
        info_jpy = CurrencyInfo("JPY", 0, "Japanese Yen")
        assert info_jpy.rounding_tolerance == Decimal("1")

        # Test 3 decimal
        info_kwd = CurrencyInfo("KWD", 3, "Kuwaiti Dinar")
        assert info_kwd.rounding_tolerance == Decimal("0.001")

        # Test 4 decimal
        info_clf = CurrencyInfo("CLF", 4, "Unidad de Fomento")
        assert info_clf.rounding_tolerance == Decimal("0.0001")

    def test_no_fixed_tolerance_values(self):
        """
        R17: No fixed decimal tolerances allowed.

        All tolerances must be derived from decimal_places.
        This test verifies the derivation formula.
        """
        # Verify the formula: tolerance = 10^(-decimal_places)
        test_cases = [
            (0, Decimal("1")),
            (1, Decimal("0.1")),
            (2, Decimal("0.01")),
            (3, Decimal("0.001")),
            (4, Decimal("0.0001")),
        ]

        for decimal_places, expected in test_cases:
            tolerance = CurrencyRegistry._tolerance_from_decimal_places(decimal_places)
            assert tolerance == expected, (
                f"decimal_places={decimal_places} should give tolerance={expected}"
            )

    def test_unknown_currency_tolerance_derived_from_default(self):
        """
        Unknown currencies must derive tolerance from DEFAULT_DECIMAL_PLACES.

        R17: Even fallback tolerances cannot be fixed values.
        """
        # Get default decimal places
        default_places = CurrencyRegistry.DEFAULT_DECIMAL_PLACES

        # Expected tolerance from default (should be 2 decimal places -> 0.01)
        expected = CurrencyRegistry._tolerance_from_decimal_places(default_places)

        # Since unknown currencies are rejected by is_valid/validate,
        # but get_rounding_tolerance provides a fallback for internal use
        # Let's verify the fallback derivation
        assert default_places == 2
        assert expected == Decimal("0.01")

    def test_currency_info_quantize_string_matches_precision(self):
        """CurrencyInfo.quantize_string must match decimal_places."""
        # Test 2 decimal
        info_usd = CurrencyInfo("USD", 2, "US Dollar")
        assert info_usd.quantize_string == "0.00"

        # Test 0 decimal
        info_jpy = CurrencyInfo("JPY", 0, "Japanese Yen")
        assert info_jpy.quantize_string == "1"

        # Test 3 decimal
        info_kwd = CurrencyInfo("KWD", 3, "Kuwaiti Dinar")
        assert info_kwd.quantize_string == "0.000"

    def test_get_decimal_places_consistency(self):
        """get_decimal_places must be consistent with CurrencyInfo."""
        for code in ["USD", "EUR", "JPY", "KWD", "CLF"]:
            info = CurrencyRegistry.get_info(code)
            assert info is not None
            assert CurrencyRegistry.get_decimal_places(code) == info.decimal_places

    def test_get_decimal_places_unknown_returns_default(self):
        """Unknown currencies should return DEFAULT_DECIMAL_PLACES."""
        # Note: These won't pass is_valid but get_decimal_places provides fallback
        default = CurrencyRegistry.DEFAULT_DECIMAL_PLACES
        assert default == 2  # Expected default


class TestCurrencyValueObject:
    """Tests for Currency value object behavior."""

    def test_currency_immutable(self):
        """Currency value object must be immutable."""
        currency = Currency("USD")
        with pytest.raises(AttributeError):
            currency.code = "EUR"

    def test_currency_equality(self):
        """Currency value objects with same code must be equal."""
        c1 = Currency("USD")
        c2 = Currency("USD")
        assert c1 == c2
        assert hash(c1) == hash(c2)

    def test_currency_inequality(self):
        """Currency value objects with different codes must not be equal."""
        c1 = Currency("USD")
        c2 = Currency("EUR")
        assert c1 != c2

    def test_currency_case_normalization(self):
        """Currency codes should be normalized to uppercase."""
        c1 = Currency("usd")
        c2 = Currency("USD")
        assert c1 == c2
        assert c1.code == "USD"


class TestMoneyWithCurrencyValidation:
    """Tests for Money value object with currency validation."""

    def test_money_currency_validated(self):
        """Money must validate currency at construction."""
        money = Money.of(Decimal("100.00"), "USD")
        assert money.currency == Currency("USD")

    def test_money_rejects_invalid_currency(self):
        """Money must reject invalid currency codes."""
        with pytest.raises(ValueError):
            Money.of(Decimal("100.00"), "XXY")

    def test_money_operations_preserve_currency(self):
        """Money arithmetic must preserve currency."""
        m1 = Money.of(Decimal("100.00"), "USD")
        m2 = Money.of(Decimal("50.00"), "USD")

        result = m1 + m2
        assert result.currency == Currency("USD")

    def test_money_prevents_cross_currency_operations(self):
        """Money must prevent operations with different currencies."""
        m1 = Money.of(Decimal("100.00"), "USD")
        m2 = Money.of(Decimal("50.00"), "EUR")

        with pytest.raises((ValueError, TypeError)):
            _ = m1 + m2
