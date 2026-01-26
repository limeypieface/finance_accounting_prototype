"""
Balance validation tests for the posting engine.

Verifies:
- Unbalanced entries rejected
- Multi-currency entries balanced per currency
- Rounding line handling
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
from finance_kernel.domain.values import Money


def _make_strategy(event_type: str, line_specs_fn):
    """Factory to create strategies with proper instance-based properties."""

    class DynamicStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str, version: int = 1):
            self._event_type = evt_type
            self._version = version

        @property
        def event_type(self) -> str:
            return self._event_type

        @property
        def version(self) -> int:
            return self._version

        def _compute_line_specs(
            self, event: EventEnvelope, ref: ReferenceData
        ) -> tuple[LineSpec, ...]:
            return line_specs_fn(event, ref)

    return DynamicStrategy(event_type)


class TestBalanceValidation:
    """Tests for double-entry balance validation."""

    def test_balanced_entry_posts(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that a balanced entry posts successfully."""

        def balanced_lines(event, ref):
            return (
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
            )

        StrategyRegistry.register(_make_strategy("test.balanced", balanced_lines))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="test.balanced",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop("test.balanced", None)

    def test_unbalanced_entry_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that an unbalanced entry is rejected."""

        def unbalanced_lines(event, ref):
            return (
                LineSpec(
                    account_code="1000",
                    side=LineSide.DEBIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
                LineSpec(
                    account_code="4000",
                    side=LineSide.CREDIT,
                    money=Money.of(Decimal("90.00"), "USD"),  # Unbalanced!
                ),
            )

        StrategyRegistry.register(_make_strategy("test.unbalanced", unbalanced_lines))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="test.unbalanced",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            # Should fail validation
            assert result.status == PostingStatus.VALIDATION_FAILED
            assert result.validation is not None
            assert not result.validation.is_valid
        finally:
            StrategyRegistry._strategies.pop("test.unbalanced", None)

    def test_multi_currency_balanced_per_currency(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that multi-currency entries must balance per currency."""

        def multicurrency_lines(event, ref):
            return (
                # USD balanced
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
                # EUR balanced
                LineSpec(
                    account_code="1100",
                    side=LineSide.DEBIT,
                    money=Money.of(Decimal("85.00"), "EUR"),
                ),
                LineSpec(
                    account_code="4000",
                    side=LineSide.CREDIT,
                    money=Money.of(Decimal("85.00"), "EUR"),
                ),
            )

        StrategyRegistry.register(_make_strategy("test.multicurrency", multicurrency_lines))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="test.multicurrency",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop("test.multicurrency", None)

    def test_multiple_debits_one_credit(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test entry with multiple debits and one credit."""

        def multidebit_lines(event, ref):
            return (
                LineSpec(
                    account_code="5000",
                    side=LineSide.DEBIT,
                    money=Money.of(Decimal("50.00"), "USD"),
                ),
                LineSpec(
                    account_code="1200",
                    side=LineSide.DEBIT,
                    money=Money.of(Decimal("50.00"), "USD"),
                ),
                LineSpec(
                    account_code="2000",
                    side=LineSide.CREDIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
            )

        StrategyRegistry.register(_make_strategy("test.multidebit", multidebit_lines))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="test.multidebit",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop("test.multidebit", None)

    def test_one_debit_multiple_credits(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test entry with one debit and multiple credits."""

        def multicredit_lines(event, ref):
            return (
                LineSpec(
                    account_code="1000",
                    side=LineSide.DEBIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
                LineSpec(
                    account_code="4000",
                    side=LineSide.CREDIT,
                    money=Money.of(Decimal("80.00"), "USD"),
                ),
                LineSpec(
                    account_code="2000",
                    side=LineSide.CREDIT,
                    money=Money.of(Decimal("20.00"), "USD"),
                ),
            )

        StrategyRegistry.register(_make_strategy("test.multicredit", multicredit_lines))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="test.multicredit",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop("test.multicredit", None)


class TestRoundingLineHandling:
    """Tests for rounding line handling."""

    def test_small_imbalance_rounded(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        journal_selector,
    ):
        """Test that small imbalances are corrected with rounding lines."""

        def rounding_lines(event, ref):
            # Create slightly unbalanced lines (simulating conversion remainder)
            return (
                LineSpec(
                    account_code="1000",
                    side=LineSide.DEBIT,
                    money=Money.of(Decimal("100.003"), "USD"),  # Slight excess
                ),
                LineSpec(
                    account_code="4000",
                    side=LineSide.CREDIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
            )

        StrategyRegistry.register(_make_strategy("test.rounding", rounding_lines))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="test.rounding",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED

            # Check that a rounding line was added
            entry = journal_selector.get_entry(result.journal_entry_id)
            assert entry is not None

            # Should have 3 lines (2 original + 1 rounding)
            assert len(entry.lines) == 3

            # Find the rounding line
            rounding_lines = [l for l in entry.lines if l.is_rounding]
            assert len(rounding_lines) == 1

            # Verify entry is now balanced
            assert entry.is_balanced
        finally:
            StrategyRegistry._strategies.pop("test.rounding", None)
