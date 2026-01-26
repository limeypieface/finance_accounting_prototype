"""
Period uniqueness and integrity tests (R12) and Adjustment policy tests (R13).

R12 Verifies:
- period_code must be globally unique
- Date ranges must not overlap
- Period resolution must be deterministic

R13 Verifies:
- allows_adjustments must be enforced in posting and correction logic
- Closed periods must be immutable
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    PeriodOverlapError,
    PeriodAlreadyClosedError,
    PeriodImmutableError,
    AdjustmentsNotAllowedError,
    PeriodNotFoundError,
    ClosedPeriodError,
)


def _register_test_strategy(event_type: str) -> None:
    """Register a simple balanced strategy for testing."""

    class TestStrategy(BasePostingStrategy):
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

    StrategyRegistry.register(TestStrategy(event_type))


class TestR12PeriodCodeUniqueness:
    """R12: period_code must be globally unique."""

    def test_duplicate_period_code_rejected(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that creating a period with duplicate code fails."""
        # Create first period
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # Attempt to create second period with same code (different dates)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            period_service.create_period(
                period_code="2025-01",  # Duplicate!
                name="Another January",
                start_date=date(2025, 2, 1),
                end_date=date(2025, 2, 28),
                actor_id=test_actor_id,
            )
            session.flush()

        # Rollback to clean up the failed transaction
        session.rollback()


class TestR12DateRangeNoOverlap:
    """R12: Date ranges must not overlap."""

    def test_overlapping_period_rejected(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that creating overlapping periods fails."""
        # Create first period: Jan 1-31
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # Attempt to create overlapping period: Jan 15 - Feb 15
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-01-late",
                name="Late January",
                start_date=date(2025, 1, 15),
                end_date=date(2025, 2, 15),
                actor_id=test_actor_id,
            )

        assert "2025-01" in str(exc_info.value)
        assert "2025-01-late" in str(exc_info.value)

    def test_adjacent_periods_allowed(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that adjacent (non-overlapping) periods are allowed."""
        # Create January
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # Create February (adjacent, not overlapping)
        feb = period_service.create_period(
            period_code="2025-02",
            name="February 2025",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
        )
        session.flush()

        assert feb.period_code == "2025-02"

    def test_period_completely_inside_existing_rejected(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that a period inside another is rejected."""
        # Create Q1
        period_service.create_period(
            period_code="2025-Q1",
            name="Q1 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # Attempt to create January (inside Q1)
        with pytest.raises(PeriodOverlapError):
            period_service.create_period(
                period_code="2025-01",
                name="January 2025",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 31),
                actor_id=test_actor_id,
            )

    def test_invalid_date_range_rejected(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that start_date > end_date is rejected."""
        with pytest.raises(ValueError, match="cannot be after"):
            period_service.create_period(
                period_code="2025-INVALID",
                name="Invalid Period",
                start_date=date(2025, 2, 1),
                end_date=date(2025, 1, 1),  # Before start!
                actor_id=test_actor_id,
            )


class TestR12DeterministicResolution:
    """R12: Period resolution must be deterministic."""

    def test_date_resolves_to_single_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that any date resolves to exactly one period (if any)."""
        # Create non-overlapping periods
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        period_service.create_period(
            period_code="2025-02",
            name="February 2025",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
        )
        session.flush()

        # Test resolution is deterministic
        jan_15 = period_service.get_period_for_date(date(2025, 1, 15))
        assert jan_15 is not None
        assert jan_15.period_code == "2025-01"

        feb_15 = period_service.get_period_for_date(date(2025, 2, 15))
        assert feb_15 is not None
        assert feb_15.period_code == "2025-02"

        # Boundary dates
        jan_31 = period_service.get_period_for_date(date(2025, 1, 31))
        assert jan_31 is not None
        assert jan_31.period_code == "2025-01"

        feb_1 = period_service.get_period_for_date(date(2025, 2, 1))
        assert feb_1 is not None
        assert feb_1.period_code == "2025-02"

    def test_date_outside_periods_returns_none(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that dates outside any period return None."""
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # Date before any period
        result = period_service.get_period_for_date(date(2024, 12, 31))
        assert result is None

        # Date after any period
        result = period_service.get_period_for_date(date(2025, 2, 1))
        assert result is None


class TestR13AdjustmentPolicyEnforcement:
    """R13: allows_adjustments must be enforced."""

    def test_adjustment_allowed_when_enabled(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that adjustments succeed when period allows them."""
        today = deterministic_clock.now().date()
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        # Create period WITH adjustments allowed
        period_service.create_period(
            period_code="ADJ-ALLOWED",
            name="Adjustable Period",
            start_date=start,
            end_date=end,
            actor_id=test_actor_id,
            allows_adjustments=True,  # Enabled!
        )
        session.flush()

        event_type = "test.adjustment.allowed"
        _register_test_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=today,
                actor_id=test_actor_id,
                producer="test",
                payload={},
                is_adjustment=True,  # This is an adjustment
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_adjustment_rejected_when_disabled(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that adjustments fail when period doesn't allow them."""
        today = deterministic_clock.now().date()
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        # Create period WITHOUT adjustments allowed
        period_service.create_period(
            period_code="NO-ADJ",
            name="No Adjustments Period",
            start_date=start,
            end_date=end,
            actor_id=test_actor_id,
            allows_adjustments=False,  # Disabled!
        )
        session.flush()

        event_type = "test.adjustment.rejected"
        _register_test_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=today,
                actor_id=test_actor_id,
                producer="test",
                payload={},
                is_adjustment=True,  # This is an adjustment
            )
            assert result.status == PostingStatus.ADJUSTMENTS_NOT_ALLOWED
            assert "NO-ADJ" in result.message
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_regular_posting_succeeds_regardless_of_adjustment_flag(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that regular (non-adjustment) postings succeed even when adjustments disabled."""
        today = deterministic_clock.now().date()
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        # Create period WITHOUT adjustments allowed
        period_service.create_period(
            period_code="REGULAR-ONLY",
            name="Regular Only Period",
            start_date=start,
            end_date=end,
            actor_id=test_actor_id,
            allows_adjustments=False,
        )
        session.flush()

        event_type = "test.regular.posting"
        _register_test_strategy(event_type)

        try:
            # Regular posting (is_adjustment=False)
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=today,
                actor_id=test_actor_id,
                producer="test",
                payload={},
                is_adjustment=False,  # Not an adjustment
            )
            assert result.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestR13ClosedPeriodImmutability:
    """R13: Closed periods must be immutable."""

    def test_cannot_enable_adjustments_on_closed_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that enabling adjustments on closed period fails."""
        period_service.create_period(
            period_code="CLOSED-ADJ",
            name="Closed Period",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
            allows_adjustments=False,
        )
        session.flush()

        # Close the period
        period_service.close_period("CLOSED-ADJ", test_actor_id)
        session.flush()

        # Try to enable adjustments
        with pytest.raises(PeriodImmutableError) as exc_info:
            period_service.enable_adjustments("CLOSED-ADJ", test_actor_id)

        assert "CLOSED-ADJ" in str(exc_info.value)
        assert "immutable" in str(exc_info.value).lower()

    def test_cannot_disable_adjustments_on_closed_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that disabling adjustments on closed period fails."""
        period_service.create_period(
            period_code="CLOSED-DIS",
            name="Closed Period",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
            allows_adjustments=True,
        )
        session.flush()

        # Close the period
        period_service.close_period("CLOSED-DIS", test_actor_id)
        session.flush()

        # Try to disable adjustments
        with pytest.raises(PeriodImmutableError):
            period_service.disable_adjustments("CLOSED-DIS", test_actor_id)

    def test_cannot_reopen_closed_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that reopening a closed period fails."""
        period_service.create_period(
            period_code="CLOSED-REOPEN",
            name="Closed Period",
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # Close the period
        period_service.close_period("CLOSED-REOPEN", test_actor_id)
        session.flush()

        # Try to reopen
        with pytest.raises(PeriodImmutableError) as exc_info:
            period_service.reopen_period("CLOSED-REOPEN", test_actor_id)

        assert "reopen" in str(exc_info.value).lower()

    def test_cannot_close_already_closed_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that closing an already closed period fails."""
        period_service.create_period(
            period_code="DOUBLE-CLOSE",
            name="Period to close twice",
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30),
            actor_id=test_actor_id,
        )
        session.flush()

        # Close the period
        period_service.close_period("DOUBLE-CLOSE", test_actor_id)
        session.flush()

        # Try to close again
        with pytest.raises(PeriodAlreadyClosedError):
            period_service.close_period("DOUBLE-CLOSE", test_actor_id)

    def test_cannot_post_to_closed_period(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting to a closed period fails."""
        # Create and close a period
        period_service.create_period(
            period_code="CLOSED-POST",
            name="Closed Period",
            start_date=date(2025, 5, 1),
            end_date=date(2025, 5, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        period_service.close_period("CLOSED-POST", test_actor_id)
        session.flush()

        event_type = "test.closed.period"
        _register_test_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 5, 15),  # Date in closed period
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.PERIOD_CLOSED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestR13AdjustmentFlagOnOpenPeriod:
    """Test adjustment flag modifications on open periods."""

    def test_can_enable_adjustments_on_open_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that enabling adjustments on open period succeeds."""
        period_service.create_period(
            period_code="ENABLE-ADJ",
            name="Enable Adjustments",
            start_date=date(2025, 6, 1),
            end_date=date(2025, 6, 30),
            actor_id=test_actor_id,
            allows_adjustments=False,
        )
        session.flush()

        # Enable adjustments
        result = period_service.enable_adjustments("ENABLE-ADJ", test_actor_id)

        assert result.allows_adjustments is True

    def test_can_disable_adjustments_on_open_period(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that disabling adjustments on open period succeeds."""
        period_service.create_period(
            period_code="DISABLE-ADJ",
            name="Disable Adjustments",
            start_date=date(2025, 7, 1),
            end_date=date(2025, 7, 31),
            actor_id=test_actor_id,
            allows_adjustments=True,
        )
        session.flush()

        # Disable adjustments
        result = period_service.disable_adjustments("DISABLE-ADJ", test_actor_id)

        assert result.allows_adjustments is False
