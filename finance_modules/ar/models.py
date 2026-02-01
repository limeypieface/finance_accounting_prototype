"""
Accounts Receivable Domain Models (``finance_modules.ar.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of accounts receivable:
customers, invoices, credit memos, receipts, dunning records, auto-apply
rules, and credit decisions.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``ARService`` and returned to callers.  No dependency on kernel services,
database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``DunningHistory`` records track collection escalation for compliance.
* ``CreditDecision`` records track credit limit changes with reasons.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.ar.models")


class InvoiceStatus(Enum):
    """Invoice lifecycle states."""
    DRAFT = "draft"
    ISSUED = "issued"
    DELIVERED = "delivered"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    WRITTEN_OFF = "written_off"
    CANCELLED = "cancelled"


class ReceiptStatus(Enum):
    """Receipt processing states."""
    UNALLOCATED = "unallocated"
    PARTIALLY_ALLOCATED = "partially_allocated"
    FULLY_ALLOCATED = "fully_allocated"


class CreditMemoStatus(Enum):
    """Credit memo states."""
    DRAFT = "draft"
    ISSUED = "issued"
    APPLIED = "applied"
    REFUNDED = "refunded"


@dataclass(frozen=True)
class Customer:
    """A customer who owes money."""
    id: UUID
    code: str
    name: str
    credit_limit: Decimal | None = None
    payment_terms_days: int = 30
    default_gl_account_code: str | None = None  # default revenue account
    tax_exempt: bool = False
    tax_id: str | None = None
    is_active: bool = True
    dunning_level: int = 0  # 0 = current, 1+ = past due levels


@dataclass(frozen=True)
class InvoiceLine:
    """A single line item on a customer invoice."""
    id: UUID
    invoice_id: UUID
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    gl_account_code: str  # revenue account
    tax_code: str | None = None
    tax_amount: Decimal = Decimal("0")


@dataclass(frozen=True)
class Invoice:
    """A customer invoice."""
    id: UUID
    customer_id: UUID
    invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    balance_due: Decimal
    status: InvoiceStatus = InvoiceStatus.DRAFT
    lines: tuple[InvoiceLine, ...] = field(default_factory=tuple)
    sales_order_id: UUID | None = None


@dataclass(frozen=True)
class Receipt:
    """A payment received from a customer."""
    id: UUID
    customer_id: UUID
    receipt_date: date
    amount: Decimal
    currency: str
    payment_method: str  # check, ach, wire, credit_card
    reference: str  # check number, transaction ID
    status: ReceiptStatus = ReceiptStatus.UNALLOCATED
    bank_account_id: UUID | None = None
    unallocated_amount: Decimal = Decimal("0")

    def __post_init__(self):
        # Validate amount is positive
        if self.amount <= 0:
            logger.warning(
                "receipt_invalid_amount",
                extra={"receipt_id": str(self.id), "amount": str(self.amount)},
            )
            raise ValueError("Receipt amount must be positive")

        # Validate unallocated_amount is non-negative
        if self.unallocated_amount < 0:
            raise ValueError("unallocated_amount cannot be negative")

        # Validate unallocated_amount does not exceed amount
        if self.unallocated_amount > self.amount:
            logger.warning(
                "receipt_unallocated_exceeds_amount",
                extra={
                    "receipt_id": str(self.id),
                    "unallocated_amount": str(self.unallocated_amount),
                    "amount": str(self.amount),
                },
            )
            raise ValueError(
                f"unallocated_amount ({self.unallocated_amount}) "
                f"cannot exceed amount ({self.amount})"
            )

        logger.debug(
            "receipt_created",
            extra={
                "receipt_id": str(self.id),
                "customer_id": str(self.customer_id),
                "amount": str(self.amount),
                "currency": self.currency,
                "payment_method": self.payment_method,
                "status": self.status.value,
            },
        )


@dataclass(frozen=True)
class ReceiptAllocation:
    """Allocation of a receipt to one or more invoices."""
    id: UUID
    receipt_id: UUID
    invoice_id: UUID
    amount: Decimal
    discount_taken: Decimal = Decimal("0")


@dataclass(frozen=True)
class CreditMemo:
    """A credit to be applied against customer balance."""
    id: UUID
    customer_id: UUID
    credit_memo_number: str
    issue_date: date
    amount: Decimal
    currency: str
    reason: str
    status: CreditMemoStatus = CreditMemoStatus.DRAFT
    original_invoice_id: UUID | None = None  # if related to specific invoice
    applied_to_invoice_id: UUID | None = None


class DunningLevel(Enum):
    """Dunning severity levels for collection letters."""
    REMINDER = "reminder"
    FIRST_NOTICE = "first_notice"
    SECOND_NOTICE = "second_notice"
    FINAL_NOTICE = "final_notice"
    COLLECTION = "collection"


@dataclass(frozen=True)
class DunningHistory:
    """Record of a dunning letter sent to a customer."""
    id: UUID
    customer_id: UUID
    level: DunningLevel
    sent_date: date
    as_of_date: date
    total_overdue: Decimal
    invoice_count: int
    currency: str = "USD"
    notes: str | None = None


@dataclass(frozen=True)
class CreditDecision:
    """Result of a credit limit check or update."""
    id: UUID
    customer_id: UUID
    decision_date: date
    previous_limit: Decimal | None
    new_limit: Decimal | None
    order_amount: Decimal | None = None
    approved: bool = True
    reason: str | None = None
    decided_by: UUID | None = None


@dataclass(frozen=True)
class AutoApplyRule:
    """Rule for automatic payment application."""
    id: UUID
    name: str
    priority: int
    match_field: str  # "invoice_number", "amount", "customer_reference"
    tolerance: Decimal = Decimal("0")
    is_active: bool = True
