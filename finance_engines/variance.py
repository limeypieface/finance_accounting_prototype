"""
finance_engines.variance -- Price, quantity, FX, and standard cost variance calculations.

Responsibility:
    Calculate variances between expected and actual values for prices,
    quantities, foreign exchange rates, and standard costs.  Provides
    variance allocation across multiple targets with deterministic
    rounding (remainder to last target).

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values.
    Consumed by the matching engine and the engine dispatcher (via invokers).

Invariants enforced:
    - R6 (replay safety): identical inputs produce identical outputs;
      no internal state or clock access.
    - R16 (ISO 4217): currency consistency enforced across expected/actual
      values; mismatches raise ValueError.
    - R17 (precision-derived tolerance): allocation rounding uses
      Decimal quantize to 2 decimal places; remainder assigned to last target.
    - Purity: no clock access, no I/O.

Failure modes:
    - ValueError from ``price_variance`` and ``standard_cost_variance`` if
      expected and actual currencies do not match.
    - ValueError from ``allocate_variance`` if total weight is zero.
    - Division-by-zero safe: variance_percent returns Decimal("0") when
      expected is zero.

Audit relevance:
    Variance results drive PPV (Purchase Price Variance) and SPV (Standard
    Price Variance) postings to variance accounts.  FX variances support
    period-end revaluation entries.  All calculations are traced via
    ``@traced_engine``.

Usage:
    from finance_engines.variance import VarianceCalculator, VarianceType
    from finance_kernel.domain.values import Money

    calculator = VarianceCalculator()
    result = calculator.price_variance(
        expected_price=Money.of("10.00", "USD"),
        actual_price=Money.of("10.50", "USD"),
        quantity=Decimal("100"),
    )
    print(result.variance)  # Money: 50.00 USD (unfavorable)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from finance_kernel.domain.values import Money, Currency
from finance_kernel.logging_config import get_logger
from finance_engines.tracer import traced_engine

logger = get_logger("engines.variance")


class VarianceType(str, Enum):
    """Type of variance being calculated."""

    PRICE = "price"  # PPV, SPV
    QUANTITY = "quantity"  # Usage variance
    FX = "fx"  # Exchange rate variance
    STANDARD_COST = "standard_cost"  # Actual vs standard cost


class VarianceDisposition(str, Enum):
    """What to do with a variance."""

    POST_TO_VARIANCE_ACCOUNT = "post"  # Standard: post to PPV account
    CAPITALIZE_TO_INVENTORY = "capitalize"  # Actual costing: adjust inventory
    ALLOCATE_TO_COGS = "allocate"  # Period-end allocation
    WRITE_OFF = "write_off"  # Immaterial variances


@dataclass(frozen=True)
class VarianceResult:
    """
    Result of a variance calculation.

    All fields are immutable. Use properties for derived values.
    """

    variance_type: VarianceType
    expected: Money
    actual: Money
    variance: Money
    is_favorable: bool
    description: str | None = None

    @property
    def variance_percent(self) -> Decimal:
        """Variance as a percentage of expected."""
        if self.expected.is_zero:
            return Decimal("0")
        return (self.variance.amount / self.expected.amount) * Decimal("100")

    @property
    def absolute_variance(self) -> Money:
        """Absolute value of variance."""
        if self.variance.amount < Decimal("0"):
            return self.variance * Decimal("-1")
        return self.variance


@dataclass(frozen=True)
class VarianceAllocation:
    """
    Allocation of variance to a target.

    Used when variance is distributed across multiple items.
    """

    target_id: str
    target_type: str
    allocated_variance: Money
    allocation_basis: Decimal  # Weight/percentage used


class VarianceCalculator:
    """
    Pure function calculator for variances.

    Contract:
        No I/O, no database access, fully deterministic.
        All reference data passed as parameters.
    Guarantees:
        - ``price_variance`` formula: (Actual - Expected) * Quantity.
        - ``quantity_variance`` formula: (Actual Qty - Expected Qty) * Standard Price.
        - ``fx_variance`` formula: Original Amount * (Current Rate - Original Rate).
        - ``allocate_variance`` distributes proportionally with rounding
          remainder assigned to the last target (deterministic).
        - ``is_favorable`` semantics: True when actual < expected for cost
          variances; True when gain for FX.
    Non-goals:
        - Does not interpret favorability in the context of assets vs
          liabilities for FX; callers must apply context.
        - Does not persist variance results.
    """

    @traced_engine("variance", "1.0", fingerprint_fields=("expected_price", "actual_price", "quantity"))
    def price_variance(
        self,
        expected_price: Money,
        actual_price: Money,
        quantity: Decimal,
    ) -> VarianceResult:
        """
        Calculate price variance (PPV, SPV, etc.).

        Formula: (Actual Price - Expected Price) x Quantity

        Preconditions:
            expected_price and actual_price must be in the same currency.
            quantity is a Decimal (never float).

        Postconditions:
            Returns VarianceResult where variance = (actual - expected) * qty.
            is_favorable is True when actual < expected (cost saving).

        Args:
            expected_price: Expected/standard unit price (e.g., PO price)
            actual_price: Actual unit price (e.g., invoice price)
            quantity: Quantity involved

        Returns:
            VarianceResult with variance amount

        Raises:
            ValueError: If currencies don't match
        """
        t0 = time.monotonic()
        logger.info("price_variance_started", extra={
            "expected_price": str(expected_price.amount),
            "actual_price": str(actual_price.amount),
            "quantity": str(quantity),
            "currency": expected_price.currency.code,
        })

        if expected_price.currency != actual_price.currency:
            logger.error("price_variance_currency_mismatch", extra={
                "expected_currency": expected_price.currency.code,
                "actual_currency": actual_price.currency.code,
            })
            raise ValueError(
                f"Currency mismatch: expected {expected_price.currency.code}, "
                f"got {actual_price.currency.code}"
            )

        expected_total = expected_price * quantity
        actual_total = actual_price * quantity
        variance = actual_total - expected_total

        # Favorable if actual < expected (spent less than expected)
        is_favorable = variance.amount < Decimal("0")

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("price_variance_calculated", extra={
            "variance_amount": str(variance.amount),
            "is_favorable": is_favorable,
            "expected_total": str(expected_total.amount),
            "actual_total": str(actual_total.amount),
            "duration_ms": duration_ms,
        })

        return VarianceResult(
            variance_type=VarianceType.PRICE,
            expected=expected_total,
            actual=actual_total,
            variance=variance,
            is_favorable=is_favorable,
            description=f"Price variance: {expected_price} → {actual_price} × {quantity}",
        )

    @traced_engine("variance", "1.0", fingerprint_fields=("expected_quantity", "actual_quantity", "standard_price"))
    def quantity_variance(
        self,
        expected_quantity: Decimal,
        actual_quantity: Decimal,
        standard_price: Money,
    ) -> VarianceResult:
        """
        Calculate quantity/usage variance.

        Formula: (Actual Quantity - Expected Quantity) x Standard Price

        Preconditions:
            expected_quantity and actual_quantity are Decimal (never float).
            standard_price is a valid Money instance.

        Postconditions:
            Returns VarianceResult where variance = (actual_qty - expected_qty) * price.
            is_favorable is True when actual_qty < expected_qty (usage saving).

        Args:
            expected_quantity: Expected/standard quantity
            actual_quantity: Actual quantity used
            standard_price: Standard price per unit

        Returns:
            VarianceResult with variance amount
        """
        t0 = time.monotonic()
        logger.info("quantity_variance_started", extra={
            "expected_quantity": str(expected_quantity),
            "actual_quantity": str(actual_quantity),
            "standard_price": str(standard_price.amount),
            "currency": standard_price.currency.code,
        })

        expected_total = standard_price * expected_quantity
        actual_total = standard_price * actual_quantity
        variance = actual_total - expected_total

        # Favorable if actual < expected (used less than expected)
        is_favorable = variance.amount < Decimal("0")

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("quantity_variance_calculated", extra={
            "variance_amount": str(variance.amount),
            "is_favorable": is_favorable,
            "duration_ms": duration_ms,
        })

        return VarianceResult(
            variance_type=VarianceType.QUANTITY,
            expected=expected_total,
            actual=actual_total,
            variance=variance,
            is_favorable=is_favorable,
            description=(
                f"Quantity variance: {expected_quantity} → {actual_quantity} "
                f"@ {standard_price}"
            ),
        )

    @traced_engine("variance", "1.0", fingerprint_fields=("original_amount", "original_rate", "current_rate"))
    def fx_variance(
        self,
        original_amount: Money,
        original_rate: Decimal,
        current_rate: Decimal,
        functional_currency: Currency | str,
    ) -> VarianceResult:
        """
        Calculate foreign exchange variance.

        Formula: Original Amount x (Current Rate - Original Rate)

        Preconditions:
            original_rate and current_rate are Decimal (never float).
            functional_currency is a valid ISO 4217 code or Currency.

        Postconditions:
            Returns VarianceResult where variance = amount * (current - original).
            Result currency is the functional_currency.
            is_favorable is True when the rate movement produces a gain.

        Args:
            original_amount: Amount in foreign currency
            original_rate: Exchange rate at booking
            current_rate: Current exchange rate
            functional_currency: Reporting currency

        Returns:
            VarianceResult with FX gain/loss
        """
        t0 = time.monotonic()
        func_code = functional_currency if isinstance(functional_currency, str) else functional_currency.code
        logger.info("fx_variance_started", extra={
            "original_amount": str(original_amount.amount),
            "source_currency": original_amount.currency.code,
            "original_rate": str(original_rate),
            "current_rate": str(current_rate),
            "functional_currency": func_code,
        })

        if isinstance(functional_currency, str):
            functional_currency = Currency(functional_currency)

        # Convert at original and current rates
        expected = Money.of(
            original_amount.amount * original_rate,
            functional_currency,
        )
        actual = Money.of(
            original_amount.amount * current_rate,
            functional_currency,
        )
        variance = actual - expected

        # For FX, favorable depends on whether it's an asset or liability
        # Positive variance = gain if asset, loss if liability
        # We report raw variance; caller interprets favorability based on context
        is_favorable = variance.amount > Decimal("0")

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("fx_variance_calculated", extra={
            "variance_amount": str(variance.amount),
            "is_favorable": is_favorable,
            "rate_change": str(current_rate - original_rate),
            "duration_ms": duration_ms,
        })

        return VarianceResult(
            variance_type=VarianceType.FX,
            expected=expected,
            actual=actual,
            variance=variance,
            is_favorable=is_favorable,
            description=(
                f"FX variance: {original_amount} @ {original_rate} → {current_rate}"
            ),
        )

    @traced_engine("variance", "1.0", fingerprint_fields=("standard_cost", "actual_cost", "quantity"))
    def standard_cost_variance(
        self,
        standard_cost: Money,
        actual_cost: Money,
        quantity: Decimal = Decimal("1"),
    ) -> VarianceResult:
        """
        Calculate standard cost variance.

        Formula: (Actual Cost - Standard Cost) x Quantity

        Preconditions:
            standard_cost and actual_cost must be in the same currency.
            quantity is a Decimal (never float).

        Postconditions:
            Returns VarianceResult where variance = (actual - standard) * qty.
            is_favorable is True when actual < standard (cost saving).

        Args:
            standard_cost: Standard/expected unit cost
            actual_cost: Actual unit cost
            quantity: Quantity (default 1 for total cost comparison)

        Returns:
            VarianceResult with cost variance
        """
        logger.info("standard_cost_variance_started", extra={
            "standard_cost": str(standard_cost.amount),
            "actual_cost": str(actual_cost.amount),
            "quantity": str(quantity),
            "currency": standard_cost.currency.code,
        })

        if standard_cost.currency != actual_cost.currency:
            logger.error("standard_cost_variance_currency_mismatch", extra={
                "standard_currency": standard_cost.currency.code,
                "actual_currency": actual_cost.currency.code,
            })
            raise ValueError(
                f"Currency mismatch: standard {standard_cost.currency.code}, "
                f"actual {actual_cost.currency.code}"
            )

        expected_total = standard_cost * quantity
        actual_total = actual_cost * quantity
        variance = actual_total - expected_total

        is_favorable = variance.amount < Decimal("0")

        logger.info("standard_cost_variance_calculated", extra={
            "variance_amount": str(variance.amount),
            "is_favorable": is_favorable,
        })

        return VarianceResult(
            variance_type=VarianceType.STANDARD_COST,
            expected=expected_total,
            actual=actual_total,
            variance=variance,
            is_favorable=is_favorable,
            description=f"Cost variance: {standard_cost} → {actual_cost} × {quantity}",
        )

    def allocate_variance(
        self,
        variance: VarianceResult,
        targets: list[tuple[str, str, Decimal]],  # (id, type, weight)
    ) -> list[VarianceAllocation]:
        """
        Allocate a variance across multiple targets by weight.

        Args:
            variance: The variance to allocate
            targets: List of (target_id, target_type, weight) tuples

        Returns:
            List of VarianceAllocation with proportional amounts

        Note:
            Rounding differences go to the last target (deterministic).
        """
        logger.info("variance_allocation_started", extra={
            "variance_type": variance.variance_type.value,
            "variance_amount": str(variance.variance.amount),
            "target_count": len(targets),
        })

        if not targets:
            logger.debug("variance_allocation_no_targets", extra={})
            return []

        total_weight = sum(t[2] for t in targets)
        if total_weight == Decimal("0"):
            logger.error("variance_allocation_zero_weight", extra={
                "target_count": len(targets),
            })
            raise ValueError("Total weight cannot be zero")

        allocations: list[VarianceAllocation] = []
        allocated_so_far = Money.zero(variance.variance.currency)

        for i, (target_id, target_type, weight) in enumerate(targets):
            is_last = i == len(targets) - 1

            if is_last:
                # Last target gets remainder (handles rounding)
                allocated_amount = variance.variance - allocated_so_far
            else:
                # Pro-rata allocation
                ratio = weight / total_weight
                allocated_amount = Money.of(
                    (variance.variance.amount * ratio).quantize(
                        Decimal("0.01")
                    ),
                    variance.variance.currency,
                )
                allocated_so_far = allocated_so_far + allocated_amount

            allocations.append(
                VarianceAllocation(
                    target_id=target_id,
                    target_type=target_type,
                    allocated_variance=allocated_amount,
                    allocation_basis=weight / total_weight,
                )
            )

        logger.info("variance_allocation_completed", extra={
            "allocations_count": len(allocations),
            "total_allocated": str(sum(a.allocated_variance.amount for a in allocations)),
        })

        return allocations
