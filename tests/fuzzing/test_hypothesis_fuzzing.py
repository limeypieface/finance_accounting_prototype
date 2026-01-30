"""
H1-H2: Hypothesis-Based Fuzzing Framework.

Property-based testing using Hypothesis to automatically generate
adversarial inputs and verify invariants hold.

These tests verify that:
1. Random payloads don't crash the system
2. Validation always returns structured errors (not exceptions)
3. Posting invariants hold across all generated inputs
4. Large/malformed payloads are handled gracefully
"""

import pytest
from uuid import uuid4
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from hypothesis import given, strategies as st, settings, assume, HealthCheck
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
        def dictionaries(*args, **kwargs):
            return None

        @staticmethod
        def lists(*args, **kwargs):
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

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.clock import DeterministicClock


# Hypothesis strategies for finance domain

if HYPOTHESIS_AVAILABLE:
    @composite
    def money_amounts(draw):
        """Generate valid money amounts."""
        # Generate decimals that could be money amounts
        amount = draw(st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("999999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ))
        return str(amount)

    @composite
    def currency_codes(draw):
        """Generate currency codes (valid and invalid)."""
        valid_currencies = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
        invalid_currencies = ["XXX", "ABC", "123", "", "TOOLONG"]
        all_currencies = valid_currencies + invalid_currencies
        return draw(st.sampled_from(all_currencies))

    @composite
    def account_codes(draw):
        """Generate account codes (valid and invalid)."""
        valid_accounts = ["1000", "1100", "2000", "4000", "5000"]
        invalid_accounts = ["INVALID", "", "999999", "ABC123"]
        all_accounts = valid_accounts + invalid_accounts
        return draw(st.sampled_from(all_accounts))

    @composite
    def line_sides(draw):
        """Generate line sides (valid and invalid)."""
        valid_sides = ["debit", "credit"]
        invalid_sides = ["DEBIT", "Credit", "both", "", "unknown"]
        all_sides = valid_sides + invalid_sides
        return draw(st.sampled_from(all_sides))

    @composite
    def journal_line(draw, valid_only=False):
        """Generate a journal line dictionary."""
        if valid_only:
            account = draw(st.sampled_from(["1000", "1100", "4000", "5000"]))
            side = draw(st.sampled_from(["debit", "credit"]))
            amount = draw(st.decimals(
                min_value=Decimal("0.01"),
                max_value=Decimal("10000.00"),
                places=2,
                allow_nan=False,
                allow_infinity=False,
            ))
            currency = "USD"
        else:
            account = draw(account_codes())
            side = draw(line_sides())
            # Include potentially problematic amounts
            amount = draw(st.one_of(
                st.decimals(
                    min_value=Decimal("-1000"),
                    max_value=Decimal("1000000"),
                    places=4,
                    allow_nan=False,
                    allow_infinity=False,
                ),
                st.just(Decimal("0")),
                st.just(Decimal("-0.01")),
            ))
            currency = draw(currency_codes())

        return {
            "account_code": account,
            "side": side,
            "amount": str(amount),
            "currency": currency,
        }

    @composite
    def posting_payload(draw, valid_only=False):
        """Generate a complete posting payload."""
        num_lines = draw(st.integers(min_value=0, max_value=10))
        lines = [draw(journal_line(valid_only)) for _ in range(num_lines)]
        return {"lines": lines}


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestPayloadFuzzing:
    """
    H1: Payload fuzzing tests.

    Verify that random payloads don't crash the system.
    """

    @given(payload=st.dictionaries(
        keys=st.text(min_size=0, max_size=50),
        values=st.one_of(
            st.text(max_size=100),
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            st.booleans(),
            st.none(),
        ),
        max_size=20,
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_random_dict_payload_doesnt_crash(
        self,
        payload,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Random dictionary payloads should not crash the system.

        They may fail validation, but should return a proper result.
        """
        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="fuzzer",
                payload=payload,
            )

            # Any status is acceptable as long as we get a result
            assert result.status is not None
            assert isinstance(result.status, PostingStatus)

        except (TypeError, ValueError, InvalidOperation) as e:
            # These are acceptable - invalid input caught at boundary
            pass
        except Exception as e:
            # Unexpected exceptions should fail
            pytest.fail(f"Unexpected exception for payload {payload}: {e}")

    @given(payload=posting_payload())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_generated_payload_returns_structured_result(
        self,
        payload,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Generated posting payloads should return structured results.
        """
        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="fuzzer",
                payload=payload,
            )

            # Result should be structured
            assert result is not None
            assert hasattr(result, 'status')
            assert hasattr(result, 'event_id')

            # If validation failed, should have validation info
            if result.status == PostingStatus.VALIDATION_FAILED:
                assert result.validation is not None or result.message is not None

        except (TypeError, ValueError, InvalidOperation) as e:
            # Acceptable boundary validation
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestAmountFuzzing:
    """
    Tests for amount edge cases.
    """

    @given(amount=st.decimals(
        min_value=Decimal("-1000000"),
        max_value=Decimal("1000000"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ))
    @settings(max_examples=100)
    def test_amount_boundary_handling(
        self,
        amount,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Test handling of various amount values.
        """
        payload = {
            "lines": [
                {"account_code": "1000", "side": "debit", "amount": str(amount), "currency": "USD"},
                {"account_code": "4000", "side": "credit", "amount": str(amount), "currency": "USD"},
            ]
        }

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="fuzzer",
                payload=payload,
            )

            # Negative amounts should fail
            if amount < 0:
                assert result.status != PostingStatus.POSTED, (
                    f"Negative amount {amount} should not be posted"
                )

            # Zero amounts might fail validation
            if amount == 0:
                # System behavior for zero may vary
                pass

        except (TypeError, ValueError, InvalidOperation):
            pass

    @given(amount_str=st.text(max_size=50))
    @settings(max_examples=50)
    def test_malformed_amount_string_handling(
        self,
        amount_str,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Test handling of malformed amount strings.
        """
        payload = {
            "lines": [
                {"account_code": "1000", "side": "debit", "amount": amount_str, "currency": "USD"},
                {"account_code": "4000", "side": "credit", "amount": amount_str, "currency": "USD"},
            ]
        }

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="fuzzer",
                payload=payload,
            )

            # Malformed amounts should not be posted successfully
            # (unless they happen to be valid decimal strings)
            try:
                Decimal(amount_str)
                # If it's a valid decimal, posting might succeed
            except InvalidOperation:
                # Invalid decimal string - should fail
                assert result.status != PostingStatus.POSTED

        except (TypeError, ValueError, InvalidOperation):
            # Expected for malformed input
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestOversizePayloadHandling:
    """
    H2: Oversize payload tests.
    """

    @given(size=st.integers(min_value=100, max_value=1000))
    @settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
    def test_many_lines_payload(
        self,
        size,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Test handling of payloads with many lines.
        """
        lines = []
        for i in range(size):
            lines.append({
                "account_code": "1000",
                "side": "debit" if i % 2 == 0 else "credit",
                "amount": "1.00",
                "currency": "USD",
            })

        payload = {"lines": lines}

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="fuzzer",
                payload=payload,
            )

            # System should handle gracefully (may reject as unbalanced)
            assert result is not None

        except Exception as e:
            # Should not crash with memory errors etc.
            assert not isinstance(e, MemoryError)

    @given(depth=st.integers(min_value=1, max_value=20))
    @settings(max_examples=10)
    def test_deeply_nested_payload(
        self,
        depth,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Test handling of deeply nested payload structures.
        """
        # Build nested structure
        nested = {"value": "innermost"}
        for _ in range(depth):
            nested = {"nested": nested}

        payload = {
            "lines": [
                {"account_code": "1000", "side": "debit", "amount": "100", "currency": "USD"},
                {"account_code": "4000", "side": "credit", "amount": "100", "currency": "USD"},
            ],
            "metadata": nested,
        }

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="fuzzer",
                payload=payload,
            )

            # Should handle nested structures
            assert result is not None

        except RecursionError:
            # If recursion limit hit, that's a valid rejection
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestInvariantProperties:
    """
    Property-based tests for posting invariants.
    """

    @given(payload=posting_payload(valid_only=True))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_idempotency_property(
        self,
        payload,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Property: Same event_id always returns same result.
        """
        assume(len(payload.get("lines", [])) >= 2)

        event_id = uuid4()

        # First attempt
        result1 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="fuzzer",
            payload=payload,
        )

        # Second attempt with same event_id
        result2 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="fuzzer",
            payload=payload,
        )

        # Idempotency property
        if result1.status == PostingStatus.POSTED:
            assert result2.status == PostingStatus.ALREADY_POSTED
            assert result2.journal_entry_id == result1.journal_entry_id

    @given(payload=posting_payload(valid_only=True))
    @settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
    def test_balance_property(
        self,
        payload,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Property: Posted entries are always balanced.
        """
        assume(len(payload.get("lines", [])) >= 2)

        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="fuzzer",
            payload=payload,
        )

        if result.status == PostingStatus.POSTED:
            from finance_kernel.models.journal import JournalEntry
            entry = session.get(JournalEntry, result.journal_entry_id)
            assert entry.is_balanced, "Posted entry must be balanced"
