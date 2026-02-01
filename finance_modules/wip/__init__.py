"""
Work-in-Process Module (``finance_modules.wip``).

Responsibility
--------------
Thin ERP glue for manufacturing cost accounting: material issues, labor
charges, overhead application, work-order completion, scrap, rework,
byproduct recording, and period-end variance analysis (labor, material,
overhead).

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``WIPService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New WIP event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.
* L5  -- Link creation and journal posting share a single transaction.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Variance calculations require standard cost data; missing data returns zero.

Audit relevance
---------------
Standard cost variance analysis supports manufacturing cost control and
compliance with cost accounting standards.  All WIP transactions produce
immutable journal entries with full provenance through the kernel audit
chain (R11).

Total: ~200 lines of module-specific code.
Cost rollup and variance engines come from shared engines.
"""

from finance_modules.wip.config import WIPConfig
from finance_modules.wip.models import (
    ByproductRecord,
    LaborEntry,
    Operation,
    OverheadApplication,
    ProductionCostSummary,
    UnitCostBreakdown,
    WorkOrder,
    WorkOrderLine,
)
from finance_modules.wip.profiles import WIP_PROFILES
from finance_modules.wip.workflows import WORK_ORDER_WORKFLOW

__all__ = [
    "WorkOrder",
    "WorkOrderLine",
    "Operation",
    "LaborEntry",
    "OverheadApplication",
    "ProductionCostSummary",
    "ByproductRecord",
    "UnitCostBreakdown",
    "WIP_PROFILES",
    "WORK_ORDER_WORKFLOW",
    "WIPConfig",
]
