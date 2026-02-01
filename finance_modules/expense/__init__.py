"""
Travel & Expense Module (``finance_modules.expense``).

Responsibility
--------------
Thin ERP glue for the travel-and-expense cycle: expense reports, line-item
categorization, policy compliance validation, manager approvals,
reimbursements, corporate card transaction matching, mileage, and per diem.

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``ExpenseService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New expense event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Policy validation helpers raise ``ValueError`` for invalid inputs.

Audit relevance
---------------
Every expense posting produces an immutable journal entry with full
provenance through the kernel audit chain (R11).  Policy violation records
support SOX internal controls over employee reimbursements.

Total: ~150 lines of module-specific code.
Policy validation uses pure helper functions in ``helpers.py``.
"""

from finance_modules.expense.config import ExpenseConfig
from finance_modules.expense.models import (
    CardTransaction,
    CorporateCard,
    ExpenseLine,
    ExpensePolicy,
    ExpenseReport,
    MileageRate,
    PerDiemRate,
    PolicyViolation,
)
from finance_modules.expense.profiles import EXPENSE_PROFILES
from finance_modules.expense.workflows import EXPENSE_REPORT_WORKFLOW

__all__ = [
    "ExpenseReport",
    "ExpenseLine",
    "CorporateCard",
    "CardTransaction",
    "ExpensePolicy",
    "PolicyViolation",
    "MileageRate",
    "PerDiemRate",
    "EXPENSE_PROFILES",
    "EXPENSE_REPORT_WORKFLOW",
    "ExpenseConfig",
]
