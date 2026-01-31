"""
Inventory Module.

Handles stock items, receipts, issues, adjustments, and valuation.

Total: ~200 lines of module-specific code.
Valuation engines (FIFO, LIFO, weighted avg) come from shared engines.
"""

from finance_modules.inventory.models import (
    Item,
    Location,
    StockLevel,
    InventoryReceipt,
    InventoryIssue,
    InventoryAdjustment,
    StockTransfer,
    CycleCount,
    ABCClassification,
    ReorderPoint,
    ItemValue,
)
from finance_modules.inventory.profiles import INVENTORY_PROFILES
from finance_modules.inventory.workflows import (
    RECEIPT_WORKFLOW,
    ISSUE_WORKFLOW,
    TRANSFER_WORKFLOW,
)
from finance_modules.inventory.config import InventoryConfig
from finance_modules.inventory.service import InventoryService

__all__ = [
    "Item",
    "Location",
    "StockLevel",
    "InventoryReceipt",
    "InventoryIssue",
    "InventoryAdjustment",
    "StockTransfer",
    "CycleCount",
    "ABCClassification",
    "ReorderPoint",
    "ItemValue",
    "INVENTORY_PROFILES",
    "RECEIPT_WORKFLOW",
    "ISSUE_WORKFLOW",
    "TRANSFER_WORKFLOW",
    "InventoryConfig",
    "InventoryService",
]
