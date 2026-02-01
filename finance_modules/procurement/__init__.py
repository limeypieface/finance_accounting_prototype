"""
Procurement Module (``finance_modules.procurement``).

Responsibility
--------------
Thin ERP glue for the procure-to-pay cycle: purchase requisitions, purchase
orders, receiving, three-way matching, encumbrance accounting, commitment
tracking, PO amendments, and quantity variance recording.

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``ProcurementService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New procurement event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.
* L5  -- Link creation and journal posting share a single transaction.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Encumbrance relief may fail if no matching encumbrance exists.

Audit relevance
---------------
Encumbrance accounting supports budgetary control compliance.  Three-way
matching (PO/receipt/invoice) is a key SOX control.  All procurement
transactions produce immutable journal entries with full provenance through
the kernel audit chain (R11).

Total: ~200 lines of module-specific code.
Three-way match and approval routing use shared engines.
"""

from finance_modules.procurement.config import ProcurementConfig
from finance_modules.procurement.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderVersion,
    Receipt,
    ReceiptMatch,
    Requisition,
    RequisitionLine,
    SupplierScore,
)
from finance_modules.procurement.profiles import PROCUREMENT_PROFILES
from finance_modules.procurement.workflows import (
    PURCHASE_ORDER_WORKFLOW,
    REQUISITION_WORKFLOW,
)

__all__ = [
    "Requisition",
    "RequisitionLine",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "Receipt",
    "PurchaseOrderVersion",
    "ReceiptMatch",
    "SupplierScore",
    "PROCUREMENT_PROFILES",
    "REQUISITION_WORKFLOW",
    "PURCHASE_ORDER_WORKFLOW",
    "ProcurementConfig",
]
