"""
DCAA Expense ORM Models (``finance_modules.expense.dcaa_orm``).

Responsibility
--------------
SQLAlchemy ORM models that persist the DCAA expense compliance DTOs
defined in ``finance_modules.expense.dcaa_types``.  Each ORM class mirrors
a DTO and provides ``to_dto()`` / ``from_dto()`` round-trip conversion.

Architecture position
---------------------
**Modules layer** -- persistence companions to the pure DTO models.
Inherits from ``TrackedBase`` (kernel DB base).

Invariants enforced
-------------------
* D6 -- pre-travel authorization: TravelAuthorizationModel persists
  authorization records with approval status.
* All monetary fields use Decimal (Numeric(38,9)) -- NEVER float.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# TravelAuthorizationModel
# ---------------------------------------------------------------------------


class TravelAuthorizationModel(TrackedBase):
    """ORM model for ``TravelAuthorization``.

    Pre-travel authorization record (D6 / FAR 31.205-46).  Must be
    approved before travel expenses can be submitted.
    """

    __tablename__ = "expense_travel_authorizations"

    __table_args__ = (
        Index("idx_travel_auth_employee", "employee_id"),
        Index("idx_travel_auth_status", "status"),
        Index("idx_travel_auth_dates", "travel_start", "travel_end"),
    )

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("parties.id"), nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    destination: Mapped[str] = mapped_column(String(200), nullable=False)
    travel_start: Mapped[date] = mapped_column(Date, nullable=False)
    travel_end: Mapped[date] = mapped_column(Date, nullable=False)
    total_estimated: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    contract_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="draft",
    )

    # Relationships
    lines: Mapped[list["TravelAuthLineModel"]] = relationship(
        "TravelAuthLineModel",
        back_populates="authorization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.expense.dcaa_types import (
            TravelAuthStatus,
            TravelAuthorization,
        )

        return TravelAuthorization(
            authorization_id=self.id,
            employee_id=self.employee_id,
            purpose=self.purpose,
            destination=self.destination,
            travel_start=self.travel_start,
            travel_end=self.travel_end,
            estimated_costs=tuple(line.to_dto() for line in self.lines),
            total_estimated=self.total_estimated,
            currency=self.currency,
            contract_id=self.contract_id,
            status=TravelAuthStatus(self.status),
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TravelAuthorizationModel":
        return cls(
            id=dto.authorization_id,
            employee_id=dto.employee_id,
            purpose=dto.purpose,
            destination=dto.destination,
            travel_start=dto.travel_start,
            travel_end=dto.travel_end,
            total_estimated=dto.total_estimated,
            currency=dto.currency,
            contract_id=dto.contract_id,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# TravelAuthLineModel
# ---------------------------------------------------------------------------


class TravelAuthLineModel(TrackedBase):
    """ORM model for ``TravelCostEstimate``.

    A line item in a travel authorization request.
    """

    __tablename__ = "expense_travel_auth_lines"

    __table_args__ = (
        Index("idx_travel_auth_line_auth", "authorization_id"),
    )

    authorization_id: Mapped[UUID] = mapped_column(
        ForeignKey("expense_travel_authorizations.id"), nullable=False,
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    estimated_amount: Mapped[Decimal] = mapped_column(nullable=False)
    gsa_rate: Mapped[Decimal | None] = mapped_column(nullable=True)
    nights: Mapped[int] = mapped_column(default=0)
    days: Mapped[int] = mapped_column(default=0)

    # Relationship
    authorization: Mapped["TravelAuthorizationModel"] = relationship(
        "TravelAuthorizationModel", back_populates="lines",
    )

    def to_dto(self):
        from finance_modules.expense.dcaa_types import (
            TravelCostEstimate,
            TravelExpenseCategory,
        )

        return TravelCostEstimate(
            category=TravelExpenseCategory(self.category),
            estimated_amount=self.estimated_amount,
            gsa_rate=self.gsa_rate,
            nights=self.nights,
            days=self.days,
        )

    @classmethod
    def from_dto(
        cls, dto, authorization_id: UUID, created_by_id: UUID,
    ) -> "TravelAuthLineModel":
        return cls(
            authorization_id=authorization_id,
            category=dto.category.value if hasattr(dto.category, "value") else dto.category,
            estimated_amount=dto.estimated_amount,
            gsa_rate=dto.gsa_rate,
            nights=dto.nights,
            days=dto.days,
            created_by_id=created_by_id,
        )
