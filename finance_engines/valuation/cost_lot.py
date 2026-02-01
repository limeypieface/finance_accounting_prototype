"""
finance_engines.valuation.cost_lot -- Cost lot domain objects for the ValuationLayer.

Responsibility:
    Define immutable value objects for inventory cost lots, layers,
    layer consumption details, consumption results, and standard cost
    variance tracking.  These model the FIFO/LIFO/specific/standard
    cost flow through inventory.

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values (Money, Quantity) and
    finance_kernel/domain/economic_link (ArtifactRef, EconomicLink).
    The stateful ValuationService lives in finance_services/.

Invariants enforced:
    - Positive lot quantity: CostLot.__post_init__ rejects quantity <= 0.
    - Non-negative cost: CostLot.__post_init__ rejects negative costs.
    - R6 (replay safety): all value objects are frozen dataclasses.
    - Consumption non-empty: ConsumptionResult.create rejects empty
      consumption lists.
    - EconomicLink trail: ConsumptionResult records CONSUMED_BY links
      for every layer consumed.

Failure modes:
    - ValueError from CostLot.__post_init__ if quantity <= 0 or cost < 0.
    - ValueError from ConsumptionResult.create if consumptions list is empty.
    - Division-by-zero safe: unit_cost returns Money.zero when quantity is 0;
      average_unit_cost returns Money.zero when total_quantity is 0.

Audit relevance:
    Cost lots are the foundation for COGS calculations and inventory
    valuation.  Each lot's source_ref traces back to the receiving event.
    Consumption links (CONSUMED_BY) provide a complete audit trail from
    issuance back to receipt.  Standard cost variances are tracked
    explicitly in StandardCostResult.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType, EconomicLink
from finance_kernel.domain.values import Money, Quantity
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.valuation.cost_lot")


class CostMethod(str, Enum):
    """Cost valuation methods."""

    FIFO = "fifo"           # First-in, first-out
    LIFO = "lifo"           # Last-in, first-out
    STANDARD = "standard"   # Standard cost with variance tracking
    SPECIFIC = "specific"   # Specific identification (lot picking)
    WEIGHTED_AVG = "weighted_avg"  # Weighted average (for future use)


@dataclass(frozen=True, slots=True)
class CostLot:
    """
    Immutable cost lot representing a batch of inventory at a specific cost.

    A cost lot is created when inventory is received (purchase, production, etc.)
    and consumed when inventory is issued (sale, usage, etc.).

    The lot tracks:
    - Original quantity and cost at acquisition
    - Unit cost (derived from total/quantity)
    - Date for FIFO/LIFO ordering
    - Source reference (what created this lot)

    Remaining quantity is NOT stored here - it's derived from EconomicLink
    CONSUMED_BY relationships via LinkGraphService.get_unconsumed_value().
    """

    lot_id: UUID
    item_id: str
    location_id: str | None
    lot_date: date  # For FIFO/LIFO ordering
    original_quantity: Quantity
    original_cost: Money
    cost_method: CostMethod
    source_ref: ArtifactRef  # What created this lot (receipt, production order, etc.)
    metadata: Mapping[str, Any] | None = None  # Additional lot attributes

    def __post_init__(self) -> None:
        if self.original_quantity.value <= 0:
            logger.error("cost_lot_invalid_quantity", extra={
                "lot_id": str(self.lot_id),
                "item_id": self.item_id,
                "quantity": str(self.original_quantity.value),
            })
            raise ValueError(f"Lot quantity must be positive, got {self.original_quantity}")
        if self.original_cost.is_negative:
            logger.error("cost_lot_negative_cost", extra={
                "lot_id": str(self.lot_id),
                "item_id": self.item_id,
                "cost": str(self.original_cost.amount),
            })
            raise ValueError(f"Lot cost cannot be negative, got {self.original_cost}")

    @property
    def lot_ref(self) -> ArtifactRef:
        """Get ArtifactRef for this lot."""
        return ArtifactRef.cost_lot(self.lot_id)

    @property
    def unit_cost(self) -> Money:
        """Calculate unit cost from total cost and quantity."""
        if self.original_quantity.value == 0:
            return Money.zero(self.original_cost.currency.code)
        unit_amount = self.original_cost.amount / self.original_quantity.value
        return Money.of(unit_amount, self.original_cost.currency.code)

    @classmethod
    def create(
        cls,
        lot_id: UUID,
        item_id: str,
        quantity: Quantity,
        total_cost: Money,
        lot_date: date,
        source_ref: ArtifactRef,
        location_id: str | None = None,
        cost_method: CostMethod = CostMethod.FIFO,
        metadata: Mapping[str, Any] | None = None,
    ) -> CostLot:
        """Factory method to create a new cost lot.

        Preconditions:
            quantity.value > 0 and total_cost >= 0.

        Postconditions:
            Returns a frozen CostLot with unit_cost = total_cost / quantity.

        Raises:
            ValueError: If quantity <= 0 or total_cost < 0 (via __post_init__).
        """
        logger.info("cost_lot_created", extra={
            "lot_id": str(lot_id),
            "item_id": item_id,
            "quantity": str(quantity.value),
            "unit": quantity.unit,
            "total_cost": str(total_cost.amount),
            "currency": total_cost.currency.code,
            "lot_date": lot_date.isoformat(),
            "cost_method": cost_method.value,
            "location_id": location_id,
        })

        return cls(
            lot_id=lot_id,
            item_id=item_id,
            location_id=location_id,
            lot_date=lot_date,
            original_quantity=quantity,
            original_cost=total_cost,
            cost_method=cost_method,
            source_ref=source_ref,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class CostLayer:
    """
    A cost layer showing current state of a lot (original + remaining).

    This is a view object that combines the immutable CostLot with
    dynamically calculated remaining values from the link graph.
    """

    lot: CostLot
    remaining_quantity: Quantity
    remaining_value: Money
    consumption_count: int  # Number of consumption events

    @property
    def consumed_quantity(self) -> Quantity:
        """Quantity that has been consumed."""
        consumed_value = self.lot.original_quantity.value - self.remaining_quantity.value
        return Quantity(value=consumed_value, unit=self.lot.original_quantity.unit)

    @property
    def consumed_value(self) -> Money:
        """Value that has been consumed."""
        consumed_amount = self.lot.original_cost.amount - self.remaining_value.amount
        return Money.of(consumed_amount, self.lot.original_cost.currency.code)

    @property
    def is_depleted(self) -> bool:
        """True if lot has no remaining quantity."""
        return self.remaining_quantity.value <= 0

    @property
    def is_available(self) -> bool:
        """True if lot has remaining quantity."""
        return self.remaining_quantity.value > 0

    @property
    def unit_cost(self) -> Money:
        """Unit cost (from the lot)."""
        return self.lot.unit_cost

    @classmethod
    def from_lot_with_remaining(
        cls,
        lot: CostLot,
        remaining_quantity: Quantity,
        consumption_count: int = 0,
    ) -> CostLayer:
        """Create a layer from a lot with calculated remaining.

        Preconditions:
            remaining_quantity.value >= 0 (may be 0 for depleted lots).

        Postconditions:
            remaining_value is computed as remaining_quantity * lot.unit_cost.
        """
        # Calculate remaining value based on remaining quantity and unit cost
        remaining_value = Money.of(
            remaining_quantity.value * lot.unit_cost.amount,
            lot.original_cost.currency.code,
        )
        return cls(
            lot=lot,
            remaining_quantity=remaining_quantity,
            remaining_value=remaining_value,
            consumption_count=consumption_count,
        )

    @classmethod
    def full_lot(cls, lot: CostLot) -> CostLayer:
        """Create a layer representing a full (unconsumed) lot.

        Postconditions:
            remaining_quantity == lot.original_quantity,
            remaining_value == lot.original_cost,
            consumption_count == 0.
        """
        return cls(
            lot=lot,
            remaining_quantity=lot.original_quantity,
            remaining_value=lot.original_cost,
            consumption_count=0,
        )


@dataclass(frozen=True, slots=True)
class CostLayerConsumption:
    """
    Detail of consumption from a single cost layer.

    Part of a ConsumptionResult showing how much was taken from each lot.
    """

    lot_ref: ArtifactRef
    lot_id: UUID
    quantity_consumed: Quantity
    cost_consumed: Money
    unit_cost: Money
    remaining_in_lot: Quantity

    @classmethod
    def create(
        cls,
        layer: CostLayer,
        quantity_consumed: Quantity,
    ) -> CostLayerConsumption:
        """Create consumption detail from a layer and quantity."""
        cost_consumed = Money.of(
            quantity_consumed.value * layer.unit_cost.amount,
            layer.unit_cost.currency.code,
        )
        remaining = Quantity(
            value=layer.remaining_quantity.value - quantity_consumed.value,
            unit=layer.remaining_quantity.unit,
        )
        return cls(
            lot_ref=layer.lot.lot_ref,
            lot_id=layer.lot.lot_id,
            quantity_consumed=quantity_consumed,
            cost_consumed=cost_consumed,
            unit_cost=layer.unit_cost,
            remaining_in_lot=remaining,
        )


@dataclass(frozen=True, slots=True)
class ConsumptionResult:
    """
    Result of consuming inventory from cost layers.

    Contains:
    - Detail of each layer consumed
    - Total quantity and cost consumed
    - Links created for audit trail
    """

    consuming_event_ref: ArtifactRef  # What consumed the inventory
    item_id: str
    cost_method: CostMethod
    layers_consumed: tuple[CostLayerConsumption, ...]
    total_quantity: Quantity
    total_cost: Money
    links_created: tuple[EconomicLink, ...]

    @property
    def layer_count(self) -> int:
        """Number of layers consumed."""
        return len(self.layers_consumed)

    @property
    def average_unit_cost(self) -> Money:
        """Weighted average unit cost of consumed inventory."""
        if self.total_quantity.value == 0:
            return Money.zero(self.total_cost.currency.code)
        avg_amount = self.total_cost.amount / self.total_quantity.value
        return Money.of(avg_amount, self.total_cost.currency.code)

    @classmethod
    def create(
        cls,
        consuming_event_ref: ArtifactRef,
        item_id: str,
        cost_method: CostMethod,
        consumptions: list[CostLayerConsumption],
        links: list[EconomicLink],
    ) -> ConsumptionResult:
        """Create result from list of layer consumptions."""
        if not consumptions:
            logger.error("consumption_result_empty", extra={
                "consuming_event_ref": str(consuming_event_ref),
                "item_id": item_id,
            })
            raise ValueError("At least one consumption required")

        # Sum quantities and costs
        total_qty_value = sum(c.quantity_consumed.value for c in consumptions)
        total_cost_amount = sum(c.cost_consumed.amount for c in consumptions)

        # Use first consumption for units/currency
        first = consumptions[0]
        total_quantity = Quantity(value=total_qty_value, unit=first.quantity_consumed.unit)
        total_cost = Money.of(total_cost_amount, first.cost_consumed.currency.code)

        logger.info("consumption_result_created", extra={
            "consuming_event_ref": str(consuming_event_ref),
            "item_id": item_id,
            "cost_method": cost_method.value,
            "layer_count": len(consumptions),
            "total_quantity": str(total_qty_value),
            "total_cost": str(total_cost_amount),
            "links_created": len(links),
        })

        return cls(
            consuming_event_ref=consuming_event_ref,
            item_id=item_id,
            cost_method=cost_method,
            layers_consumed=tuple(consumptions),
            total_quantity=total_quantity,
            total_cost=total_cost,
            links_created=tuple(links),
        )


@dataclass(frozen=True, slots=True)
class StandardCostResult:
    """
    Result of consuming at standard cost with variance tracking.

    Used when cost_method is STANDARD - issues inventory at a predetermined
    standard cost and tracks the variance from actual cost.
    """

    consumption: ConsumptionResult
    standard_cost: Money  # What we charged (standard)
    actual_cost: Money    # What it actually cost (from layers)
    variance: Money       # standard - actual (favorable if positive)

    @property
    def is_favorable(self) -> bool:
        """True if actual cost was less than standard."""
        return self.variance.is_positive

    @property
    def is_unfavorable(self) -> bool:
        """True if actual cost was more than standard."""
        return self.variance.is_negative

    @property
    def variance_percentage(self) -> Decimal:
        """Variance as percentage of standard cost."""
        if self.standard_cost.is_zero:
            return Decimal("0")
        return (self.variance.amount / self.standard_cost.amount) * 100
