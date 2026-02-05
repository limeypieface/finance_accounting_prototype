"""
DCAA Rate Control ORM Models (``finance_modules.contracts.rate_orm``).

Responsibility
--------------
SQLAlchemy ORM models that persist the DCAA rate control DTOs defined in
``finance_modules.contracts.rate_types``.  Each ORM class mirrors a DTO
and provides ``to_dto()`` / ``from_dto()`` round-trip conversion.

Architecture position
---------------------
**Modules layer** -- persistence companions to the pure DTO models.
Inherits from ``TrackedBase`` (kernel DB base).

Invariants enforced
-------------------
* D8 -- rate ceiling: stores approved rates and contract ceilings for
  verification at charge time.
* All rate/monetary fields use Decimal (Numeric(38,9)) -- NEVER float.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# LaborRateScheduleModel
# ---------------------------------------------------------------------------


class LaborRateScheduleModel(TrackedBase):
    """ORM model for ``LaborRateSchedule``.

    Approved labor rates per employee classification and labor category.
    """

    __tablename__ = "contract_labor_rate_schedules"

    __table_args__ = (
        UniqueConstraint(
            "employee_classification", "labor_category", "effective_from",
            name="uq_labor_rate_class_cat_date",
        ),
        Index("idx_labor_rate_class", "employee_classification"),
        Index("idx_labor_rate_category", "labor_category"),
        Index("idx_labor_rate_effective", "effective_from", "effective_to"),
    )

    employee_classification: Mapped[str] = mapped_column(
        String(200), nullable=False,
    )
    labor_category: Mapped[str] = mapped_column(String(100), nullable=False)
    base_rate: Mapped[Decimal] = mapped_column(nullable=False)
    loaded_rate: Mapped[Decimal] = mapped_column(nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    rate_source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="provisional",
    )

    def to_dto(self):
        from finance_modules.contracts.rate_types import (
            LaborRateSchedule,
            RateSource,
        )

        return LaborRateSchedule(
            schedule_id=self.id,
            employee_classification=self.employee_classification,
            labor_category=self.labor_category,
            base_rate=self.base_rate,
            loaded_rate=self.loaded_rate,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            rate_source=RateSource(self.rate_source),
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "LaborRateScheduleModel":
        return cls(
            id=dto.schedule_id,
            employee_classification=dto.employee_classification,
            labor_category=dto.labor_category,
            base_rate=dto.base_rate,
            loaded_rate=dto.loaded_rate,
            effective_from=dto.effective_from,
            effective_to=dto.effective_to,
            rate_source=dto.rate_source.value if hasattr(dto.rate_source, "value") else dto.rate_source,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# ContractRateCeilingModel
# ---------------------------------------------------------------------------


class ContractRateCeilingModel(TrackedBase):
    """ORM model for ``ContractRateCeiling``.

    Maximum billing rate per contract per labor category.
    """

    __tablename__ = "contract_rate_ceilings"

    __table_args__ = (
        UniqueConstraint(
            "contract_id", "labor_category", "effective_from",
            name="uq_rate_ceiling_contract_cat_date",
        ),
        Index("idx_rate_ceiling_contract", "contract_id"),
        Index("idx_rate_ceiling_category", "labor_category"),
    )

    contract_id: Mapped[UUID] = mapped_column(nullable=False)
    labor_category: Mapped[str] = mapped_column(String(100), nullable=False)
    max_hourly_rate: Mapped[Decimal] = mapped_column(nullable=False)
    max_loaded_rate: Mapped[Decimal | None] = mapped_column(nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    ceiling_source: Mapped[str] = mapped_column(
        String(200), nullable=False, default="",
    )

    def to_dto(self):
        from finance_modules.contracts.rate_types import ContractRateCeiling

        return ContractRateCeiling(
            contract_id=self.contract_id,
            labor_category=self.labor_category,
            max_hourly_rate=self.max_hourly_rate,
            max_loaded_rate=self.max_loaded_rate,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            ceiling_source=self.ceiling_source,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ContractRateCeilingModel":
        return cls(
            contract_id=dto.contract_id,
            labor_category=dto.labor_category,
            max_hourly_rate=dto.max_hourly_rate,
            max_loaded_rate=dto.max_loaded_rate,
            effective_from=dto.effective_from,
            effective_to=dto.effective_to,
            ceiling_source=dto.ceiling_source,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# IndirectRateModel
# ---------------------------------------------------------------------------


class IndirectRateModel(TrackedBase):
    """ORM model for ``IndirectRateRecord``.

    Tracks provisional and final indirect cost rates per fiscal year.
    """

    __tablename__ = "contract_indirect_rates"

    __table_args__ = (
        UniqueConstraint(
            "rate_type", "fiscal_year", "rate_status",
            name="uq_indirect_rate_type_year_status",
        ),
        Index("idx_indirect_rate_year", "fiscal_year"),
        Index("idx_indirect_rate_type", "rate_type"),
    )

    rate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    rate_value: Mapped[Decimal] = mapped_column(nullable=False)
    base_description: Mapped[str] = mapped_column(String(200), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    rate_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="provisional",
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(nullable=True)
    approval_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    def to_dto(self):
        from finance_modules.contracts.rate_types import (
            IndirectRateRecord,
            IndirectRateType,
            RateSource,
        )

        return IndirectRateRecord(
            rate_id=self.id,
            rate_type=IndirectRateType(self.rate_type),
            rate_value=self.rate_value,
            base_description=self.base_description,
            fiscal_year=self.fiscal_year,
            rate_status=RateSource(self.rate_status),
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            approved_by=self.approved_by,
            approval_date=self.approval_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "IndirectRateModel":
        return cls(
            id=dto.rate_id,
            rate_type=dto.rate_type.value if hasattr(dto.rate_type, "value") else dto.rate_type,
            rate_value=dto.rate_value,
            base_description=dto.base_description,
            fiscal_year=dto.fiscal_year,
            rate_status=dto.rate_status.value if hasattr(dto.rate_status, "value") else dto.rate_status,
            effective_from=dto.effective_from,
            effective_to=dto.effective_to,
            approved_by=dto.approved_by,
            approval_date=dto.approval_date,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# RateReconciliationModel
# ---------------------------------------------------------------------------


class RateReconciliationModel(TrackedBase):
    """ORM model for ``RateReconciliationRecord``.

    Year-end provisional-to-final rate adjustment record.
    Append-only once created.
    """

    __tablename__ = "contract_rate_reconciliations"

    __table_args__ = (
        Index("idx_rate_recon_year", "fiscal_year"),
        Index("idx_rate_recon_type", "rate_type"),
    )

    fiscal_year: Mapped[int] = mapped_column(nullable=False)
    rate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provisional_rate: Mapped[Decimal] = mapped_column(nullable=False)
    final_rate: Mapped[Decimal] = mapped_column(nullable=False)
    rate_difference: Mapped[Decimal] = mapped_column(nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(nullable=False)
    adjustment_amount: Mapped[Decimal] = mapped_column(nullable=False)
    direction: Mapped[str] = mapped_column(String(50), nullable=False)

    def to_dto(self):
        from finance_modules.contracts.rate_types import (
            IndirectRateType,
            RateReconciliationRecord,
            ReconciliationDirection,
        )

        return RateReconciliationRecord(
            reconciliation_id=self.id,
            fiscal_year=self.fiscal_year,
            rate_type=IndirectRateType(self.rate_type),
            provisional_rate=self.provisional_rate,
            final_rate=self.final_rate,
            base_amount=self.base_amount,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "RateReconciliationModel":
        return cls(
            id=dto.reconciliation_id,
            fiscal_year=dto.fiscal_year,
            rate_type=dto.rate_type.value if hasattr(dto.rate_type, "value") else dto.rate_type,
            provisional_rate=dto.provisional_rate,
            final_rate=dto.final_rate,
            rate_difference=dto.rate_difference,
            base_amount=dto.base_amount,
            adjustment_amount=dto.adjustment_amount,
            direction=dto.direction.value if hasattr(dto.direction, "value") else dto.direction,
            created_by_id=created_by_id,
        )
