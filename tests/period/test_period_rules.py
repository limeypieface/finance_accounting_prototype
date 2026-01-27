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


class TestOverlappingPeriodScenarios:
    """
    Comprehensive tests for overlapping period scenarios.

    Tests various edge cases for period overlap detection:
    - Partial overlap at start
    - Partial overlap at end
    - Complete containment (new inside existing)
    - Complete containment (existing inside new)
    - Same exact dates
    - Single day overlap at boundaries
    - Multiple existing periods with gap
    """

    def test_partial_overlap_at_start(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test overlap when new period's start overlaps existing period's end."""
        # Existing: Jan 1-31
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Jan 20 - Feb 20 (overlaps last 12 days of January)
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-01-late",
                name="Late January",
                start_date=date(2025, 1, 20),
                end_date=date(2025, 2, 20),
                actor_id=test_actor_id,
            )

        error = exc_info.value
        assert "2025-01" in str(error)
        assert "2025-01-late" in str(error)

    def test_partial_overlap_at_end(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test overlap when new period's end overlaps existing period's start."""
        # Existing: Feb 1-28
        period_service.create_period(
            period_code="2025-02",
            name="February 2025",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Jan 15 - Feb 10 (overlaps first 10 days of February)
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-01-late",
                name="Late January Extended",
                start_date=date(2025, 1, 15),
                end_date=date(2025, 2, 10),
                actor_id=test_actor_id,
            )

        error = exc_info.value
        assert "2025-02" in str(error)

    def test_new_period_completely_inside_existing(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test when new period is completely contained within existing period."""
        # Existing: Q1 (Jan 1 - Mar 31)
        period_service.create_period(
            period_code="2025-Q1",
            name="Q1 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Feb 1-28 (completely inside Q1)
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-02",
                name="February 2025",
                start_date=date(2025, 2, 1),
                end_date=date(2025, 2, 28),
                actor_id=test_actor_id,
            )

        assert "2025-Q1" in str(exc_info.value)

    def test_existing_period_completely_inside_new(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test when existing period is completely contained within new period."""
        # Existing: Feb 1-28
        period_service.create_period(
            period_code="2025-02",
            name="February 2025",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Q1 (Jan 1 - Mar 31) which contains February
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-Q1",
                name="Q1 2025",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 3, 31),
                actor_id=test_actor_id,
            )

        assert "2025-02" in str(exc_info.value)

    def test_same_exact_dates_rejected(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that periods with identical date ranges are rejected."""
        # Existing: Jan 1-31
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Same dates, different code
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-01-ALT",
                name="January 2025 Alternative",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 31),
                actor_id=test_actor_id,
            )

        assert "2025-01" in str(exc_info.value)

    def test_single_day_overlap_at_boundary_start(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test overlap when periods share exactly one day at the boundary (start)."""
        # Existing: Jan 1-31
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Jan 31 - Feb 28 (shares Jan 31 with existing)
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-02-early",
                name="Early February",
                start_date=date(2025, 1, 31),  # Overlaps by one day!
                end_date=date(2025, 2, 28),
                actor_id=test_actor_id,
            )

        assert "2025-01" in str(exc_info.value)

    def test_single_day_overlap_at_boundary_end(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test overlap when periods share exactly one day at the boundary (end)."""
        # Existing: Feb 1 - Feb 28
        period_service.create_period(
            period_code="2025-02",
            name="February 2025",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Jan 1 - Feb 1 (shares Feb 1 with existing)
        with pytest.raises(PeriodOverlapError) as exc_info:
            period_service.create_period(
                period_code="2025-01-ext",
                name="Extended January",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 2, 1),  # Overlaps by one day!
                actor_id=test_actor_id,
            )

        assert "2025-02" in str(exc_info.value)

    def test_single_day_period_overlap(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test overlap with a single-day period."""
        # Existing: Jan 1-31
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # New period: Single day (Jan 15 only)
        with pytest.raises(PeriodOverlapError):
            period_service.create_period(
                period_code="2025-01-15",
                name="January 15 Only",
                start_date=date(2025, 1, 15),
                end_date=date(2025, 1, 15),  # Single day period
                actor_id=test_actor_id,
            )

    def test_period_fits_in_gap_between_existing_periods(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that a new period can fit in a gap between existing periods."""
        # Existing: Jan 1-15
        period_service.create_period(
            period_code="2025-01-H1",
            name="January 2025 First Half",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 15),
            actor_id=test_actor_id,
        )
        # Existing: Jan 20-31 (gap from Jan 16-19)
        period_service.create_period(
            period_code="2025-01-H2",
            name="January 2025 Second Half",
            start_date=date(2025, 1, 20),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # This should work: fits perfectly in the gap
        gap_period = period_service.create_period(
            period_code="2025-01-GAP",
            name="January Gap",
            start_date=date(2025, 1, 16),
            end_date=date(2025, 1, 19),
            actor_id=test_actor_id,
        )
        assert gap_period.period_code == "2025-01-GAP"
        session.flush()

    def test_overlap_detected_with_first_of_multiple_periods(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test overlap is detected when new period overlaps with an existing period."""
        # Create a single period
        period_service.create_period(
            period_code="2025-01-H1",
            name="January 2025 First Half",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 15),
            actor_id=test_actor_id,
        )
        session.flush()

        # This should fail: overlaps with first half
        with pytest.raises(PeriodOverlapError):
            period_service.create_period(
                period_code="2025-01-BAD",
                name="Bad Period",
                start_date=date(2025, 1, 14),  # Overlaps with H1
                end_date=date(2025, 1, 18),
                actor_id=test_actor_id,
            )

    def test_multiple_overlapping_attempts_all_fail(
        self,
        session,
        period_service: PeriodService,
        test_actor_id,
    ):
        """Test that multiple different overlap scenarios all fail correctly."""
        # Create base period
        period_service.create_period(
            period_code="2025-01",
            name="January 2025",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        # All of these should fail
        overlap_scenarios = [
            ("overlap-1", date(2025, 1, 1), date(2025, 1, 15)),   # Start overlap
            ("overlap-2", date(2025, 1, 15), date(2025, 1, 31)),  # End overlap
            ("overlap-3", date(2025, 1, 10), date(2025, 1, 20)),  # Middle overlap
            ("overlap-4", date(2024, 12, 15), date(2025, 2, 15)), # Encompassing overlap
            ("overlap-5", date(2025, 1, 1), date(2025, 1, 1)),    # Single day start
            ("overlap-6", date(2025, 1, 31), date(2025, 1, 31)),  # Single day end
        ]

        for code, start, end in overlap_scenarios:
            with pytest.raises(PeriodOverlapError, match="2025-01"):
                period_service.create_period(
                    period_code=code,
                    name=f"Test {code}",
                    start_date=start,
                    end_date=end,
                    actor_id=test_actor_id,
                )


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


class TestR83AdjustmentsInClosedPeriods:
    """
    R8.3: Adjustments must be rejected in closed periods.

    Even if allows_adjustments=True when the period was open, once closed
    no adjustments (and no regular postings) should be accepted.
    """

    def test_adjustment_rejected_in_closed_period_even_if_allowed(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that adjustments are rejected in closed periods even if
        allows_adjustments was True when the period was open.

        This is the key test for R8.3.
        """
        # Create a period that ALLOWS adjustments
        period_service.create_period(
            period_code="CLOSED-ADJ-ALLOWED",
            name="Closed Period With Adjustments Allowed",
            start_date=date(2025, 8, 1),
            end_date=date(2025, 8, 31),
            actor_id=test_actor_id,
            allows_adjustments=True,  # Adjustments were allowed!
        )
        session.flush()

        # Close the period
        period_service.close_period("CLOSED-ADJ-ALLOWED", test_actor_id)
        session.flush()

        event_type = "test.r83.closed_adj"
        _register_test_strategy(event_type)

        try:
            # Try to post an adjustment to the closed period
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 8, 15),  # Date in closed period
                actor_id=test_actor_id,
                producer="test",
                payload={},
                is_adjustment=True,  # Adjustment posting
            )

            # Should be rejected because period is closed
            assert result.status == PostingStatus.PERIOD_CLOSED, (
                f"Expected PERIOD_CLOSED but got {result.status}. "
                f"Adjustments should be rejected in closed periods."
            )
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_regular_posting_also_rejected_in_closed_period(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that regular postings are also rejected in closed periods.

        This confirms that closing a period blocks ALL postings, not just adjustments.
        """
        period_service.create_period(
            period_code="CLOSED-REGULAR",
            name="Closed Period Regular",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 9, 30),
            actor_id=test_actor_id,
            allows_adjustments=False,
        )
        session.flush()

        period_service.close_period("CLOSED-REGULAR", test_actor_id)
        session.flush()

        event_type = "test.r83.closed_regular"
        _register_test_strategy(event_type)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 9, 15),
                actor_id=test_actor_id,
                producer="test",
                payload={},
                is_adjustment=False,  # Regular posting
            )

            assert result.status == PostingStatus.PERIOD_CLOSED
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_adjustment_succeeds_before_close_fails_after(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test the lifecycle: adjustment succeeds while open, fails after close.

        This demonstrates the transition point where behavior changes.
        """
        period_service.create_period(
            period_code="LIFECYCLE-ADJ",
            name="Lifecycle Test Period",
            start_date=date(2025, 10, 1),
            end_date=date(2025, 10, 31),
            actor_id=test_actor_id,
            allows_adjustments=True,
        )
        session.flush()

        event_type = "test.r83.lifecycle"
        _register_test_strategy(event_type)

        try:
            # Post adjustment while period is OPEN - should succeed
            result1 = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 10, 15),
                actor_id=test_actor_id,
                producer="test",
                payload={"seq": 1},
                is_adjustment=True,
            )
            assert result1.status == PostingStatus.POSTED, (
                f"Adjustment should succeed in open period, got {result1.status}"
            )

            # Now close the period
            period_service.close_period("LIFECYCLE-ADJ", test_actor_id)
            session.flush()

            # Try another adjustment - should FAIL
            result2 = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 10, 20),
                actor_id=test_actor_id,
                producer="test",
                payload={"seq": 2},
                is_adjustment=True,
            )
            assert result2.status == PostingStatus.PERIOD_CLOSED, (
                f"Adjustment should fail after close, got {result2.status}"
            )
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestR85CrossPeriodCorrections:
    """
    R8.5: Cross-period corrections must forward-adjust, never back-post.

    When correcting entries from a closed period, the correction must be
    posted to a current OPEN period (forward adjustment), not back-posted
    to the closed period.

    This enforces the immutability of closed periods while still allowing
    necessary corrections via reversing entries in the current period.
    """

    def test_cannot_back_post_correction_to_closed_period(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that corrections cannot be back-posted to closed periods.

        Scenario:
        - January (closed): Has an erroneous entry
        - February (open): Current period
        - User tries to post a correction with effective_date in January
        - Should be REJECTED - must forward-adjust instead
        """
        # Create and close January
        period_service.create_period(
            period_code="2025-01-CLOSED",
            name="January 2025 (Closed)",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
            allows_adjustments=True,  # Adjustments allowed, but still closed
        )
        session.flush()
        period_service.close_period("2025-01-CLOSED", test_actor_id)
        session.flush()

        # Create open February
        period_service.create_period(
            period_code="2025-02-OPEN",
            name="February 2025 (Open)",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
            allows_adjustments=True,
        )
        session.flush()

        event_type = "test.r85.back_post"
        _register_test_strategy(event_type)

        try:
            # Attempt to post correction to January (closed)
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 1, 15),  # Back-dating to closed period!
                actor_id=test_actor_id,
                producer="test",
                payload={"correction": True},
                is_adjustment=True,
            )

            # Must be rejected
            assert result.status == PostingStatus.PERIOD_CLOSED, (
                f"Back-posting to closed period should fail. Got: {result.status}"
            )
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_forward_adjustment_succeeds_for_correction(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that forward adjustments succeed for corrections.

        This is the CORRECT way to handle corrections to closed periods:
        post the correction in the current open period.
        """
        # Create and close January
        period_service.create_period(
            period_code="2025-01-FWD",
            name="January 2025 (Closed)",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()
        period_service.close_period("2025-01-FWD", test_actor_id)
        session.flush()

        # Create open February with adjustments allowed
        period_service.create_period(
            period_code="2025-02-FWD",
            name="February 2025 (Open)",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            actor_id=test_actor_id,
            allows_adjustments=True,
        )
        session.flush()

        event_type = "test.r85.forward_adj"
        _register_test_strategy(event_type)

        try:
            # Post forward adjustment to February (open period)
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 2, 15),  # Forward to current open period
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "correction_for_period": "2025-01-FWD",
                    "original_error": "Wrong account used",
                },
                is_adjustment=True,
            )

            assert result.status == PostingStatus.POSTED, (
                f"Forward adjustment should succeed. Got: {result.status}"
            )
            assert result.journal_entry_id is not None
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_correction_workflow_full_cycle(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test a full correction workflow:
        1. Post original entry in open period
        2. Close the period
        3. Discover error - try back-post (fails)
        4. Post forward adjustment (succeeds)

        This demonstrates the proper correction workflow.
        """
        # Step 1: Create January, post original entry
        period_service.create_period(
            period_code="CYCLE-JAN",
            name="Cycle Test January",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        event_type = "test.r85.cycle"
        _register_test_strategy(event_type)

        try:
            # Post original entry
            original_result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 1, 10),
                actor_id=test_actor_id,
                producer="test",
                payload={"description": "Original entry"},
            )
            assert original_result.status == PostingStatus.POSTED
            original_entry_id = original_result.journal_entry_id

            # Step 2: Close January
            period_service.close_period("CYCLE-JAN", test_actor_id)
            session.flush()

            # Create February
            period_service.create_period(
                period_code="CYCLE-FEB",
                name="Cycle Test February",
                start_date=date(2025, 2, 1),
                end_date=date(2025, 2, 28),
                actor_id=test_actor_id,
                allows_adjustments=True,
            )
            session.flush()

            # Step 3: Discover error, try to back-post - should FAIL
            back_post_result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 1, 10),  # Same date as original (closed)
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "description": "Correction - back posted",
                    "corrects": str(original_entry_id),
                },
                is_adjustment=True,
            )
            assert back_post_result.status == PostingStatus.PERIOD_CLOSED, (
                "Back-posting correction should fail"
            )

            # Step 4: Post forward adjustment - should SUCCEED
            forward_result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 2, 1),  # Forward to February
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "description": "Correction - forward adjustment",
                    "corrects": str(original_entry_id),
                    "original_period": "CYCLE-JAN",
                },
                is_adjustment=True,
            )
            assert forward_result.status == PostingStatus.POSTED, (
                f"Forward adjustment should succeed. Got: {forward_result.status}"
            )

            # Both entries exist: original in January, correction in February
            assert original_entry_id is not None
            assert forward_result.journal_entry_id is not None
            assert original_entry_id != forward_result.journal_entry_id

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_no_open_period_available_for_correction(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        period_service: PeriodService,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that corrections fail if no open period is available.

        This is an edge case where all periods are closed.
        """
        # Create and close January
        period_service.create_period(
            period_code="ALL-CLOSED-JAN",
            name="January (Closed)",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()
        period_service.close_period("ALL-CLOSED-JAN", test_actor_id)
        session.flush()

        # No February period exists at all

        event_type = "test.r85.no_period"
        _register_test_strategy(event_type)

        try:
            # Try to post to a date with no period
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2025, 2, 15),  # No period exists
                actor_id=test_actor_id,
                producer="test",
                payload={},
                is_adjustment=True,
            )

            # Should fail due to no period found
            assert result.status == PostingStatus.PERIOD_CLOSED, (
                f"Should fail when no period exists. Got: {result.status}"
            )
        finally:
            StrategyRegistry._strategies.pop(event_type, None)
