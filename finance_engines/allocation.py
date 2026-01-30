"""
Allocation Engine - Allocate amounts across multiple targets.

Pure functions with deterministic rounding. No I/O.

Usage:
    from finance_engines.allocation import AllocationEngine, AllocationTarget, AllocationMethod
    from finance_kernel.domain.values import Money

    engine = AllocationEngine()
    result = engine.allocate(
        amount=Money.of("1000.00", "USD"),
        targets=[
            AllocationTarget(target_id="inv-1", eligible_amount=Money.of("300.00", "USD")),
            AllocationTarget(target_id="inv-2", eligible_amount=Money.of("700.00", "USD")),
        ],
        method=AllocationMethod.PRORATA,
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence
from uuid import UUID

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.allocation")


class AllocationMethod(str, Enum):
    """Method for allocating amounts."""

    PRORATA = "prorata"  # By relative amount
    FIFO = "fifo"  # Oldest first by date
    LIFO = "lifo"  # Newest first by date
    SPECIFIC = "specific"  # User-designated order
    WEIGHTED = "weighted"  # By explicit weight factor
    EQUAL = "equal"  # Split evenly


@dataclass(frozen=True)
class AllocationTarget:
    """
    A target that can receive an allocation.

    Immutable value object.
    """

    target_id: str | UUID
    target_type: str = "invoice"
    eligible_amount: Money | None = None
    weight: Decimal = Decimal("1")
    priority: int = 0  # For SPECIFIC method (lower = higher priority)
    date: date | None = None  # For FIFO/LIFO

    def __post_init__(self) -> None:
        if self.weight < Decimal("0"):
            raise ValueError("Weight cannot be negative")


@dataclass(frozen=True)
class AllocationLine:
    """
    Result of allocation to a single target.

    Immutable value object.
    """

    target_id: str | UUID
    target_type: str
    allocated: Money
    remaining: Money
    is_fully_allocated: bool

    @property
    def allocation_percent(self) -> Decimal:
        """What percentage of eligible was allocated."""
        total = self.allocated + self.remaining
        if total.is_zero:
            return Decimal("100")
        return (self.allocated.amount / total.amount) * Decimal("100")


@dataclass(frozen=True)
class AllocationResult:
    """
    Complete allocation result.

    Immutable value object with all allocation details.
    """

    source_amount: Money
    method: AllocationMethod
    lines: tuple[AllocationLine, ...]
    total_allocated: Money
    unallocated: Money
    rounding_adjustment: Money

    @property
    def is_fully_allocated(self) -> bool:
        """True if entire source amount was allocated."""
        return self.unallocated.is_zero

    @property
    def allocation_count(self) -> int:
        """Number of targets that received allocations."""
        return sum(1 for line in self.lines if not line.allocated.is_zero)


class AllocationEngine:
    """
    Allocate amounts across multiple targets.

    Pure functions with deterministic rounding.
    No I/O, no database access.

    Rounding Strategy:
        - All intermediate calculations use full precision
        - Final amounts rounded to currency decimal places
        - Rounding difference assigned to designated target (last by default)
        - This ensures total allocated always equals source amount
    """

    def allocate(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        method: AllocationMethod,
        rounding_target_index: int | None = None,
    ) -> AllocationResult:
        """
        Allocate amount to targets using specified method.

        Args:
            amount: Amount to allocate
            targets: Sequence of allocation targets
            method: Allocation method to use
            rounding_target_index: Which target gets rounding adjustment (default: last)

        Returns:
            AllocationResult with all allocation details
        """
        t0 = time.monotonic()
        logger.info("allocation_started", extra={
            "amount": str(amount.amount),
            "currency": amount.currency.code,
            "method": method.value,
            "target_count": len(targets),
        })

        if not targets:
            logger.warning("allocation_no_targets", extra={
                "amount": str(amount.amount),
                "method": method.value,
            })
            return AllocationResult(
                source_amount=amount,
                method=method,
                lines=(),
                total_allocated=Money.zero(amount.currency),
                unallocated=amount,
                rounding_adjustment=Money.zero(amount.currency),
            )

        match method:
            case AllocationMethod.PRORATA:
                return self._allocate_prorata(amount, targets, rounding_target_index)
            case AllocationMethod.FIFO:
                return self._allocate_fifo(amount, targets)
            case AllocationMethod.LIFO:
                return self._allocate_lifo(amount, targets)
            case AllocationMethod.SPECIFIC:
                return self._allocate_specific(amount, targets)
            case AllocationMethod.WEIGHTED:
                return self._allocate_weighted(amount, targets, rounding_target_index)
            case AllocationMethod.EQUAL:
                return self._allocate_equal(amount, targets, rounding_target_index)
            case _:
                logger.error("allocation_unknown_method", extra={
                    "method": str(method),
                })
                raise ValueError(f"Unknown allocation method: {method}")

    def allocate_prorata(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        rounding_target_index: int | None = None,
    ) -> AllocationResult:
        """Convenience method for pro-rata allocation."""
        return self._allocate_prorata(amount, targets, rounding_target_index)

    def allocate_fifo(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
    ) -> AllocationResult:
        """Convenience method for FIFO allocation."""
        return self._allocate_fifo(amount, targets)

    def allocate_equal(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        rounding_target_index: int | None = None,
    ) -> AllocationResult:
        """Convenience method for equal allocation."""
        return self._allocate_equal(amount, targets, rounding_target_index)

    def _allocate_prorata(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        rounding_target_index: int | None,
    ) -> AllocationResult:
        """Allocate proportionally by eligible amount."""
        # Calculate total eligible
        total_eligible = Decimal("0")
        for target in targets:
            if target.eligible_amount is None:
                raise ValueError(
                    f"Target {target.target_id} missing eligible_amount for prorata"
                )
            if target.eligible_amount.currency != amount.currency:
                raise ValueError(
                    f"Currency mismatch: {target.eligible_amount.currency} vs {amount.currency}"
                )
            total_eligible += target.eligible_amount.amount

        if total_eligible == Decimal("0"):
            # Nothing eligible, return unallocated
            return AllocationResult(
                source_amount=amount,
                method=AllocationMethod.PRORATA,
                lines=tuple(
                    AllocationLine(
                        target_id=t.target_id,
                        target_type=t.target_type,
                        allocated=Money.zero(amount.currency),
                        remaining=t.eligible_amount or Money.zero(amount.currency),
                        is_fully_allocated=True,
                    )
                    for t in targets
                ),
                total_allocated=Money.zero(amount.currency),
                unallocated=amount,
                rounding_adjustment=Money.zero(amount.currency),
            )

        return self._allocate_by_ratio(
            amount=amount,
            targets=targets,
            method=AllocationMethod.PRORATA,
            get_ratio=lambda t: t.eligible_amount.amount / total_eligible,
            rounding_target_index=rounding_target_index,
        )

    def _allocate_weighted(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        rounding_target_index: int | None,
    ) -> AllocationResult:
        """Allocate by explicit weight factors."""
        total_weight = sum(t.weight for t in targets)

        if total_weight == Decimal("0"):
            raise ValueError("Total weight cannot be zero")

        return self._allocate_by_ratio(
            amount=amount,
            targets=targets,
            method=AllocationMethod.WEIGHTED,
            get_ratio=lambda t: t.weight / total_weight,
            rounding_target_index=rounding_target_index,
        )

    def _allocate_equal(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        rounding_target_index: int | None,
    ) -> AllocationResult:
        """Allocate equally to all targets."""
        count = len(targets)
        return self._allocate_by_ratio(
            amount=amount,
            targets=targets,
            method=AllocationMethod.EQUAL,
            get_ratio=lambda t: Decimal("1") / Decimal(str(count)),
            rounding_target_index=rounding_target_index,
        )

    def _allocate_by_ratio(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
        method: AllocationMethod,
        get_ratio: callable,
        rounding_target_index: int | None,
    ) -> AllocationResult:
        """Common logic for ratio-based allocations."""
        if rounding_target_index is None:
            rounding_target_index = len(targets) - 1

        currency = amount.currency
        decimal_places = Decimal(10) ** -currency.decimal_places
        lines: list[AllocationLine] = []
        allocated_so_far = Decimal("0")

        for i, target in enumerate(targets):
            is_rounding_target = i == rounding_target_index

            if is_rounding_target:
                # Rounding target gets remainder
                allocated_amount = amount.amount - allocated_so_far
            else:
                ratio = get_ratio(target)
                allocated_amount = (amount.amount * ratio).quantize(
                    decimal_places, rounding=ROUND_HALF_UP
                )
                allocated_so_far += allocated_amount

            allocated_money = Money.of(allocated_amount, currency)

            # Calculate remaining on target
            if target.eligible_amount is not None:
                remaining = target.eligible_amount - allocated_money
                if remaining.amount < Decimal("0"):
                    # Over-allocated: cap at eligible, track excess
                    allocated_money = target.eligible_amount
                    remaining = Money.zero(currency)
                is_fully = remaining.is_zero
            else:
                remaining = Money.zero(currency)
                is_fully = True

            lines.append(
                AllocationLine(
                    target_id=target.target_id,
                    target_type=target.target_type,
                    allocated=allocated_money,
                    remaining=remaining,
                    is_fully_allocated=is_fully,
                )
            )

        total_allocated = sum(
            (line.allocated.amount for line in lines),
            Decimal("0"),
        )
        total_allocated_money = Money.of(total_allocated, currency)
        unallocated = amount - total_allocated_money

        # Calculate rounding adjustment (difference from naive allocation)
        naive_total = sum(
            (amount.amount * get_ratio(t)).quantize(decimal_places, rounding=ROUND_HALF_UP)
            for t in targets
        )
        rounding_adjustment = Money.of(amount.amount - naive_total, currency)

        logger.info("allocation_by_ratio_completed", extra={
            "method": method.value,
            "source_amount": str(amount.amount),
            "total_allocated": str(total_allocated),
            "unallocated": str(unallocated.amount),
            "rounding_adjustment": str(rounding_adjustment.amount),
            "line_count": len(lines),
        })

        return AllocationResult(
            source_amount=amount,
            method=method,
            lines=tuple(lines),
            total_allocated=total_allocated_money,
            unallocated=unallocated,
            rounding_adjustment=rounding_adjustment,
        )

    def _allocate_fifo(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
    ) -> AllocationResult:
        """Allocate to oldest first (by date)."""
        # Sort by date (oldest first), then by priority
        sorted_targets = sorted(
            targets,
            key=lambda t: (t.date or date.min, t.priority),
        )
        return self._allocate_sequential(amount, sorted_targets, AllocationMethod.FIFO)

    def _allocate_lifo(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
    ) -> AllocationResult:
        """Allocate to newest first (by date)."""
        # Sort by date (newest first), then by priority
        sorted_targets = sorted(
            targets,
            key=lambda t: (t.date or date.max, -t.priority),
            reverse=True,
        )
        return self._allocate_sequential(amount, sorted_targets, AllocationMethod.LIFO)

    def _allocate_specific(
        self,
        amount: Money,
        targets: Sequence[AllocationTarget],
    ) -> AllocationResult:
        """Allocate in priority order (user-designated)."""
        # Sort by priority (lower = higher priority)
        sorted_targets = sorted(targets, key=lambda t: t.priority)
        return self._allocate_sequential(amount, sorted_targets, AllocationMethod.SPECIFIC)

    def _allocate_sequential(
        self,
        amount: Money,
        sorted_targets: Sequence[AllocationTarget],
        method: AllocationMethod,
    ) -> AllocationResult:
        """
        Allocate sequentially until amount exhausted.

        Each target receives up to its eligible amount.
        """
        currency = amount.currency
        remaining_to_allocate = amount.amount
        lines: list[AllocationLine] = []

        for target in sorted_targets:
            if remaining_to_allocate <= Decimal("0"):
                # Nothing left to allocate
                lines.append(
                    AllocationLine(
                        target_id=target.target_id,
                        target_type=target.target_type,
                        allocated=Money.zero(currency),
                        remaining=target.eligible_amount or Money.zero(currency),
                        is_fully_allocated=False,
                    )
                )
                continue

            eligible = (
                target.eligible_amount.amount
                if target.eligible_amount
                else remaining_to_allocate
            )

            # Allocate min of remaining and eligible
            to_allocate = min(remaining_to_allocate, eligible)
            remaining_to_allocate -= to_allocate

            allocated_money = Money.of(to_allocate, currency)
            target_remaining = Money.of(eligible - to_allocate, currency)

            lines.append(
                AllocationLine(
                    target_id=target.target_id,
                    target_type=target.target_type,
                    allocated=allocated_money,
                    remaining=target_remaining,
                    is_fully_allocated=target_remaining.is_zero,
                )
            )

        total_allocated = amount.amount - remaining_to_allocate
        total_allocated_money = Money.of(total_allocated, currency)
        unallocated = Money.of(remaining_to_allocate, currency)

        logger.info("allocation_sequential_completed", extra={
            "method": method.value,
            "source_amount": str(amount.amount),
            "total_allocated": str(total_allocated),
            "unallocated": str(remaining_to_allocate),
            "targets_funded": sum(1 for l in lines if not l.allocated.is_zero),
            "line_count": len(lines),
        })

        return AllocationResult(
            source_amount=amount,
            method=method,
            lines=tuple(lines),
            total_allocated=total_allocated_money,
            unallocated=unallocated,
            rounding_adjustment=Money.zero(currency),  # No rounding in sequential
        )
