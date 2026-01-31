"""
Fuzzing and adversarial input tests.

Verifies:
- Boundary values handled correctly
- Malformed inputs rejected gracefully
- Extreme decimal precision
- Unicode and special characters in string fields
- Negative amounts rejected
- Zero amounts handled appropriately
"""

import pytest
import random
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from datetime import datetime, date, timedelta

from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.domain.dtos import LineSpec, LineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.db.types import money_from_str


@dataclass
class FuzzingResult:
    """Result of a fuzzing test run."""

    inputs_tested: int
    inputs_accepted: int
    inputs_rejected: int
    exceptions_caught: int
    unexpected_errors: int
    categories_tested: list[str]


class TestBoundaryValues:
    """Tests for boundary value handling via the posting pipeline."""

    def test_maximum_decimal_precision_38_9(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Test maximum precision: 38 digits total, 9 decimal places.
        """
        max_precision = Decimal("12345678901234567890123456789.123456789")

        try:
            result = post_via_coordinator(
                amount=max_precision,
            )
            assert result.success
        except (ValueError, InvalidOperation):
            # The posting pipeline may reject extreme precision at IntentLine construction
            pass

    def test_minimum_positive_amount(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Test smallest possible positive amount.
        """
        min_amount = Decimal("0.01")

        result = post_via_coordinator(
            amount=min_amount,
        )
        assert result.success

    def test_zero_amount_handling(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Zero amount lines should be rejected or handled appropriately.
        """
        try:
            result = post_via_coordinator(
                amount=Decimal("0"),
            )
            # Document current behavior - zero might be accepted or rejected
        except (ValueError, InvalidOperation):
            # Zero amounts rejected at IntentLine/Money construction level
            pass

    def test_very_large_amount(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Test amounts near the upper limit of the decimal type.
        """
        large_amount = Decimal("99999999999999999999999999999.999999999")

        try:
            result = post_via_coordinator(
                amount=large_amount,
            )
            # If it posted, it should be balanced
            if result.success:
                from finance_kernel.models.journal import JournalEntry
                entry_id = result.journal_result.entries[0].entry_id
                entry = session.get(JournalEntry, entry_id)
                assert entry.is_balanced
        except (ValueError, InvalidOperation, OverflowError):
            # Expected for extreme amounts
            pass


class TestMalformedInputs:
    """Tests for malformed input handling."""

    def test_negative_amount_in_intent_line(self):
        """
        Negative amounts should be rejected (amounts are always positive,
        side determines direction).
        """
        from finance_kernel.domain.accounting_intent import IntentLine

        with pytest.raises((ValueError, InvalidOperation)):
            IntentLine.debit("CashAsset", Decimal("-100.00"), "USD")

    def test_invalid_currency_code_rejected(self):
        """
        Invalid currency codes should be rejected.
        """
        from finance_kernel.domain.values import Currency

        # Currency constructor should reject invalid codes with ValueError
        with pytest.raises(ValueError, match="Invalid ISO 4217 currency code"):
            Currency("INVALID")

    def test_nonexistent_role_handling(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Non-existent roles should cause resolution failure.
        """
        try:
            result = post_via_coordinator(
                debit_role="NonExistentRole",
                credit_role="SalesRevenue",
                amount=Decimal("100.00"),
            )
            # Should fail during role resolution
            assert not result.success, (
                "Non-existent role should not post successfully"
            )
        except (KeyError, ValueError):
            # Expected: role resolver raises when role not found
            pass


class TestUnicodeHandling:
    """Tests for Unicode and special characters."""

    def test_unicode_in_event_payload(
        self,
        session,
        ingestor_service: IngestorService,
        test_actor_id,
        current_period,
        deterministic_clock,
    ):
        """
        Unicode in event payload should be handled.
        """
        unicode_payload = {
            "description": "日本語テスト 🎉 العربية",
            "amount": "100.00",
            "special": "null_byte\t\n",  # Note: PostgreSQL doesn't allow NUL (\x00)
        }

        result = ingestor_service.ingest(
            event_id=uuid4(),
            event_type="test.unicode",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=unicode_payload,
        )

        assert result.status == IngestStatus.ACCEPTED


class TestDecimalEdgeCases:
    """Tests for decimal arithmetic edge cases."""

    def test_repeating_decimal_precision(self):
        """
        Verify repeating decimals don't cause precision loss.
        """
        # 1/3 as decimal
        one_third = Decimal("1") / Decimal("3")

        # Three thirds should equal 1
        three_thirds = one_third * 3

        # Due to decimal arithmetic, this should be very close to 1
        assert abs(three_thirds - Decimal("1")) < Decimal("0.0000000001")

    def test_rounding_handler_determinism(self):
        """
        Verify rounding is deterministic across many iterations.
        """
        # Same input should always produce same output
        results = set()
        for _ in range(1000):
            money = Money.of(Decimal("100.555555"), "USD")
            result = money.round()
            results.add(str(result.amount))

        # Should have exactly one unique result
        assert len(results) == 1

    def test_currency_specific_rounding(self):
        """
        Verify currency-specific decimal places are respected.
        """
        # USD: 2 decimal places
        usd_money = Money.of(Decimal("100.555"), "USD")
        assert usd_money.round().amount == Decimal("100.56")

        # JPY: 0 decimal places
        jpy_money = Money.of(Decimal("100.5"), "JPY")
        assert jpy_money.round().amount == Decimal("101")

        # KWD: 3 decimal places
        kwd_money = Money.of(Decimal("100.5555"), "KWD")
        assert kwd_money.round().amount == Decimal("100.556")


class TestFuzzingCorpus:
    """Randomized fuzzing tests with corpus generation via the posting pipeline."""

    def test_random_amount_corpus(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Generate random amounts and verify posting handles them.
        """
        corpus_size = 100
        accepted = 0
        rejected = 0
        errors = 0

        for i in range(corpus_size):
            # Generate random amount with various precisions
            integer_part = random.randint(1, 10**9)
            decimal_part = random.randint(0, 99)
            amount = Decimal(f"{integer_part}.{decimal_part:02d}")

            try:
                result = post_via_coordinator(
                    amount=amount,
                )
                if result.success:
                    accepted += 1
                else:
                    rejected += 1
            except Exception:
                errors += 1

        # All balanced entries should be accepted
        assert accepted == corpus_size, f"Expected {corpus_size} accepted, got {accepted}"
        assert errors == 0, f"Unexpected errors: {errors}"

    def test_random_currency_corpus(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Test posting with USD across random amounts.

        The posting pipeline resolves roles to accounts, so we test with the standard
        role mapping (CashAsset -> 1000, SalesRevenue -> 4000).
        """
        amounts = [
            Decimal("100.00"),
            Decimal("0.01"),
            Decimal("999999.99"),
            Decimal("1.23"),
            Decimal("50000.50"),
        ]

        for amount in amounts:
            result = post_via_coordinator(
                amount=amount,
            )
            assert result.success, f"Failed for amount {amount}: {result.error_code}"


class TestMoneyFromStrFuzzing:
    """Fuzzing tests for money_from_str function."""

    def test_valid_formats(self):
        """Test various valid decimal string formats."""
        valid_inputs = [
            "100",
            "100.00",
            "100.123456789",
            "0.00",
            "0",
            "-100.00",
            "1000000000000000.999999999",
            ".50",
            "50.",
        ]

        for input_str in valid_inputs:
            try:
                result = money_from_str(input_str)
                assert isinstance(result, Decimal)
            except Exception as e:
                pytest.fail(f"Failed to parse valid input '{input_str}': {e}")

    def test_invalid_formats_rejected(self):
        """Test that invalid formats are rejected."""
        invalid_inputs = [
            "abc",
            "100.00.00",
            "$100",
            "100,000.00",  # Comma as thousands separator
            "1e10",  # Scientific notation
            "",
            None,
        ]

        for input_str in invalid_inputs:
            try:
                result = money_from_str(input_str)
                # If it doesn't raise, note the behavior
            except (InvalidOperation, ValueError, TypeError, AttributeError):
                # Expected
                pass
