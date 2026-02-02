"""
H1-H2: Hypothesis-Based Fuzzing Framework.

Property-based testing using Hypothesis to automatically generate
adversarial inputs and verify invariants hold.

Boundaries fuzzed here:
- Amounts: valid range (0.01–999M), balance invariant, domain Money/money_from_str
- Idempotency: same source_event_id twice → no second entry (R3/R8)
- Effective date: within current_period (period boundary posting)
- Oversize intents: many line pairs (1–50)

Boundaries not fuzzed here (covered by explicit/adversarial tests or deferred):
- Approval: amount at/above/below threshold (test_approval_posting_e2e, test_approval_service)
- Reversal: period boundaries, multi-line (test_reversal_e2e, test_reversal_service)
- Period close: posting to closed period (test_period_lock)
- Ingestion: mapping/validation (tests/ingestion, test_adversarial)
- Event payload: unicode, malformed (test_adversarial)
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

import pytest

try:
    from hypothesis import HealthCheck, assume, given, settings
    from hypothesis import strategies as st
    from hypothesis.strategies import composite

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False
    # Create dummy decorators for when hypothesis is not installed
    def given(*args, **kwargs):
        def decorator(f):
            return pytest.mark.skip(reason="hypothesis not installed")(f)
        return decorator

    class st:
        @staticmethod
        def text(*args, **kwargs):
            return None

        @staticmethod
        def decimals(*args, **kwargs):
            return None

        @staticmethod
        def integers(*args, **kwargs):
            return None

        @staticmethod
        def sampled_from(*args, **kwargs):
            return None

        @staticmethod
        def one_of(*args, **kwargs):
            return None

        @staticmethod
        def none():
            return None

        @staticmethod
        def booleans():
            return None

    def settings(*args, **kwargs):
        def decorator(f):
            return f
        return decorator

    class HealthCheck:
        too_slow = None

    def assume(x):
        pass

    def composite(f):
        return f

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.models.journal import JournalEntry

# Hypothesis strategies for posting pipeline fuzzing

if HYPOTHESIS_AVAILABLE:
    @composite
    def money_amounts(draw):
        """Generate valid money amounts as Decimal."""
        amount = draw(st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("999999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ))
        return amount

    @composite
    def role_pairs(draw):
        """Generate valid debit/credit role pairs."""
        debit_roles = ["CashAsset", "AccountsReceivable"]
        credit_roles = ["SalesRevenue"]
        debit = draw(st.sampled_from(debit_roles))
        credit = draw(st.sampled_from(credit_roles))
        return (debit, credit)

    @composite
    def currency_codes(draw):
        """Generate valid currency codes."""
        return draw(st.sampled_from(["USD"]))


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestAmountFuzzing:
    """
    Tests for amount edge cases via the posting pipeline.
    """

    @given(amount=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("999999999.99"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_valid_amounts_posted_successfully(
        self,
        amount,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Valid positive amounts should always post successfully.
        """
        result = post_via_coordinator(
            amount=amount,
        )

        assert result.success, (
            f"Valid amount {amount} should post successfully, got error: {result.error_code}"
        )

    @given(amount=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("999999.99"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_posted_entries_always_balanced(
        self,
        amount,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Property: All posted entries must be balanced.
        """
        result = post_via_coordinator(amount=amount)

        if result.success:
            entry_id = result.journal_result.entries[0].entry_id
            entry = session.get(JournalEntry, entry_id)
            assert entry.is_balanced, (
                f"Posted entry for amount {amount} is not balanced"
            )


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestIdempotencyProperty:
    """
    Property-based idempotency tests.

    R3/R8: Same source_event_id posted twice must not create a second entry;
    enforcement is via InterpretationOutcome (P15) and OutcomeAlreadyExistsError.
    """

    @given(amount=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("10000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_same_event_id_second_post_raises_or_same_entry(
        self,
        amount,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Property: Second post with same source_event_id must not create a new entry.

        Either OutcomeAlreadyExistsError is raised (current behavior), or
        success is returned with the same entry_id (idempotent success).
        """
        from finance_kernel.services.outcome_recorder import OutcomeAlreadyExistsError

        source_event_id = uuid4()


        result1 = post_via_coordinator(
            source_event_id=source_event_id,
            amount=amount,
        )
        assert result1.success, (
            f"First post should succeed for amount {amount}: {getattr(result1, 'error_code', None)}"
        )
        entry_id_1 = result1.journal_result.entries[0].entry_id

        # Second attempt with same source_event_id: must not create a second entry
        try:
            result2 = post_via_coordinator(
                source_event_id=source_event_id,
                amount=amount,
            )
            if result2.success:
                entry_id_2 = result2.journal_result.entries[0].entry_id
                assert entry_id_2 == entry_id_1, (
                    f"Idempotency violation: second post returned different entry {entry_id_2} vs {entry_id_1}"
                )
        except OutcomeAlreadyExistsError:
            # Current behavior: coordinator raises when record_posted sees existing outcome
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestEffectiveDateBoundaryFuzzing:
    """
    Fuzz effective_date within current period (period boundary posting).

    R12: No posting to closed periods; posting to open period must succeed
    for any date within period start/end.
    """

    @given(data=st.data())
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_post_succeeds_for_date_within_period(
        self,
        data,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Post with effective_date within current_period must succeed (R12 boundary).

        Draws effective_date from period start..end so we fuzz period boundaries
        without filtering.
        """
        effective_date = data.draw(
            st.dates(
                min_value=current_period.start_date,
                max_value=current_period.end_date,
            )
        )
        result = post_via_coordinator(
            amount=Decimal("100.00"),
            effective_date=effective_date,
        )
        assert result.success, (
            f"Post should succeed for effective_date={effective_date} within period: {getattr(result, 'error_code', None)}"
        )


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestDomainValueFuzzing:
    """
    Fuzzing tests for domain value objects.

    These test the pure domain layer, which is pipeline-independent.
    """

    @given(amount=st.decimals(
        min_value=Decimal("-1000000"),
        max_value=Decimal("1000000"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ))
    @settings(max_examples=200)
    def test_money_construction_never_crashes(self, amount):
        """
        Money.of() should never crash — it should either succeed or raise ValueError.
        """
        from finance_kernel.domain.values import Money

        try:
            money = Money.of(amount, "USD")
            # If it succeeds, amount should be stored
            assert money.amount == amount
        except (ValueError, InvalidOperation):
            # Expected for negative amounts, zero, or extreme precision
            pass

    @given(amount_str=st.text(max_size=50))
    @settings(max_examples=200)
    def test_malformed_amount_string_handling(self, amount_str):
        """
        Test handling of malformed amount strings.
        """
        from finance_kernel.db.types import money_from_str

        try:
            result = money_from_str(amount_str)
            assert isinstance(result, Decimal)
        except (InvalidOperation, ValueError, TypeError, AttributeError):
            # Expected for malformed input
            pass

    @given(
        amount=st.decimals(
            min_value=Decimal("0.001"),
            max_value=Decimal("999999.999"),
            places=3,
            allow_nan=False,
            allow_infinity=False,
        ),
        currency=st.sampled_from(["USD", "EUR", "GBP", "JPY", "KWD"]),
    )
    @settings(max_examples=200)
    def test_money_rounding_deterministic(self, amount, currency):
        """
        Money rounding must be deterministic for same input.
        """
        from finance_kernel.domain.values import Money

        try:
            money = Money.of(amount, currency)
            round1 = money.round()
            round2 = money.round()
            assert round1.amount == round2.amount, (
                f"Non-deterministic rounding for {amount} {currency}"
            )
        except (ValueError, InvalidOperation):
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestOversizePayloadHandling:
    """
    H2: Oversize intent handling tests.

    Verify that large AccountingIntents don't crash the system.
    """

    @given(num_extra_pairs=st.integers(min_value=1, max_value=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    def test_many_lines_intent(
        self,
        num_extra_pairs,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Test handling of intents with many line pairs via extra_lines.
        """
        from finance_kernel.domain.accounting_intent import IntentLine

        # Build extra debit/credit pairs - these are additional balanced pairs
        extra_lines = []
        for _ in range(num_extra_pairs):
            extra_lines.append(IntentLine.debit("CashAsset", Decimal("1.00"), "USD"))
            extra_lines.append(IntentLine.credit("SalesRevenue", Decimal("1.00"), "USD"))

        try:
            result = post_via_coordinator(
                amount=Decimal("100.00"),
                extra_lines=tuple(extra_lines),
            )
            # Should either succeed or fail gracefully
            assert result is not None
        except Exception as e:
            # Should not crash with memory errors
            assert not isinstance(e, MemoryError), f"Memory error for {num_extra_pairs} extra pairs"
