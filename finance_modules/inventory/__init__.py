"""
Inventory Module (``finance_modules.inventory``).

Responsibility
--------------
Thin ERP glue for stock-item lifecycle: receipts, issues, adjustments,
transfers, cycle counts, revaluations, ABC classification, and reorder-point
calculations.  All monetary computation is delegated to ``finance_engines``
(ValuationLayer, VarianceCalculator); all journal posting is delegated to
``finance_kernel`` via ``ModulePostingService``.

Architecture
------------
Layer: **Modules** -- declarative profiles, workflows, config schemas, and a
thin orchestration service.  This package MUST NOT contain business logic that
belongs in engines or kernel.  It imports from ``finance_engines`` and
``finance_kernel`` but never the reverse.

Invariants
----------
- R7  -- Each service method owns its transaction boundary (commit / rollback).
- R14 -- No ``if/switch`` on ``event_type`` in the posting pipeline; profile
         dispatch is handled by where-clause selectors in the kernel.
- R15 -- New inventory event types require only a new profile + registration.
- L1  -- All journal lines use account ROLES resolved to COA codes at posting
         time.

Failure Modes
-------------
- ``ValuationLayer`` may raise if insufficient lot quantity for consumption.
- ``ModulePostingService`` may return a non-success result (BLOCKED/REJECTED).
- Any exception triggers a session rollback before re-raising.

Audit Relevance
---------------
Every inventory movement that touches the GL is captured as an immutable
``JournalEntry`` with full provenance (``source_event_id``, snapshot columns).
Cost-lot creation and consumption are linked via ``EconomicLink`` artifacts
for end-to-end traceability from PO receipt through COGS recognition.

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
