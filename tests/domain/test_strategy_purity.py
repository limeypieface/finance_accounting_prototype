"""
Tests for strategy purity and input immutability (Rule 2.5).

R2.5 Verifies:
- Strategy output never mutates EventEnvelope
- Strategy output never mutates ReferenceData
- Strategies are pure functions: same input -> same output
"""

import copy
import pytest
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from finance_kernel.domain.bookkeeper import Bookkeeper
from finance_kernel.domain.dtos import (
    EventEnvelope,
    LineSide,
    LineSpec,
    ReferenceData,
)
from finance_kernel.domain.strategy import BasePostingStrategy, StrategyResult
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.values import Currency, Money


class TestR25StrategyInputImmutability:
    """R2.5: Strategy output never mutates EventEnvelope or ReferenceData."""

    def setup_method(self):
        """Clear registry before each test."""
        StrategyRegistry.clear()

    def teardown_method(self):
        """Re-register the generic strategy after each test."""
        import importlib
        import finance_kernel.domain.strategies.generic_strategy as gs
        importlib.reload(gs)

    def _create_test_envelope(self) -> EventEnvelope:
        """Create a test EventEnvelope with nested payload."""
        return EventEnvelope(
            event_id=uuid4(),
            event_type="test.mutation.check",
            occurred_at=datetime.now(timezone.utc),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={
                "amount": "100.00",
                "currency": "USD",
                "debit_account": "1000",
                "credit_account": "4000",
                "nested": {
                    "level1": {
                        "level2": "deep_value"
                    }
                },
                "items": [1, 2, 3],
            },
            payload_hash="abc123",
        )

    def _create_test_reference_data(self) -> ReferenceData:
        """Create test ReferenceData."""
        account_1 = uuid4()
        account_2 = uuid4()
        rounding_account = uuid4()

        return ReferenceData(
            account_ids_by_code={
                "1000": account_1,
                "4000": account_2,
                "9999": rounding_account,
            },
            active_account_codes=frozenset(["1000", "4000", "9999"]),
            valid_currencies=frozenset([Currency("USD"), Currency("EUR")]),
            rounding_account_ids={"USD": rounding_account},
        )

    def test_strategy_cannot_mutate_event_envelope(self):
        """
        Test that a strategy cannot mutate the EventEnvelope passed to it.

        A malicious or buggy strategy might try to modify the event envelope.
        Since EventEnvelope is a frozen dataclass AND the payload is wrapped
        in MappingProxyType, mutation attempts should raise TypeError.

        This test verifies that mutation attempts are blocked.
        """
        mutation_blocked = False

        class MutatingStrategy(BasePostingStrategy):
            """Strategy that attempts to mutate its inputs."""

            @property
            def event_type(self) -> str:
                return "test.mutation.check"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(
                self, event: EventEnvelope, reference_data: ReferenceData
            ) -> list[LineSpec]:
                nonlocal mutation_blocked
                # Attempt to mutate the payload (should raise TypeError)
                try:
                    event.payload["MUTATED"] = True
                except TypeError:
                    # Mutation blocked - this is expected
                    mutation_blocked = True

                # Return valid lines so the strategy "succeeds"
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                ]

        StrategyRegistry.register(MutatingStrategy())

        bookkeeper = Bookkeeper()
        event = self._create_test_envelope()
        ref_data = self._create_test_reference_data()

        # Store expected values before running
        original_event_id = event.event_id
        original_event_type = event.event_type
        expected_keys = set(event.payload.keys())

        # Run the potentially mutating strategy
        result = bookkeeper.propose(event, ref_data)

        # The strategy should have produced a valid result
        assert result.is_valid, f"Strategy failed: {result.validation.errors}"

        # Verify mutation was blocked
        assert mutation_blocked, "Mutation should have been blocked by MappingProxyType"

        # Verify EventEnvelope attributes are unchanged
        assert event.event_id == original_event_id
        assert event.event_type == original_event_type

        # Verify payload was not mutated
        assert "MUTATED" not in event.payload, "Payload should not contain MUTATED key"
        assert set(event.payload.keys()) == expected_keys, "Payload keys changed"

        # Verify nested structures are also protected (nested dicts become MappingProxyType too)
        nested = event.payload.get("nested")
        if nested:
            try:
                nested["INJECTED"] = "evil"
                assert False, "Nested dict mutation should have raised TypeError"
            except TypeError:
                pass  # Expected - nested dicts are also frozen

        # Verify lists become tuples (immutable)
        items = event.payload.get("items")
        assert isinstance(items, tuple), f"Items should be a tuple, got {type(items)}"

    def test_strategy_cannot_mutate_reference_data(self):
        """
        Test that a strategy cannot mutate the ReferenceData passed to it.

        ReferenceData contains account lookups and currency info that strategies
        use for validation. A malicious strategy should not be able to modify
        these lookups to affect other transactions.
        """

        class RefDataMutatingStrategy(BasePostingStrategy):
            """Strategy that attempts to mutate reference data."""

            @property
            def event_type(self) -> str:
                return "test.mutation.refdata"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(
                self, event: EventEnvelope, reference_data: ReferenceData
            ) -> list[LineSpec]:
                # Attempt to mutate account_ids_by_code
                try:
                    reference_data.account_ids_by_code["HACKED"] = uuid4()
                except (TypeError, AttributeError):
                    pass

                # Attempt to mutate rounding_account_ids
                try:
                    reference_data.rounding_account_ids["HACKED"] = uuid4()
                except (TypeError, AttributeError):
                    pass

                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                ]

        StrategyRegistry.register(RefDataMutatingStrategy())

        bookkeeper = Bookkeeper()
        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.mutation.refdata",
            occurred_at=datetime.now(timezone.utc),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="abc",
        )
        ref_data = self._create_test_reference_data()

        # Store original state
        original_account_codes = set(ref_data.account_ids_by_code.keys())
        original_rounding_currencies = set(ref_data.rounding_account_ids.keys())

        # Run the potentially mutating strategy
        result = bookkeeper.propose(event, ref_data)
        assert result.is_valid

        # Verify account_ids_by_code was not mutated
        assert "HACKED" not in ref_data.account_ids_by_code, (
            "account_ids_by_code was mutated!"
        )
        assert set(ref_data.account_ids_by_code.keys()) == original_account_codes

        # Verify rounding_account_ids was not mutated
        assert "HACKED" not in ref_data.rounding_account_ids, (
            "rounding_account_ids was mutated!"
        )
        assert set(ref_data.rounding_account_ids.keys()) == original_rounding_currencies

    def test_same_input_produces_identical_output(self):
        """
        Test that the same EventEnvelope and ReferenceData always produce
        identical output (strategy determinism).

        This verifies R2: Strategies are pure functions.
        """

        class DeterministicStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.deterministic"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(
                self, event: EventEnvelope, reference_data: ReferenceData
            ) -> list[LineSpec]:
                # Use payload to generate amount deterministically
                amount = Decimal(event.payload.get("amount", "100.00"))
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(amount, "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(amount, "USD"),
                    ),
                ]

        StrategyRegistry.register(DeterministicStrategy())

        bookkeeper = Bookkeeper()

        # Create fixed inputs
        fixed_event_id = uuid4()
        fixed_actor_id = uuid4()
        fixed_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        def create_event():
            return EventEnvelope(
                event_id=fixed_event_id,
                event_type="test.deterministic",
                occurred_at=fixed_time,
                effective_date=date(2024, 6, 15),
                actor_id=fixed_actor_id,
                producer="test",
                payload={"amount": "250.00"},
                payload_hash="fixed_hash",
            )

        ref_data = self._create_test_reference_data()

        # Run multiple times
        results = [bookkeeper.propose(create_event(), ref_data) for _ in range(5)]

        # All should be valid
        for r in results:
            assert r.is_valid

        # Compare all outputs
        first_entry = results[0].proposed_entry
        for i, r in enumerate(results[1:], start=2):
            entry = r.proposed_entry
            assert entry is not None
            assert first_entry is not None

            # Lines should be identical
            assert len(entry.lines) == len(first_entry.lines)
            for j, (line1, line2) in enumerate(zip(first_entry.lines, entry.lines)):
                assert line1.account_id == line2.account_id, f"Run {i}, line {j} account mismatch"
                assert line1.side == line2.side, f"Run {i}, line {j} side mismatch"
                assert line1.amount == line2.amount, f"Run {i}, line {j} amount mismatch"
                assert line1.currency == line2.currency, f"Run {i}, line {j} currency mismatch"

    def test_frozen_dataclass_prevents_direct_mutation(self):
        """Test that frozen dataclasses prevent direct attribute mutation."""
        event = self._create_test_envelope()

        # Attempt to mutate frozen dataclass attribute should raise
        with pytest.raises(AttributeError):
            event.event_type = "hacked.event"

        with pytest.raises(AttributeError):
            event.actor_id = uuid4()

        with pytest.raises(AttributeError):
            event.payload = {"hacked": True}

    def test_reference_data_frozen_prevents_direct_mutation(self):
        """Test that ReferenceData is frozen and prevents direct mutation."""
        ref_data = self._create_test_reference_data()

        # Attempt to mutate should raise
        with pytest.raises(AttributeError):
            ref_data.account_ids_by_code = {}

        with pytest.raises(AttributeError):
            ref_data.valid_currencies = frozenset()

    def test_strategy_result_does_not_leak_mutable_references(self):
        """
        Test that the StrategyResult does not contain references to mutable
        input data that could be modified after the fact.
        """

        class LeakCheckStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.leak.check"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(
                self, event: EventEnvelope, reference_data: ReferenceData
            ) -> list[LineSpec]:
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                ]

        StrategyRegistry.register(LeakCheckStrategy())

        bookkeeper = Bookkeeper()
        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.leak.check",
            occurred_at=datetime.now(timezone.utc),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={"mutable_list": [1, 2, 3]},
            payload_hash="abc",
        )
        ref_data = self._create_test_reference_data()

        result = bookkeeper.propose(event, ref_data)
        assert result.is_valid

        # The proposed entry contains the event envelope
        proposed = result.proposed_entry
        assert proposed is not None

        # Verify the proposed entry's event_envelope is the same object
        # (frozen, so immutable) or a copy
        assert proposed.event_envelope.event_id == event.event_id

        # The lines should be a tuple (immutable)
        assert isinstance(proposed.lines, tuple)

        # Each ProposedLine should be frozen
        for line in proposed.lines:
            with pytest.raises(AttributeError):
                line.amount = Decimal("9999")


class TestStrategyPurityWithEdgeCases:
    """Additional edge case tests for strategy purity."""

    def setup_method(self):
        StrategyRegistry.clear()

    def teardown_method(self):
        import importlib
        import finance_kernel.domain.strategies.generic_strategy as gs
        importlib.reload(gs)

    def test_strategy_with_exception_does_not_corrupt_inputs(self):
        """
        Test that if a strategy raises an exception, the inputs are not
        left in a corrupted state.

        Since payloads are now wrapped in MappingProxyType, mutation attempts
        will raise TypeError rather than succeeding.
        """
        mutation_blocked = False

        class CrashingStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.crash"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(
                self, event: EventEnvelope, reference_data: ReferenceData
            ) -> list[LineSpec]:
                nonlocal mutation_blocked
                # Attempt mutation before crashing - should be blocked
                try:
                    event.payload["BEFORE_CRASH"] = True
                except TypeError:
                    mutation_blocked = True

                # Crash
                raise RuntimeError("Intentional crash")

        StrategyRegistry.register(CrashingStrategy())

        bookkeeper = Bookkeeper()
        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.crash",
            occurred_at=datetime.now(timezone.utc),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={"original": "data"},
            payload_hash="abc",
        )
        ref_data = ReferenceData(
            account_ids_by_code={"1000": uuid4()},
            active_account_codes=frozenset(["1000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={},
        )

        expected_keys = {"original"}

        # Strategy will fail due to exception
        result = bookkeeper.propose(event, ref_data)

        # Result should indicate failure
        assert not result.is_valid
        assert any(e.code == "COMPUTATION_ERROR" for e in result.validation.errors)

        # Mutation should have been blocked
        assert mutation_blocked, "Mutation should have been blocked by MappingProxyType"

        # Inputs should not be corrupted
        assert "BEFORE_CRASH" not in event.payload
        assert set(event.payload.keys()) == expected_keys

    def test_concurrent_strategy_calls_do_not_interfere(self):
        """
        Test that multiple concurrent calls to the same strategy
        do not interfere with each other's inputs.
        """
        import threading
        import time

        class SlowStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.concurrent"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(
                self, event: EventEnvelope, reference_data: ReferenceData
            ) -> list[LineSpec]:
                # Simulate slow computation
                time.sleep(0.01)

                # Read amount from payload
                amount = Decimal(event.payload.get("amount", "100"))

                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(amount, "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(amount, "USD"),
                    ),
                ]

        StrategyRegistry.register(SlowStrategy())

        bookkeeper = Bookkeeper()
        ref_data = ReferenceData(
            account_ids_by_code={"1000": uuid4(), "4000": uuid4()},
            active_account_codes=frozenset(["1000", "4000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={},
        )

        results = {}
        errors = []

        def run_proposal(thread_id: int, amount: str):
            try:
                event = EventEnvelope(
                    event_id=uuid4(),
                    event_type="test.concurrent",
                    occurred_at=datetime.now(timezone.utc),
                    effective_date=date.today(),
                    actor_id=uuid4(),
                    producer="test",
                    payload={"amount": amount},
                    payload_hash=f"hash_{thread_id}",
                )
                result = bookkeeper.propose(event, ref_data)
                results[thread_id] = (amount, result)
            except Exception as e:
                errors.append((thread_id, e))

        threads = []
        amounts = ["100", "200", "300", "400", "500"]

        for i, amt in enumerate(amounts):
            t = threading.Thread(target=run_proposal, args=(i, amt))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        # No errors should have occurred
        assert not errors, f"Thread errors: {errors}"

        # Each thread should have gotten the correct amount in its result
        for thread_id, (expected_amount, result) in results.items():
            assert result.is_valid
            entry = result.proposed_entry
            assert entry is not None
            # Find the debit line and verify amount
            debit_line = next(l for l in entry.lines if l.side == LineSide.DEBIT)
            assert debit_line.amount == Decimal(expected_amount), (
                f"Thread {thread_id} expected {expected_amount}, "
                f"got {debit_line.amount}"
            )
