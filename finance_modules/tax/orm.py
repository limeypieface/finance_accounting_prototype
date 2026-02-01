"""
Tax ORM Persistence Models (``finance_modules.tax.orm``).

Responsibility:
    SQLAlchemy ORM models that persist the frozen dataclass DTOs defined in
    ``finance_modules.tax.models``.  Each ORM class mirrors a DTO and provides
    ``to_dto()`` / ``from_dto()`` round-trip conversion.

Architecture position:
    **Modules layer** -- persistence companions to the pure DTO models.
    Inherits from ``TrackedBase`` (kernel DB base) which provides:
    id (UUID PK, auto-generated), created_at, updated_at,
    created_by_id (NOT NULL UUID), updated_by_id (nullable UUID).

Invariants enforced:
    - All monetary fields use Decimal (maps to Numeric(38,9)) -- NEVER float.
    - Enum fields stored as String(50) containing the enum .value string.
    - FK relationships within the tax module use explicit ForeignKey.
    - GL account codes stored as String(50) -- no FK to kernel Account.

Audit relevance:
    Tax jurisdictions, rates, exemptions, transactions, returns, and ASC 740
    deferred-tax constructs are all SOX-critical and subject to audit review.
    TrackedBase audit columns (created_at, updated_at, created_by_id,
    updated_by_id) are inherited by every model.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# TaxJurisdictionModel
# ---------------------------------------------------------------------------

class TaxJurisdictionModel(TrackedBase):
    """
    ORM model for ``TaxJurisdiction`` -- a tax jurisdiction (country, state,
    county, city, or district).

    Contract:
        Each jurisdiction has a unique ``code``.  Hierarchical nesting is
        modelled via ``parent_id`` self-referential FK.

    Guarantees:
        - ``code`` is unique (uq_tax_jurisdiction_code).
        - ``tax_type`` stores the TaxType enum .value string.
    """

    __tablename__ = "tax_jurisdictions"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    jurisdiction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tax_jurisdictions.id"), nullable=True,
    )
    tax_type: Mapped[str] = mapped_column(String(50), nullable=False, default="sales")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Self-referential relationship
    parent: Mapped["TaxJurisdictionModel | None"] = relationship(
        "TaxJurisdictionModel", remote_side="TaxJurisdictionModel.id", lazy="select",
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_tax_jurisdiction_code"),
        Index("idx_tax_jurisdiction_type", "jurisdiction_type"),
        Index("idx_tax_jurisdiction_parent", "parent_id"),
        Index("idx_tax_jurisdiction_active", "is_active"),
    )

    def to_dto(self):
        from finance_modules.tax.models import TaxJurisdiction, TaxType
        return TaxJurisdiction(
            id=self.id,
            code=self.code,
            name=self.name,
            jurisdiction_type=self.jurisdiction_type,
            parent_id=self.parent_id,
            tax_type=TaxType(self.tax_type),
            is_active=self.is_active,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TaxJurisdictionModel":
        return cls(
            id=dto.id,
            code=dto.code,
            name=dto.name,
            jurisdiction_type=dto.jurisdiction_type,
            parent_id=dto.parent_id,
            tax_type=dto.tax_type.value if hasattr(dto.tax_type, "value") else dto.tax_type,
            is_active=dto.is_active,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<TaxJurisdictionModel {self.code}: {self.name} ({self.jurisdiction_type})>"


# ---------------------------------------------------------------------------
# TaxRateModel
# ---------------------------------------------------------------------------

class TaxRateModel(TrackedBase):
    """
    ORM model for ``TaxRate`` -- a tax rate for a jurisdiction and category.

    Contract:
        Each rate has an effective_date; end_date is nullable (open-ended).
        A unique constraint prevents duplicate rates for the same jurisdiction,
        category, and effective date.
    """

    __tablename__ = "tax_rates"

    jurisdiction_id: Mapped[UUID] = mapped_column(
        ForeignKey("tax_jurisdictions.id"), nullable=False,
    )
    tax_category: Mapped[str] = mapped_column(String(50), nullable=False)
    rate: Mapped[Decimal] = mapped_column(nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationship to jurisdiction
    jurisdiction: Mapped["TaxJurisdictionModel"] = relationship(
        "TaxJurisdictionModel", lazy="select",
    )

    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "tax_category", "effective_date",
            name="uq_tax_rate_jurisdiction_category_date",
        ),
        Index("idx_tax_rate_jurisdiction", "jurisdiction_id"),
        Index("idx_tax_rate_effective", "effective_date"),
    )

    def to_dto(self):
        from finance_modules.tax.models import TaxRate
        return TaxRate(
            id=self.id,
            jurisdiction_id=self.jurisdiction_id,
            tax_category=self.tax_category,
            rate=self.rate,
            effective_date=self.effective_date,
            end_date=self.end_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TaxRateModel":
        return cls(
            id=dto.id,
            jurisdiction_id=dto.jurisdiction_id,
            tax_category=dto.tax_category,
            rate=dto.rate,
            effective_date=dto.effective_date,
            end_date=dto.end_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TaxRateModel jurisdiction={self.jurisdiction_id} "
            f"category={self.tax_category} rate={self.rate}>"
        )


# ---------------------------------------------------------------------------
# TaxExemptionModel
# ---------------------------------------------------------------------------

class TaxExemptionModel(TrackedBase):
    """
    ORM model for ``TaxExemption`` -- a tax exemption certificate.

    Contract:
        Each exemption links to a jurisdiction and optionally to a customer
        or vendor (Party FK).  Certificate number is unique within a
        jurisdiction.
    """

    __tablename__ = "tax_exemptions"

    exemption_type: Mapped[str] = mapped_column(String(50), nullable=False)
    jurisdiction_id: Mapped[UUID] = mapped_column(
        ForeignKey("tax_jurisdictions.id"), nullable=False,
    )
    certificate_number: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parties.id"), nullable=True,
    )
    vendor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("parties.id"), nullable=True,
    )
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    jurisdiction: Mapped["TaxJurisdictionModel"] = relationship(
        "TaxJurisdictionModel", lazy="select",
    )

    __table_args__ = (
        UniqueConstraint(
            "jurisdiction_id", "certificate_number",
            name="uq_tax_exemption_jurisdiction_cert",
        ),
        Index("idx_tax_exemption_jurisdiction", "jurisdiction_id"),
        Index("idx_tax_exemption_customer", "customer_id"),
        Index("idx_tax_exemption_vendor", "vendor_id"),
        Index("idx_tax_exemption_effective", "effective_date"),
    )

    def to_dto(self):
        from finance_modules.tax.models import ExemptionType, TaxExemption
        return TaxExemption(
            id=self.id,
            exemption_type=ExemptionType(self.exemption_type),
            jurisdiction_id=self.jurisdiction_id,
            certificate_number=self.certificate_number,
            effective_date=self.effective_date,
            customer_id=self.customer_id,
            vendor_id=self.vendor_id,
            expiration_date=self.expiration_date,
            is_verified=self.is_verified,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TaxExemptionModel":
        return cls(
            id=dto.id,
            exemption_type=dto.exemption_type.value if hasattr(dto.exemption_type, "value") else dto.exemption_type,
            jurisdiction_id=dto.jurisdiction_id,
            certificate_number=dto.certificate_number,
            effective_date=dto.effective_date,
            customer_id=dto.customer_id,
            vendor_id=dto.vendor_id,
            expiration_date=dto.expiration_date,
            is_verified=dto.is_verified,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TaxExemptionModel {self.certificate_number} "
            f"type={self.exemption_type} jurisdiction={self.jurisdiction_id}>"
        )


# ---------------------------------------------------------------------------
# TaxTransactionModel
# ---------------------------------------------------------------------------

class TaxTransactionModel(TrackedBase):
    """
    ORM model for ``TaxTransaction`` -- a single taxable transaction for
    reporting.

    Contract:
        Each transaction references a source document (source_type + source_id)
        and a jurisdiction.  Once reported (is_reported=True), the
        tax_return_id links to the return that included it.
    """

    __tablename__ = "tax_transactions"

    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[UUID] = mapped_column(nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    jurisdiction_id: Mapped[UUID] = mapped_column(
        ForeignKey("tax_jurisdictions.id"), nullable=False,
    )
    tax_type: Mapped[str] = mapped_column(String(50), nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(nullable=False)
    exempt_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    is_reported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tax_return_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tax_returns.id"), nullable=True,
    )

    # Relationships
    jurisdiction: Mapped["TaxJurisdictionModel"] = relationship(
        "TaxJurisdictionModel", lazy="select",
    )
    tax_return: Mapped["TaxReturnModel | None"] = relationship(
        "TaxReturnModel", lazy="select",
    )

    __table_args__ = (
        Index("idx_tax_transaction_source", "source_type", "source_id"),
        Index("idx_tax_transaction_jurisdiction", "jurisdiction_id"),
        Index("idx_tax_transaction_date", "transaction_date"),
        Index("idx_tax_transaction_reported", "is_reported"),
        Index("idx_tax_transaction_return", "tax_return_id"),
    )

    def to_dto(self):
        from finance_modules.tax.models import TaxTransaction, TaxType
        return TaxTransaction(
            id=self.id,
            source_type=self.source_type,
            source_id=self.source_id,
            transaction_date=self.transaction_date,
            jurisdiction_id=self.jurisdiction_id,
            tax_type=TaxType(self.tax_type),
            taxable_amount=self.taxable_amount,
            exempt_amount=self.exempt_amount,
            tax_amount=self.tax_amount,
            tax_rate=self.tax_rate,
            is_reported=self.is_reported,
            tax_return_id=self.tax_return_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TaxTransactionModel":
        return cls(
            id=dto.id,
            source_type=dto.source_type,
            source_id=dto.source_id,
            transaction_date=dto.transaction_date,
            jurisdiction_id=dto.jurisdiction_id,
            tax_type=dto.tax_type.value if hasattr(dto.tax_type, "value") else dto.tax_type,
            taxable_amount=dto.taxable_amount,
            exempt_amount=dto.exempt_amount,
            tax_amount=dto.tax_amount,
            tax_rate=dto.tax_rate,
            is_reported=dto.is_reported,
            tax_return_id=dto.tax_return_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TaxTransactionModel source={self.source_type}:{self.source_id} "
            f"amount={self.tax_amount} reported={self.is_reported}>"
        )


# ---------------------------------------------------------------------------
# TaxReturnModel
# ---------------------------------------------------------------------------

class TaxReturnModel(TrackedBase):
    """
    ORM model for ``TaxReturn`` -- a tax return for a jurisdiction and period.

    Contract:
        Each return covers a jurisdiction, tax type, and date range.
        Status follows the TaxReturnStatus lifecycle.
    """

    __tablename__ = "tax_returns"

    jurisdiction_id: Mapped[UUID] = mapped_column(
        ForeignKey("tax_jurisdictions.id"), nullable=False,
    )
    tax_type: Mapped[str] = mapped_column(String(50), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    filing_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    gross_sales: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    taxable_sales: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    exempt_sales: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    tax_collected: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    tax_due: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    filed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmation_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    jurisdiction: Mapped["TaxJurisdictionModel"] = relationship(
        "TaxJurisdictionModel", lazy="select",
    )
    transactions: Mapped[list["TaxTransactionModel"]] = relationship(
        "TaxTransactionModel", back_populates="tax_return", lazy="select",
    )

    __table_args__ = (
        Index("idx_tax_return_jurisdiction", "jurisdiction_id"),
        Index("idx_tax_return_period", "period_start", "period_end"),
        Index("idx_tax_return_status", "status"),
        Index("idx_tax_return_due_date", "filing_due_date"),
    )

    def to_dto(self):
        from finance_modules.tax.models import TaxReturn, TaxReturnStatus, TaxType
        return TaxReturn(
            id=self.id,
            jurisdiction_id=self.jurisdiction_id,
            tax_type=TaxType(self.tax_type),
            period_start=self.period_start,
            period_end=self.period_end,
            filing_due_date=self.filing_due_date,
            gross_sales=self.gross_sales,
            taxable_sales=self.taxable_sales,
            exempt_sales=self.exempt_sales,
            tax_collected=self.tax_collected,
            tax_due=self.tax_due,
            status=TaxReturnStatus(self.status),
            filed_date=self.filed_date,
            confirmation_number=self.confirmation_number,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TaxReturnModel":
        return cls(
            id=dto.id,
            jurisdiction_id=dto.jurisdiction_id,
            tax_type=dto.tax_type.value if hasattr(dto.tax_type, "value") else dto.tax_type,
            period_start=dto.period_start,
            period_end=dto.period_end,
            filing_due_date=dto.filing_due_date,
            gross_sales=dto.gross_sales,
            taxable_sales=dto.taxable_sales,
            exempt_sales=dto.exempt_sales,
            tax_collected=dto.tax_collected,
            tax_due=dto.tax_due,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            filed_date=dto.filed_date,
            confirmation_number=dto.confirmation_number,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TaxReturnModel jurisdiction={self.jurisdiction_id} "
            f"period={self.period_start}..{self.period_end} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# TemporaryDifferenceModel
# ---------------------------------------------------------------------------

class TemporaryDifferenceModel(TrackedBase):
    """
    ORM model for ``TemporaryDifference`` -- a temporary difference between
    book and tax basis (ASC 740).

    Contract:
        Each row represents a single identified temporary difference for a
        given reporting period.  ``difference_type`` is either "taxable"
        or "deductible".
    """

    __tablename__ = "tax_temporary_differences"

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    book_basis: Mapped[Decimal] = mapped_column(nullable=False)
    tax_basis: Mapped[Decimal] = mapped_column(nullable=False)
    difference_amount: Mapped[Decimal] = mapped_column(nullable=False)
    difference_type: Mapped[str] = mapped_column(String(50), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(default=Decimal("0.21"), nullable=False)
    deferred_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    period: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    __table_args__ = (
        Index("idx_temp_diff_period", "period"),
        Index("idx_temp_diff_type", "difference_type"),
    )

    def to_dto(self):
        from finance_modules.tax.models import TemporaryDifference
        return TemporaryDifference(
            id=self.id,
            description=self.description,
            book_basis=self.book_basis,
            tax_basis=self.tax_basis,
            difference_amount=self.difference_amount,
            difference_type=self.difference_type,
            tax_rate=self.tax_rate,
            deferred_amount=self.deferred_amount,
            period=self.period,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TemporaryDifferenceModel":
        return cls(
            id=dto.id,
            description=dto.description,
            book_basis=dto.book_basis,
            tax_basis=dto.tax_basis,
            difference_amount=dto.difference_amount,
            difference_type=dto.difference_type,
            tax_rate=dto.tax_rate,
            deferred_amount=dto.deferred_amount,
            period=dto.period,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TemporaryDifferenceModel {self.description} "
            f"type={self.difference_type} amount={self.difference_amount}>"
        )


# ---------------------------------------------------------------------------
# DeferredTaxAssetModel
# ---------------------------------------------------------------------------

class DeferredTaxAssetModel(TrackedBase):
    """
    ORM model for ``DeferredTaxAsset`` -- a deferred tax asset (ASC 740).

    Contract:
        Each row represents a deferred tax asset for a given source and
        reporting period.  ``net_amount = amount - valuation_allowance``.
    """

    __tablename__ = "tax_deferred_assets"

    source: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    valuation_allowance: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    period: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    __table_args__ = (
        Index("idx_deferred_asset_period", "period"),
        Index("idx_deferred_asset_source", "source"),
    )

    def to_dto(self):
        from finance_modules.tax.models import DeferredTaxAsset
        return DeferredTaxAsset(
            id=self.id,
            source=self.source,
            amount=self.amount,
            valuation_allowance=self.valuation_allowance,
            net_amount=self.net_amount,
            period=self.period,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "DeferredTaxAssetModel":
        return cls(
            id=dto.id,
            source=dto.source,
            amount=dto.amount,
            valuation_allowance=dto.valuation_allowance,
            net_amount=dto.net_amount,
            period=dto.period,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<DeferredTaxAssetModel source={self.source} net={self.net_amount}>"


# ---------------------------------------------------------------------------
# DeferredTaxLiabilityModel
# ---------------------------------------------------------------------------

class DeferredTaxLiabilityModel(TrackedBase):
    """
    ORM model for ``DeferredTaxLiability`` -- a deferred tax liability (ASC 740).

    Contract:
        Each row represents a deferred tax liability for a given source and
        reporting period.
    """

    __tablename__ = "tax_deferred_liabilities"

    source: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    period: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    __table_args__ = (
        Index("idx_deferred_liability_period", "period"),
        Index("idx_deferred_liability_source", "source"),
    )

    def to_dto(self):
        from finance_modules.tax.models import DeferredTaxLiability
        return DeferredTaxLiability(
            id=self.id,
            source=self.source,
            amount=self.amount,
            period=self.period,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "DeferredTaxLiabilityModel":
        return cls(
            id=dto.id,
            source=dto.source,
            amount=dto.amount,
            period=dto.period,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<DeferredTaxLiabilityModel source={self.source} amount={self.amount}>"


# ---------------------------------------------------------------------------
# TaxProvisionModel
# ---------------------------------------------------------------------------

class TaxProvisionModel(TrackedBase):
    """
    ORM model for ``TaxProvision`` -- tax provision summary for a period
    (ASC 740).

    Contract:
        Each row summarises the tax provision for a single reporting period.
        The ``period`` column is unique -- there is exactly one provision
        summary per period.

    Note:
        The DTO ``TaxProvision`` does not carry an ``id`` field.  The ORM
        model adds one via TrackedBase for persistence identity.  The
        ``to_dto()`` method omits it because the DTO is keyed by ``period``.
    """

    __tablename__ = "tax_provisions"

    period: Mapped[str] = mapped_column(String(50), nullable=False)
    current_tax_expense: Mapped[Decimal] = mapped_column(nullable=False)
    deferred_tax_expense: Mapped[Decimal] = mapped_column(nullable=False)
    total_tax_expense: Mapped[Decimal] = mapped_column(nullable=False)
    effective_rate: Mapped[Decimal] = mapped_column(nullable=False)
    pre_tax_income: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)

    __table_args__ = (
        UniqueConstraint("period", name="uq_tax_provision_period"),
    )

    def to_dto(self):
        from finance_modules.tax.models import TaxProvision
        return TaxProvision(
            period=self.period,
            current_tax_expense=self.current_tax_expense,
            deferred_tax_expense=self.deferred_tax_expense,
            total_tax_expense=self.total_tax_expense,
            effective_rate=self.effective_rate,
            pre_tax_income=self.pre_tax_income,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TaxProvisionModel":
        return cls(
            period=dto.period,
            current_tax_expense=dto.current_tax_expense,
            deferred_tax_expense=dto.deferred_tax_expense,
            total_tax_expense=dto.total_tax_expense,
            effective_rate=dto.effective_rate,
            pre_tax_income=dto.pre_tax_income,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TaxProvisionModel period={self.period} "
            f"total_expense={self.total_tax_expense} rate={self.effective_rate}>"
        )


# ---------------------------------------------------------------------------
# JurisdictionModel (lightweight multi-jurisdiction config entity)
# ---------------------------------------------------------------------------

class JurisdictionModel(TrackedBase):
    """
    ORM model for ``Jurisdiction`` -- a lightweight tax jurisdiction used
    for multi-jurisdiction calculations.

    Contract:
        Each jurisdiction has a unique ``code``.  This is a simpler
        representation than TaxJurisdictionModel, used for calculation-time
        jurisdiction lookups.

    Note:
        The DTO ``Jurisdiction`` does not carry an ``id`` field.  The ORM
        model adds one via TrackedBase for persistence identity.  The
        ``to_dto()`` method omits it because the DTO is keyed by ``code``.
    """

    __tablename__ = "tax_jurisdiction_configs"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(nullable=False)
    jurisdiction_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="state",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("code", name="uq_tax_jurisdiction_config_code"),
        Index("idx_tax_jurisdiction_config_type", "jurisdiction_type"),
        Index("idx_tax_jurisdiction_config_active", "is_active"),
    )

    def to_dto(self):
        from finance_modules.tax.models import Jurisdiction
        return Jurisdiction(
            code=self.code,
            name=self.name,
            tax_rate=self.tax_rate,
            jurisdiction_type=self.jurisdiction_type,
            is_active=self.is_active,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "JurisdictionModel":
        return cls(
            code=dto.code,
            name=dto.name,
            tax_rate=dto.tax_rate,
            jurisdiction_type=dto.jurisdiction_type,
            is_active=dto.is_active,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<JurisdictionModel {self.code}: {self.name} "
            f"rate={self.tax_rate} ({self.jurisdiction_type})>"
        )
