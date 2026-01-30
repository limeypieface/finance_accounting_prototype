"""
Inventory Domain Models.

The nouns of inventory: items, locations, stock levels, movements.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.inventory.models")


class ItemType(Enum):
    """Types of inventory items."""
    RAW_MATERIAL = "raw_material"
    WIP = "wip"
    FINISHED_GOODS = "finished_goods"
    CONSUMABLE = "consumable"
    SPARE_PARTS = "spare_parts"


class MovementType(Enum):
    """Types of inventory movements."""
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
    """An inventory item (SKU)."""
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


@dataclass(frozen=True)
class Location:
    """A storage location (warehouse, bin, etc.)."""
    id: UUID
    code: str
    name: str
    location_type: str  # warehouse, bin, staging, inspection
    parent_id: UUID | None = None
    is_active: bool = True


@dataclass(frozen=True)
class StockLevel:
    """Current stock level for an item at a location."""
    id: UUID
    item_id: UUID
    location_id: UUID
    quantity_on_hand: Decimal
    quantity_reserved: Decimal = Decimal("0")
    quantity_available: Decimal = Decimal("0")
    lot_number: str | None = None
    serial_number: str | None = None

    def __post_init__(self):
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
    """Receipt of inventory (from PO, production, return)."""
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
    """Issue of inventory (to production, sales, scrap)."""
    id: UUID
    item_id: UUID
    location_id: UUID
    issue_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    status: IssueStatus = IssueStatus.REQUESTED
    destination_type: str | None = None  # work_order, sales_order, scrap
    destination_id: UUID | None = None
    lot_number: str | None = None


@dataclass(frozen=True)
class InventoryAdjustment:
    """Adjustment to inventory quantity or value."""
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
    """Transfer of inventory between locations."""
    id: UUID
    item_id: UUID
    from_location_id: UUID
    to_location_id: UUID
    transfer_date: date
    quantity: Decimal
    status: TransferStatus = TransferStatus.REQUESTED
    lot_number: str | None = None
