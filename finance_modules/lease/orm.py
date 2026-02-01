"""
Module: finance_modules.lease.orm
Responsibility:
    SQLAlchemy ORM persistence models for the Lease Accounting module
    (ASC 842).  Maps frozen dataclass DTOs from
    ``finance_modules.lease.models`` to relational tables.

Architecture position:
    **Modules layer** -- ORM models inheriting from ``TrackedBase``
    (kernel DB base).  These models persist ASC 842 concepts:
    leases, lease payments, right-of-use (ROU) assets, lease
    liabilities, and lease modifications.

Invariants enforced:
    - All monetary fields use Decimal (maps to Numeric(38,9) via TrackedBase).
    - Enum fields stored as String(50) for safe serialization.
    - TrackedBase provides id, created_at, updated_at, created_by_id,
      updated_by_id automatically.
    - FK to kernel Party (lessee_id -> parties.id).

Failure modes:
    - IntegrityError on duplicate unique constraints.
    - ForeignKey violation on invalid parent references.

Audit relevance:
    - LeaseModel tracks lease classification decisions (ASC 842-10-25).
    - LeasePaymentModel tracks individual payment records.
    - ROUAssetModel and LeaseLiabilityModel support balance sheet
      disclosure requirements.
    - LeaseModificationModel tracks re-measurement events.
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

from finance_kernel.db.base import TrackedBase, UUIDString


# =============================================================================
# Lease
# =============================================================================


class LeaseModel(TrackedBase):
    """
    A lease agreement per ASC 842.

    Contract:
        Each LeaseModel represents a lease arrangement that has been
        identified, classified, and recorded.  Classification determines
        the accounting treatment (finance vs operating vs short-term).

    Guarantees:
        - ``lease_number`` is unique (uq_lease_number).
        - ``lessee_id`` references parties.id (FK to kernel Party).
        - ``classification`` is one of: finance, operating, short_term.
        - ``status`` is one of: draft, active, modified, terminated, expired.
        - ``currency`` defaults to "USD" (ISO 4217).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "lease_leases"

    __table_args__ = (
        UniqueConstraint("lease_number", name="uq_lease_number"),
        Index("idx_lease_lessee", "lessee_id"),
        Index("idx_lease_classification", "classification"),
        Index("idx_lease_status", "status"),
        Index("idx_lease_commencement", "commencement_date"),
    )

    lease_number: Mapped[str] = mapped_column(String(100), nullable=False)
    lessee_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("parties.id"),
        nullable=False,
    )
    lessor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    commencement_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    classification: Mapped[str] = mapped_column(String(50), default="operating")
    status: Mapped[str] = mapped_column(String(50), default="draft")
    monthly_payment: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    discount_rate: Mapped[Decimal] = mapped_column(default=Decimal("0.05"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Child relationships
    payments: Mapped[list["LeasePaymentModel"]] = relationship(
        "LeasePaymentModel",
        back_populates="lease",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    rou_asset: Mapped["ROUAssetModel | None"] = relationship(
        "ROUAssetModel",
        back_populates="lease",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    liability: Mapped["LeaseLiabilityModel | None"] = relationship(
        "LeaseLiabilityModel",
        back_populates="lease",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    modifications: Mapped[list["LeaseModificationModel"]] = relationship(
        "LeaseModificationModel",
        back_populates="lease",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.lease.models import (
            Lease,
            LeaseClassification,
            LeaseStatus,
        )

        return Lease(
            id=self.id,
            lease_number=self.lease_number,
            lessee_id=self.lessee_id,
            lessor_name=self.lessor_name,
            commencement_date=self.commencement_date,
            end_date=self.end_date,
            classification=LeaseClassification(self.classification),
            status=LeaseStatus(self.status),
            monthly_payment=self.monthly_payment,
            discount_rate=self.discount_rate,
            currency=self.currency,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "LeaseModel":
        return cls(
            id=dto.id,
            lease_number=dto.lease_number,
            lessee_id=dto.lessee_id,
            lessor_name=dto.lessor_name,
            commencement_date=dto.commencement_date,
            end_date=dto.end_date,
            classification=(
                dto.classification.value
                if hasattr(dto.classification, "value")
                else dto.classification
            ),
            status=(
                dto.status.value
                if hasattr(dto.status, "value")
                else dto.status
            ),
            monthly_payment=dto.monthly_payment,
            discount_rate=dto.discount_rate,
            currency=dto.currency,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<LeaseModel {self.lease_number} "
            f"({self.classification}/{self.status})>"
        )


# =============================================================================
# Lease Payment
# =============================================================================


class LeasePaymentModel(TrackedBase):
    """
    A lease payment record.

    Contract:
        Each LeasePaymentModel records a single scheduled or actual payment
        for a lease, with the split between principal and interest portions.

    Guarantees:
        - ``lease_id`` references lease_leases.id.
        - (lease_id, payment_number) is unique (uq_lease_payment_number).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "lease_payments"

    __table_args__ = (
        UniqueConstraint(
            "lease_id", "payment_number", name="uq_lease_payment_number",
        ),
        Index("idx_lease_payment_lease", "lease_id"),
        Index("idx_lease_payment_date", "payment_date"),
    )

    lease_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("lease_leases.id"),
        nullable=False,
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    principal_portion: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    interest_portion: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    payment_number: Mapped[int] = mapped_column(default=0)

    # Parent relationship
    lease: Mapped["LeaseModel"] = relationship(
        "LeaseModel",
        back_populates="payments",
    )

    def to_dto(self):
        from finance_modules.lease.models import LeasePayment

        return LeasePayment(
            id=self.id,
            lease_id=self.lease_id,
            payment_date=self.payment_date,
            amount=self.amount,
            principal_portion=self.principal_portion,
            interest_portion=self.interest_portion,
            payment_number=self.payment_number,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "LeasePaymentModel":
        return cls(
            id=dto.id,
            lease_id=dto.lease_id,
            payment_date=dto.payment_date,
            amount=dto.amount,
            principal_portion=dto.principal_portion,
            interest_portion=dto.interest_portion,
            payment_number=dto.payment_number,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<LeasePaymentModel #{self.payment_number} "
            f"amount={self.amount} date={self.payment_date}>"
        )


# =============================================================================
# Right-of-Use Asset
# =============================================================================


class ROUAssetModel(TrackedBase):
    """
    Right-of-use (ROU) asset per ASC 842.

    Contract:
        Each ROUAssetModel is a one-to-one child of a LeaseModel, recording
        the initial measurement and ongoing amortization of the ROU asset.

    Guarantees:
        - ``lease_id`` references lease_leases.id and is unique (one ROU
          per lease, enforced by uq_rou_asset_lease).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "lease_rou_assets"

    __table_args__ = (
        UniqueConstraint("lease_id", name="uq_rou_asset_lease"),
        Index("idx_rou_asset_lease", "lease_id"),
    )

    lease_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("lease_leases.id"),
        nullable=False,
    )
    initial_value: Mapped[Decimal] = mapped_column(nullable=False)
    accumulated_amortization: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    carrying_value: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    commencement_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Parent relationship
    lease: Mapped["LeaseModel"] = relationship(
        "LeaseModel",
        back_populates="rou_asset",
    )

    def to_dto(self):
        from finance_modules.lease.models import ROUAsset

        return ROUAsset(
            id=self.id,
            lease_id=self.lease_id,
            initial_value=self.initial_value,
            accumulated_amortization=self.accumulated_amortization,
            carrying_value=self.carrying_value,
            commencement_date=self.commencement_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ROUAssetModel":
        return cls(
            id=dto.id,
            lease_id=dto.lease_id,
            initial_value=dto.initial_value,
            accumulated_amortization=dto.accumulated_amortization,
            carrying_value=dto.carrying_value,
            commencement_date=dto.commencement_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ROUAssetModel lease={self.lease_id} "
            f"carrying={self.carrying_value}>"
        )


# =============================================================================
# Lease Liability
# =============================================================================


class LeaseLiabilityModel(TrackedBase):
    """
    Lease liability per ASC 842.

    Contract:
        Each LeaseLiabilityModel is a one-to-one child of a LeaseModel,
        recording the present value of remaining lease payments.

    Guarantees:
        - ``lease_id`` references lease_leases.id and is unique (one
          liability per lease, enforced by uq_lease_liability_lease).
        - All monetary fields are Decimal (Numeric(38,9)).
    """

    __tablename__ = "lease_liabilities"

    __table_args__ = (
        UniqueConstraint("lease_id", name="uq_lease_liability_lease"),
        Index("idx_lease_liability_lease", "lease_id"),
    )

    lease_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("lease_leases.id"),
        nullable=False,
    )
    initial_value: Mapped[Decimal] = mapped_column(nullable=False)
    current_balance: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    commencement_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Parent relationship
    lease: Mapped["LeaseModel"] = relationship(
        "LeaseModel",
        back_populates="liability",
    )

    def to_dto(self):
        from finance_modules.lease.models import LeaseLiability

        return LeaseLiability(
            id=self.id,
            lease_id=self.lease_id,
            initial_value=self.initial_value,
            current_balance=self.current_balance,
            commencement_date=self.commencement_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "LeaseLiabilityModel":
        return cls(
            id=dto.id,
            lease_id=dto.lease_id,
            initial_value=dto.initial_value,
            current_balance=dto.current_balance,
            commencement_date=dto.commencement_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<LeaseLiabilityModel lease={self.lease_id} "
            f"balance={self.current_balance}>"
        )


# =============================================================================
# Lease Modification
# =============================================================================


class LeaseModificationModel(TrackedBase):
    """
    A lease modification record per ASC 842.

    Contract:
        Records modifications to lease agreements, including changes to
        payment terms, lease term, and the resulting re-measurement amount.

    Guarantees:
        - ``lease_id`` references lease_leases.id.
        - ``remeasurement_amount`` is Decimal (Numeric(38,9)).
    """

    __tablename__ = "lease_modifications"

    __table_args__ = (
        Index("idx_lease_mod_lease", "lease_id"),
        Index("idx_lease_mod_date", "modification_date"),
    )

    lease_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("lease_leases.id"),
        nullable=False,
    )
    modification_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    new_monthly_payment: Mapped[Decimal | None] = mapped_column(nullable=True)
    new_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    remeasurement_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    actor_id: Mapped[UUID | None] = mapped_column(UUIDString(), nullable=True)

    # Parent relationship
    lease: Mapped["LeaseModel"] = relationship(
        "LeaseModel",
        back_populates="modifications",
    )

    def to_dto(self):
        from finance_modules.lease.models import LeaseModification

        return LeaseModification(
            id=self.id,
            lease_id=self.lease_id,
            modification_date=self.modification_date,
            description=self.description,
            new_monthly_payment=self.new_monthly_payment,
            new_end_date=self.new_end_date,
            remeasurement_amount=self.remeasurement_amount,
            actor_id=self.actor_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "LeaseModificationModel":
        return cls(
            id=dto.id,
            lease_id=dto.lease_id,
            modification_date=dto.modification_date,
            description=dto.description,
            new_monthly_payment=dto.new_monthly_payment,
            new_end_date=dto.new_end_date,
            remeasurement_amount=dto.remeasurement_amount,
            actor_id=dto.actor_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<LeaseModificationModel lease={self.lease_id} "
            f"date={self.modification_date}>"
        )
