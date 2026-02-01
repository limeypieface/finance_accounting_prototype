"""
finance_services.valuation_service -- Cost lot management with FIFO/LIFO/Standard costing.

Responsibility:
    Manage inventory cost layers: create cost lots on receipt, consume
    lots via FIFO/LIFO/Specific/Standard selection, track remaining
    values via EconomicLink (CONSUMED_BY) relationships, and compute
    standard cost variances.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Composes AllocationEngine (for sequential allocation), LinkGraphService
    (for CONSUMED_BY links), and CostLotModel (for persistence).
    Pure domain types (CostLot, CostLayer, ConsumptionResult) live in
    finance_engines.valuation.cost_lot.

Invariants enforced:
    - R6 (replay safety): lot creation is idempotent via lot_id uniqueness.
    - EconomicLink trail: every consumption creates a CONSUMED_BY link
      from consuming_ref to the lot's source_ref.
    - Positive quantity: CostLot.__post_init__ rejects quantity <= 0.
    - Sufficient inventory: consume methods raise InsufficientInventoryError
      when requested quantity exceeds available.

Failure modes:
    - InsufficientInventoryError from consume_fifo/consume_lifo if
      requested quantity exceeds available inventory for the item.
    - LotNotFoundError from consume_specific if the specified lot_id
      does not exist.
    - LotDepletedError from consume_specific if the lot has no remaining
      quantity.
    - StandardCostNotFoundError from consume_standard if no standard cost
      is registered for the item.

Audit relevance:
    Every lot creation and consumption is logged with lot_id, item_id,
    quantity, cost, and creating_event_id.  CONSUMED_BY links provide a
    complete audit trail from issuance back to receipt.  Standard cost
    variances (actual vs standard) are tracked in StandardCostResult.

Usage:
    from finance_services.valuation_service import ValuationLayer
    from finance_kernel.services.link_graph_service import LinkGraphService

    link_service = LinkGraphService(session)
    valuation = ValuationLayer(session, link_service)

    # Create a cost lot
    lot = valuation.create_lot(
        lot_id=uuid4(),
        source_ref=ArtifactRef.receipt(receipt_id),
        item_id="WIDGET-001",
        quantity=Quantity(100, "EA"),
        total_cost=Money.of("1000.00", "USD"),
        lot_date=date.today(),
        creating_event_id=event_id,
    )

    # Consume via FIFO
    result = valuation.consume_fifo(
        consuming_ref=ArtifactRef.shipment(shipment_id),
        item_id="WIDGET-001",
        quantity=Quantity(25, "EA"),
        creating_event_id=event_id,
    )
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationTarget,
)
from finance_engines.valuation.cost_lot import (
    ConsumptionResult,
    CostLayer,
    CostLayerConsumption,
    CostLot,
    CostMethod,
    StandardCostResult,
)
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    ArtifactType,
    EconomicLink,
    LinkType,
)
from finance_kernel.domain.values import Money, Quantity
from finance_kernel.exceptions import (
    InsufficientInventoryError,
    LotDepletedError,
    LotNotFoundError,
    StandardCostNotFoundError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.models.cost_lot import CostLotModel
from finance_kernel.services.link_graph_service import LinkGraphService

logger = get_logger("services.valuation")


class ValuationLayer:
    """
    Manages inventory cost layers for valuation.

    Contract:
        Receives Session and LinkGraphService via constructor injection.
        Uses AllocationEngine for sequential (FIFO/LIFO) lot ordering.
    Guarantees:
        - ``create_lot`` persists a CostLotModel and returns a frozen
          CostLot value object.
        - ``consume_fifo`` consumes from oldest lots first.
        - ``consume_lifo`` consumes from newest lots first.
        - ``consume_specific`` consumes from a named lot only.
        - ``consume_standard`` uses standard cost and tracks variance.
        - Every consumption creates CONSUMED_BY economic links.
    Non-goals:
        - Does not manage subledger entries; that is the Inventory
          SubledgerService's responsibility.
        - Does not manage physical count reconciliation.

    This service handles:
    - Cost lot creation (when inventory is received)
    - Lot consumption (when inventory is issued)
    - Layer queries (what's available, what's consumed)
    - Standard costing with variance tracking

    The service uses EconomicLink relationships to track which lots
    have been consumed by which events, enabling:
    - Accurate remaining value calculations
    - Full audit trail of cost flow
    - Graph traversal for cost analysis
    """

    def __init__(
        self,
        session: Session,
        link_graph: LinkGraphService,
        lots_by_item: dict[str, list[CostLot]] | None = None,
    ):
        """
        Initialize the valuation layer.

        Args:
            session: SQLAlchemy session for database operations.
            link_graph: LinkGraphService for link operations.
            lots_by_item: Optional in-memory lot storage (for testing).
                         When None, lots are persisted to the cost_lots table.
        """
        self.session = session
        self.link_graph = link_graph
        self.allocation = AllocationEngine()

        # If lots_by_item is provided, use in-memory mode (for tests that
        # don't have the cost_lots table). Otherwise, use DB persistence.
        self._in_memory_lots: dict[str, list[CostLot]] | None = lots_by_item

        # Standard costs (item_id -> Money) — in-memory is acceptable here
        # as standard costs are configuration data reloaded per session.
        self._standard_costs: dict[str, Money] = {}

    # =========================================================================
    # Lot Creation
    # =========================================================================

    def create_lot(
        self,
        lot_id: UUID,
        source_ref: ArtifactRef,
        item_id: str,
        quantity: Quantity,
        total_cost: Money,
        lot_date: date,
        creating_event_id: UUID,
        location_id: str | None = None,
        cost_method: CostMethod = CostMethod.FIFO,
        metadata: Mapping[str, Any] | None = None,
    ) -> CostLot:
        """
        Create a new cost lot.

        Called when inventory is received (purchase receipt, production
        completion, transfer in, etc.).

        Creates a SOURCED_FROM link: source_ref -> lot (the receipt sources the lot).

        Args:
            lot_id: Unique identifier for the lot.
            source_ref: What created this lot (receipt, production order, etc.).
            item_id: The item being received.
            quantity: Quantity received.
            total_cost: Total cost of the lot.
            lot_date: Date for FIFO/LIFO ordering.
            creating_event_id: Event that triggered this lot creation.
            location_id: Optional location/warehouse.
            cost_method: Costing method for this lot.
            metadata: Additional lot attributes.

        Returns:
            The created CostLot.
        """
        logger.info("valuation_lot_creation_started", extra={
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

        lot = CostLot.create(
            lot_id=lot_id,
            item_id=item_id,
            quantity=quantity,
            total_cost=total_cost,
            lot_date=lot_date,
            source_ref=source_ref,
            location_id=location_id,
            cost_method=cost_method,
            metadata=metadata,
        )

        # Persist the lot
        self._store_lot(lot, creating_event_id)

        # Create SOURCED_FROM link: source -> lot
        link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.SOURCED_FROM,
            parent_ref=source_ref,
            child_ref=lot.lot_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(UTC),
            metadata={
                "quantity": str(quantity.value),
                "unit": quantity.unit,
                "total_cost": str(total_cost.amount),
                "currency": total_cost.currency.code,
            },
        )
        self.link_graph.establish_link(link, allow_duplicate=True)

        logger.info("valuation_lot_creation_completed", extra={
            "lot_id": str(lot_id),
            "item_id": item_id,
            "unit_cost": str(lot.unit_cost.amount),
        })

        return lot

    def get_lot(self, lot_id: UUID) -> CostLot | None:
        """Get a lot by ID."""
        if self._in_memory_lots is not None:
            for lots in self._in_memory_lots.values():
                for lot in lots:
                    if lot.lot_id == lot_id:
                        return lot
            return None
        return self._load_lot_by_id(lot_id)

    def get_lot_by_item(
        self,
        item_id: str,
        lot_id: UUID,
    ) -> CostLot | None:
        """Get a specific lot for an item."""
        if self._in_memory_lots is not None:
            lots = self._in_memory_lots.get(item_id, [])
            for lot in lots:
                if lot.lot_id == lot_id:
                    return lot
            return None
        return self._load_lot_by_id(lot_id)

    # =========================================================================
    # Layer Queries
    # =========================================================================

    def get_available_layers(
        self,
        item_id: str,
        location_id: str | None = None,
        as_of_date: date | None = None,
    ) -> list[CostLayer]:
        """
        Get all available cost layers for an item.

        Uses LinkGraphService.get_unconsumed_value() to calculate
        remaining quantities from CONSUMED_BY links.

        Args:
            item_id: The item to query.
            location_id: Optional location filter.
            as_of_date: Optional date filter (lots created on or before).

        Returns:
            List of CostLayer with remaining quantities.
        """
        logger.debug("available_layers_query_started", extra={
            "item_id": item_id,
            "location_id": location_id,
            "as_of_date": as_of_date.isoformat() if as_of_date else None,
        })

        lots = self._load_lots_for_item(item_id)
        layers: list[CostLayer] = []

        for lot in lots:
            # Apply filters
            if location_id and lot.location_id != location_id:
                continue
            if as_of_date and lot.lot_date > as_of_date:
                continue

            # Get unconsumed value via link graph
            unconsumed = self.link_graph.get_unconsumed_value(
                parent_ref=lot.lot_ref,
                original_amount=lot.original_cost,
                link_types=frozenset({LinkType.CONSUMED_BY}),
                amount_metadata_key="cost_consumed",
            )

            # Calculate remaining quantity from remaining value
            if lot.unit_cost.is_zero:
                remaining_qty_amount = lot.original_quantity.value
            else:
                remaining_qty_amount = (
                    unconsumed.remaining_amount.amount / lot.unit_cost.amount
                )

            remaining_quantity = Quantity(
                value=remaining_qty_amount,
                unit=lot.original_quantity.unit,
            )

            layer = CostLayer(
                lot=lot,
                remaining_quantity=remaining_quantity,
                remaining_value=unconsumed.remaining_amount,
                consumption_count=unconsumed.child_count,
            )

            # Only include if still available
            if layer.is_available:
                layers.append(layer)

        logger.debug("available_layers_query_completed", extra={
            "item_id": item_id,
            "total_lots": len(lots),
            "available_layers": len(layers),
        })

        return layers

    def get_total_available_quantity(
        self,
        item_id: str,
        location_id: str | None = None,
    ) -> Quantity:
        """Get total available quantity across all layers."""
        layers = self.get_available_layers(item_id, location_id)
        if not layers:
            return Quantity(value=Decimal("0"), unit="EA")

        total = sum(layer.remaining_quantity.value for layer in layers)
        unit = layers[0].remaining_quantity.unit
        return Quantity(value=total, unit=unit)

    def get_total_available_value(
        self,
        item_id: str,
        location_id: str | None = None,
    ) -> Money:
        """Get total available value across all layers."""
        layers = self.get_available_layers(item_id, location_id)
        if not layers:
            return Money.zero("USD")

        total = sum(layer.remaining_value.amount for layer in layers)
        currency = layers[0].remaining_value.currency.code
        return Money.of(total, currency)

    # =========================================================================
    # Lot Consumption
    # =========================================================================

    def consume_fifo(
        self,
        consuming_ref: ArtifactRef,
        item_id: str,
        quantity: Quantity,
        creating_event_id: UUID,
        location_id: str | None = None,
    ) -> ConsumptionResult:
        """
        Consume inventory using FIFO (First-In, First-Out).

        Consumes from the oldest lots first.

        Creates CONSUMED_BY links: lot -> consuming_ref

        Args:
            consuming_ref: What is consuming the inventory (shipment, usage, etc.).
            item_id: The item being consumed.
            quantity: Quantity to consume.
            creating_event_id: Event that triggered this consumption.
            location_id: Optional location filter.

        Returns:
            ConsumptionResult with details of what was consumed.

        Raises:
            InsufficientInventoryError: Not enough inventory available.
        """
        logger.info("fifo_consumption_started", extra={
            "consuming_ref": str(consuming_ref),
            "item_id": item_id,
            "quantity": str(quantity.value),
            "unit": quantity.unit,
        })

        return self._consume_with_method(
            consuming_ref=consuming_ref,
            item_id=item_id,
            quantity=quantity,
            creating_event_id=creating_event_id,
            cost_method=CostMethod.FIFO,
            location_id=location_id,
        )

    def consume_lifo(
        self,
        consuming_ref: ArtifactRef,
        item_id: str,
        quantity: Quantity,
        creating_event_id: UUID,
        location_id: str | None = None,
    ) -> ConsumptionResult:
        """
        Consume inventory using LIFO (Last-In, First-Out).

        Consumes from the newest lots first.

        Creates CONSUMED_BY links: lot -> consuming_ref

        Args:
            consuming_ref: What is consuming the inventory (shipment, usage, etc.).
            item_id: The item being consumed.
            quantity: Quantity to consume.
            creating_event_id: Event that triggered this consumption.
            location_id: Optional location filter.

        Returns:
            ConsumptionResult with details of what was consumed.

        Raises:
            InsufficientInventoryError: Not enough inventory available.
        """
        logger.info("lifo_consumption_started", extra={
            "consuming_ref": str(consuming_ref),
            "item_id": item_id,
            "quantity": str(quantity.value),
            "unit": quantity.unit,
        })

        return self._consume_with_method(
            consuming_ref=consuming_ref,
            item_id=item_id,
            quantity=quantity,
            creating_event_id=creating_event_id,
            cost_method=CostMethod.LIFO,
            location_id=location_id,
        )

    def consume_specific(
        self,
        consuming_ref: ArtifactRef,
        item_id: str,
        lot_id: UUID,
        quantity: Quantity,
        creating_event_id: UUID,
    ) -> ConsumptionResult:
        """
        Consume from a specific lot (lot picking).

        Used for specific identification costing or when user
        explicitly selects which lot to consume from.

        Args:
            consuming_ref: What is consuming the inventory.
            item_id: The item being consumed.
            lot_id: The specific lot to consume from.
            quantity: Quantity to consume.
            creating_event_id: Event that triggered this consumption.

        Returns:
            ConsumptionResult with details of what was consumed.

        Raises:
            LotNotFoundError: Specified lot doesn't exist.
            LotDepletedError: Lot has no remaining quantity.
            InsufficientInventoryError: Not enough quantity in lot.
        """
        logger.info("specific_lot_consumption_started", extra={
            "consuming_ref": str(consuming_ref),
            "item_id": item_id,
            "lot_id": str(lot_id),
            "quantity": str(quantity.value),
        })

        lot = self.get_lot_by_item(item_id, lot_id)
        if not lot:
            logger.error("specific_lot_not_found", extra={
                "lot_id": str(lot_id),
                "item_id": item_id,
            })
            raise LotNotFoundError(str(lot_id), item_id)

        # Get current layer for this lot
        layers = self.get_available_layers(item_id)
        layer = next((l for l in layers if l.lot.lot_id == lot_id), None)

        if not layer:
            logger.warning("specific_lot_depleted", extra={
                "lot_id": str(lot_id),
                "item_id": item_id,
            })
            raise LotDepletedError(str(lot_id), item_id)

        if layer.remaining_quantity.value < quantity.value:
            logger.warning("specific_lot_insufficient_quantity", extra={
                "lot_id": str(lot_id),
                "item_id": item_id,
                "requested": str(quantity.value),
                "available": str(layer.remaining_quantity.value),
            })
            raise InsufficientInventoryError(
                item_id=item_id,
                requested_quantity=str(quantity.value),
                available_quantity=str(layer.remaining_quantity.value),
                unit=quantity.unit,
            )

        # Consume from this specific lot
        consumption = CostLayerConsumption.create(layer, quantity)

        # Create CONSUMED_BY link
        link = self._create_consumption_link(
            lot=lot,
            consuming_ref=consuming_ref,
            consumption=consumption,
            creating_event_id=creating_event_id,
        )

        return ConsumptionResult.create(
            consuming_event_ref=consuming_ref,
            item_id=item_id,
            cost_method=CostMethod.SPECIFIC,
            consumptions=[consumption],
            links=[link],
        )

    def consume_at_standard(
        self,
        consuming_ref: ArtifactRef,
        item_id: str,
        quantity: Quantity,
        creating_event_id: UUID,
        location_id: str | None = None,
    ) -> StandardCostResult:
        """
        Consume inventory at standard cost with variance tracking.

        Issues inventory at the predetermined standard cost and
        tracks the variance from actual cost.

        Args:
            consuming_ref: What is consuming the inventory.
            item_id: The item being consumed.
            quantity: Quantity to consume.
            creating_event_id: Event that triggered this consumption.
            location_id: Optional location filter.

        Returns:
            StandardCostResult with actual consumption and variance.

        Raises:
            StandardCostNotFoundError: No standard cost defined for item.
            InsufficientInventoryError: Not enough inventory available.
        """
        logger.info("standard_cost_consumption_started", extra={
            "consuming_ref": str(consuming_ref),
            "item_id": item_id,
            "quantity": str(quantity.value),
        })

        standard_cost = self._standard_costs.get(item_id)
        if not standard_cost:
            logger.error("standard_cost_not_found", extra={
                "item_id": item_id,
            })
            raise StandardCostNotFoundError(item_id)

        # Actually consume using FIFO (for actual cost calculation)
        consumption = self._consume_with_method(
            consuming_ref=consuming_ref,
            item_id=item_id,
            quantity=quantity,
            creating_event_id=creating_event_id,
            cost_method=CostMethod.STANDARD,
            location_id=location_id,
        )

        # Calculate standard cost for the quantity
        standard_total = Money.of(
            standard_cost.amount * quantity.value,
            standard_cost.currency.code,
        )

        # Calculate variance (standard - actual)
        # Favorable if actual < standard (positive variance)
        variance = Money.of(
            standard_total.amount - consumption.total_cost.amount,
            standard_cost.currency.code,
        )

        logger.info("standard_cost_consumption_completed", extra={
            "item_id": item_id,
            "standard_cost": str(standard_total.amount),
            "actual_cost": str(consumption.total_cost.amount),
            "variance": str(variance.amount),
            "is_favorable": variance.is_positive,
        })

        return StandardCostResult(
            consumption=consumption,
            standard_cost=standard_total,
            actual_cost=consumption.total_cost,
            variance=variance,
        )

    def set_standard_cost(self, item_id: str, standard_cost: Money) -> None:
        """
        Set the standard cost for an item.

        Args:
            item_id: The item.
            standard_cost: Standard cost per unit.
        """
        logger.info("standard_cost_set", extra={
            "item_id": item_id,
            "standard_cost": str(standard_cost.amount),
            "currency": standard_cost.currency.code,
        })
        self._standard_costs[item_id] = standard_cost

    def get_standard_cost(self, item_id: str) -> Money | None:
        """Get the standard cost for an item."""
        return self._standard_costs.get(item_id)

    # =========================================================================
    # Persistence Helpers (G13: DB-backed lot storage)
    # =========================================================================

    def _store_lot(self, lot: CostLot, creating_event_id: UUID) -> None:
        """Persist a CostLot — to DB or in-memory depending on mode."""
        if self._in_memory_lots is not None:
            if lot.item_id not in self._in_memory_lots:
                self._in_memory_lots[lot.item_id] = []
            self._in_memory_lots[lot.item_id].append(lot)
            return

        model = CostLotModel(
            id=lot.lot_id,
            item_id=lot.item_id,
            location_id=lot.location_id,
            lot_date=lot.lot_date,
            original_quantity=lot.original_quantity.value,
            quantity_unit=lot.original_quantity.unit,
            original_cost=lot.original_cost.amount,
            currency=lot.original_cost.currency.code,
            cost_method=lot.cost_method.value,
            source_event_id=creating_event_id,
            source_artifact_type=lot.source_ref.artifact_type.value,
            source_artifact_id=lot.source_ref.artifact_id,
            created_at=datetime.now(UTC),
            lot_metadata=dict(lot.metadata) if lot.metadata else None,
        )
        self.session.add(model)
        self.session.flush()

    def _load_lot_by_id(self, lot_id: UUID) -> CostLot | None:
        """Load a single CostLot from the database by ID."""
        stmt = select(CostLotModel).where(CostLotModel.id == lot_id)
        model = self.session.execute(stmt).scalars().first()
        if model is None:
            return None
        return self._model_to_domain(model)

    def _load_lots_for_item(self, item_id: str) -> list[CostLot]:
        """Load all CostLots for an item from DB or in-memory storage."""
        if self._in_memory_lots is not None:
            return self._in_memory_lots.get(item_id, [])

        stmt = (
            select(CostLotModel)
            .where(CostLotModel.item_id == item_id)
            .order_by(CostLotModel.lot_date)
        )
        models = self.session.execute(stmt).scalars().all()
        return [self._model_to_domain(m) for m in models]

    @staticmethod
    def _model_to_domain(model: CostLotModel) -> CostLot:
        """Convert a CostLotModel ORM row to a CostLot domain object."""
        return CostLot(
            lot_id=model.id,
            item_id=model.item_id,
            location_id=model.location_id,
            lot_date=model.lot_date,
            original_quantity=Quantity(
                value=model.original_quantity,
                unit=model.quantity_unit,
            ),
            original_cost=Money.of(model.original_cost, model.currency),
            cost_method=CostMethod(model.cost_method),
            source_ref=ArtifactRef(
                artifact_type=ArtifactType(model.source_artifact_type),
                artifact_id=model.source_artifact_id,
            ),
            metadata=model.lot_metadata,
        )

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _consume_with_method(
        self,
        consuming_ref: ArtifactRef,
        item_id: str,
        quantity: Quantity,
        creating_event_id: UUID,
        cost_method: CostMethod,
        location_id: str | None = None,
    ) -> ConsumptionResult:
        """
        Internal method to consume inventory using specified method.

        Uses direct quantity-based FIFO/LIFO allocation (not value-based).
        """
        t0 = time.monotonic()
        logger.debug("consume_with_method_started", extra={
            "consuming_ref": str(consuming_ref),
            "item_id": item_id,
            "quantity": str(quantity.value),
            "cost_method": cost_method.value,
            "location_id": location_id,
        })

        # Get available layers
        layers = self.get_available_layers(item_id, location_id)

        if not layers:
            logger.warning("consumption_no_layers_available", extra={
                "item_id": item_id,
                "location_id": location_id,
            })
            raise InsufficientInventoryError(
                item_id=item_id,
                requested_quantity=str(quantity.value),
                available_quantity="0",
                unit=quantity.unit,
            )

        # Check total available
        total_available = sum(l.remaining_quantity.value for l in layers)
        if total_available < quantity.value:
            # Normalize and format as fixed-point to avoid scientific notation
            req_str = format(quantity.value.normalize(), 'f')
            avail_str = format(total_available.normalize(), 'f')
            logger.warning("consumption_insufficient_inventory", extra={
                "item_id": item_id,
                "requested_quantity": req_str,
                "available_quantity": avail_str,
            })
            raise InsufficientInventoryError(
                item_id=item_id,
                requested_quantity=req_str,
                available_quantity=avail_str,
                unit=quantity.unit,
            )

        # Sort layers by date for FIFO/LIFO
        # FIFO: oldest first (ascending by date)
        # LIFO: newest first (descending by date)
        if cost_method == CostMethod.LIFO:
            sorted_layers = sorted(layers, key=lambda l: l.lot.lot_date, reverse=True)
        else:  # FIFO or STANDARD (use FIFO for actual consumption)
            sorted_layers = sorted(layers, key=lambda l: l.lot.lot_date)

        # Build consumption details and links using quantity-based allocation
        consumptions: list[CostLayerConsumption] = []
        links: list[EconomicLink] = []
        remaining_qty = quantity.value

        for layer in sorted_layers:
            if remaining_qty <= 0:
                break

            # Consume up to the remaining quantity needed or what's available in this layer
            qty_to_consume = min(remaining_qty, layer.remaining_quantity.value)
            if qty_to_consume <= 0:
                continue

            remaining_qty -= qty_to_consume
            consumed_quantity = Quantity(value=qty_to_consume, unit=quantity.unit)

            # Create consumption detail
            consumption = CostLayerConsumption.create(layer, consumed_quantity)
            consumptions.append(consumption)

            logger.debug("layer_consumed", extra={
                "lot_id": str(layer.lot.lot_id),
                "qty_consumed": str(qty_to_consume),
                "cost_consumed": str(consumption.cost_consumed.amount),
                "unit_cost": str(layer.unit_cost.amount),
            })

            # Create CONSUMED_BY link
            link = self._create_consumption_link(
                lot=layer.lot,
                consuming_ref=consuming_ref,
                consumption=consumption,
                creating_event_id=creating_event_id,
            )
            links.append(link)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("consume_with_method_completed", extra={
            "item_id": item_id,
            "cost_method": cost_method.value,
            "layers_consumed": len(consumptions),
            "total_quantity": str(quantity.value),
            "links_created": len(links),
            "duration_ms": duration_ms,
        })

        return ConsumptionResult.create(
            consuming_event_ref=consuming_ref,
            item_id=item_id,
            cost_method=cost_method,
            consumptions=consumptions,
            links=links,
        )

    def _create_consumption_link(
        self,
        lot: CostLot,
        consuming_ref: ArtifactRef,
        consumption: CostLayerConsumption,
        creating_event_id: UUID,
    ) -> EconomicLink:
        """Create a CONSUMED_BY link from lot to consuming artifact."""
        link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.CONSUMED_BY,
            parent_ref=lot.lot_ref,
            child_ref=consuming_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(UTC),
            metadata={
                "quantity_consumed": str(consumption.quantity_consumed.value),
                "unit": consumption.quantity_consumed.unit,
                "cost_consumed": str(consumption.cost_consumed.amount),
                "currency": consumption.cost_consumed.currency.code,
                "unit_cost": str(consumption.unit_cost.amount),
            },
        )
        result = self.link_graph.establish_link(link, allow_duplicate=True)
        return result.link
