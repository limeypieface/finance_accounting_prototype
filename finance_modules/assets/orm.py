"""
Fixed Assets ORM Models (``finance_modules.assets.orm``).

Responsibility
--------------
SQLAlchemy persistence models for fixed asset management -- asset categories,
assets, depreciation schedules, disposals, transfers, revaluations, and
depreciation components.  Maps frozen domain dataclasses from ``models.py``
to database tables.

Architecture position
---------------------
**Modules layer** -- persistence.  Imports from ``finance_kernel.db.base``
and sibling ``models.py``.  MUST NOT be imported by ``finance_kernel``.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import String, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# AssetCategoryModel
# ---------------------------------------------------------------------------

class AssetCategoryModel(TrackedBase):
    """
    ORM model for ``AssetCategory`` -- a category for grouping assets with
    common depreciation attributes.

    Table: ``assets_categories``
    """

    __tablename__ = "assets_categories"

    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    useful_life_years: Mapped[int]
    depreciation_method: Mapped[str] = mapped_column(String(50))
    salvage_value_percent: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    gl_asset_account: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    gl_depreciation_account: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    gl_accumulated_depreciation_account: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )

    # Relationships (children)
    assets: Mapped[list["AssetModel"]] = relationship(
        back_populates="category", cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_assets_categories_code"),
        Index("idx_assets_categories_depreciation_method", "depreciation_method"),
    )

    def to_dto(self):
        from finance_modules.assets.models import AssetCategory, DepreciationMethod
        return AssetCategory(
            id=self.id,
            code=self.code,
            name=self.name,
            useful_life_years=self.useful_life_years,
            depreciation_method=DepreciationMethod(self.depreciation_method),
            salvage_value_percent=self.salvage_value_percent,
            gl_asset_account=self.gl_asset_account,
            gl_depreciation_account=self.gl_depreciation_account,
            gl_accumulated_depreciation_account=self.gl_accumulated_depreciation_account,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "AssetCategoryModel":
        return cls(
            id=dto.id,
            code=dto.code,
            name=dto.name,
            useful_life_years=dto.useful_life_years,
            depreciation_method=dto.depreciation_method.value,
            salvage_value_percent=dto.salvage_value_percent,
            gl_asset_account=dto.gl_asset_account,
            gl_depreciation_account=dto.gl_depreciation_account,
            gl_accumulated_depreciation_account=dto.gl_accumulated_depreciation_account,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<AssetCategoryModel(id={self.id!r}, code={self.code!r}, "
            f"name={self.name!r})>"
        )


# ---------------------------------------------------------------------------
# AssetModel
# ---------------------------------------------------------------------------

class AssetModel(TrackedBase):
    """
    ORM model for ``Asset`` -- a fixed asset.

    Table: ``assets_assets``
    """

    __tablename__ = "assets_assets"

    asset_number: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500))
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets_categories.id"),
    )
    acquisition_date: Mapped[date]
    in_service_date: Mapped[date | None]
    acquisition_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    salvage_value: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    useful_life_months: Mapped[int] = mapped_column(default=0)
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        default=Decimal("0"),
    )
    net_book_value: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    location_id: Mapped[UUID | None]
    department_id: Mapped[UUID | None]
    custodian_id: Mapped[UUID | None]
    serial_number: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    purchase_order_id: Mapped[UUID | None]
    vendor_id: Mapped[UUID | None]

    # Relationships (parent)
    category: Mapped["AssetCategoryModel"] = relationship(
        back_populates="assets",
    )

    # Relationships (children)
    depreciation_schedules: Mapped[list["DepreciationScheduleModel"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan",
    )
    disposals: Mapped[list["AssetDisposalModel"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan",
    )
    transfers: Mapped[list["AssetTransferModel"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan",
    )
    revaluations: Mapped[list["AssetRevaluationModel"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan",
    )
    components: Mapped[list["DepreciationComponentModel"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("asset_number", name="uq_assets_assets_asset_number"),
        Index("idx_assets_assets_category_id", "category_id"),
        Index("idx_assets_assets_status", "status"),
        Index("idx_assets_assets_acquisition_date", "acquisition_date"),
        Index("idx_assets_assets_vendor_id", "vendor_id"),
        Index("idx_assets_assets_location_id", "location_id"),
        Index("idx_assets_assets_department_id", "department_id"),
    )

    def to_dto(self):
        from finance_modules.assets.models import Asset, AssetStatus
        return Asset(
            id=self.id,
            asset_number=self.asset_number,
            description=self.description,
            category_id=self.category_id,
            acquisition_date=self.acquisition_date,
            in_service_date=self.in_service_date,
            acquisition_cost=self.acquisition_cost,
            salvage_value=self.salvage_value,
            useful_life_months=self.useful_life_months,
            accumulated_depreciation=self.accumulated_depreciation,
            net_book_value=self.net_book_value,
            status=AssetStatus(self.status),
            location_id=self.location_id,
            department_id=self.department_id,
            custodian_id=self.custodian_id,
            serial_number=self.serial_number,
            purchase_order_id=self.purchase_order_id,
            vendor_id=self.vendor_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "AssetModel":
        return cls(
            id=dto.id,
            asset_number=dto.asset_number,
            description=dto.description,
            category_id=dto.category_id,
            acquisition_date=dto.acquisition_date,
            in_service_date=dto.in_service_date,
            acquisition_cost=dto.acquisition_cost,
            salvage_value=dto.salvage_value,
            useful_life_months=dto.useful_life_months,
            accumulated_depreciation=dto.accumulated_depreciation,
            net_book_value=dto.net_book_value,
            status=dto.status.value,
            location_id=dto.location_id,
            department_id=dto.department_id,
            custodian_id=dto.custodian_id,
            serial_number=dto.serial_number,
            purchase_order_id=dto.purchase_order_id,
            vendor_id=dto.vendor_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<AssetModel(id={self.id!r}, asset_number={self.asset_number!r}, "
            f"status={self.status!r})>"
        )


# ---------------------------------------------------------------------------
# DepreciationScheduleModel
# ---------------------------------------------------------------------------

class DepreciationScheduleModel(TrackedBase):
    """
    ORM model for ``DepreciationSchedule`` -- monthly depreciation record
    for an asset.

    Table: ``assets_depreciation_schedules``
    """

    __tablename__ = "assets_depreciation_schedules"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets_assets.id"),
    )
    period_date: Mapped[date]
    depreciation_amount: Mapped[Decimal]
    accumulated_depreciation: Mapped[Decimal]
    net_book_value: Mapped[Decimal]
    is_posted: Mapped[bool] = mapped_column(default=False)

    # Relationships
    asset: Mapped["AssetModel"] = relationship(
        back_populates="depreciation_schedules",
    )

    __table_args__ = (
        Index("idx_assets_depreciation_schedules_asset_id", "asset_id"),
        Index("idx_assets_depreciation_schedules_period_date", "period_date"),
        Index("idx_assets_depreciation_schedules_is_posted", "is_posted"),
        UniqueConstraint(
            "asset_id", "period_date",
            name="uq_assets_depreciation_schedules_asset_period",
        ),
    )

    def to_dto(self):
        from finance_modules.assets.models import DepreciationSchedule
        return DepreciationSchedule(
            id=self.id,
            asset_id=self.asset_id,
            period_date=self.period_date,
            depreciation_amount=self.depreciation_amount,
            accumulated_depreciation=self.accumulated_depreciation,
            net_book_value=self.net_book_value,
            is_posted=self.is_posted,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "DepreciationScheduleModel":
        return cls(
            id=dto.id,
            asset_id=dto.asset_id,
            period_date=dto.period_date,
            depreciation_amount=dto.depreciation_amount,
            accumulated_depreciation=dto.accumulated_depreciation,
            net_book_value=dto.net_book_value,
            is_posted=dto.is_posted,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<DepreciationScheduleModel(id={self.id!r}, "
            f"asset_id={self.asset_id!r}, period={self.period_date!r})>"
        )


# ---------------------------------------------------------------------------
# AssetDisposalModel
# ---------------------------------------------------------------------------

class AssetDisposalModel(TrackedBase):
    """
    ORM model for ``AssetDisposal`` -- record of asset disposal.

    Table: ``assets_disposals``
    """

    __tablename__ = "assets_disposals"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets_assets.id"),
    )
    disposal_date: Mapped[date]
    disposal_type: Mapped[str] = mapped_column(String(50))
    proceeds: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    accumulated_depreciation_at_disposal: Mapped[Decimal] = mapped_column(
        default=Decimal("0"),
    )
    net_book_value_at_disposal: Mapped[Decimal] = mapped_column(
        default=Decimal("0"),
    )
    gain_loss: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationships
    asset: Mapped["AssetModel"] = relationship(
        back_populates="disposals",
    )

    __table_args__ = (
        Index("idx_assets_disposals_asset_id", "asset_id"),
        Index("idx_assets_disposals_disposal_date", "disposal_date"),
        Index("idx_assets_disposals_disposal_type", "disposal_type"),
    )

    def to_dto(self):
        from finance_modules.assets.models import AssetDisposal, DisposalType
        return AssetDisposal(
            id=self.id,
            asset_id=self.asset_id,
            disposal_date=self.disposal_date,
            disposal_type=DisposalType(self.disposal_type),
            proceeds=self.proceeds,
            accumulated_depreciation_at_disposal=self.accumulated_depreciation_at_disposal,
            net_book_value_at_disposal=self.net_book_value_at_disposal,
            gain_loss=self.gain_loss,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "AssetDisposalModel":
        return cls(
            id=dto.id,
            asset_id=dto.asset_id,
            disposal_date=dto.disposal_date,
            disposal_type=dto.disposal_type.value,
            proceeds=dto.proceeds,
            accumulated_depreciation_at_disposal=dto.accumulated_depreciation_at_disposal,
            net_book_value_at_disposal=dto.net_book_value_at_disposal,
            gain_loss=dto.gain_loss,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<AssetDisposalModel(id={self.id!r}, asset_id={self.asset_id!r}, "
            f"type={self.disposal_type!r})>"
        )


# ---------------------------------------------------------------------------
# AssetTransferModel
# ---------------------------------------------------------------------------

class AssetTransferModel(TrackedBase):
    """
    ORM model for ``AssetTransfer`` -- record of asset transfer between
    cost centers.

    Table: ``assets_transfers``
    """

    __tablename__ = "assets_transfers"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets_assets.id"),
    )
    transfer_date: Mapped[date]
    from_cost_center: Mapped[str] = mapped_column(String(100))
    to_cost_center: Mapped[str] = mapped_column(String(100))
    transferred_by: Mapped[UUID | None]

    # Relationships
    asset: Mapped["AssetModel"] = relationship(
        back_populates="transfers",
    )

    __table_args__ = (
        Index("idx_assets_transfers_asset_id", "asset_id"),
        Index("idx_assets_transfers_transfer_date", "transfer_date"),
        Index("idx_assets_transfers_from_cost_center", "from_cost_center"),
        Index("idx_assets_transfers_to_cost_center", "to_cost_center"),
    )

    def to_dto(self):
        from finance_modules.assets.models import AssetTransfer
        return AssetTransfer(
            id=self.id,
            asset_id=self.asset_id,
            transfer_date=self.transfer_date,
            from_cost_center=self.from_cost_center,
            to_cost_center=self.to_cost_center,
            transferred_by=self.transferred_by,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "AssetTransferModel":
        return cls(
            id=dto.id,
            asset_id=dto.asset_id,
            transfer_date=dto.transfer_date,
            from_cost_center=dto.from_cost_center,
            to_cost_center=dto.to_cost_center,
            transferred_by=dto.transferred_by,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<AssetTransferModel(id={self.id!r}, asset_id={self.asset_id!r}, "
            f"from={self.from_cost_center!r}, to={self.to_cost_center!r})>"
        )


# ---------------------------------------------------------------------------
# AssetRevaluationModel
# ---------------------------------------------------------------------------

class AssetRevaluationModel(TrackedBase):
    """
    ORM model for ``AssetRevaluation`` -- record of asset revaluation to
    fair value.

    Table: ``assets_revaluations``
    """

    __tablename__ = "assets_revaluations"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets_assets.id"),
    )
    revaluation_date: Mapped[date]
    old_carrying_value: Mapped[Decimal]
    new_fair_value: Mapped[Decimal]
    revaluation_surplus: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationships
    asset: Mapped["AssetModel"] = relationship(
        back_populates="revaluations",
    )

    __table_args__ = (
        Index("idx_assets_revaluations_asset_id", "asset_id"),
        Index("idx_assets_revaluations_revaluation_date", "revaluation_date"),
    )

    def to_dto(self):
        from finance_modules.assets.models import AssetRevaluation
        return AssetRevaluation(
            id=self.id,
            asset_id=self.asset_id,
            revaluation_date=self.revaluation_date,
            old_carrying_value=self.old_carrying_value,
            new_fair_value=self.new_fair_value,
            revaluation_surplus=self.revaluation_surplus,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "AssetRevaluationModel":
        return cls(
            id=dto.id,
            asset_id=dto.asset_id,
            revaluation_date=dto.revaluation_date,
            old_carrying_value=dto.old_carrying_value,
            new_fair_value=dto.new_fair_value,
            revaluation_surplus=dto.revaluation_surplus,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<AssetRevaluationModel(id={self.id!r}, "
            f"asset_id={self.asset_id!r}, "
            f"date={self.revaluation_date!r})>"
        )


# ---------------------------------------------------------------------------
# DepreciationComponentModel
# ---------------------------------------------------------------------------

class DepreciationComponentModel(TrackedBase):
    """
    ORM model for ``DepreciationComponent`` -- a component of an asset for
    component-level depreciation (IAS 16).

    Table: ``assets_depreciation_components``
    """

    __tablename__ = "assets_depreciation_components"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets_assets.id"),
    )
    component_name: Mapped[str] = mapped_column(String(200))
    cost: Mapped[Decimal]
    useful_life_months: Mapped[int]
    depreciation_method: Mapped[str] = mapped_column(
        String(50), default="straight_line",
    )
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        default=Decimal("0"),
    )

    # Relationships
    asset: Mapped["AssetModel"] = relationship(
        back_populates="components",
    )

    __table_args__ = (
        Index("idx_assets_depreciation_components_asset_id", "asset_id"),
        UniqueConstraint(
            "asset_id", "component_name",
            name="uq_assets_depreciation_components_asset_component",
        ),
    )

    def to_dto(self):
        from finance_modules.assets.models import DepreciationComponent
        return DepreciationComponent(
            id=self.id,
            asset_id=self.asset_id,
            component_name=self.component_name,
            cost=self.cost,
            useful_life_months=self.useful_life_months,
            depreciation_method=self.depreciation_method,
            accumulated_depreciation=self.accumulated_depreciation,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "DepreciationComponentModel":
        return cls(
            id=dto.id,
            asset_id=dto.asset_id,
            component_name=dto.component_name,
            cost=dto.cost,
            useful_life_months=dto.useful_life_months,
            depreciation_method=dto.depreciation_method,
            accumulated_depreciation=dto.accumulated_depreciation,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<DepreciationComponentModel(id={self.id!r}, "
            f"asset_id={self.asset_id!r}, "
            f"name={self.component_name!r})>"
        )
