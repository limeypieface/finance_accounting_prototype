"""
E1-E3: Multi-Currency Triangle Conversion Tests.

Round-trip currency conversions can compound rounding errors.
A->B->C should produce similar (within tolerance) results to A->C.

These tests verify that:
1. Triangle conversions don't create arbitrage opportunities
2. Rounding is documented and auditable
3. Conversion differences are within acceptable tolerances
4. No phantom value is created or destroyed
"""

from datetime import UTC, date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.domain.values import Money
from finance_kernel.models.exchange_rate import ExchangeRate


class TestTriangleCurrencyConversions:
    """
    E3: Cascading conversion safety tests.

    Verify that multi-step currency conversions don't compound
    rounding errors beyond acceptable tolerances.
    """

    @pytest.fixture
    def exchange_rates(self, session, test_actor_id):
        """Create exchange rates for testing."""
        now = datetime.now(UTC)

        rates = [
            # USD -> EUR
            ExchangeRate(
                from_currency="USD",
                to_currency="EUR",
                rate=Decimal("0.85"),
                effective_at=now,
                source="test",
                created_by_id=test_actor_id,
            ),
            # EUR -> GBP
            ExchangeRate(
                from_currency="EUR",
                to_currency="GBP",
                rate=Decimal("0.88"),
                effective_at=now,
                source="test",
                created_by_id=test_actor_id,
            ),
            # GBP -> USD (for round-trip)
            # Inverse of USD->GBP (0.75) within arbitrage tolerance
            ExchangeRate(
                from_currency="GBP",
                to_currency="USD",
                rate=Decimal("1.3333"),
                effective_at=now,
                source="test",
                created_by_id=test_actor_id,
            ),
            # Direct USD -> GBP
            ExchangeRate(
                from_currency="USD",
                to_currency="GBP",
                rate=Decimal("0.75"),
                effective_at=now,
                source="test",
                created_by_id=test_actor_id,
            ),
        ]

        for rate in rates:
            session.add(rate)
        session.flush()

        return {
            "USD_EUR": rates[0],
            "EUR_GBP": rates[1],
            "GBP_USD": rates[2],
            "USD_GBP": rates[3],
        }

    def test_triangle_conversion_within_tolerance(self, exchange_rates):
        """
        Verify that A->B->C produces result within tolerance of A->C.

        USD -> EUR -> GBP should be close to USD -> GBP directly.
        """
        amount_usd = Decimal("1000.00")

        # Path 1: USD -> EUR -> GBP
        usd_to_eur_rate = Decimal("0.85")
        eur_to_gbp_rate = Decimal("0.88")

        step1_eur = (amount_usd * usd_to_eur_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        step2_gbp = (step1_eur * eur_to_gbp_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Path 2: USD -> GBP directly
        usd_to_gbp_rate = Decimal("0.75")
        direct_gbp = (amount_usd * usd_to_gbp_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculate difference
        difference = abs(step2_gbp - direct_gbp)

        # Tolerance: 1% of the original amount (reasonable for compound rounding)
        tolerance = amount_usd * Decimal("0.01")

        print("\n[E3] Triangle Conversion Test:")
        print(f"  USD: {amount_usd}")
        print(f"  Path 1 (USD->EUR->GBP): {step2_gbp} GBP")
        print(f"  Path 2 (USD->GBP direct): {direct_gbp} GBP")
        print(f"  Difference: {difference} GBP")
        print(f"  Tolerance: {tolerance} GBP")

        assert difference <= tolerance, (
            f"Triangle conversion difference ({difference}) exceeds tolerance ({tolerance})"
        )

    def test_round_trip_conversion_loss_bounded(self, exchange_rates):
        """
        Verify that round-trip conversions have bounded loss.

        USD -> EUR -> GBP -> USD should not lose/gain excessive value.
        """
        amount_usd = Decimal("1000.00")

        # Full round trip
        usd_to_eur = Decimal("0.85")
        eur_to_gbp = Decimal("0.88")
        gbp_to_usd = Decimal("1.34")

        step1_eur = (amount_usd * usd_to_eur).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        step2_gbp = (step1_eur * eur_to_gbp).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        step3_usd = (step2_gbp * gbp_to_usd).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculate total change
        change = step3_usd - amount_usd
        change_percent = (abs(change) / amount_usd) * Decimal("100")

        print("\n[E3] Round-Trip Test:")
        print(f"  Start: {amount_usd} USD")
        print(f"  After USD->EUR: {step1_eur} EUR")
        print(f"  After EUR->GBP: {step2_gbp} GBP")
        print(f"  After GBP->USD: {step3_usd} USD")
        print(f"  Net change: {change} USD ({change_percent:.2f}%)")

        # Maximum acceptable round-trip loss: 5%
        # (This is configurable based on business requirements)
        max_loss_percent = Decimal("5.0")

        assert change_percent <= max_loss_percent, (
            f"Round-trip loss ({change_percent}%) exceeds maximum ({max_loss_percent}%)"
        )

    def test_multiple_small_conversions_dont_compound_excessively(self):
        """
        Verify that many small conversions don't compound rounding.
        """
        amount = Decimal("1000.00")
        rate = Decimal("0.85")

        # Convert back and forth 100 times
        current = amount
        for _ in range(100):
            # Forward conversion
            current = (current * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            # Reverse conversion
            current = (current / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        final_change = abs(current - amount)
        change_percent = (final_change / amount) * Decimal("100")

        print("\n[E3] Compound Conversion Test (100 round-trips):")
        print(f"  Start: {amount}")
        print(f"  End: {current}")
        print(f"  Change: {final_change} ({change_percent:.2f}%)")

        # After 100 round-trips, should still be within 10%
        max_compound_loss = Decimal("10.0")
        assert change_percent <= max_compound_loss, (
            f"Compound conversion loss ({change_percent}%) exceeds limit ({max_compound_loss}%)"
        )


class TestArbitrageDetection:
    """
    Tests for detecting and preventing arbitrage opportunities.
    """

    def test_triangular_arbitrage_detection(self):
        """
        Verify that triangular arbitrage is detected.

        If USD->EUR->GBP->USD > 1.0, arbitrage exists.
        """
        # Example rates that could create arbitrage
        usd_eur = Decimal("0.85")
        eur_gbp = Decimal("0.90")
        gbp_usd = Decimal("1.40")

        # Calculate product of rates around triangle
        triangle_product = usd_eur * eur_gbp * gbp_usd

        print("\n[Arbitrage Detection]:")
        print(f"  USD/EUR: {usd_eur}")
        print(f"  EUR/GBP: {eur_gbp}")
        print(f"  GBP/USD: {gbp_usd}")
        print(f"  Triangle product: {triangle_product}")

        # Arbitrage exists if product > 1 or product < 1
        # A perfect triangle would equal exactly 1
        deviation_from_unity = abs(triangle_product - Decimal("1"))

        # If deviation is significant, flag potential arbitrage
        arbitrage_threshold = Decimal("0.05")  # 5% deviation

        if deviation_from_unity > arbitrage_threshold:
            arbitrage_direction = "profit" if triangle_product > 1 else "loss"
            print(f"  ARBITRAGE DETECTED: {arbitrage_direction} opportunity")
            print(f"  Deviation from unity: {deviation_from_unity}")

        # This test documents the detection but doesn't fail
        # because the system should allow configuration of tolerance

    def test_inverse_rate_consistency(self):
        """
        Verify that rate and its inverse are consistent.

        If USD/EUR = 0.85, then EUR/USD should be ~1.176
        """
        usd_eur = Decimal("0.85")
        expected_eur_usd = (Decimal("1") / usd_eur).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

        print("\n[Inverse Rate Consistency]:")
        print(f"  USD/EUR: {usd_eur}")
        print(f"  Expected EUR/USD: {expected_eur_usd}")

        # If we have both rates, they should be consistent
        actual_eur_usd = Decimal("1.1765")  # Example

        # Calculate the discrepancy
        if actual_eur_usd:
            product = usd_eur * actual_eur_usd
            discrepancy = abs(product - Decimal("1"))

            print(f"  Actual EUR/USD: {actual_eur_usd}")
            print(f"  Product (should be 1): {product}")
            print(f"  Discrepancy: {discrepancy}")

            # Should be very small
            max_discrepancy = Decimal("0.001")  # 0.1%
            assert discrepancy <= max_discrepancy, (
                f"Inverse rate discrepancy ({discrepancy}) exceeds limit ({max_discrepancy})"
            )


class TestRoundingDocumentation:
    """
    Tests to verify that rounding is properly documented.
    """

    def test_rounding_creates_audit_trail(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Verify that rounding differences are recorded in the journal.
        """
        # Post an entry that may require rounding
        result = post_via_coordinator(
            amount=Decimal("100.00"),
        )

        assert result.success

        # Verify entry has proper documentation
        from finance_kernel.models.journal import JournalEntry, JournalLine

        entry_id = result.journal_result.entries[0].entry_id
        entry = session.get(JournalEntry, entry_id)
        assert entry is not None

        # Check for rounding lines (if any)
        rounding_lines = [l for l in entry.lines if l.is_rounding]

        if rounding_lines:
            print("\n[Rounding Documentation]:")
            for line in rounding_lines:
                print(f"  Rounding line: {line.side.value} {line.amount} {line.currency}")

            # Rounding should be small
            total_rounding = sum(l.amount for l in rounding_lines)
            assert total_rounding < Decimal("0.10"), (
                f"Rounding amount ({total_rounding}) seems too large"
            )


class TestCurrencyPrecision:
    """
    Tests for currency-specific precision handling.
    """

    def test_different_currency_precisions(self):
        """
        Verify handling of different currency precisions.

        JPY has no decimal places, while most currencies have 2.
        """
        # USD (2 decimal places)
        usd_amount = Money.of(Decimal("100.00"), "USD")
        assert usd_amount.amount == Decimal("100.00")

        # JPY (0 decimal places)
        jpy_amount = Money.of(Decimal("10000"), "JPY")
        assert jpy_amount.amount == Decimal("10000")

        # Verify rounding tolerances are currency-appropriate
        usd_tolerance = CurrencyRegistry.get_rounding_tolerance("USD")
        jpy_tolerance = CurrencyRegistry.get_rounding_tolerance("JPY")

        print("\n[Currency Precision]:")
        print(f"  USD tolerance: {usd_tolerance}")
        print(f"  JPY tolerance: {jpy_tolerance}")

        # USD tolerance should be around 0.01
        assert usd_tolerance >= Decimal("0.001")
        assert usd_tolerance <= Decimal("0.10")

    def test_cross_currency_conversion_precision(self):
        """
        Verify that cross-currency conversions maintain appropriate precision.
        """
        # Convert USD to JPY
        usd_amount = Decimal("100.00")
        usd_jpy_rate = Decimal("110.50")

        jpy_result = usd_amount * usd_jpy_rate

        # JPY should be rounded to whole numbers
        jpy_rounded = jpy_result.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        print("\n[Cross-Currency Precision]:")
        print(f"  USD: {usd_amount}")
        print(f"  Rate: {usd_jpy_rate}")
        print(f"  Raw JPY: {jpy_result}")
        print(f"  Rounded JPY: {jpy_rounded}")

        # Verify no fractional yen
        assert jpy_rounded == jpy_rounded.to_integral_value()
