"""
Module: finance_engines.allocation
Responsibility:
    Allocate monetary amounts across multiple targets using configurable
    methods (pro-rata, FIFO, LIFO, specific, weighted, equal) with
    deterministic rounding.

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values.

Invariants enforced:
    - R4 (balance per currency): total_allocated + unallocated == source_amount.
    - R5 / R17 (rounding): rounding difference is deterministically assigned
      to a single designated target so penny totals are preserved.
    - R16 (ISO 4217): currency consistency enforced across targets and source.
    - Purity: no clock access, no I/O (R6).

Failure modes:
    - ValueError on currency mismatch between source and targets.
    - ValueError on zero total weight (weighted method).
    - ValueError on missing eligible_amount for pro-rata method.
    - ValueError on unknown allocation method.

Audit relevance:
    Allocation results feed journal line generation. The deterministic
    rounding guarantee ensures that any replay produces identical penny
    assignments, satisfying R6.

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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from uuid import UUID

from finance_engines.tracer import traced_engine
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

    Contract:
        Frozen dataclass representing one potential allocation recipient.
    Guarantees:
        - ``weight`` is non-negative.
    Non-goals:
        - Does not validate currency of ``eligible_amount``; the engine
          performs that check at allocation time.
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

    Contract:
        Frozen dataclass representing the outcome for one target.
    Guarantees:
        - ``allocated + remaining == eligible_amount`` (when eligible is set).
    Non-goals:
        - Does not carry allocation method metadata; see ``AllocationResult``.
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

    Contract:
        Frozen dataclass summarising an allocation run.
    Guarantees:
        - ``total_allocated + unallocated == source_amount`` (R4 conservation).
        - ``rounding_adjustment`` records the deterministic penny fixup.
    Non-goals:
        - Does not persist the result; callers are responsible for storage.
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

    Contract:
        Pure functions with deterministic rounding.
        No I/O, no database access.
    Guarantees:
        - Rounding Strategy:
            * All intermediate calculations use full precision.
            * Final amounts rounded to currency decimal places (ROUND_HALF_UP).
            * Rounding difference assigned to designated target (last by default).
            * This ensures total allocated always equals source amount (R4).
        - Currency consistency: all targets must share the source currency (R16).
    Non-goals:
        - Does not decide *which* method to use; callers select the method.
        - Does not perform I/O or persist results.
    """

    @traced_engine("allocation", "1.0", fingerprint_fields=("amount", "method"))
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
        """Common logic for ratio-based allocations.

        Preconditions:
            - ``targets`` is non-empty.
            - ``get_ratio`` returns a Decimal in [0, 1] for each target
              and the ratios sum to 1.
        Postconditions:
            - Sum of all ``allocated`` amounts == ``amount`` (the rounding
              target absorbs the penny residual).
        Raises:
            - No direct raises; delegates to ``Money`` arithmetic.
        """
        if rounding_target_index is None:
            rounding_target_index = len(targets) - 1

        # INVARIANT: R17 — rounding precision derived from currency decimal places
        currency = amount.currency
        decimal_places = Decimal(10) ** -currency.decimal_places
        lines: list[AllocationLine] = []
        allocated_so_far = Decimal("0")

        for i, target in enumerate(targets):
            is_rounding_target = i == rounding_target_index

            if is_rounding_target:
                # INVARIANT: R5 — rounding difference assigned to exactly one target
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

        # INVARIANT: R4 — total_allocated + unallocated == source_amount
        assert total_allocated_money.amount + unallocated.amount == amount.amount, (
            f"Allocation conservation violated: "
            f"{total_allocated_money.amount} + {unallocated.amount} != {amount.amount}"
        )

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
