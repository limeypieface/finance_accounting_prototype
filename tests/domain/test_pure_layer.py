"""
Tests for the pure domain layer.

These tests verify:
- DTOs are immutable
- Clock abstraction works correctly
- Currency registry validates correctly
- Posting strategies are pure and deterministic
- Bookkeeper transforms events correctly
"""

from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.bookkeeper import Bookkeeper, BookkeeperResult
from finance_kernel.domain.clock import (
    Clock,
    DeterministicClock,
    SequentialClock,
    SystemClock,
)
from finance_kernel.domain.currency import CurrencyInfo, CurrencyRegistry
from finance_kernel.domain.dtos import (
    EventEnvelope,
    LineSide,
    LineSpec,
    ProposedJournalEntry,
    ProposedLine,
    ReferenceData,
    ValidationError,
    ValidationResult,
)
from finance_kernel.domain.strategy import BasePostingStrategy, StrategyResult
from finance_kernel.domain.strategy_registry import (
    StrategyNotFoundError,
    StrategyRegistry,
)
from finance_kernel.domain.values import Currency, Money


class TestDTOImmutability:
    """Tests that DTOs are truly immutable."""

    def test_line_spec_is_frozen(self):
        """LineSpec should be immutable."""
        spec = LineSpec(
            account_code="1000",
            side=LineSide.DEBIT,
            money=Money.of(Decimal("100.00"), "USD"),
        )

        with pytest.raises(AttributeError):
            spec.money = Money.of(Decimal("200.00"), "USD")

    def test_event_envelope_is_frozen(self):
        """EventEnvelope should be immutable."""
        envelope = EventEnvelope(
            event_id=uuid4(),
            event_type="test.event",
            occurred_at=datetime.now(UTC),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={"amount": "100.00"},
            payload_hash="abc123",
        )

        with pytest.raises(AttributeError):
            envelope.event_type = "modified.event"

    def test_validation_result_is_frozen(self):
        """ValidationResult should be immutable."""
        result = ValidationResult.success()

        with pytest.raises(AttributeError):
            result.is_valid = False


class TestDeterministicClock:
    """Tests for the deterministic clock."""

    def test_deterministic_clock_returns_fixed_time(self):
        """DeterministicClock should return the same time."""
        fixed_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        clock = DeterministicClock(fixed_time)

        assert clock.now() == fixed_time
        assert clock.now() == fixed_time  # Same time again

    def test_deterministic_clock_can_advance(self):
        """DeterministicClock should advance when requested."""
        fixed_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        clock = DeterministicClock(fixed_time)

        clock.advance(60)  # Advance 60 seconds
        assert clock.now() == datetime(2024, 6, 15, 12, 1, 0, tzinfo=UTC)

    def test_deterministic_clock_tick(self):
        """DeterministicClock.tick() should advance by 1 second."""
        fixed_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        clock = DeterministicClock(fixed_time)

        time1 = clock.tick()
        time2 = clock.tick()

        assert time2 > time1
        assert (time2 - time1).seconds == 1

    def test_sequential_clock(self):
        """SequentialClock should return times in sequence."""
        times = [
            datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC),
            datetime(2024, 6, 15, 12, 1, 0, tzinfo=UTC),
            datetime(2024, 6, 15, 12, 2, 0, tzinfo=UTC),
        ]
        clock = SequentialClock(times)

        assert clock.now() == times[0]
        assert clock.now() == times[1]
        assert clock.now() == times[2]
        # After exhaustion, returns last time
        assert clock.now() == times[2]


class TestCurrencyRegistry:
    """Tests for the currency registry."""

    def test_valid_currencies_are_valid(self):
        """Common currencies should be valid."""
        assert CurrencyRegistry.is_valid("USD")
        assert CurrencyRegistry.is_valid("EUR")
        assert CurrencyRegistry.is_valid("GBP")
        assert CurrencyRegistry.is_valid("JPY")

    def test_invalid_currencies_are_invalid(self):
        """Invalid codes should be rejected."""
        assert not CurrencyRegistry.is_valid("INVALID")
        assert not CurrencyRegistry.is_valid("XXY")
        assert not CurrencyRegistry.is_valid("")
        assert not CurrencyRegistry.is_valid(None)

    def test_currency_decimal_places(self):
        """Currency decimal places should be correct."""
        assert CurrencyRegistry.get_decimal_places("USD") == 2
        assert CurrencyRegistry.get_decimal_places("JPY") == 0
        assert CurrencyRegistry.get_decimal_places("KWD") == 3
        assert CurrencyRegistry.get_decimal_places("CLF") == 4

    def test_currency_rounding_tolerance(self):
        """Rounding tolerance should be derived from decimal places."""
        assert CurrencyRegistry.get_rounding_tolerance("USD") == Decimal("0.01")
        assert CurrencyRegistry.get_rounding_tolerance("JPY") == Decimal("1")
        assert CurrencyRegistry.get_rounding_tolerance("KWD") == Decimal("0.001")

    def test_currency_info(self):
        """CurrencyInfo should contain correct information."""
        info = CurrencyRegistry.get_info("USD")
        assert info is not None
        assert info.code == "USD"
        assert info.decimal_places == 2
        assert info.name == "US Dollar"

    def test_validate_normalizes_case(self):
        """validate() should normalize to uppercase."""
        assert CurrencyRegistry.validate("usd") == "USD"
        assert CurrencyRegistry.validate(" Eur ") == "EUR"

    def test_validate_raises_on_invalid(self):
        """validate() should raise ValueError on invalid code."""
        with pytest.raises(ValueError):
            CurrencyRegistry.validate("INVALID")


class TestLineSpec:
    """Tests for LineSpec DTO."""

    def test_line_spec_creation(self):
        """LineSpec should be creatable with valid data."""
        spec = LineSpec(
            account_code="1000",
            side=LineSide.DEBIT,
            money=Money.of(Decimal("100.00"), "USD"),
        )

        assert spec.account_code == "1000"
        assert spec.side == LineSide.DEBIT
        assert spec.amount == Decimal("100.00")  # backward compat property
        assert spec.currency == "USD"  # backward compat property
        assert spec.money.amount == Decimal("100.00")
        assert spec.money.currency.code == "USD"

    def test_line_spec_rejects_negative_amount(self):
        """LineSpec should reject negative amounts."""
        with pytest.raises(ValueError):
            LineSpec(
                account_code="1000",
                side=LineSide.DEBIT,
                money=Money.of(Decimal("-100.00"), "USD"),
            )

    def test_line_spec_create_factory(self):
        """LineSpec.create() should work as backward-compat factory."""
        spec = LineSpec.create(
            account_code="1000",
            side=LineSide.DEBIT,
            amount=Decimal("100.00"),
            currency="USD",
        )

        assert spec.account_code == "1000"
        assert spec.amount == Decimal("100.00")
        assert spec.currency == "USD"


class TestProposedJournalEntry:
    """Tests for ProposedJournalEntry DTO."""

    def test_proposed_entry_balance_calculation(self):
        """ProposedJournalEntry should calculate balances correctly."""
        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.event",
            occurred_at=datetime.now(UTC),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="abc",
        )

        lines = (
            ProposedLine(
                account_id=uuid4(),
                account_code="1000",
                side=LineSide.DEBIT,
                money=Money.of(Decimal("100.00"), "USD"),
            ),
            ProposedLine(
                account_id=uuid4(),
                account_code="4000",
                side=LineSide.CREDIT,
                money=Money.of(Decimal("100.00"), "USD"),
            ),
        )

        entry = ProposedJournalEntry(
            event_envelope=event,
            lines=lines,
        )

        assert entry.total_debits("USD") == Decimal("100.00")
        assert entry.total_credits("USD") == Decimal("100.00")
        assert entry.is_balanced("USD")
        assert entry.imbalance("USD") == Decimal("0")

    def test_proposed_entry_detects_imbalance(self):
        """ProposedJournalEntry should detect imbalanced entries."""
        event = EventEnvelope(
            event_id=uuid4(),
            event_type="test.event",
            occurred_at=datetime.now(UTC),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="abc",
        )

        lines = (
            ProposedLine(
                account_id=uuid4(),
                account_code="1000",
                side=LineSide.DEBIT,
                money=Money.of(Decimal("100.00"), "USD"),
            ),
            ProposedLine(
                account_id=uuid4(),
                account_code="4000",
                side=LineSide.CREDIT,
                money=Money.of(Decimal("90.00"), "USD"),  # Imbalanced
            ),
        )

        entry = ProposedJournalEntry(
            event_envelope=event,
            lines=lines,
        )

        assert not entry.is_balanced("USD")
        assert entry.imbalance("USD") == Decimal("10.00")


class TestStrategyRegistry:
    """Tests for the strategy registry."""

    def setup_method(self):
        """Clear registry before each test."""
        StrategyRegistry.clear()

    def teardown_method(self):
        """Re-register the generic strategy after each test."""
        # Re-import to re-register the generic.posting strategy
        import importlib

        import finance_kernel.domain.strategies.generic_strategy as gs
        importlib.reload(gs)

    def test_register_and_get_strategy(self):
        """Should be able to register and retrieve a strategy."""

        class TestStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.strategy"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return []

        strategy = TestStrategy()
        StrategyRegistry.register(strategy)

        retrieved = StrategyRegistry.get("test.strategy")
        assert retrieved is strategy

    def test_get_nonexistent_raises(self):
        """Getting a nonexistent strategy should raise."""
        with pytest.raises(StrategyNotFoundError):
            StrategyRegistry.get("nonexistent.type")

    def test_multiple_versions(self):
        """Should support multiple versions of a strategy."""

        class TestStrategyV1(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.versioned"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return []

        class TestStrategyV2(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.versioned"

            @property
            def version(self) -> int:
                return 2

            def _compute_line_specs(self, event, reference_data):
                return []

        v1 = TestStrategyV1()
        v2 = TestStrategyV2()
        StrategyRegistry.register(v1)
        StrategyRegistry.register(v2)

        # Get specific versions
        assert StrategyRegistry.get("test.versioned", version=1) is v1
        assert StrategyRegistry.get("test.versioned", version=2) is v2

        # Get latest (should be v2)
        assert StrategyRegistry.get("test.versioned") is v2


class TestBookkeeper:
    """Tests for the Bookkeeper."""

    def setup_method(self):
        """Clear registry before each test."""
        StrategyRegistry.clear()

    def teardown_method(self):
        """Re-register the generic strategy after each test."""
        # Re-import to re-register the generic.posting strategy
        import importlib

        import finance_kernel.domain.strategies.generic_strategy as gs
        importlib.reload(gs)

    def test_bookkeeper_requires_strategy(self):
        """Bookkeeper should fail without a registered strategy."""
        bookkeeper = Bookkeeper()

        event = EventEnvelope(
            event_id=uuid4(),
            event_type="unknown.event",
            occurred_at=datetime.now(UTC),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="abc",
        )

        reference_data = ReferenceData(
            account_ids_by_code={},
            active_account_codes=frozenset(),
            valid_currencies=frozenset([Currency("USD")]),  # R4: Currency value objects
            rounding_account_ids={},
        )

        result = bookkeeper.propose(event, reference_data)

        assert not result.is_valid
        assert any(
            e.code == "STRATEGY_NOT_FOUND"
            for e in result.validation.errors
        )

    def test_bookkeeper_can_handle_check(self):
        """Bookkeeper.can_handle() should check strategy availability."""
        bookkeeper = Bookkeeper()

        assert not bookkeeper.can_handle("unknown.event")

        # Register a strategy
        class TestStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "known.event"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return []

        StrategyRegistry.register(TestStrategy())

        assert bookkeeper.can_handle("known.event")
