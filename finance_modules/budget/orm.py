"""
SQLAlchemy ORM persistence models for the Budget module.

Responsibility
--------------
Provide database-backed persistence for budget domain entities: budgets
(versions), budget lines, budget transfers, and budget allocations.
``BudgetVariance`` and ``ForecastEntry`` are computed/transient DTOs and
do not require ORM persistence.

Architecture position
---------------------
**Modules layer** -- ORM models consumed by ``BudgetService`` for
persistence.  Inherits from ``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Enum fields stored as String(50) for readability and portability.
* ``BudgetLineModel`` uniqueness: one entry per version + account + period.
* ``BudgetTransferModel`` references source and target lines within the
  same budget version.

Audit relevance
---------------
* ``BudgetVersionModel`` tracks full version history for accountability.
* ``BudgetTransferModel`` provides an auditable record of inter-line
  budget movements.
* ``BudgetAllocationModel`` records top-down budget distribution.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# BudgetModel (budget header / version container)
# ---------------------------------------------------------------------------


class BudgetModel(TrackedBase):
    """
    A budget version (original, revised, forecast).

    Maps to the ``BudgetVersion`` DTO in ``finance_modules.budget.models``.

    Guarantees:
        - ``name`` + ``fiscal_year`` is unique per budget version.
        - ``status`` follows the lifecycle: draft -> approved -> locked -> archived.
    """

    __tablename__ = "budget_budgets"

    __table_args__ = (
        UniqueConstraint("name", "fiscal_year", name="uq_budget_name_year"),
        Index("idx_budget_fiscal_year", "fiscal_year"),
        Index("idx_budget_status", "status"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    fiscal_year: Mapped[int]
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationships
    lines: Mapped[list["BudgetLineModel"]] = relationship(
        "BudgetLineModel",
        back_populates="budget",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.budget.models import BudgetStatus, BudgetVersion

        return BudgetVersion(
            id=self.id,
            name=self.name,
            fiscal_year=self.fiscal_year,
            status=BudgetStatus(self.status),
            description=self.description,
            created_date=self.created_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BudgetModel":
        from finance_modules.budget.models import BudgetStatus

        return cls(
            id=dto.id,
            name=dto.name,
            fiscal_year=dto.fiscal_year,
            status=dto.status.value if isinstance(dto.status, BudgetStatus) else dto.status,
            description=dto.description,
            created_date=dto.created_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<BudgetModel {self.name} FY{self.fiscal_year} [{self.status}]>"


# ---------------------------------------------------------------------------
# BudgetLineModel
# ---------------------------------------------------------------------------


class BudgetLineModel(TrackedBase):
    """
    A single budget line item within a budget version.

    Maps to the ``BudgetEntry`` DTO in ``finance_modules.budget.models``.

    Guarantees:
        - One entry per (version_id, account_code, period).
        - ``amount`` uses Decimal (Numeric(38,9)).
    """

    __tablename__ = "budget_lines"

    __table_args__ = (
        UniqueConstraint(
            "version_id", "account_code", "period",
            name="uq_budget_line_version_account_period",
        ),
        Index("idx_budget_line_version", "version_id"),
        Index("idx_budget_line_account", "account_code"),
        Index("idx_budget_line_period", "period"),
    )

    version_id: Mapped[UUID] = mapped_column(ForeignKey("budget_budgets.id"), nullable=False)
    account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    dimensions_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Parent relationship
    budget: Mapped["BudgetModel"] = relationship(
        "BudgetModel",
        back_populates="lines",
    )

    def to_dto(self):
        import json

        from finance_modules.budget.models import BudgetEntry

        dimensions = None
        if self.dimensions_json:
            raw = json.loads(self.dimensions_json)
            dimensions = tuple(tuple(pair) for pair in raw)

        return BudgetEntry(
            id=self.id,
            version_id=self.version_id,
            account_code=self.account_code,
            period=self.period,
            amount=self.amount,
            currency=self.currency,
            dimensions=dimensions,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BudgetLineModel":
        import json

        dimensions_json = None
        if dto.dimensions:
            dimensions_json = json.dumps([list(pair) for pair in dto.dimensions])

        return cls(
            id=dto.id,
            version_id=dto.version_id,
            account_code=dto.account_code,
            period=dto.period,
            amount=dto.amount,
            currency=dto.currency,
            dimensions_json=dimensions_json,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<BudgetLineModel {self.account_code} {self.period} {self.amount}>"


# ---------------------------------------------------------------------------
# BudgetVersionModel (snapshot of a budget amendment)
# ---------------------------------------------------------------------------


class BudgetVersionModel(TrackedBase):
    """
    A versioned snapshot capturing a budget amendment or revision.

    Guarantees:
        - (budget_id, version_number) is unique.
        - Tracks the reason and date of the amendment.
    """

    __tablename__ = "budget_versions"

    __table_args__ = (
        UniqueConstraint("budget_id", "version_number", name="uq_budget_version_number"),
        Index("idx_budget_version_budget", "budget_id"),
    )

    budget_id: Mapped[UUID] = mapped_column(ForeignKey("budget_budgets.id"), nullable=False)
    version_number: Mapped[int]
    amendment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amendment_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    previous_total: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    new_total: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    amended_by: Mapped[UUID]

    def to_dto(self):
        return {
            "budget_id": self.budget_id,
            "version_number": self.version_number,
            "amendment_date": self.amendment_date,
            "amendment_reason": self.amendment_reason,
            "previous_total": self.previous_total,
            "new_total": self.new_total,
            "amended_by": self.amended_by,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "BudgetVersionModel":
        return cls(
            budget_id=dto["budget_id"],
            version_number=dto["version_number"],
            amendment_date=dto["amendment_date"],
            amendment_reason=dto["amendment_reason"],
            previous_total=dto.get("previous_total", Decimal("0")),
            new_total=dto.get("new_total", Decimal("0")),
            amended_by=dto["amended_by"],
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<BudgetVersionModel budget={self.budget_id} v{self.version_number}>"


# ---------------------------------------------------------------------------
# BudgetTransferModel
# ---------------------------------------------------------------------------


class BudgetTransferModel(TrackedBase):
    """
    An auditable transfer of budget between two lines within a version.

    Guarantees:
        - Records source and target account/period for traceability.
        - Transfer amount is always positive (direction is implicit from
          source -> target).
    """

    __tablename__ = "budget_transfers"

    __table_args__ = (
        Index("idx_budget_transfer_version", "version_id"),
        Index("idx_budget_transfer_date", "transfer_date"),
    )

    version_id: Mapped[UUID] = mapped_column(ForeignKey("budget_budgets.id"), nullable=False)
    from_account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    from_period: Mapped[str] = mapped_column(String(20), nullable=False)
    to_account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    to_period: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    transferred_by: Mapped[UUID]

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "version_id": self.version_id,
            "from_account_code": self.from_account_code,
            "from_period": self.from_period,
            "to_account_code": self.to_account_code,
            "to_period": self.to_period,
            "amount": self.amount,
            "currency": self.currency,
            "transfer_date": self.transfer_date,
            "reason": self.reason,
            "transferred_by": self.transferred_by,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "BudgetTransferModel":
        return cls(
            id=dto.get("id"),
            version_id=dto["version_id"],
            from_account_code=dto["from_account_code"],
            from_period=dto["from_period"],
            to_account_code=dto["to_account_code"],
            to_period=dto["to_period"],
            amount=dto["amount"],
            currency=dto.get("currency", "USD"),
            transfer_date=dto["transfer_date"],
            reason=dto.get("reason"),
            transferred_by=dto["transferred_by"],
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BudgetTransferModel {self.from_account_code}->{self.to_account_code} "
            f"{self.amount}>"
        )


# ---------------------------------------------------------------------------
# BudgetAllocationModel
# ---------------------------------------------------------------------------


class BudgetAllocationModel(TrackedBase):
    """
    A record of top-down budget distribution to a department or cost center.

    Guarantees:
        - Links a budget version to a target entity (department, cost center).
        - Records the allocated amount and the method used.
    """

    __tablename__ = "budget_allocations"

    __table_args__ = (
        Index("idx_budget_alloc_version", "version_id"),
        Index("idx_budget_alloc_target", "target_entity_id"),
    )

    version_id: Mapped[UUID] = mapped_column(ForeignKey("budget_budgets.id"), nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    account_code: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    allocation_method: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)
    allocated_by: Mapped[UUID]

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "version_id": self.version_id,
            "target_entity_type": self.target_entity_type,
            "target_entity_id": self.target_entity_id,
            "account_code": self.account_code,
            "period": self.period,
            "allocated_amount": self.allocated_amount,
            "currency": self.currency,
            "allocation_method": self.allocation_method,
            "allocation_date": self.allocation_date,
            "allocated_by": self.allocated_by,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "BudgetAllocationModel":
        return cls(
            id=dto.get("id"),
            version_id=dto["version_id"],
            target_entity_type=dto["target_entity_type"],
            target_entity_id=dto["target_entity_id"],
            account_code=dto["account_code"],
            period=dto["period"],
            allocated_amount=dto["allocated_amount"],
            currency=dto.get("currency", "USD"),
            allocation_method=dto.get("allocation_method", "manual"),
            allocation_date=dto["allocation_date"],
            allocated_by=dto["allocated_by"],
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BudgetAllocationModel {self.target_entity_id} "
            f"{self.account_code} {self.allocated_amount}>"
        )
