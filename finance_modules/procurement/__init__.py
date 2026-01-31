"""
Procurement Module.

Handles purchase requisitions, purchase orders, and receiving.

Total: ~200 lines of module-specific code.
Three-way match and approval routing use shared engines.
"""

from finance_modules.procurement.models import (
    Requisition,
    RequisitionLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Receipt,
    PurchaseOrderVersion,
    ReceiptMatch,
    SupplierScore,
)
from finance_modules.procurement.profiles import PROCUREMENT_PROFILES
from finance_modules.procurement.workflows import (
    REQUISITION_WORKFLOW,
    PURCHASE_ORDER_WORKFLOW,
)
from finance_modules.procurement.config import ProcurementConfig

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
