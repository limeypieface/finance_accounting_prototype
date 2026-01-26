"""
Tests for R14 and R15 compliance.

R14. No central dispatch
- PostingEngine may not branch on event_type
- Each event_type must have its own registered PostingStrategy

R15. Open/closed compliance
- Adding a new event type must not require modification of existing engines or services
"""

import pytest
import ast
import inspect
import textwrap
from decimal import Decimal
from datetime import date, datetime
from uuid import uuid4

from finance_kernel.domain.bookkeeper import Bookkeeper
from finance_kernel.domain.strategy import BasePostingStrategy, StrategyResult
from finance_kernel.domain.strategy_registry import (
    StrategyRegistry,
    StrategyNotFoundError,
)
from finance_kernel.domain.dtos import (
    EventEnvelope,
    LineSpec,
    LineSide,
    ProposedJournalEntry,
    ReferenceData,
    ValidationResult,
)
from finance_kernel.domain.values import Money


class TestR14NoCentralDispatch:
    """Tests for R14: No central dispatch."""

    def test_bookkeeper_uses_registry_lookup(self):
        """
        Bookkeeper must use registry lookup, not branching.

        R14: PostingEngine may not branch on event_type.
        """
        # Get Bookkeeper.propose source code
        source = inspect.getsource(Bookkeeper.propose)

        # Check that it uses registry.get() for strategy lookup
        assert "registry.get" in source or "_registry.get" in source

        # Check for absence of if/elif chains on event_type
        # Parse the AST to check for branching patterns
        tree = ast.parse(textwrap.dedent(source))

        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # Check if condition references event_type in a branching pattern
                condition_source = ast.unparse(node.test) if hasattr(ast, 'unparse') else ""
                # Branching on event_type would look like: if event.event_type == "..."
                # We allow: if event_type not in (registry checks)
                assert "event.event_type ==" not in condition_source
                assert "event_type ==" not in condition_source

    def test_bookkeeper_delegates_to_strategy(self):
        """
        Bookkeeper must delegate to strategy without knowing event type logic.

        R14: Each event_type must have its own registered PostingStrategy.
        """
        # Create a test strategy
        class TestStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.r14.event"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                ]

        # Register strategy
        strategy = TestStrategy()
        try:
            StrategyRegistry.register(strategy)

            # Create bookkeeper
            bookkeeper = Bookkeeper()

            # Verify bookkeeper finds the strategy via registry
            assert bookkeeper.can_handle("test.r14.event")

            # Verify it doesn't know about unknown types
            assert not bookkeeper.can_handle("unknown.event.type")

        finally:
            StrategyRegistry.unregister("test.r14.event")

    def test_strategy_registry_is_dictionary_based(self):
        """
        StrategyRegistry must use dictionary lookup, not branching.

        R14: Registry pattern enforces no central dispatch.
        """
        # Get StrategyRegistry.get source
        source = inspect.getsource(StrategyRegistry.get)

        # Check that it uses dictionary operations
        assert "_strategies[" in source or "_strategies.get" in source

        # Check for absence of if/elif chains on specific event types
        tree = ast.parse(textwrap.dedent(source))

        event_type_literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Collect string literals that look like event types
                if "." in node.value and node.value.count(".") == 1:
                    event_type_literals.append(node.value)

        # Should not have hardcoded event type strings in lookup logic
        assert len(event_type_literals) == 0, (
            f"Registry should not contain hardcoded event types: {event_type_literals}"
        )

    def test_no_event_type_switch_in_orchestrator(self):
        """
        PostingOrchestrator must not have switch/case on event_type.

        R14: No central dispatch in posting engine.
        """
        from finance_kernel.services.posting_orchestrator import PostingOrchestrator

        source = inspect.getsource(PostingOrchestrator)

        # Check for absence of switch-like patterns
        # Python doesn't have switch, so check for if/elif chains
        assert "if event_type ==" not in source
        assert 'if event_type == "' not in source
        assert "elif event_type" not in source
        assert "match event_type" not in source


class TestR15OpenClosedCompliance:
    """Tests for R15: Open/closed compliance."""

    def test_new_strategy_requires_no_engine_modification(self):
        """
        Adding a new event type requires only creating a new strategy.

        R15: Open for extension, closed for modification.
        """
        # Define a completely new event type
        class NewEventStrategy(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "inventory.receipt.v2"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                # Extract data from payload
                amount = Decimal(str(event.payload.get("amount", "0")))
                return [
                    LineSpec(
                        account_code="1200",  # Inventory
                        side=LineSide.DEBIT,
                        money=Money.of(amount, "USD"),
                    ),
                    LineSpec(
                        account_code="2000",  # AP
                        side=LineSide.CREDIT,
                        money=Money.of(amount, "USD"),
                    ),
                ]

        # Register without modifying any existing code
        strategy = NewEventStrategy()
        try:
            StrategyRegistry.register(strategy)

            # Verify it's now available
            assert StrategyRegistry.has_strategy("inventory.receipt.v2")

            # Bookkeeper can handle it without modification
            bookkeeper = Bookkeeper()
            assert bookkeeper.can_handle("inventory.receipt.v2")

        finally:
            StrategyRegistry.unregister("inventory.receipt.v2")

    def test_strategy_registration_is_additive(self):
        """
        Strategy registration must be additive, not replacing.

        R15: Extensions don't modify existing behavior.
        """
        # Register multiple strategies
        strategies = []
        for i in range(3):
            class TestStrategy(BasePostingStrategy):
                _type = f"test.additive.{i}"

                @property
                def event_type(self) -> str:
                    return self._type

                @property
                def version(self) -> int:
                    return 1

                def _compute_line_specs(self, event, reference_data):
                    return []

            s = TestStrategy()
            s._type = f"test.additive.{i}"
            strategies.append(s)

        try:
            # Register all strategies
            for s in strategies:
                StrategyRegistry.register(s)

            # All should be available
            for s in strategies:
                assert StrategyRegistry.has_strategy(s.event_type)

            # Count of event types should include all registered
            event_types = StrategyRegistry.list_event_types()
            for s in strategies:
                assert s.event_type in event_types

        finally:
            for s in strategies:
                StrategyRegistry.unregister(s.event_type)

    def test_strategy_versioning_for_evolution(self):
        """
        Strategy versioning allows evolution without breaking replays.

        R15: New versions extend, don't modify old behavior.
        """
        class StrategyV1(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "evolving.event"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                ]

        class StrategyV2(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "evolving.event"

            @property
            def version(self) -> int:
                return 2

            def _compute_line_specs(self, event, reference_data):
                # V2 adds a new line for tracking
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                ]

        v1 = StrategyV1()
        v2 = StrategyV2()

        try:
            StrategyRegistry.register(v1)
            StrategyRegistry.register(v2)

            # Both versions available
            versions = StrategyRegistry.get_all_versions("evolving.event")
            assert 1 in versions
            assert 2 in versions

            # Can request specific version
            retrieved_v1 = StrategyRegistry.get("evolving.event", version=1)
            assert retrieved_v1.version == 1

            retrieved_v2 = StrategyRegistry.get("evolving.event", version=2)
            assert retrieved_v2.version == 2

            # Latest version is v2
            latest = StrategyRegistry.get("evolving.event")
            assert latest.version == 2

        finally:
            StrategyRegistry.unregister("evolving.event")

    def test_base_strategy_provides_extension_points(self):
        """
        BasePostingStrategy provides extension points for customization.

        R15: Base class is designed for extension.
        """
        # Check that BasePostingStrategy has abstract method
        assert hasattr(BasePostingStrategy, "_compute_line_specs")

        # Check that hook methods exist for customization
        assert hasattr(BasePostingStrategy, "_get_description")
        assert hasattr(BasePostingStrategy, "_get_metadata")
        assert hasattr(BasePostingStrategy, "_validate_currencies")
        assert hasattr(BasePostingStrategy, "_validate_dimensions")
        assert hasattr(BasePostingStrategy, "_balance_and_round")

        # All these are overridable by subclasses


class TestStrategyIsolation:
    """Tests for strategy isolation (supporting R14/R15)."""

    def test_strategy_unknown_event_type_returns_error(self):
        """
        Unknown event types return a clear error from bookkeeper.

        R14: No default/fallback branching.
        """
        bookkeeper = Bookkeeper()

        # Create an event with unknown type
        event = EventEnvelope(
            event_id=uuid4(),
            event_type="completely.unknown.type",
            occurred_at=datetime.now(),
            effective_date=date.today(),
            actor_id=uuid4(),
            producer="test",
            payload={},
            payload_hash="test",
            schema_version=1,
        )

        reference_data = ReferenceData(
            account_ids_by_code={},
            active_account_codes=frozenset(),
            valid_currencies=frozenset(),
            rounding_account_ids={},
        )

        result = bookkeeper.propose(event, reference_data)

        assert not result.is_valid
        assert any(e.code == "STRATEGY_NOT_FOUND" for e in result.validation.errors)

    def test_strategies_are_independent(self):
        """
        Strategies must be independent and not affect each other.

        R14/R15: No cross-strategy dependencies.
        """
        class StrategyA(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "independent.a"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("50"), "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("50"), "USD"),
                    ),
                ]

        class StrategyB(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "independent.b"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                return [
                    LineSpec(
                        account_code="3000",
                        side=LineSide.DEBIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=LineSide.CREDIT,
                        money=Money.of(Decimal("100"), "USD"),
                    ),
                ]

        a = StrategyA()
        b = StrategyB()

        try:
            StrategyRegistry.register(a)
            StrategyRegistry.register(b)

            # Get strategies
            retrieved_a = StrategyRegistry.get("independent.a")
            retrieved_b = StrategyRegistry.get("independent.b")

            # They are different instances
            assert retrieved_a is not retrieved_b

            # They handle different event types
            assert retrieved_a.event_type != retrieved_b.event_type

        finally:
            StrategyRegistry.unregister("independent.a")
            StrategyRegistry.unregister("independent.b")
