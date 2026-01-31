"""
Procurement Domain Models.

The nouns of procurement: requisitions, purchase orders, receipts.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.procurement.models")


class RequisitionStatus(Enum):
    """Requisition lifecycle states."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONVERTED = "converted"  # to PO
    CANCELLED = "cancelled"


class POStatus(Enum):
    """Purchase order lifecycle states."""
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    INVOICED = "invoiced"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ReceiptStatus(Enum):
    """Receipt processing states."""
    PENDING = "pending"
    INSPECTING = "inspecting"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RequisitionLine:
    """A line item on a purchase requisition."""
    id: UUID
    requisition_id: UUID
    line_number: int
    item_id: UUID | None = None  # may be non-stock
    description: str = ""
    quantity: Decimal = Decimal("0")
    unit_of_measure: str = "EA"
    estimated_unit_cost: Decimal = Decimal("0")
    estimated_total: Decimal = Decimal("0")
    required_date: date | None = None
    gl_account_code: str | None = None
    project_id: UUID | None = None


@dataclass(frozen=True)
class Requisition:
    """A purchase requisition."""
    id: UUID
    requisition_number: str
    requester_id: UUID
    request_date: date
    description: str
    total_amount: Decimal = Decimal("0")
    currency: str = "USD"
    status: RequisitionStatus = RequisitionStatus.DRAFT
    department_id: UUID | None = None
    approved_by: UUID | None = None
    approved_date: date | None = None
    lines: tuple[RequisitionLine, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PurchaseOrderLine:
    """A line item on a purchase order."""
    id: UUID
    purchase_order_id: UUID
    line_number: int
    item_id: UUID | None = None
    description: str = ""
    quantity_ordered: Decimal = Decimal("0")
    quantity_received: Decimal = Decimal("0")
    quantity_invoiced: Decimal = Decimal("0")
    unit_of_measure: str = "EA"
    unit_price: Decimal = Decimal("0")
    line_total: Decimal = Decimal("0")
    required_date: date | None = None
    gl_account_code: str | None = None
    requisition_line_id: UUID | None = None

    def __post_init__(self):
        # Validate quantity_received does not exceed quantity_ordered
        if self.quantity_received > self.quantity_ordered:
            logger.warning(
                "po_line_over_receipt",
                extra={
                    "po_line_id": str(self.id),
                    "quantity_ordered": str(self.quantity_ordered),
                    "quantity_received": str(self.quantity_received),
                },
            )
            raise ValueError(
                f"quantity_received ({self.quantity_received}) "
                f"cannot exceed quantity_ordered ({self.quantity_ordered})"
            )

        # Validate quantity_invoiced does not exceed quantity_received
        if self.quantity_invoiced > self.quantity_received:
            logger.warning(
                "po_line_over_invoice",
                extra={
                    "po_line_id": str(self.id),
                    "quantity_invoiced": str(self.quantity_invoiced),
                    "quantity_received": str(self.quantity_received),
                },
            )
            raise ValueError(
                f"quantity_invoiced ({self.quantity_invoiced}) "
                f"cannot exceed quantity_received ({self.quantity_received})"
            )

        logger.debug(
            "purchase_order_line_created",
            extra={
                "po_line_id": str(self.id),
                "purchase_order_id": str(self.purchase_order_id),
                "line_number": self.line_number,
                "quantity_ordered": str(self.quantity_ordered),
                "unit_price": str(self.unit_price),
            },
        )


@dataclass(frozen=True)
class PurchaseOrder:
    """A purchase order."""
    id: UUID
    po_number: str
    vendor_id: UUID
    order_date: date
    expected_date: date | None = None
    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    total_amount: Decimal = Decimal("0")
    currency: str = "USD"
    status: POStatus = POStatus.DRAFT
    buyer_id: UUID | None = None
    ship_to_location_id: UUID | None = None
    bill_to_location_id: UUID | None = None
    lines: tuple[PurchaseOrderLine, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Receipt:
    """A goods receipt against a purchase order."""
    id: UUID
    receipt_number: str
    purchase_order_id: UUID
    po_line_id: UUID
    receipt_date: date
    quantity_received: Decimal
    quantity_accepted: Decimal = Decimal("0")
    quantity_rejected: Decimal = Decimal("0")
    status: ReceiptStatus = ReceiptStatus.PENDING
    receiver_id: UUID | None = None
    location_id: UUID | None = None
    lot_number: str | None = None


@dataclass(frozen=True)
class PurchaseOrderVersion:
    """A versioned snapshot of a PO amendment."""
    po_id: UUID
    version: int
    amendment_date: date
    amendment_reason: str
    changes: tuple[str, ...]
    previous_total: Decimal
    new_total: Decimal
    amended_by: UUID


@dataclass(frozen=True)
class ReceiptMatch:
    """A match record linking a receipt to a PO for 3-way matching."""
    id: UUID
    receipt_id: UUID
    po_id: UUID
    po_line_id: UUID
    matched_quantity: Decimal
    matched_amount: Decimal
    variance_amount: Decimal
    match_type: str  # "2-way", "3-way"
    matched_date: date


@dataclass(frozen=True)
class SupplierScore:
    """Supplier performance scorecard for a given period."""
    vendor_id: UUID
    period: str  # e.g. "2026-Q1"
    delivery_score: Decimal  # 0-100
    quality_score: Decimal
    price_score: Decimal
    overall_score: Decimal
    evaluation_date: date
    evaluator_id: UUID | None = None
