"""
Period lock tests for the posting engine.

Verifies:
- Closed period rejection
- Open period acceptance
- Boundary date handling
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.period_service import PeriodService
from finance_kernel.models.fiscal_period import PeriodStatus
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
from finance_kernel.domain.values import Money


def _register_simple_strategy(event_type: str) -> None:
    """Register a simple balanced strategy for testing."""

    class SimpleStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str):
            self._event_type = evt_type
            self._version = 1

        @property
        def event_type(self) -> str:
            return self._event_type

        @property
        def version(self) -> int:
            return self._version

        def _compute_line_specs(
            self, event: EventEnvelope, ref: ReferenceData
        ) -> tuple[LineSpec, ...]:
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

    StrategyRegistry.register(SimpleStrategy(event_type))


class TestClosedPeriodEnforcement:
    """Tests for closed period enforcement."""

    def test_posting_to_open_period_succeeds(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting to an open period succeeds."""
        event_type = "test.open_period"
        _register_simple_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_posting_to_closed_period_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        create_period,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting to a closed period is rejected."""
        # Create and close a period
        today = deterministic_clock.now().date()
        last_month = today.replace(day=1) - timedelta(days=1)
        start = last_month.replace(day=1)

        closed_period = create_period(
            period_code="CLOSED-01",
            name="Closed Period",
            start_date=start,
            end_date=last_month,
        )

        # Close the period
        period_service.close_period(closed_period.period_code, test_actor_id)
        session.flush()

        event_type = "test.closed_period"
        _register_simple_strategy(event_type)

        try:
            # Try to post to closed period
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=start + timedelta(days=5),  # Date in closed period
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )

            assert result.status == PostingStatus.PERIOD_CLOSED
            assert "CLOSED-01" in result.message
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_posting_to_nonexistent_period_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting to a date with no period is rejected."""
        # Use a date far in the future (no period exists)
        future_date = deterministic_clock.now().date() + timedelta(days=365 * 10)

        event_type = "test.no_period"
        _register_simple_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=future_date,
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )

            assert result.status == PostingStatus.PERIOD_CLOSED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestPeriodBoundaries:
    """Tests for period boundary handling."""

    def test_posting_on_period_start_date(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test posting on the first day of a period."""
        event_type = "test.start_date"
        _register_simple_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_posting_on_period_end_date(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test posting on the last day of a period."""
        event_type = "test.end_date"
        _register_simple_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.end_date,
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)
