"""
Budgeting Module (``finance_modules.budget``).

Responsibility
--------------
Thin ERP glue for budgetary control: budget entries, version management,
encumbrance recording and relief, budget transfers, forecasting, budget
locking, and budget-vs-actual variance analysis.

Architecture position
---------------------
**Modules layer** -- declarative profiles, config schemas, and a service
facade that delegates variance calculations to ``VarianceCalculator`` and
all journal posting to ``finance_kernel`` via ``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``BudgetService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New budget event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Budget lock prevents modifications to approved budget versions.

Audit relevance
---------------
Budget control is a key SOX internal control.  All budget postings produce
immutable journal entries with full provenance through the kernel audit
chain (R11).  Budget-vs-actual variance reports support management
oversight and regulatory compliance.
"""

from finance_modules.budget.models import (
    BudgetEntry,
    BudgetLock,
    BudgetVariance,
    BudgetVersion,
    Encumbrance,
    EncumbranceStatus,
    ForecastEntry,
)
from finance_modules.budget.profiles import BUDGET_PROFILES
from finance_modules.budget.config import BudgetConfig

__all__ = [
    "BudgetEntry",
    "BudgetLock",
    "BudgetVariance",
    "BudgetVersion",
    "Encumbrance",
    "EncumbranceStatus",
    "ForecastEntry",
    "BUDGET_PROFILES",
    "BudgetConfig",
]
