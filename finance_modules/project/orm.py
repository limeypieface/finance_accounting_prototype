"""
SQLAlchemy ORM persistence models for the Project Accounting module.

Responsibility
--------------
Provide database-backed persistence for project accounting entities:
projects, project phases (WBS elements), and project cost entries.
Transient computation DTOs (``ProjectBudget``, ``EVMSnapshot``) are
derived from journal data and do not require ORM persistence.

Architecture position
---------------------
**Modules layer** -- ORM models consumed by ``ProjectService`` for
persistence.  Inherits from ``TrackedBase`` (kernel db layer).

Invariants enforced
-------------------
* All monetary fields use ``Decimal`` (Numeric(38,9)) -- NEVER float.
* Enum fields stored as String(50) for readability and portability.
* ``ProjectPhaseModel`` supports hierarchy via ``parent_id`` self-reference.
* ``ProjectCostEntryModel`` links costs to a project and optional phase.

Audit relevance
---------------
* ``ProjectModel`` lifecycle (active -> on_hold -> completed -> cancelled)
  is auditable via status field.
* ``ProjectCostEntryModel`` records individual cost charges for EVM
  calculation and DCAA compliance.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase

# ---------------------------------------------------------------------------
# ProjectModel
# ---------------------------------------------------------------------------


class ProjectModel(TrackedBase):
    """
    A project with cost tracking.

    Maps to the ``Project`` DTO in ``finance_modules.project.models``.

    Guarantees:
        - ``name`` is unique across all projects.
        - ``project_type`` classifies the billing model
          (fixed_price, cost_plus, time_and_materials).
        - ``status`` follows the lifecycle:
          active -> on_hold -> completed -> cancelled.
    """

    __tablename__ = "project_projects"

    __table_args__ = (
        UniqueConstraint("name", name="uq_project_name"),
        Index("idx_project_status", "status"),
        Index("idx_project_type", "project_type"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    project_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_budget: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Relationships
    phases: Mapped[list["ProjectPhaseModel"]] = relationship(
        "ProjectPhaseModel",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="ProjectPhaseModel.project_id",
    )

    cost_entries: Mapped[list["ProjectCostEntryModel"]] = relationship(
        "ProjectCostEntryModel",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.project.models import Project

        return Project(
            id=self.id,
            name=self.name,
            project_type=self.project_type,
            status=self.status,
            start_date=self.start_date,
            end_date=self.end_date,
            total_budget=self.total_budget,
            currency=self.currency,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ProjectModel":
        return cls(
            id=dto.id,
            name=dto.name,
            project_type=dto.project_type,
            status=dto.status,
            start_date=dto.start_date,
            end_date=dto.end_date,
            total_budget=dto.total_budget,
            currency=dto.currency,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ProjectModel {self.name} [{self.status}]>"


# ---------------------------------------------------------------------------
# ProjectPhaseModel (WBS Element)
# ---------------------------------------------------------------------------


class ProjectPhaseModel(TrackedBase):
    """
    A Work Breakdown Structure (WBS) element / project phase.

    Maps to the ``WBSElement`` DTO in ``finance_modules.project.models``.

    Guarantees:
        - Belongs to exactly one ``ProjectModel``.
        - (project_id, code) is unique.
        - Supports hierarchy via ``parent_id`` self-reference.
    """

    __tablename__ = "project_phases"

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_project_phase_code"),
        Index("idx_project_phase_project", "project_id"),
        Index("idx_project_phase_parent", "parent_id"),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("project_projects.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("project_phases.id"), nullable=True)
    budget_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    actual_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    level: Mapped[int] = mapped_column(default=1)

    # Relationships
    project: Mapped["ProjectModel"] = relationship(
        "ProjectModel",
        back_populates="phases",
        foreign_keys=[project_id],
    )

    parent: Mapped["ProjectPhaseModel | None"] = relationship(
        "ProjectPhaseModel",
        remote_side="ProjectPhaseModel.id",
        foreign_keys=[parent_id],
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.project.models import WBSElement

        return WBSElement(
            id=self.id,
            project_id=self.project_id,
            code=self.code,
            name=self.name,
            parent_id=self.parent_id,
            budget_amount=self.budget_amount,
            actual_cost=self.actual_cost,
            level=self.level,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ProjectPhaseModel":
        return cls(
            id=dto.id,
            project_id=dto.project_id,
            code=dto.code,
            name=dto.name,
            parent_id=dto.parent_id,
            budget_amount=dto.budget_amount,
            actual_cost=dto.actual_cost,
            level=dto.level,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ProjectPhaseModel {self.code}: {self.name}>"


# ---------------------------------------------------------------------------
# ProjectCostEntryModel
# ---------------------------------------------------------------------------


class ProjectCostEntryModel(TrackedBase):
    """
    A cost charge against a project, optionally scoped to a WBS element.

    Guarantees:
        - Belongs to exactly one ``ProjectModel``.
        - Optionally references a ``ProjectPhaseModel`` (WBS element).
        - Records the cost type, amount, period, and source reference.
    """

    __tablename__ = "project_cost_entries"

    __table_args__ = (
        Index("idx_project_cost_project", "project_id"),
        Index("idx_project_cost_phase", "phase_id"),
        Index("idx_project_cost_period", "period"),
        Index("idx_project_cost_type", "cost_type"),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("project_projects.id"), nullable=False)
    phase_id: Mapped[UUID | None] = mapped_column(ForeignKey("project_phases.id"), nullable=True)
    cost_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_event_id: Mapped[UUID | None]
    gl_account_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    project: Mapped["ProjectModel"] = relationship(
        "ProjectModel",
        back_populates="cost_entries",
    )

    phase: Mapped["ProjectPhaseModel | None"] = relationship(
        "ProjectPhaseModel",
        foreign_keys=[phase_id],
        lazy="selectin",
    )

    def to_dto(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "phase_id": self.phase_id,
            "cost_type": self.cost_type,
            "description": self.description,
            "amount": self.amount,
            "currency": self.currency,
            "period": self.period,
            "entry_date": self.entry_date,
            "source_event_id": self.source_event_id,
            "gl_account_code": self.gl_account_code,
        }

    @classmethod
    def from_dto(cls, dto: dict, created_by_id: UUID) -> "ProjectCostEntryModel":
        return cls(
            id=dto.get("id"),
            project_id=dto["project_id"],
            phase_id=dto.get("phase_id"),
            cost_type=dto["cost_type"],
            description=dto.get("description"),
            amount=dto["amount"],
            currency=dto.get("currency", "USD"),
            period=dto["period"],
            entry_date=dto["entry_date"],
            source_event_id=dto.get("source_event_id"),
            gl_account_code=dto.get("gl_account_code"),
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return f"<ProjectCostEntryModel project={self.project_id} {self.cost_type} {self.amount}>"
