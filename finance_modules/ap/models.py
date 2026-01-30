"""
Accounts Payable Domain Models.

The nouns of accounts payable: vendors, invoices, payments.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.ap.models")


class InvoiceStatus(Enum):
    """Invoice workflow states."""
    DRAFT = "draft"
    PENDING_MATCH = "pending_match"
    MATCHED = "matched"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PAID = "paid"
    CANCELLED = "cancelled"


class PaymentMethod(Enum):
    """How payments are made."""
    CHECK = "check"
    ACH = "ach"
    WIRE = "wire"
    VIRTUAL_CARD = "virtual_card"


class PaymentStatus(Enum):
    """Payment workflow states."""
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    CLEARED = "cleared"
    VOIDED = "voided"


@dataclass(frozen=True)
class Vendor:
    """A supplier or service provider."""
    id: UUID
    code: str
    name: str
    tax_id: str | None = None
    payment_terms_days: int = 30
    default_payment_method: PaymentMethod = PaymentMethod.ACH
    default_gl_account_code: str | None = None  # default expense account
    is_active: bool = True
    is_1099_eligible: bool = False


@dataclass(frozen=True)
class InvoiceLine:
    """A single line item on a vendor invoice."""
    id: UUID
    invoice_id: UUID
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    gl_account_code: str
    po_line_id: UUID | None = None  # for three-way match
    receipt_line_id: UUID | None = None  # for three-way match


@dataclass(frozen=True)
class Invoice:
    """A vendor invoice to be paid."""
    id: UUID
    vendor_id: UUID
    invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: InvoiceStatus = InvoiceStatus.DRAFT
    po_id: UUID | None = None  # linked purchase order
    lines: tuple[InvoiceLine, ...] = field(default_factory=tuple)
    match_variance: Decimal | None = None
    approved_by_id: UUID | None = None
    approved_at: date | None = None

    def __post_init__(self):
        # Validate total = subtotal + tax
        expected_total = self.subtotal + self.tax_amount
        if self.total_amount != expected_total:
            logger.warning(
                "invoice_total_mismatch",
                extra={
                    "invoice_id": str(self.id),
                    "total_amount": str(self.total_amount),
                    "expected_total": str(expected_total),
                },
            )
            raise ValueError(
                f"total_amount ({self.total_amount}) must equal "
                f"subtotal + tax_amount ({expected_total})"
            )

        # Validate credit memo consistency
        if self.subtotal < 0 and self.tax_amount > 0:
            logger.warning(
                "invoice_credit_memo_invalid_tax",
                extra={
                    "invoice_id": str(self.id),
                    "subtotal": str(self.subtotal),
                    "tax_amount": str(self.tax_amount),
                },
            )
            raise ValueError(
                "Credit memo (negative subtotal) cannot have positive tax_amount"
            )

        logger.debug(
            "invoice_created",
            extra={
                "invoice_id": str(self.id),
                "vendor_id": str(self.vendor_id),
                "invoice_number": self.invoice_number,
                "total_amount": str(self.total_amount),
                "currency": self.currency,
                "status": self.status.value,
                "line_count": len(self.lines),
            },
        )


@dataclass(frozen=True)
class Payment:
    """A payment to a vendor."""
    id: UUID
    vendor_id: UUID
    payment_date: date
    payment_method: PaymentMethod
    amount: Decimal
    currency: str
    reference: str  # check number, ACH trace, etc.
    status: PaymentStatus = PaymentStatus.DRAFT
    invoice_ids: tuple[UUID, ...] = field(default_factory=tuple)  # invoices being paid
    discount_taken: Decimal = Decimal("0")
    bank_account_id: UUID | None = None


@dataclass(frozen=True)
class PaymentBatch:
    """A batch of payments for processing together."""
    id: UUID
    batch_date: date
    payment_method: PaymentMethod
    payment_ids: tuple[UUID, ...]
    total_amount: Decimal
    status: str = "draft"  # draft, submitted, processed
