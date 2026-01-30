"""
Tests for Allocation Engine.

Covers:
- Pro-rata allocation
- FIFO allocation
- LIFO allocation
- Specific (priority) allocation
- Weighted allocation
- Equal allocation
- Rounding handling
- Edge cases and error handling
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from finance_kernel.domain.values import Money
from finance_engines.allocation import (
    AllocationEngine,
    AllocationTarget,
    AllocationLine,
    AllocationResult,
    AllocationMethod,
)


class TestProRataAllocation:
    """Tests for pro-rata allocation by eligible amount."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_simple_prorata(self):
        """Allocates proportionally by eligible amount."""
        result = self.engine.allocate(
            amount=Money.of("1000.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="inv-1",
                    eligible_amount=Money.of("300.00", "USD"),
                ),
                AllocationTarget(
                    target_id="inv-2",
                    eligible_amount=Money.of("700.00", "USD"),
                ),
            ],
            method=AllocationMethod.PRORATA,
        )

        assert result.method == AllocationMethod.PRORATA
        assert result.total_allocated == Money.of("1000.00", "USD")
        assert result.unallocated.is_zero
        assert result.is_fully_allocated

        # Check individual allocations
        assert result.lines[0].allocated == Money.of("300.00", "USD")
        assert result.lines[1].allocated == Money.of("700.00", "USD")

    def test_prorata_with_rounding(self):
        """Handles rounding correctly - last target gets remainder."""
        result = self.engine.allocate(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="a",
                    eligible_amount=Money.of("33.33", "USD"),
                ),
                AllocationTarget(
                    target_id="b",
                    eligible_amount=Money.of("33.33", "USD"),
                ),
                AllocationTarget(
                    target_id="c",
                    eligible_amount=Money.of("33.34", "USD"),
                ),
            ],
            method=AllocationMethod.PRORATA,
        )

        # Total should still be exactly 100.00
        total = sum(line.allocated.amount for line in result.lines)
        assert total == Decimal("100.00")

    def test_prorata_partial_payment(self):
        """Partial payment allocates proportionally."""
        result = self.engine.allocate(
            amount=Money.of("500.00", "USD"),  # Only paying 500 of 1000
            targets=[
                AllocationTarget(
                    target_id="inv-1",
                    eligible_amount=Money.of("300.00", "USD"),
                ),
                AllocationTarget(
                    target_id="inv-2",
                    eligible_amount=Money.of("700.00", "USD"),
                ),
            ],
            method=AllocationMethod.PRORATA,
        )

        assert result.lines[0].allocated == Money.of("150.00", "USD")
        assert result.lines[1].allocated == Money.of("350.00", "USD")

        # Check remaining amounts
        assert result.lines[0].remaining == Money.of("150.00", "USD")
        assert result.lines[1].remaining == Money.of("350.00", "USD")

    def test_prorata_missing_eligible_raises(self):
        """Raises error if target missing eligible_amount."""
        with pytest.raises(ValueError, match="eligible_amount"):
            self.engine.allocate(
                amount=Money.of("100.00", "USD"),
                targets=[
                    AllocationTarget(target_id="a"),  # No eligible_amount
                ],
                method=AllocationMethod.PRORATA,
            )

    def test_prorata_currency_mismatch_raises(self):
        """Raises error if currencies don't match."""
        with pytest.raises(ValueError, match="Currency mismatch"):
            self.engine.allocate(
                amount=Money.of("100.00", "USD"),
                targets=[
                    AllocationTarget(
                        target_id="a",
                        eligible_amount=Money.of("100.00", "EUR"),
                    ),
                ],
                method=AllocationMethod.PRORATA,
            )


class TestFIFOAllocation:
    """Tests for FIFO (oldest first) allocation."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_fifo_order(self):
        """Allocates to oldest invoices first."""
        result = self.engine.allocate(
            amount=Money.of("150.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="inv-new",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 3, 15),
                ),
                AllocationTarget(
                    target_id="inv-old",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 1, 15),
                ),
                AllocationTarget(
                    target_id="inv-mid",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 2, 15),
                ),
            ],
            method=AllocationMethod.FIFO,
        )

        # Should allocate to oldest first: inv-old (100), inv-mid (50), inv-new (0)
        lines_by_id = {str(line.target_id): line for line in result.lines}

        assert lines_by_id["inv-old"].allocated == Money.of("100.00", "USD")
        assert lines_by_id["inv-old"].is_fully_allocated
        assert lines_by_id["inv-mid"].allocated == Money.of("50.00", "USD")
        assert not lines_by_id["inv-mid"].is_fully_allocated
        assert lines_by_id["inv-new"].allocated == Money.of("0.00", "USD")

    def test_fifo_exact_amount(self):
        """FIFO with exact amount to cover all."""
        result = self.engine.allocate(
            amount=Money.of("300.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="a",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 1, 1),
                ),
                AllocationTarget(
                    target_id="b",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 2, 1),
                ),
                AllocationTarget(
                    target_id="c",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 3, 1),
                ),
            ],
            method=AllocationMethod.FIFO,
        )

        assert result.is_fully_allocated
        assert all(line.is_fully_allocated for line in result.lines)


class TestLIFOAllocation:
    """Tests for LIFO (newest first) allocation."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_lifo_order(self):
        """Allocates to newest invoices first."""
        result = self.engine.allocate(
            amount=Money.of("150.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="inv-old",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 1, 15),
                ),
                AllocationTarget(
                    target_id="inv-new",
                    eligible_amount=Money.of("100.00", "USD"),
                    date=date(2024, 3, 15),
                ),
            ],
            method=AllocationMethod.LIFO,
        )

        lines_by_id = {str(line.target_id): line for line in result.lines}

        # Newest first
        assert lines_by_id["inv-new"].allocated == Money.of("100.00", "USD")
        assert lines_by_id["inv-old"].allocated == Money.of("50.00", "USD")


class TestSpecificAllocation:
    """Tests for specific (priority-based) allocation."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_specific_by_priority(self):
        """Allocates by priority order."""
        result = self.engine.allocate(
            amount=Money.of("150.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="low-priority",
                    eligible_amount=Money.of("100.00", "USD"),
                    priority=10,
                ),
                AllocationTarget(
                    target_id="high-priority",
                    eligible_amount=Money.of("100.00", "USD"),
                    priority=1,
                ),
                AllocationTarget(
                    target_id="mid-priority",
                    eligible_amount=Money.of("100.00", "USD"),
                    priority=5,
                ),
            ],
            method=AllocationMethod.SPECIFIC,
        )

        lines_by_id = {str(line.target_id): line for line in result.lines}

        # Lower priority number = higher priority
        assert lines_by_id["high-priority"].allocated == Money.of("100.00", "USD")
        assert lines_by_id["mid-priority"].allocated == Money.of("50.00", "USD")
        assert lines_by_id["low-priority"].allocated == Money.of("0.00", "USD")


class TestWeightedAllocation:
    """Tests for weighted allocation."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_weighted_allocation(self):
        """Allocates by explicit weights."""
        result = self.engine.allocate(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(target_id="a", weight=Decimal("1")),
                AllocationTarget(target_id="b", weight=Decimal("2")),
                AllocationTarget(target_id="c", weight=Decimal("2")),
            ],
            method=AllocationMethod.WEIGHTED,
        )

        # Weights: 1+2+2 = 5
        # a: 1/5 = 20%, b: 2/5 = 40%, c: 2/5 = 40%
        assert result.lines[0].allocated == Money.of("20.00", "USD")
        assert result.lines[1].allocated == Money.of("40.00", "USD")
        assert result.lines[2].allocated == Money.of("40.00", "USD")

    def test_weighted_zero_total_raises(self):
        """Raises error if total weight is zero."""
        with pytest.raises(ValueError, match="zero"):
            self.engine.allocate(
                amount=Money.of("100.00", "USD"),
                targets=[
                    AllocationTarget(target_id="a", weight=Decimal("0")),
                    AllocationTarget(target_id="b", weight=Decimal("0")),
                ],
                method=AllocationMethod.WEIGHTED,
            )


class TestEqualAllocation:
    """Tests for equal allocation."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_equal_allocation(self):
        """Splits evenly among targets."""
        result = self.engine.allocate(
            amount=Money.of("90.00", "USD"),
            targets=[
                AllocationTarget(target_id="a"),
                AllocationTarget(target_id="b"),
                AllocationTarget(target_id="c"),
            ],
            method=AllocationMethod.EQUAL,
        )

        # 90 / 3 = 30 each
        assert all(line.allocated == Money.of("30.00", "USD") for line in result.lines)

    def test_equal_with_rounding(self):
        """Handles rounding for uneven splits."""
        result = self.engine.allocate(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(target_id="a"),
                AllocationTarget(target_id="b"),
                AllocationTarget(target_id="c"),
            ],
            method=AllocationMethod.EQUAL,
        )

        # 100 / 3 = 33.33... - rounding needed
        total = sum(line.allocated.amount for line in result.lines)
        assert total == Decimal("100.00")


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_empty_targets(self):
        """Returns unallocated when no targets."""
        result = self.engine.allocate(
            amount=Money.of("100.00", "USD"),
            targets=[],
            method=AllocationMethod.PRORATA,
        )

        assert result.unallocated == Money.of("100.00", "USD")
        assert result.total_allocated.is_zero
        assert len(result.lines) == 0

    def test_zero_amount(self):
        """Handles zero amount allocation."""
        result = self.engine.allocate(
            amount=Money.of("0.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="a",
                    eligible_amount=Money.of("100.00", "USD"),
                ),
            ],
            method=AllocationMethod.PRORATA,
        )

        assert result.total_allocated.is_zero
        assert result.lines[0].allocated.is_zero

    def test_negative_weight_raises(self):
        """Raises error for negative weight."""
        with pytest.raises(ValueError, match="negative"):
            AllocationTarget(target_id="a", weight=Decimal("-1"))

    def test_rounding_adjustment_tracked(self):
        """Rounding adjustment is tracked in result."""
        result = self.engine.allocate(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(target_id="a"),
                AllocationTarget(target_id="b"),
                AllocationTarget(target_id="c"),
            ],
            method=AllocationMethod.EQUAL,
        )

        # 100 / 3 = 33.33... each
        # With rounding, total must still be exactly 100.00
        total = sum(line.allocated.amount for line in result.lines)
        assert total == Decimal("100.00")

        # Rounding adjustment should be tracked
        assert result.rounding_adjustment is not None


class TestAllocationResultProperties:
    """Tests for AllocationResult value object."""

    def test_immutable(self):
        """AllocationResult is immutable."""
        result = AllocationResult(
            source_amount=Money.of("100.00", "USD"),
            method=AllocationMethod.PRORATA,
            lines=(),
            total_allocated=Money.of("100.00", "USD"),
            unallocated=Money.of("0.00", "USD"),
            rounding_adjustment=Money.of("0.00", "USD"),
        )

        with pytest.raises(AttributeError):
            result.total_allocated = Money.of("50.00", "USD")

    def test_allocation_count(self):
        """Counts non-zero allocations."""
        result = AllocationResult(
            source_amount=Money.of("100.00", "USD"),
            method=AllocationMethod.FIFO,
            lines=(
                AllocationLine(
                    target_id="a",
                    target_type="invoice",
                    allocated=Money.of("50.00", "USD"),
                    remaining=Money.of("0.00", "USD"),
                    is_fully_allocated=True,
                ),
                AllocationLine(
                    target_id="b",
                    target_type="invoice",
                    allocated=Money.of("50.00", "USD"),
                    remaining=Money.of("50.00", "USD"),
                    is_fully_allocated=False,
                ),
                AllocationLine(
                    target_id="c",
                    target_type="invoice",
                    allocated=Money.of("0.00", "USD"),
                    remaining=Money.of("100.00", "USD"),
                    is_fully_allocated=False,
                ),
            ),
            total_allocated=Money.of("100.00", "USD"),
            unallocated=Money.of("0.00", "USD"),
            rounding_adjustment=Money.of("0.00", "USD"),
        )

        assert result.allocation_count == 2  # a and b got allocations, not c


class TestConvenienceMethods:
    """Tests for convenience allocation methods."""

    def setup_method(self):
        self.engine = AllocationEngine()

    def test_allocate_prorata_method(self):
        """allocate_prorata convenience method works."""
        result = self.engine.allocate_prorata(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="a",
                    eligible_amount=Money.of("50.00", "USD"),
                ),
                AllocationTarget(
                    target_id="b",
                    eligible_amount=Money.of("50.00", "USD"),
                ),
            ],
        )

        assert result.method == AllocationMethod.PRORATA

    def test_allocate_fifo_method(self):
        """allocate_fifo convenience method works."""
        result = self.engine.allocate_fifo(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(
                    target_id="a",
                    eligible_amount=Money.of("50.00", "USD"),
                    date=date(2024, 1, 1),
                ),
            ],
        )

        assert result.method == AllocationMethod.FIFO

    def test_allocate_equal_method(self):
        """allocate_equal convenience method works."""
        result = self.engine.allocate_equal(
            amount=Money.of("100.00", "USD"),
            targets=[
                AllocationTarget(target_id="a"),
                AllocationTarget(target_id="b"),
            ],
        )

        assert result.method == AllocationMethod.EQUAL
