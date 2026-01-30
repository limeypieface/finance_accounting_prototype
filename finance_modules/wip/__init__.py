"""
Work-in-Process (WIP) Module.

Handles production orders, job costing, and manufacturing variance.

Total: ~200 lines of module-specific code.
Cost rollup and variance engines come from shared engines.
"""

from finance_modules.wip.models import (
    WorkOrder,
    WorkOrderLine,
    Operation,
    LaborEntry,
    OverheadApplication,
)
from finance_modules.wip.profiles import WIP_PROFILES
from finance_modules.wip.workflows import WORK_ORDER_WORKFLOW
from finance_modules.wip.config import WIPConfig

__all__ = [
    "WorkOrder",
    "WorkOrderLine",
    "Operation",
    "LaborEntry",
    "OverheadApplication",
    "WIP_PROFILES",
    "WORK_ORDER_WORKFLOW",
    "WIPConfig",
]
