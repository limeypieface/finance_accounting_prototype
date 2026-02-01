"""
Work-in-Process Domain Models (``finance_modules.wip.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of manufacturing:
work orders, operations, labor charges, overhead allocation, scrap records,
byproduct records, production cost summaries, and unit cost breakdowns.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``WipService`` and returned to callers.  No dependency on kernel services,
database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``ProductionCostSummary`` records support cost accounting disclosure.
* ``UnitCostBreakdown`` records support standard cost variance analysis.
* ``ByproductRecord`` records track byproduct value for full cost traceability.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.wip.models")


class WorkOrderStatus(Enum):
    """Work order lifecycle states."""
    PLANNED = "planned"
    RELEASED = "released"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class OperationStatus(Enum):
    """Operation status within a work order."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class WorkOrder:
    """A production work order."""
    id: UUID
    order_number: str
    item_id: UUID  # finished goods item
    quantity_ordered: Decimal
    quantity_completed: Decimal = Decimal("0")
    quantity_scrapped: Decimal = Decimal("0")
    planned_start_date: date | None = None
    planned_end_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    status: WorkOrderStatus = WorkOrderStatus.PLANNED
    parent_work_order_id: UUID | None = None  # for subassemblies
    sales_order_id: UUID | None = None

    def __post_init__(self):
        # Validate quantity_ordered is positive
        if self.quantity_ordered <= 0:
            raise ValueError("quantity_ordered must be positive")

        # Validate quantity_completed does not exceed quantity_ordered
        if self.quantity_completed > self.quantity_ordered:
            logger.warning(
                "work_order_over_completion",
                extra={
                    "work_order_id": str(self.id),
                    "order_number": self.order_number,
                    "quantity_ordered": str(self.quantity_ordered),
                    "quantity_completed": str(self.quantity_completed),
                },
            )
            raise ValueError(
                f"quantity_completed ({self.quantity_completed}) "
                f"cannot exceed quantity_ordered ({self.quantity_ordered})"
            )

        # Validate quantity_completed is non-negative
        if self.quantity_completed < 0:
            raise ValueError("quantity_completed cannot be negative")

        # Validate quantity_scrapped is non-negative
        if self.quantity_scrapped < 0:
            raise ValueError("quantity_scrapped cannot be negative")

        logger.debug(
            "work_order_created",
            extra={
                "work_order_id": str(self.id),
                "order_number": self.order_number,
                "item_id": str(self.item_id),
                "quantity_ordered": str(self.quantity_ordered),
                "status": self.status.value,
            },
        )


@dataclass(frozen=True)
class WorkOrderLine:
    """Material component for a work order (BOM explosion)."""
    id: UUID
    work_order_id: UUID
    item_id: UUID  # component item
    quantity_required: Decimal
    quantity_issued: Decimal = Decimal("0")
    unit_cost: Decimal = Decimal("0")
    operation_seq: int = 10  # which operation consumes this


@dataclass(frozen=True)
class Operation:
    """A manufacturing operation within a work order."""
    id: UUID
    work_order_id: UUID
    sequence: int
    work_center_id: UUID
    description: str
    setup_time_hours: Decimal = Decimal("0")
    run_time_hours: Decimal = Decimal("0")
    labor_rate: Decimal = Decimal("0")
    overhead_rate: Decimal = Decimal("0")
    status: OperationStatus = OperationStatus.NOT_STARTED
    quantity_completed: Decimal = Decimal("0")


@dataclass(frozen=True)
class LaborEntry:
    """Labor time charged to a work order operation."""
    id: UUID
    work_order_id: UUID
    operation_id: UUID
    employee_id: UUID
    work_date: date
    hours: Decimal
    labor_rate: Decimal
    labor_cost: Decimal
    entry_type: str = "run"  # "setup", "run", "rework"


@dataclass(frozen=True)
class OverheadApplication:
    """Overhead applied to a work order."""
    id: UUID
    work_order_id: UUID
    application_date: date
    overhead_type: str  # "fixed", "variable", "setup"
    basis: str  # "labor_hours", "machine_hours", "units"
    rate: Decimal
    quantity: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ProductionCostSummary:
    """Aggregated production costs for a job."""
    job_id: UUID
    material_cost: Decimal = Decimal("0")
    labor_cost: Decimal = Decimal("0")
    overhead_cost: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    units_produced: Decimal = Decimal("0")


@dataclass(frozen=True)
class ByproductRecord:
    """A byproduct from a production job."""
    id: UUID
    job_id: UUID
    item_id: UUID
    description: str
    value: Decimal
    quantity: Decimal = Decimal("1")


@dataclass(frozen=True)
class UnitCostBreakdown:
    """Per-unit cost breakdown by component."""
    job_id: UUID
    units_produced: Decimal
    material_per_unit: Decimal
    labor_per_unit: Decimal
    overhead_per_unit: Decimal
    total_per_unit: Decimal
