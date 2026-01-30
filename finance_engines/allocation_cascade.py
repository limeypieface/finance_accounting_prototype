"""
Cascading Indirect Cost Allocation Engine for DCAA Compliance.

Pure functions with deterministic behavior. No I/O.

This engine handles multi-step indirect cost allocation required for
government contract accounting (FAR/CAS compliance):

    Direct Labor → Apply Fringe Rate → Fringe Pool
                ↓
    Direct Costs + Fringe → Apply Overhead Rate → Overhead Pool
                ↓
    Direct + Fringe + Overhead → Apply G&A Rate → G&A Pool
                ↓
    Total Cost by Contract

Usage:
    from finance_engines.allocation_cascade import (
        AllocationStep,
        execute_cascade,
        build_dcaa_cascade,
    )
    from finance_kernel.domain.values import Money

    steps = build_dcaa_cascade()
    balances = {
        "DIRECT_LABOR": Money.of("100000.00", "USD"),
        "DIRECT_MATERIAL": Money.of("50000.00", "USD"),
    }
    rates = {
        "fringe": Decimal("0.35"),
        "overhead": Decimal("0.45"),
        "g&a": Decimal("0.10"),
    }

    results, final_balances = execute_cascade(steps, balances, rates, "USD")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_engines.tracer import traced_engine

logger = get_logger("engines.allocation_cascade")


class AllocationBase:
    """Constants for allocation base types."""

    POOL_BALANCE = "pool_balance"  # Apply rate to single pool
    CUMULATIVE = "cumulative"  # Apply rate to running total


@dataclass(frozen=True)
class AllocationStep:
    """
    Single step in indirect cost allocation cascade.

    Immutable value object defining how to allocate from one pool to another.

    Attributes:
        pool_from: Source pool dimension value (e.g., "DIRECT_LABOR")
        pool_to: Target pool dimension value (e.g., "FRINGE")
        rate_type: Rate identifier (e.g., "fringe", "overhead", "g&a")
        base: What to apply rate to - "pool_balance" or "cumulative"
        description: Human-readable description of this step

    Example:
        Apply 35% fringe rate to direct labor:
        AllocationStep("DIRECT_LABOR", "FRINGE", "fringe", "pool_balance")
    """

    pool_from: str
    pool_to: str
    rate_type: str
    base: str = AllocationBase.POOL_BALANCE
    description: str = ""

    def __post_init__(self) -> None:
        if self.base not in (AllocationBase.POOL_BALANCE, AllocationBase.CUMULATIVE):
            raise ValueError(
                f"Invalid base type: {self.base}. "
                f"Must be '{AllocationBase.POOL_BALANCE}' or '{AllocationBase.CUMULATIVE}'"
            )


@dataclass(frozen=True)
class AllocationStepResult:
    """
    Result of executing a single allocation step.

    Immutable value object capturing the outcome of one step.

    Attributes:
        step: The step that was executed
        source_balance: Balance of source pool at time of execution
        rate_applied: The rate that was applied (may be 0 if rate not found)
        amount_allocated: Amount moved from implicit source to target pool
        cumulative_base: Running total after this step (for next step's reference)
    """

    step: AllocationStep
    source_balance: Money
    rate_applied: Decimal
    amount_allocated: Money
    cumulative_base: Money


@traced_engine("allocation_cascade", "1.0", fingerprint_fields=("steps", "pool_balances", "rates"))
def execute_cascade(
    steps: Sequence[AllocationStep],
    pool_balances: dict[str, Money],
    rates: dict[str, Decimal],
    currency: str,
) -> tuple[list[AllocationStepResult], dict[str, Money]]:
    """
    Execute multi-step indirect cost allocation cascade.

    Pure function - no side effects, no I/O, deterministic output.

    Args:
        steps: Ordered sequence of allocation steps to execute
        pool_balances: Current balance by pool dimension value
        rates: Rate by rate_type (e.g., {"fringe": Decimal("0.35")})
        currency: Currency code for zero amounts and consistency

    Returns:
        Tuple of:
        - List of AllocationStepResult for each step executed
        - Final pool balances after all allocations

    Raises:
        ValueError: If step has invalid base type

    Example:
        >>> steps = [
        ...     AllocationStep("DIRECT_LABOR", "FRINGE", "fringe", "pool_balance"),
        ...     AllocationStep("DIRECT_COST", "OVERHEAD", "overhead", "cumulative"),
        ...     AllocationStep("TOTAL_DIRECT", "G&A", "g&a", "cumulative"),
        ... ]
        >>> balances = {"DIRECT_LABOR": Money.of("100000", "USD")}
        >>> rates = {"fringe": Decimal("0.35")}
        >>> results, final = execute_cascade(steps, balances, rates, "USD")

    Notes:
        - Missing pools are treated as zero balance
        - Missing rates are treated as zero (no allocation)
        - Input pool_balances dict is NOT mutated
        - Results are rounded to 2 decimal places using ROUND_HALF_UP
    """
    t0 = time.monotonic()
    logger.info("cascade_execution_started", extra={
        "step_count": len(steps),
        "pool_count": len(pool_balances),
        "rate_count": len(rates),
        "currency": currency,
    })

    results: list[AllocationStepResult] = []
    balances = dict(pool_balances)  # Don't mutate input
    zero = Money.zero(currency)
    cumulative = zero

    for step in steps:
        # Get source balance (default to zero if pool doesn't exist)
        source = balances.get(step.pool_from, zero)

        # Determine base for rate application
        if step.base == AllocationBase.POOL_BALANCE:
            base_amount = source
        elif step.base == AllocationBase.CUMULATIVE:
            base_amount = cumulative
        else:
            # Should not happen due to __post_init__ validation
            raise ValueError(f"Unknown base type: {step.base}")

        # Get rate (default to zero if not found)
        rate = rates.get(step.rate_type, Decimal("0"))
        if step.rate_type not in rates:
            logger.warning("cascade_step_rate_not_found", extra={
                "rate_type": step.rate_type,
                "pool_from": step.pool_from,
                "pool_to": step.pool_to,
            })

        # Calculate allocation amount with deterministic rounding
        allocated_amount_raw = base_amount.amount * rate
        allocated_amount = Money.of(
            allocated_amount_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            currency,
        )

        # Update target pool balance
        target_balance = balances.get(step.pool_to, zero)
        balances[step.pool_to] = Money.of(
            target_balance.amount + allocated_amount.amount,
            currency,
        )

        # Update cumulative for next step
        # Cumulative includes: previous cumulative + source balance + allocated amount
        cumulative = Money.of(
            cumulative.amount + source.amount + allocated_amount.amount,
            currency,
        )

        logger.debug("cascade_step_executed", extra={
            "pool_from": step.pool_from,
            "pool_to": step.pool_to,
            "rate_type": step.rate_type,
            "base_type": step.base,
            "source_balance": str(source.amount),
            "rate_applied": str(rate),
            "amount_allocated": str(allocated_amount.amount),
            "cumulative_base": str(cumulative.amount),
        })

        results.append(
            AllocationStepResult(
                step=step,
                source_balance=source,
                rate_applied=rate,
                amount_allocated=allocated_amount,
                cumulative_base=cumulative,
            )
        )

    duration_ms = round((time.monotonic() - t0) * 1000, 2)
    logger.info("cascade_execution_completed", extra={
        "step_count": len(results),
        "final_pool_count": len(balances),
        "duration_ms": duration_ms,
    })

    return results, balances


def build_dcaa_cascade() -> tuple[AllocationStep, ...]:
    """
    Build standard DCAA indirect cost allocation cascade.

    Returns tuple of steps for typical government contract costing:
    1. Fringe benefits applied to direct labor
    2. Overhead applied to direct costs plus fringe
    3. G&A applied to total costs (direct + fringe + overhead)

    Returns:
        Tuple of AllocationStep defining the standard cascade

    Usage:
        >>> steps = build_dcaa_cascade()
        >>> results, final = execute_cascade(steps, balances, rates, "USD")

    Notes:
        This is a convenience function providing a standard cascade.
        Custom cascades can be built by creating AllocationStep tuples directly.
    """
    return (
        AllocationStep(
            pool_from="DIRECT_LABOR",
            pool_to="FRINGE",
            rate_type="fringe",
            base=AllocationBase.POOL_BALANCE,
            description="Apply fringe benefits rate to direct labor",
        ),
        AllocationStep(
            pool_from="DIRECT_COST",
            pool_to="OVERHEAD",
            rate_type="overhead",
            base=AllocationBase.CUMULATIVE,
            description="Apply overhead rate to direct costs plus fringe",
        ),
        AllocationStep(
            pool_from="TOTAL_DIRECT",
            pool_to="G&A",
            rate_type="g&a",
            base=AllocationBase.CUMULATIVE,
            description="Apply G&A rate to total costs",
        ),
    )


def calculate_contract_total(
    pool_balances: dict[str, Money],
    direct_pools: Sequence[str],
    indirect_pools: Sequence[str],
    currency: str,
) -> Money:
    """
    Calculate total contract cost from pool balances.

    Convenience function to sum direct and indirect cost pools
    into a total contract cost.

    Args:
        pool_balances: Pool balances after cascade execution
        direct_pools: Pool codes for direct costs (e.g., ["DIRECT_LABOR", "DIRECT_MATERIAL"])
        indirect_pools: Pool codes for indirect costs (e.g., ["FRINGE", "OVERHEAD", "G&A"])
        currency: Currency code for the result

    Returns:
        Total contract cost (sum of all specified pools)

    Example:
        >>> total = calculate_contract_total(
        ...     final_balances,
        ...     direct_pools=["DIRECT_LABOR", "DIRECT_MATERIAL", "DIRECT_OTHER"],
        ...     indirect_pools=["FRINGE", "OVERHEAD", "G&A"],
        ...     currency="USD",
        ... )
    """
    zero = Money.zero(currency)
    total = Decimal("0")

    for pool in direct_pools:
        total += pool_balances.get(pool, zero).amount

    for pool in indirect_pools:
        total += pool_balances.get(pool, zero).amount

    logger.info("contract_total_calculated", extra={
        "direct_pool_count": len(direct_pools),
        "indirect_pool_count": len(indirect_pools),
        "total_amount": str(total),
        "currency": currency,
    })

    return Money.of(total, currency)
