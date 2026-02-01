"""
Accounts Receivable ORM Models (``finance_modules.ar.orm``).

Responsibility
--------------
SQLAlchemy persistence models for the AR module.  Maps frozen domain
dataclasses from ``models.py`` to database tables.

Architecture position
---------------------
**Modules layer** -- persistence.  Imports from ``finance_kernel.db.base``
and sibling ``models.py``.  MUST NOT be imported by ``finance_kernel``.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# 1. CustomerProfileModel
# ---------------------------------------------------------------------------


class CustomerProfileModel(TrackedBase):
    """
    ORM model for AR customer profiles.

    Maps to the ``Customer`` frozen dataclass.  Customer identity is
    anchored to the kernel ``Party`` model via ``customer_id`` FK.

    Guarantees:
        - customer_id FK to parties.id.
        - code is unique within AR (uq_ar_customer_profiles_code).
        - payment_terms_days defaults to 30.
        - dunning_level defaults to 0.
    """

    __tablename__ = "ar_customer_profiles"

    __table_args__ = (
        UniqueConstraint("code", name="uq_ar_customer_profiles_code"),
        Index("idx_ar_customer_profiles_customer_id", "customer_id"),
        Index("idx_ar_customer_profiles_is_active", "is_active"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    credit_limit: Mapped[Decimal | None] = mapped_column(nullable=True)
    payment_terms_days: Mapped[int] = mapped_column(default=30)
    default_gl_account_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    tax_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    dunning_level: Mapped[int] = mapped_column(default=0)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import Customer

        return Customer(
            id=self.id,
            code=self.code,
            name=self.name,
            credit_limit=self.credit_limit,
            payment_terms_days=self.payment_terms_days,
            default_gl_account_code=self.default_gl_account_code,
            tax_exempt=self.tax_exempt,
            tax_id=self.tax_id,
            is_active=self.is_active,
            dunning_level=self.dunning_level,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "CustomerProfileModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            customer_id=dto.id,
            code=dto.code,
            name=dto.name,
            credit_limit=dto.credit_limit,
            payment_terms_days=dto.payment_terms_days,
            default_gl_account_code=dto.default_gl_account_code,
            tax_exempt=dto.tax_exempt,
            tax_id=dto.tax_id,
            is_active=dto.is_active,
            dunning_level=dto.dunning_level,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<CustomerProfileModel {self.code}: {self.name}>"


# ---------------------------------------------------------------------------
# 2. ARInvoiceModel
# ---------------------------------------------------------------------------


class ARInvoiceModel(TrackedBase):
    """
    ORM model for AR invoices.

    Maps to the ``Invoice`` frozen dataclass.  Lines are stored in a
    separate child table via ``lines`` relationship.

    Guarantees:
        - invoice_number is unique (uq_ar_invoices_invoice_number).
        - Monetary fields use Decimal (Numeric(38,9) via type_annotation_map).
        - status stored as string enum value.
        - balance_due tracks remaining amount owed.
    """

    __tablename__ = "ar_invoices"

    __table_args__ = (
        UniqueConstraint(
            "invoice_number", name="uq_ar_invoices_invoice_number"
        ),
        Index("idx_ar_invoices_customer_id", "customer_id"),
        Index("idx_ar_invoices_status", "status"),
        Index("idx_ar_invoices_due_date", "due_date"),
        Index("idx_ar_invoices_sales_order_id", "sales_order_id"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(nullable=False)
    balance_due: Mapped[Decimal] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    sales_order_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Relationship to child lines
    lines: Mapped[list["ARInvoiceLineModel"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import Invoice, InvoiceStatus

        return Invoice(
            id=self.id,
            customer_id=self.customer_id,
            invoice_number=self.invoice_number,
            invoice_date=self.invoice_date,
            due_date=self.due_date,
            currency=self.currency,
            subtotal=self.subtotal,
            tax_amount=self.tax_amount,
            total_amount=self.total_amount,
            balance_due=self.balance_due,
            status=InvoiceStatus(self.status),
            lines=tuple(line.to_dto() for line in self.lines),
            sales_order_id=self.sales_order_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARInvoiceModel":
        """Create ORM model from frozen dataclass."""
        model = cls(
            id=dto.id,
            customer_id=dto.customer_id,
            invoice_number=dto.invoice_number,
            invoice_date=dto.invoice_date,
            due_date=dto.due_date,
            currency=dto.currency,
            subtotal=dto.subtotal,
            tax_amount=dto.tax_amount,
            total_amount=dto.total_amount,
            balance_due=dto.balance_due,
            status=dto.status.value,
            sales_order_id=dto.sales_order_id,
            created_by_id=created_by_id,
        )
        model.lines = [
            ARInvoiceLineModel.from_dto(line, created_by_id)
            for line in dto.lines
        ]
        return model

    def __repr__(self) -> str:
        return (
            f"<ARInvoiceModel {self.invoice_number} "
            f"status={self.status} balance={self.balance_due}>"
        )


# ---------------------------------------------------------------------------
# 3. ARInvoiceLineModel
# ---------------------------------------------------------------------------


class ARInvoiceLineModel(TrackedBase):
    """
    ORM model for AR invoice line items.

    Maps to the ``InvoiceLine`` frozen dataclass.  Each line belongs to
    exactly one ARInvoiceModel.

    Guarantees:
        - invoice_id FK to ar_invoices.id.
        - Monetary fields use Decimal.
        - GL account stored as string code (no FK to kernel accounts table).
    """

    __tablename__ = "ar_invoice_lines"

    __table_args__ = (
        Index("idx_ar_invoice_lines_invoice_id", "invoice_id"),
        Index("idx_ar_invoice_lines_gl_account", "gl_account_code"),
    )

    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("ar_invoices.id"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    gl_account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    tax_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationship to parent invoice
    invoice: Mapped["ARInvoiceModel"] = relationship(
        back_populates="lines",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import InvoiceLine

        return InvoiceLine(
            id=self.id,
            invoice_id=self.invoice_id,
            line_number=self.line_number,
            description=self.description,
            quantity=self.quantity,
            unit_price=self.unit_price,
            amount=self.amount,
            gl_account_code=self.gl_account_code,
            tax_code=self.tax_code,
            tax_amount=self.tax_amount,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARInvoiceLineModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            invoice_id=dto.invoice_id,
            line_number=dto.line_number,
            description=dto.description,
            quantity=dto.quantity,
            unit_price=dto.unit_price,
            amount=dto.amount,
            gl_account_code=dto.gl_account_code,
            tax_code=dto.tax_code,
            tax_amount=dto.tax_amount,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARInvoiceLineModel line={self.line_number} "
            f"amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 4. ARReceiptModel
# ---------------------------------------------------------------------------


class ARReceiptModel(TrackedBase):
    """
    ORM model for AR receipts (payments received from customers).

    Maps to the ``Receipt`` frozen dataclass.

    Guarantees:
        - customer_id FK to parties.id.
        - Monetary fields use Decimal.
        - status stored as string enum value.
        - unallocated_amount tracks remaining unapplied balance.
    """

    __tablename__ = "ar_receipts"

    __table_args__ = (
        Index("idx_ar_receipts_customer_id", "customer_id"),
        Index("idx_ar_receipts_status", "status"),
        Index("idx_ar_receipts_receipt_date", "receipt_date"),
        Index("idx_ar_receipts_reference", "reference"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="unallocated")
    bank_account_id: Mapped[UUID | None] = mapped_column(nullable=True)
    unallocated_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationship to child allocations
    allocations: Mapped[list["ARReceiptAllocationModel"]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import Receipt, ReceiptStatus

        return Receipt(
            id=self.id,
            customer_id=self.customer_id,
            receipt_date=self.receipt_date,
            amount=self.amount,
            currency=self.currency,
            payment_method=self.payment_method,
            reference=self.reference,
            status=ReceiptStatus(self.status),
            bank_account_id=self.bank_account_id,
            unallocated_amount=self.unallocated_amount,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARReceiptModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            customer_id=dto.customer_id,
            receipt_date=dto.receipt_date,
            amount=dto.amount,
            currency=dto.currency,
            payment_method=dto.payment_method,
            reference=dto.reference,
            status=dto.status.value,
            bank_account_id=dto.bank_account_id,
            unallocated_amount=dto.unallocated_amount,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARReceiptModel {self.reference} "
            f"status={self.status} amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 5. ARReceiptAllocationModel
# ---------------------------------------------------------------------------


class ARReceiptAllocationModel(TrackedBase):
    """
    ORM model for AR receipt allocations.

    Maps to the ``ReceiptAllocation`` frozen dataclass.  Each allocation
    applies a portion of a receipt to a specific invoice.

    Guarantees:
        - receipt_id FK to ar_receipts.id.
        - invoice_id FK to ar_invoices.id.
        - Monetary fields use Decimal.
    """

    __tablename__ = "ar_receipt_allocations"

    __table_args__ = (
        Index("idx_ar_receipt_allocations_receipt_id", "receipt_id"),
        Index("idx_ar_receipt_allocations_invoice_id", "invoice_id"),
    )

    receipt_id: Mapped[UUID] = mapped_column(
        ForeignKey("ar_receipts.id"), nullable=False
    )
    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("ar_invoices.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    discount_taken: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationship to parent receipt
    receipt: Mapped["ARReceiptModel"] = relationship(
        back_populates="allocations",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import ReceiptAllocation

        return ReceiptAllocation(
            id=self.id,
            receipt_id=self.receipt_id,
            invoice_id=self.invoice_id,
            amount=self.amount,
            discount_taken=self.discount_taken,
        )

    @classmethod
    def from_dto(
        cls, dto, created_by_id: UUID
    ) -> "ARReceiptAllocationModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            receipt_id=dto.receipt_id,
            invoice_id=dto.invoice_id,
            amount=dto.amount,
            discount_taken=dto.discount_taken,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARReceiptAllocationModel receipt={self.receipt_id} "
            f"invoice={self.invoice_id} amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 6. ARCreditMemoModel
# ---------------------------------------------------------------------------


class ARCreditMemoModel(TrackedBase):
    """
    ORM model for AR credit memos.

    Maps to the ``CreditMemo`` frozen dataclass.

    Guarantees:
        - customer_id FK to parties.id.
        - credit_memo_number is unique (uq_ar_credit_memos_number).
        - Monetary fields use Decimal.
        - status stored as string enum value.
    """

    __tablename__ = "ar_credit_memos"

    __table_args__ = (
        UniqueConstraint(
            "credit_memo_number", name="uq_ar_credit_memos_number"
        ),
        Index("idx_ar_credit_memos_customer_id", "customer_id"),
        Index("idx_ar_credit_memos_status", "status"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    credit_memo_number: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    original_invoice_id: Mapped[UUID | None] = mapped_column(nullable=True)
    applied_to_invoice_id: Mapped[UUID | None] = mapped_column(nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import CreditMemo, CreditMemoStatus

        return CreditMemo(
            id=self.id,
            customer_id=self.customer_id,
            credit_memo_number=self.credit_memo_number,
            issue_date=self.issue_date,
            amount=self.amount,
            currency=self.currency,
            reason=self.reason,
            status=CreditMemoStatus(self.status),
            original_invoice_id=self.original_invoice_id,
            applied_to_invoice_id=self.applied_to_invoice_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARCreditMemoModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            customer_id=dto.customer_id,
            credit_memo_number=dto.credit_memo_number,
            issue_date=dto.issue_date,
            amount=dto.amount,
            currency=dto.currency,
            reason=dto.reason,
            status=dto.status.value,
            original_invoice_id=dto.original_invoice_id,
            applied_to_invoice_id=dto.applied_to_invoice_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARCreditMemoModel {self.credit_memo_number} "
            f"status={self.status} amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 7. ARDunningHistoryModel
# ---------------------------------------------------------------------------


class ARDunningHistoryModel(TrackedBase):
    """
    ORM model for AR dunning history records.

    Maps to the ``DunningHistory`` frozen dataclass.  Each record represents
    a dunning letter sent to a customer at a specific severity level.

    Guarantees:
        - customer_id FK to parties.id.
        - level stored as string enum value (DunningLevel).
        - Monetary fields use Decimal.
    """

    __tablename__ = "ar_dunning_history"

    __table_args__ = (
        Index("idx_ar_dunning_history_customer_id", "customer_id"),
        Index("idx_ar_dunning_history_level", "level"),
        Index("idx_ar_dunning_history_sent_date", "sent_date"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    sent_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_overdue: Mapped[Decimal] = mapped_column(nullable=False)
    invoice_count: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import DunningHistory, DunningLevel

        return DunningHistory(
            id=self.id,
            customer_id=self.customer_id,
            level=DunningLevel(self.level),
            sent_date=self.sent_date,
            as_of_date=self.as_of_date,
            total_overdue=self.total_overdue,
            invoice_count=self.invoice_count,
            currency=self.currency,
            notes=self.notes,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARDunningHistoryModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            customer_id=dto.customer_id,
            level=dto.level.value,
            sent_date=dto.sent_date,
            as_of_date=dto.as_of_date,
            total_overdue=dto.total_overdue,
            invoice_count=dto.invoice_count,
            currency=dto.currency,
            notes=dto.notes,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARDunningHistoryModel customer={self.customer_id} "
            f"level={self.level} sent={self.sent_date}>"
        )


# ---------------------------------------------------------------------------
# 8. ARCreditDecisionModel
# ---------------------------------------------------------------------------


class ARCreditDecisionModel(TrackedBase):
    """
    ORM model for AR credit decisions.

    Maps to the ``CreditDecision`` frozen dataclass.  Each record captures
    a credit limit check or update decision.

    Guarantees:
        - customer_id FK to parties.id.
        - Monetary fields use Decimal.
        - approved defaults to True.
    """

    __tablename__ = "ar_credit_decisions"

    __table_args__ = (
        Index("idx_ar_credit_decisions_customer_id", "customer_id"),
        Index("idx_ar_credit_decisions_decision_date", "decision_date"),
        Index("idx_ar_credit_decisions_approved", "approved"),
    )

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    previous_limit: Mapped[Decimal | None] = mapped_column(nullable=True)
    new_limit: Mapped[Decimal | None] = mapped_column(nullable=True)
    order_amount: Mapped[Decimal | None] = mapped_column(nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import CreditDecision

        return CreditDecision(
            id=self.id,
            customer_id=self.customer_id,
            decision_date=self.decision_date,
            previous_limit=self.previous_limit,
            new_limit=self.new_limit,
            order_amount=self.order_amount,
            approved=self.approved,
            reason=self.reason,
            decided_by=self.decided_by,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARCreditDecisionModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            customer_id=dto.customer_id,
            decision_date=dto.decision_date,
            previous_limit=dto.previous_limit,
            new_limit=dto.new_limit,
            order_amount=dto.order_amount,
            approved=dto.approved,
            reason=dto.reason,
            decided_by=dto.decided_by,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARCreditDecisionModel customer={self.customer_id} "
            f"approved={self.approved} date={self.decision_date}>"
        )


# ---------------------------------------------------------------------------
# 9. ARAutoApplyRuleModel
# ---------------------------------------------------------------------------


class ARAutoApplyRuleModel(TrackedBase):
    """
    ORM model for AR automatic payment application rules.

    Maps to the ``AutoApplyRule`` frozen dataclass.  Rules define how
    incoming receipts are automatically matched to open invoices.

    Guarantees:
        - name is unique (uq_ar_auto_apply_rules_name).
        - priority determines rule evaluation order.
        - tolerance uses Decimal.
    """

    __tablename__ = "ar_auto_apply_rules"

    __table_args__ = (
        UniqueConstraint("name", name="uq_ar_auto_apply_rules_name"),
        Index("idx_ar_auto_apply_rules_priority", "priority"),
        Index("idx_ar_auto_apply_rules_is_active", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False)
    match_field: Mapped[str] = mapped_column(String(100), nullable=False)
    tolerance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ar.models import AutoApplyRule

        return AutoApplyRule(
            id=self.id,
            name=self.name,
            priority=self.priority,
            match_field=self.match_field,
            tolerance=self.tolerance,
            is_active=self.is_active,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ARAutoApplyRuleModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            name=dto.name,
            priority=dto.priority,
            match_field=dto.match_field,
            tolerance=dto.tolerance,
            is_active=dto.is_active,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ARAutoApplyRuleModel {self.name} "
            f"priority={self.priority} active={self.is_active}>"
        )
