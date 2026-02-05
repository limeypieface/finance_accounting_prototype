"""
Inventory Domain Models (``finance_modules.inventory.models``).

Responsibility
--------------
Frozen value objects representing the nouns of inventory management: items,
locations, stock levels, receipts, issues, adjustments, transfers, cycle
counts, ABC classifications, reorder points, and item-value records.

Architecture
------------
Layer: **Modules** -- pure domain data structures.  All dataclasses are
``frozen=True`` (immutable) to align with the event-sourced, append-only
design.  These models carry NO database identity and NO I/O; they are used
as DTOs between the service layer and callers.

Invariants
----------
- ``StockLevel`` enforces non-negative ``quantity_on_hand`` and
  ``quantity_reserved``, and validates that
  ``quantity_available == quantity_on_hand - quantity_reserved``.
- ``Item`` requires ``standard_cost > 0`` (standard costing business rule).
- All monetary fields use ``Decimal`` -- never ``float`` (R16/R17).

Failure Modes
-------------
- Construction of a ``StockLevel`` with inconsistent quantities raises
  ``ValueError`` immediately.

Audit Relevance
---------------
These DTOs are ephemeral carriers -- the audit-grade source of truth is the
``JournalEntry`` / ``JournalLine`` written by the kernel, not these objects.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.inventory.models")


class ItemType(Enum):
    """Classification of inventory items by production stage."""
    RAW_MATERIAL = "raw_material"
    WIP = "wip"
    FINISHED_GOODS = "finished_goods"
    CONSUMABLE = "consumable"
    SPARE_PARTS = "spare_parts"


class MovementType(Enum):
    """Enumeration of inventory movement categories for event classification."""
    RECEIPT = "receipt"
    ISSUE = "issue"
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"
    SCRAP = "scrap"
    RETURN = "return"


class ReceiptStatus(Enum):
    """Receipt processing states."""
    PENDING = "pending"
    INSPECTING = "inspecting"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PUTAWAY = "putaway"


class IssueStatus(Enum):
    """Issue processing states."""
    REQUESTED = "requested"
    PICKED = "picked"
    SHIPPED = "shipped"
    DELIVERED = "delivered"


class TransferStatus(Enum):
    """Transfer processing states."""
    REQUESTED = "requested"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"


@dataclass(frozen=True)
class Item:
    """
    An inventory item (SKU / stock-keeping unit).

    Contract: Immutable value object.  ``standard_cost`` is expressed in the
    entity's functional currency and uses ``Decimal`` (never float).
    Business rule: ``standard_cost`` must be positive (required for standard
    costing and COGS; zero-cost items are not supported).
    """
    id: UUID
    code: str
    description: str
    item_type: ItemType
    unit_of_measure: str
    standard_cost: Decimal
    is_active: bool = True
    is_lot_controlled: bool = False
    is_serial_controlled: bool = False
    reorder_point: Decimal | None = None
    reorder_quantity: Decimal | None = None
    gl_asset_account: str | None = None
    gl_expense_account: str | None = None  # for consumables

    def __post_init__(self):
        if self.standard_cost <= 0:
            logger.warning(
                "item_standard_cost_invalid",
                extra={
                    "item_id": str(self.id),
                    "code": self.code,
                    "standard_cost": str(self.standard_cost),
                },
            )
            raise ValueError(
                f"standard_cost must be positive (got {self.standard_cost})"
            )


@dataclass(frozen=True)
class Location:
    """
    A storage location (warehouse, bin, staging area, inspection zone).

    Contract: Immutable.  ``parent_id`` enables hierarchical location trees.
    """
    id: UUID
    code: str
    name: str
    location_type: str  # warehouse, bin, staging, inspection
    parent_id: UUID | None = None
    is_active: bool = True


@dataclass(frozen=True)
class StockLevel:
    """
    Current stock level for an item at a location.

    Contract: Immutable.  Construction validates the quantity invariant:
    ``quantity_available == quantity_on_hand - quantity_reserved``.

    Guarantees: ``quantity_on_hand >= 0`` and ``quantity_reserved >= 0``.

    Raises:
        ValueError: If any quantity invariant is violated at construction time.
    """
    id: UUID
    item_id: UUID
    location_id: UUID
    quantity_on_hand: Decimal
    quantity_reserved: Decimal = Decimal("0")
    quantity_available: Decimal = Decimal("0")
    lot_number: str | None = None
    serial_number: str | None = None

    def __post_init__(self):
        # INVARIANT: stock quantities must be non-negative and internally consistent.
        # Validate quantity_on_hand is non-negative
        if self.quantity_on_hand < 0:
            raise ValueError("quantity_on_hand cannot be negative")

        # Validate quantity_reserved is non-negative
        if self.quantity_reserved < 0:
            raise ValueError("quantity_reserved cannot be negative")

        # Validate quantity_available equals quantity_on_hand - quantity_reserved
        expected_available = self.quantity_on_hand - self.quantity_reserved
        if self.quantity_available != expected_available:
            logger.warning(
                "stock_level_quantity_mismatch",
                extra={
                    "stock_level_id": str(self.id),
                    "item_id": str(self.item_id),
                    "location_id": str(self.location_id),
                    "quantity_on_hand": str(self.quantity_on_hand),
                    "quantity_reserved": str(self.quantity_reserved),
                    "quantity_available": str(self.quantity_available),
                    "expected_available": str(expected_available),
                },
            )
            raise ValueError(
                f"quantity_available ({self.quantity_available}) must equal "
                f"quantity_on_hand - quantity_reserved ({expected_available})"
            )

        logger.debug(
            "stock_level_created",
            extra={
                "stock_level_id": str(self.id),
                "item_id": str(self.item_id),
                "location_id": str(self.location_id),
                "quantity_on_hand": str(self.quantity_on_hand),
                "quantity_available": str(self.quantity_available),
            },
        )


@dataclass(frozen=True)
class InventoryReceipt:
    """
    Receipt of inventory (from purchase order, production order, or return).

    Contract: Immutable.  ``total_cost`` should equal ``quantity * unit_cost``
    (caller-enforced).
    """
    id: UUID
    item_id: UUID
    location_id: UUID
    receipt_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    status: ReceiptStatus = ReceiptStatus.PENDING
    source_type: str | None = None  # purchase_order, production_order, return
    source_id: UUID | None = None
    lot_number: str | None = None


@dataclass(frozen=True)
class InventoryIssue:
    """
    Issue of inventory (to production, sales, or scrap).

    Contract: Immutable.  ``total_cost`` is determined by the costing engine
    (FIFO/LIFO consumption), not by the caller.
    """
    id: UUID
    item_id: UUID
    location_id: UUID
    issue_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    status: IssueStatus = IssueStatus.REQUESTED
    destination_type: str | None = None  # manufacturing_order, sales_order, scrap
    destination_id: UUID | None = None
    lot_number: str | None = None


@dataclass(frozen=True)
class InventoryAdjustment:
    """
    Adjustment to inventory quantity or value (cycle count, physical count, etc.).

    Contract: Immutable.  ``reason_code`` is mandatory for audit traceability.
    """
    id: UUID
    item_id: UUID
    location_id: UUID
    adjustment_date: date
    quantity_change: Decimal
    value_change: Decimal
    reason_code: str
    reference: str | None = None


@dataclass(frozen=True)
class StockTransfer:
    """
    Transfer of inventory between locations.

    Contract: Immutable.  ``from_location_id != to_location_id`` (caller-enforced).
    """
    id: UUID
    item_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    transfer_date: date
    quantity: Decimal
    status: TransferStatus = TransferStatus.REQUESTED
    lot_number: str | None = None


@dataclass(frozen=True)
class CycleCount:
    """
    A cycle count result for an item at a location.

    Contract: Immutable.  ``variance_quantity`` should equal
    ``actual_quantity - expected_quantity`` (caller-enforced).
    """
    id: UUID
    count_date: date
    item_id: str
    location_id: str | None
    expected_quantity: Decimal
    actual_quantity: Decimal
    variance_quantity: Decimal  # actual - expected
    variance_amount: Decimal
    currency: str = "USD"
    counter_id: UUID | None = None
    notes: str = ""


@dataclass(frozen=True)
class ABCClassification:
    """
    ABC analysis result for an item (Pareto-based inventory prioritisation).

    Contract: Immutable.  ``classification`` is one of ``"A"``, ``"B"``, ``"C"``.
    """
    item_id: str
    classification: str  # A, B, C
    annual_value: Decimal
    cumulative_percent: Decimal
    as_of_date: date


@dataclass(frozen=True)
class ReorderPoint:
    """
    Computed reorder-point (ROP) parameters for an item.

    Contract: Immutable.  ``eoq`` is zero when Economic Order Quantity was not
    requested.
    """
    item_id: str
    location_id: str | None
    reorder_point: Decimal
    safety_stock: Decimal
    eoq: Decimal
    avg_daily_usage: Decimal
    lead_time_days: int


@dataclass(frozen=True)
class ItemValue:
    """
    Item annual value for ABC classification input.

    Contract: Immutable.  ``annual_value`` uses ``Decimal`` (never float).
    """
    item_id: str
    annual_value: Decimal
