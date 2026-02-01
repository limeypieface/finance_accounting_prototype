"""
Accounts Payable ORM Models (``finance_modules.ap.orm``).

Responsibility
--------------
SQLAlchemy persistence models for the AP module.  Maps frozen domain
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
# 1. VendorProfileModel
# ---------------------------------------------------------------------------


class VendorProfileModel(TrackedBase):
    """
    ORM model for AP vendor profiles.

    Maps to the ``Vendor`` frozen dataclass.  Vendor identity is anchored
    to the kernel ``Party`` model via ``vendor_id`` FK.

    Guarantees:
        - vendor_id FK to parties.id.
        - code is unique within AP (uq_ap_vendor_profiles_code).
        - payment_terms_days defaults to 30.
        - default_payment_method stored as string enum value.
    """

    __tablename__ = "ap_vendor_profiles"

    __table_args__ = (
        UniqueConstraint("code", name="uq_ap_vendor_profiles_code"),
        Index("idx_ap_vendor_profiles_vendor_id", "vendor_id"),
        Index("idx_ap_vendor_profiles_is_active", "is_active"),
    )

    vendor_id: Mapped[UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_terms_days: Mapped[int] = mapped_column(default=30)
    default_payment_method: Mapped[str] = mapped_column(
        String(50), default="ach"
    )
    default_gl_account_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_1099_eligible: Mapped[bool] = mapped_column(Boolean, default=False)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ap.models import PaymentMethod, Vendor

        return Vendor(
            id=self.id,
            code=self.code,
            name=self.name,
            tax_id=self.tax_id,
            payment_terms_days=self.payment_terms_days,
            default_payment_method=PaymentMethod(self.default_payment_method),
            default_gl_account_code=self.default_gl_account_code,
            is_active=self.is_active,
            is_1099_eligible=self.is_1099_eligible,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "VendorProfileModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            vendor_id=dto.id,
            code=dto.code,
            name=dto.name,
            tax_id=dto.tax_id,
            payment_terms_days=dto.payment_terms_days,
            default_payment_method=dto.default_payment_method.value,
            default_gl_account_code=dto.default_gl_account_code,
            is_active=dto.is_active,
            is_1099_eligible=dto.is_1099_eligible,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<VendorProfileModel {self.code}: {self.name}>"


# ---------------------------------------------------------------------------
# 2. APInvoiceModel
# ---------------------------------------------------------------------------


class APInvoiceModel(TrackedBase):
    """
    ORM model for AP invoices.

    Maps to the ``Invoice`` frozen dataclass.  Lines are stored in a
    separate child table via ``lines`` relationship.

    Guarantees:
        - invoice_number is unique (uq_ap_invoices_invoice_number).
        - Monetary fields use Decimal (Numeric(38,9) via type_annotation_map).
        - status stored as string enum value.
    """

    __tablename__ = "ap_invoices"

    __table_args__ = (
        UniqueConstraint(
            "invoice_number", name="uq_ap_invoices_invoice_number"
        ),
        Index("idx_ap_invoices_vendor_id", "vendor_id"),
        Index("idx_ap_invoices_status", "status"),
        Index("idx_ap_invoices_due_date", "due_date"),
        Index("idx_ap_invoices_po_id", "po_id"),
    )

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    po_id: Mapped[UUID | None] = mapped_column(nullable=True)
    match_variance: Mapped[Decimal | None] = mapped_column(nullable=True)
    approved_by_id: Mapped[UUID | None] = mapped_column(nullable=True)
    approved_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationship to child lines
    lines: Mapped[list["APInvoiceLineModel"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ap.models import Invoice, InvoiceStatus

        return Invoice(
            id=self.id,
            vendor_id=self.vendor_id,
            invoice_number=self.invoice_number,
            invoice_date=self.invoice_date,
            due_date=self.due_date,
            currency=self.currency,
            subtotal=self.subtotal,
            tax_amount=self.tax_amount,
            total_amount=self.total_amount,
            status=InvoiceStatus(self.status),
            po_id=self.po_id,
            lines=tuple(line.to_dto() for line in self.lines),
            match_variance=self.match_variance,
            approved_by_id=self.approved_by_id,
            approved_at=self.approved_at,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "APInvoiceModel":
        """Create ORM model from frozen dataclass."""
        model = cls(
            id=dto.id,
            vendor_id=dto.vendor_id,
            invoice_number=dto.invoice_number,
            invoice_date=dto.invoice_date,
            due_date=dto.due_date,
            currency=dto.currency,
            subtotal=dto.subtotal,
            tax_amount=dto.tax_amount,
            total_amount=dto.total_amount,
            status=dto.status.value,
            po_id=dto.po_id,
            match_variance=dto.match_variance,
            approved_by_id=dto.approved_by_id,
            approved_at=dto.approved_at,
            created_by_id=created_by_id,
        )
        model.lines = [
            APInvoiceLineModel.from_dto(line, created_by_id)
            for line in dto.lines
        ]
        return model

    def __repr__(self) -> str:
        return (
            f"<APInvoiceModel {self.invoice_number} "
            f"status={self.status} total={self.total_amount}>"
        )


# ---------------------------------------------------------------------------
# 3. APInvoiceLineModel
# ---------------------------------------------------------------------------


class APInvoiceLineModel(TrackedBase):
    """
    ORM model for AP invoice line items.

    Maps to the ``InvoiceLine`` frozen dataclass.  Each line belongs to
    exactly one APInvoiceModel.

    Guarantees:
        - invoice_id FK to ap_invoices.id.
        - Monetary fields use Decimal.
        - GL account stored as string code (no FK to kernel accounts table).
    """

    __tablename__ = "ap_invoice_lines"

    __table_args__ = (
        Index("idx_ap_invoice_lines_invoice_id", "invoice_id"),
        Index("idx_ap_invoice_lines_gl_account", "gl_account_code"),
        Index("idx_ap_invoice_lines_po_line_id", "po_line_id"),
    )

    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("ap_invoices.id"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    gl_account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    po_line_id: Mapped[UUID | None] = mapped_column(nullable=True)
    receipt_line_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Relationship to parent invoice
    invoice: Mapped["APInvoiceModel"] = relationship(
        back_populates="lines",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ap.models import InvoiceLine

        return InvoiceLine(
            id=self.id,
            invoice_id=self.invoice_id,
            line_number=self.line_number,
            description=self.description,
            quantity=self.quantity,
            unit_price=self.unit_price,
            amount=self.amount,
            gl_account_code=self.gl_account_code,
            po_line_id=self.po_line_id,
            receipt_line_id=self.receipt_line_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "APInvoiceLineModel":
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
            po_line_id=dto.po_line_id,
            receipt_line_id=dto.receipt_line_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<APInvoiceLineModel line={self.line_number} "
            f"amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 4. APPaymentModel
# ---------------------------------------------------------------------------


class APPaymentModel(TrackedBase):
    """
    ORM model for AP payments.

    Maps to the ``Payment`` frozen dataclass.  Invoice IDs are stored
    as JSON text since the dataclass uses ``tuple[UUID, ...]``.

    Guarantees:
        - vendor_id FK to parties.id.
        - Monetary fields use Decimal.
        - payment_method and status stored as string enum values.
    """

    __tablename__ = "ap_payments"

    __table_args__ = (
        Index("idx_ap_payments_vendor_id", "vendor_id"),
        Index("idx_ap_payments_status", "status"),
        Index("idx_ap_payments_payment_date", "payment_date"),
        Index("idx_ap_payments_reference", "reference"),
    )

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    invoice_ids_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    discount_taken: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    bank_account_id: Mapped[UUID | None] = mapped_column(nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        import json

        from finance_modules.ap.models import Payment, PaymentMethod, PaymentStatus

        invoice_ids = ()
        if self.invoice_ids_json:
            invoice_ids = tuple(
                UUID(uid) for uid in json.loads(self.invoice_ids_json)
            )

        return Payment(
            id=self.id,
            vendor_id=self.vendor_id,
            payment_date=self.payment_date,
            payment_method=PaymentMethod(self.payment_method),
            amount=self.amount,
            currency=self.currency,
            reference=self.reference,
            status=PaymentStatus(self.status),
            invoice_ids=invoice_ids,
            discount_taken=self.discount_taken,
            bank_account_id=self.bank_account_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "APPaymentModel":
        """Create ORM model from frozen dataclass."""
        import json

        invoice_ids_json = None
        if dto.invoice_ids:
            invoice_ids_json = json.dumps([str(uid) for uid in dto.invoice_ids])

        return cls(
            id=dto.id,
            vendor_id=dto.vendor_id,
            payment_date=dto.payment_date,
            payment_method=dto.payment_method.value,
            amount=dto.amount,
            currency=dto.currency,
            reference=dto.reference,
            status=dto.status.value,
            invoice_ids_json=invoice_ids_json,
            discount_taken=dto.discount_taken,
            bank_account_id=dto.bank_account_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<APPaymentModel {self.reference} "
            f"status={self.status} amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 5. APPaymentBatchModel
# ---------------------------------------------------------------------------


class APPaymentBatchModel(TrackedBase):
    """
    ORM model for AP payment batches.

    Maps to the ``PaymentBatch`` frozen dataclass.  Payment IDs are stored
    as JSON text since the dataclass uses ``tuple[UUID, ...]``.

    Guarantees:
        - Monetary fields use Decimal.
        - status stored as string.
    """

    __tablename__ = "ap_payment_batches"

    __table_args__ = (
        Index("idx_ap_payment_batches_batch_date", "batch_date"),
        Index("idx_ap_payment_batches_status", "status"),
    )

    batch_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    payment_ids_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        import json

        from finance_modules.ap.models import PaymentBatch, PaymentMethod

        payment_ids = ()
        if self.payment_ids_json:
            payment_ids = tuple(
                UUID(uid) for uid in json.loads(self.payment_ids_json)
            )

        return PaymentBatch(
            id=self.id,
            batch_date=self.batch_date,
            payment_method=PaymentMethod(self.payment_method),
            payment_ids=payment_ids,
            total_amount=self.total_amount,
            status=self.status,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "APPaymentBatchModel":
        """Create ORM model from frozen dataclass."""
        import json

        payment_ids_json = None
        if dto.payment_ids:
            payment_ids_json = json.dumps(
                [str(uid) for uid in dto.payment_ids]
            )

        return cls(
            id=dto.id,
            batch_date=dto.batch_date,
            payment_method=dto.payment_method.value,
            payment_ids_json=payment_ids_json,
            total_amount=dto.total_amount,
            status=dto.status,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<APPaymentBatchModel {self.batch_date} "
            f"status={self.status} total={self.total_amount}>"
        )


# ---------------------------------------------------------------------------
# 6. APPaymentRunModel
# ---------------------------------------------------------------------------


class APPaymentRunModel(TrackedBase):
    """
    ORM model for AP payment runs.

    Maps to the ``PaymentRun`` frozen dataclass.  Lines are stored in a
    separate child table via ``lines`` relationship.

    Guarantees:
        - status stored as string enum value.
        - Monetary fields use Decimal.
    """

    __tablename__ = "ap_payment_runs"

    __table_args__ = (
        Index("idx_ap_payment_runs_status", "status"),
        Index("idx_ap_payment_runs_payment_date", "payment_date"),
    )

    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    total_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    line_count: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    executed_by: Mapped[UUID | None] = mapped_column(nullable=True)

    # Relationship to child lines
    lines: Mapped[list["APPaymentRunLineModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ap.models import PaymentRun, PaymentRunStatus

        return PaymentRun(
            id=self.id,
            payment_date=self.payment_date,
            currency=self.currency,
            status=PaymentRunStatus(self.status),
            total_amount=self.total_amount,
            line_count=self.line_count,
            created_by=self.created_by,
            executed_by=self.executed_by,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "APPaymentRunModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            payment_date=dto.payment_date,
            currency=dto.currency,
            status=dto.status.value,
            total_amount=dto.total_amount,
            line_count=dto.line_count,
            created_by=dto.created_by,
            executed_by=dto.executed_by,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<APPaymentRunModel {self.payment_date} "
            f"status={self.status} total={self.total_amount}>"
        )


# ---------------------------------------------------------------------------
# 7. APPaymentRunLineModel
# ---------------------------------------------------------------------------


class APPaymentRunLineModel(TrackedBase):
    """
    ORM model for AP payment run line items.

    Maps to the ``PaymentRunLine`` frozen dataclass.  Each line belongs to
    exactly one APPaymentRunModel.

    Guarantees:
        - run_id FK to ap_payment_runs.id.
        - invoice_id and vendor_id reference upstream entities.
        - Monetary fields use Decimal.
    """

    __tablename__ = "ap_payment_run_lines"

    __table_args__ = (
        Index("idx_ap_payment_run_lines_run_id", "run_id"),
        Index("idx_ap_payment_run_lines_invoice_id", "invoice_id"),
        Index("idx_ap_payment_run_lines_vendor_id", "vendor_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ap_payment_runs.id"), nullable=False
    )
    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("ap_invoices.id"), nullable=False
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    payment_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Relationship to parent run
    run: Mapped["APPaymentRunModel"] = relationship(
        back_populates="lines",
    )

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ap.models import PaymentRunLine

        return PaymentRunLine(
            id=self.id,
            run_id=self.run_id,
            invoice_id=self.invoice_id,
            vendor_id=self.vendor_id,
            amount=self.amount,
            discount_amount=self.discount_amount,
            payment_id=self.payment_id,
        )

    @classmethod
    def from_dto(
        cls, dto, created_by_id: UUID
    ) -> "APPaymentRunLineModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            run_id=dto.run_id,
            invoice_id=dto.invoice_id,
            vendor_id=dto.vendor_id,
            amount=dto.amount,
            discount_amount=dto.discount_amount,
            payment_id=dto.payment_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<APPaymentRunLineModel invoice={self.invoice_id} "
            f"amount={self.amount}>"
        )


# ---------------------------------------------------------------------------
# 8. APVendorHoldModel
# ---------------------------------------------------------------------------


class APVendorHoldModel(TrackedBase):
    """
    ORM model for AP vendor holds.

    Maps to the ``VendorHold`` frozen dataclass.

    Guarantees:
        - vendor_id FK to parties.id.
        - status stored as string enum value.
    """

    __tablename__ = "ap_vendor_holds"

    __table_args__ = (
        Index("idx_ap_vendor_holds_vendor_id", "vendor_id"),
        Index("idx_ap_vendor_holds_status", "status"),
    )

    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    hold_date: Mapped[date] = mapped_column(Date, nullable=False)
    held_by: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")
    released_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    released_by: Mapped[UUID | None] = mapped_column(nullable=True)

    def to_dto(self):
        """Convert ORM model to frozen dataclass."""
        from finance_modules.ap.models import HoldStatus, VendorHold

        return VendorHold(
            id=self.id,
            vendor_id=self.vendor_id,
            reason=self.reason,
            hold_date=self.hold_date,
            held_by=self.held_by,
            status=HoldStatus(self.status),
            released_date=self.released_date,
            released_by=self.released_by,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "APVendorHoldModel":
        """Create ORM model from frozen dataclass."""
        return cls(
            id=dto.id,
            vendor_id=dto.vendor_id,
            reason=dto.reason,
            hold_date=dto.hold_date,
            held_by=dto.held_by,
            status=dto.status.value,
            released_date=dto.released_date,
            released_by=dto.released_by,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<APVendorHoldModel vendor={self.vendor_id} "
            f"status={self.status}>"
        )
