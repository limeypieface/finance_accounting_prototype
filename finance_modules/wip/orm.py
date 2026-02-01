"""
Module: finance_modules.wip.orm
Responsibility: SQLAlchemy ORM persistence models for the Work-in-Process (WIP)
    module.  Maps frozen dataclass DTOs from wip.models to relational tables for
    work orders, BOM lines, operations, labor entries, overhead applications,
    byproduct records, production cost summaries, and unit cost breakdowns.

Architecture position: Modules > WIP > ORM.  Inherits from TrackedBase
    (finance_kernel.db.base).  References Sindri-owned entities (Item, WorkCenter)
    via UUID columns with NO foreign key constraints.

Invariants enforced:
    - All monetary fields use Decimal (Numeric(38,9)) -- NEVER float (R16/R17).
    - Enum fields stored as String(50) for portability and readability.
    - TrackedBase provides: id (UUID PK), created_at, updated_at,
      created_by_id (NOT NULL), updated_by_id (nullable).
    - Parent-child relationships within module use explicit ForeignKey.

Failure modes:
    - IntegrityError on duplicate work order number (uq_wip_order_number).
    - IntegrityError on FK violation for child records referencing work orders.

Audit relevance:
    - These ORM records are operational artifacts for manufacturing cost
      accumulation.  The authoritative financial truth remains
      JournalEntry/JournalLine.  These records support traceability from
      journal postings back to specific work orders, operations, and labor charges.
"""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString

if TYPE_CHECKING:
    pass


# =============================================================================
# WorkOrderModel
# =============================================================================

class WorkOrderModel(TrackedBase):
    """
    ORM model for production work orders.

    Maps to: finance_modules.wip.models.WorkOrder (frozen dataclass).

    Guarantees:
        - order_number is globally unique (uq_wip_order_number constraint).
        - quantity_ordered > 0 (caller/DTO-enforced, not ORM-enforced).
        - Parent-child hierarchy via parent_work_order_id self-referential FK.
        - item_id references a Sindri-owned finished goods Item (no FK).
        - sales_order_id references an external sales order (no FK).
    """

    __tablename__ = "wip_work_orders"

    __table_args__ = (
        UniqueConstraint("order_number", name="uq_wip_order_number"),
        Index("idx_wip_wo_item", "item_id"),
        Index("idx_wip_wo_status", "status"),
        Index("idx_wip_wo_parent", "parent_work_order_id"),
        Index("idx_wip_wo_sales_order", "sales_order_id"),
        Index("idx_wip_wo_planned_start", "planned_start_date"),
    )

    # Work order identification
    order_number: Mapped[str] = mapped_column(String(100))

    # Sindri entity reference (finished goods item, no FK)
    item_id: Mapped[UUID] = mapped_column()

    # Quantities
    quantity_ordered: Mapped[Decimal] = mapped_column()
    quantity_completed: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    quantity_scrapped: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Dates
    planned_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Status (WorkOrderStatus enum stored as string)
    status: Mapped[str] = mapped_column(String(50), default="planned")

    # Parent work order for subassemblies (self-referential FK)
    parent_work_order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("wip_work_orders.id"), nullable=True,
    )

    # External reference (no FK)
    sales_order_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Relationships
    parent_work_order: Mapped["WorkOrderModel | None"] = relationship(
        "WorkOrderModel",
        remote_side="WorkOrderModel.id",
        foreign_keys=[parent_work_order_id],
    )

    lines: Mapped[list["WorkOrderLineModel"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    operations: Mapped[list["OperationModel"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    labor_entries: Mapped[list["LaborEntryModel"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    overhead_applications: Mapped[list["OverheadApplicationModel"]] = relationship(
        back_populates="work_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        """Convert ORM model to frozen WorkOrder DTO."""
        from finance_modules.wip.models import WorkOrder, WorkOrderStatus
        return WorkOrder(
            id=self.id,
            order_number=self.order_number,
            item_id=self.item_id,
            quantity_ordered=self.quantity_ordered,
            quantity_completed=self.quantity_completed,
            quantity_scrapped=self.quantity_scrapped,
            planned_start_date=self.planned_start_date,
            planned_end_date=self.planned_end_date,
            actual_start_date=self.actual_start_date,
            actual_end_date=self.actual_end_date,
            status=WorkOrderStatus(self.status),
            parent_work_order_id=self.parent_work_order_id,
            sales_order_id=self.sales_order_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "WorkOrderModel":
        """Create ORM model from frozen WorkOrder DTO."""
        return cls(
            id=dto.id,
            order_number=dto.order_number,
            item_id=dto.item_id,
            quantity_ordered=dto.quantity_ordered,
            quantity_completed=dto.quantity_completed,
            quantity_scrapped=dto.quantity_scrapped,
            planned_start_date=dto.planned_start_date,
            planned_end_date=dto.planned_end_date,
            actual_start_date=dto.actual_start_date,
            actual_end_date=dto.actual_end_date,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            parent_work_order_id=dto.parent_work_order_id,
            sales_order_id=dto.sales_order_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<WorkOrderModel {self.order_number} item={self.item_id} "
            f"status={self.status}>"
        )


# =============================================================================
# WorkOrderLineModel
# =============================================================================

class WorkOrderLineModel(TrackedBase):
    """
    ORM model for work order material components (BOM explosion lines).

    Maps to: finance_modules.wip.models.WorkOrderLine (frozen dataclass).

    Guarantees:
        - work_order_id references parent WorkOrderModel via FK.
        - item_id references a Sindri-owned component Item (no FK).
        - operation_seq links the material to the consuming operation.
    """

    __tablename__ = "wip_work_order_lines"

    __table_args__ = (
        Index("idx_wip_wol_wo", "work_order_id"),
        Index("idx_wip_wol_item", "item_id"),
    )

    # Parent work order (FK within module)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )

    # Sindri entity reference (component item, no FK)
    item_id: Mapped[UUID] = mapped_column()

    # Material requirements
    quantity_required: Mapped[Decimal] = mapped_column()
    quantity_issued: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    unit_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Operation linkage
    operation_seq: Mapped[int] = mapped_column(default=10)

    # Relationship back to parent
    work_order: Mapped["WorkOrderModel"] = relationship(
        back_populates="lines",
    )

    def to_dto(self):
        """Convert ORM model to frozen WorkOrderLine DTO."""
        from finance_modules.wip.models import WorkOrderLine
        return WorkOrderLine(
            id=self.id,
            work_order_id=self.work_order_id,
            item_id=self.item_id,
            quantity_required=self.quantity_required,
            quantity_issued=self.quantity_issued,
            unit_cost=self.unit_cost,
            operation_seq=self.operation_seq,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "WorkOrderLineModel":
        """Create ORM model from frozen WorkOrderLine DTO."""
        return cls(
            id=dto.id,
            work_order_id=dto.work_order_id,
            item_id=dto.item_id,
            quantity_required=dto.quantity_required,
            quantity_issued=dto.quantity_issued,
            unit_cost=dto.unit_cost,
            operation_seq=dto.operation_seq,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<WorkOrderLineModel {self.id} wo={self.work_order_id} "
            f"item={self.item_id} req={self.quantity_required}>"
        )


# =============================================================================
# OperationModel
# =============================================================================

class OperationModel(TrackedBase):
    """
    ORM model for manufacturing operations within a work order.

    Maps to: finance_modules.wip.models.Operation (frozen dataclass).

    Guarantees:
        - work_order_id references parent WorkOrderModel via FK.
        - work_center_id references a Sindri-owned WorkCenter (no FK).
        - sequence provides deterministic ordering of operations within a work order.
        - Time and rate fields use Decimal (Numeric(38,9)).
    """

    __tablename__ = "wip_operations"

    __table_args__ = (
        Index("idx_wip_op_wo", "work_order_id"),
        Index("idx_wip_op_wc", "work_center_id"),
        Index("idx_wip_op_seq", "work_order_id", "sequence"),
        Index("idx_wip_op_status", "status"),
    )

    # Parent work order (FK within module)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )

    # Operation sequencing
    sequence: Mapped[int] = mapped_column()

    # Sindri entity reference (work center, no FK)
    work_center_id: Mapped[UUID] = mapped_column()

    # Description
    description: Mapped[str] = mapped_column(String(255))

    # Time estimates
    setup_time_hours: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    run_time_hours: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Rates
    labor_rate: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    overhead_rate: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Status (OperationStatus enum stored as string)
    status: Mapped[str] = mapped_column(String(50), default="not_started")

    # Completion tracking
    quantity_completed: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationship back to parent
    work_order: Mapped["WorkOrderModel"] = relationship(
        back_populates="operations",
    )

    def to_dto(self):
        """Convert ORM model to frozen Operation DTO."""
        from finance_modules.wip.models import Operation, OperationStatus
        return Operation(
            id=self.id,
            work_order_id=self.work_order_id,
            sequence=self.sequence,
            work_center_id=self.work_center_id,
            description=self.description,
            setup_time_hours=self.setup_time_hours,
            run_time_hours=self.run_time_hours,
            labor_rate=self.labor_rate,
            overhead_rate=self.overhead_rate,
            status=OperationStatus(self.status),
            quantity_completed=self.quantity_completed,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "OperationModel":
        """Create ORM model from frozen Operation DTO."""
        return cls(
            id=dto.id,
            work_order_id=dto.work_order_id,
            sequence=dto.sequence,
            work_center_id=dto.work_center_id,
            description=dto.description,
            setup_time_hours=dto.setup_time_hours,
            run_time_hours=dto.run_time_hours,
            labor_rate=dto.labor_rate,
            overhead_rate=dto.overhead_rate,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            quantity_completed=dto.quantity_completed,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<OperationModel {self.id} wo={self.work_order_id} "
            f"seq={self.sequence} status={self.status}>"
        )


# =============================================================================
# LaborEntryModel
# =============================================================================

class LaborEntryModel(TrackedBase):
    """
    ORM model for labor time charged to a work order operation.

    Maps to: finance_modules.wip.models.LaborEntry (frozen dataclass).

    Guarantees:
        - work_order_id references parent WorkOrderModel via FK.
        - operation_id references OperationModel via FK.
        - employee_id references a Sindri-owned or kernel Party entity (no FK).
        - labor_cost = hours * labor_rate (caller-enforced, not ORM-enforced).
    """

    __tablename__ = "wip_labor_entries"

    __table_args__ = (
        Index("idx_wip_labor_wo", "work_order_id"),
        Index("idx_wip_labor_op", "operation_id"),
        Index("idx_wip_labor_emp", "employee_id"),
        Index("idx_wip_labor_date", "work_date"),
        Index("idx_wip_labor_type", "entry_type"),
    )

    # Parent references (FK within module)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_operations.id"),
    )

    # Employee reference (no FK -- may be kernel Party or external)
    employee_id: Mapped[UUID] = mapped_column()

    # Labor details
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column()
    labor_rate: Mapped[Decimal] = mapped_column()
    labor_cost: Mapped[Decimal] = mapped_column()

    # Entry type
    entry_type: Mapped[str] = mapped_column(
        String(50), default="run",
    )  # "setup", "run", "rework"

    # Relationships back to parents
    work_order: Mapped["WorkOrderModel"] = relationship(
        back_populates="labor_entries",
    )

    operation: Mapped["OperationModel"] = relationship(
        foreign_keys=[operation_id],
    )

    def to_dto(self):
        """Convert ORM model to frozen LaborEntry DTO."""
        from finance_modules.wip.models import LaborEntry
        return LaborEntry(
            id=self.id,
            work_order_id=self.work_order_id,
            operation_id=self.operation_id,
            employee_id=self.employee_id,
            work_date=self.work_date,
            hours=self.hours,
            labor_rate=self.labor_rate,
            labor_cost=self.labor_cost,
            entry_type=self.entry_type,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "LaborEntryModel":
        """Create ORM model from frozen LaborEntry DTO."""
        return cls(
            id=dto.id,
            work_order_id=dto.work_order_id,
            operation_id=dto.operation_id,
            employee_id=dto.employee_id,
            work_date=dto.work_date,
            hours=dto.hours,
            labor_rate=dto.labor_rate,
            labor_cost=dto.labor_cost,
            entry_type=dto.entry_type,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<LaborEntryModel {self.id} wo={self.work_order_id} "
            f"emp={self.employee_id} hours={self.hours} type={self.entry_type}>"
        )


# =============================================================================
# OverheadApplicationModel
# =============================================================================

class OverheadApplicationModel(TrackedBase):
    """
    ORM model for overhead applied to a work order.

    Maps to: finance_modules.wip.models.OverheadApplication (frozen dataclass).

    Guarantees:
        - work_order_id references parent WorkOrderModel via FK.
        - amount = rate * quantity (caller-enforced, not ORM-enforced).
        - overhead_type classifies as fixed, variable, or setup.
        - basis identifies the allocation driver (labor_hours, machine_hours, units).
    """

    __tablename__ = "wip_overhead_applications"

    __table_args__ = (
        Index("idx_wip_oh_wo", "work_order_id"),
        Index("idx_wip_oh_date", "application_date"),
        Index("idx_wip_oh_type", "overhead_type"),
    )

    # Parent work order (FK within module)
    work_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )

    # Application details
    application_date: Mapped[date] = mapped_column(Date)
    overhead_type: Mapped[str] = mapped_column(String(50))  # fixed, variable, setup
    basis: Mapped[str] = mapped_column(String(50))  # labor_hours, machine_hours, units

    # Rate and amount
    rate: Mapped[Decimal] = mapped_column()
    quantity: Mapped[Decimal] = mapped_column()
    amount: Mapped[Decimal] = mapped_column()

    # Relationship back to parent
    work_order: Mapped["WorkOrderModel"] = relationship(
        back_populates="overhead_applications",
    )

    def to_dto(self):
        """Convert ORM model to frozen OverheadApplication DTO."""
        from finance_modules.wip.models import OverheadApplication
        return OverheadApplication(
            id=self.id,
            work_order_id=self.work_order_id,
            application_date=self.application_date,
            overhead_type=self.overhead_type,
            basis=self.basis,
            rate=self.rate,
            quantity=self.quantity,
            amount=self.amount,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "OverheadApplicationModel":
        """Create ORM model from frozen OverheadApplication DTO."""
        return cls(
            id=dto.id,
            work_order_id=dto.work_order_id,
            application_date=dto.application_date,
            overhead_type=dto.overhead_type,
            basis=dto.basis,
            rate=dto.rate,
            quantity=dto.quantity,
            amount=dto.amount,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<OverheadApplicationModel {self.id} wo={self.work_order_id} "
            f"type={self.overhead_type} amount={self.amount}>"
        )


# =============================================================================
# ByproductRecordModel
# =============================================================================

class ByproductRecordModel(TrackedBase):
    """
    ORM model for byproducts from a production job.

    Maps to: finance_modules.wip.models.ByproductRecord (frozen dataclass).

    Guarantees:
        - job_id references the producing WorkOrderModel via FK.
        - item_id references a Sindri-owned byproduct Item (no FK).
        - value uses Decimal (Numeric(38,9)) for full cost traceability.
    """

    __tablename__ = "wip_byproduct_records"

    __table_args__ = (
        Index("idx_wip_bp_job", "job_id"),
        Index("idx_wip_bp_item", "item_id"),
    )

    # Parent job (work order FK within module)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )

    # Sindri entity reference (byproduct item, no FK)
    item_id: Mapped[UUID] = mapped_column()

    # Byproduct details
    description: Mapped[str] = mapped_column(String(255))
    value: Mapped[Decimal] = mapped_column()
    quantity: Mapped[Decimal] = mapped_column(default=Decimal("1"))

    # Relationship back to parent work order
    work_order: Mapped["WorkOrderModel"] = relationship(
        foreign_keys=[job_id],
    )

    def to_dto(self):
        """Convert ORM model to frozen ByproductRecord DTO."""
        from finance_modules.wip.models import ByproductRecord
        return ByproductRecord(
            id=self.id,
            job_id=self.job_id,
            item_id=self.item_id,
            description=self.description,
            value=self.value,
            quantity=self.quantity,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ByproductRecordModel":
        """Create ORM model from frozen ByproductRecord DTO."""
        return cls(
            id=dto.id,
            job_id=dto.job_id,
            item_id=dto.item_id,
            description=dto.description,
            value=dto.value,
            quantity=dto.quantity,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ByproductRecordModel {self.id} job={self.job_id} "
            f"item={self.item_id} value={self.value}>"
        )


# =============================================================================
# ProductionCostSummaryModel
# =============================================================================

class ProductionCostSummaryModel(TrackedBase):
    """
    ORM model for aggregated production costs for a job.

    Maps to: finance_modules.wip.models.ProductionCostSummary (frozen dataclass).

    Note: The DTO uses ``job_id`` as its identity (no separate ``id``).  The ORM
    model adds a UUID PK via TrackedBase; ``job_id`` references the work order.

    Guarantees:
        - total_cost = material_cost + labor_cost + overhead_cost (caller-enforced).
        - All cost fields use Decimal (Numeric(38,9)).
    """

    __tablename__ = "wip_production_cost_summaries"

    __table_args__ = (
        Index("idx_wip_pcs_job", "job_id"),
    )

    # Parent job (work order FK within module)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )

    # Cost breakdown
    material_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    labor_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    overhead_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Production output
    units_produced: Mapped[Decimal] = mapped_column(default=Decimal("0"))

    # Relationship back to parent work order
    work_order: Mapped["WorkOrderModel"] = relationship(
        foreign_keys=[job_id],
    )

    def to_dto(self):
        """Convert ORM model to frozen ProductionCostSummary DTO."""
        from finance_modules.wip.models import ProductionCostSummary
        return ProductionCostSummary(
            job_id=self.job_id,
            material_cost=self.material_cost,
            labor_cost=self.labor_cost,
            overhead_cost=self.overhead_cost,
            total_cost=self.total_cost,
            units_produced=self.units_produced,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "ProductionCostSummaryModel":
        """Create ORM model from frozen ProductionCostSummary DTO."""
        return cls(
            job_id=dto.job_id,
            material_cost=dto.material_cost,
            labor_cost=dto.labor_cost,
            overhead_cost=dto.overhead_cost,
            total_cost=dto.total_cost,
            units_produced=dto.units_produced,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<ProductionCostSummaryModel job={self.job_id} "
            f"total={self.total_cost} units={self.units_produced}>"
        )


# =============================================================================
# UnitCostBreakdownModel
# =============================================================================

class UnitCostBreakdownModel(TrackedBase):
    """
    ORM model for per-unit cost breakdown by component.

    Maps to: finance_modules.wip.models.UnitCostBreakdown (frozen dataclass).

    Note: The DTO uses ``job_id`` as its identity (no separate ``id``).  The ORM
    model adds a UUID PK via TrackedBase; ``job_id`` references the work order.

    Guarantees:
        - total_per_unit = material_per_unit + labor_per_unit + overhead_per_unit
          (caller-enforced).
        - All per-unit fields use Decimal (Numeric(38,9)).
    """

    __tablename__ = "wip_unit_cost_breakdowns"

    __table_args__ = (
        Index("idx_wip_ucb_job", "job_id"),
    )

    # Parent job (work order FK within module)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("wip_work_orders.id"),
    )

    # Production output
    units_produced: Mapped[Decimal] = mapped_column()

    # Per-unit cost breakdown
    material_per_unit: Mapped[Decimal] = mapped_column()
    labor_per_unit: Mapped[Decimal] = mapped_column()
    overhead_per_unit: Mapped[Decimal] = mapped_column()
    total_per_unit: Mapped[Decimal] = mapped_column()

    # Relationship back to parent work order
    work_order: Mapped["WorkOrderModel"] = relationship(
        foreign_keys=[job_id],
    )

    def to_dto(self):
        """Convert ORM model to frozen UnitCostBreakdown DTO."""
        from finance_modules.wip.models import UnitCostBreakdown
        return UnitCostBreakdown(
            job_id=self.job_id,
            units_produced=self.units_produced,
            material_per_unit=self.material_per_unit,
            labor_per_unit=self.labor_per_unit,
            overhead_per_unit=self.overhead_per_unit,
            total_per_unit=self.total_per_unit,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "UnitCostBreakdownModel":
        """Create ORM model from frozen UnitCostBreakdown DTO."""
        return cls(
            job_id=dto.job_id,
            units_produced=dto.units_produced,
            material_per_unit=dto.material_per_unit,
            labor_per_unit=dto.labor_per_unit,
            overhead_per_unit=dto.overhead_per_unit,
            total_per_unit=dto.total_per_unit,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<UnitCostBreakdownModel job={self.job_id} "
            f"total_per_unit={self.total_per_unit}>"
        )
