"""
Payroll Module (``finance_modules.payroll``).

Responsibility
--------------
Thin ERP glue for payroll processing: timecards, pay runs, paychecks,
federal/state/FICA withholding, benefits deductions, employer contributions,
labor distribution, and NACHA/ACH direct deposit batch generation.

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates tax calculations to pure helpers and all
journal posting to ``finance_kernel`` via ``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``PayrollService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New payroll event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Withholding helper returns ``Decimal("0")`` for zero gross pay.

Audit relevance
---------------
Payroll is SOX-critical.  Every payroll posting produces an immutable
journal entry with full provenance through the kernel audit chain (R11).
Withholding calculations must be documented for IRS/state compliance.
DCAA labor distribution supports government contract cost accounting.

Total: ~200 lines of module-specific code.
Tax calculation engines come from pure helpers in ``helpers.py``.
"""

from finance_modules.payroll.models import (
    BenefitsDeduction,
    Employee,
    EmployerContribution,
    PayPeriod,
    Timecard,
    PayrollRun,
    Paycheck,
    WithholdingResult,
)
from finance_modules.payroll.profiles import PAYROLL_PROFILES
from finance_modules.payroll.workflows import PAYROLL_RUN_WORKFLOW
from finance_modules.payroll.config import PayrollConfig

__all__ = [
    "BenefitsDeduction",
    "Employee",
    "EmployerContribution",
    "PayPeriod",
    "Timecard",
    "PayrollRun",
    "Paycheck",
    "WithholdingResult",
    "PAYROLL_PROFILES",
    "PAYROLL_RUN_WORKFLOW",
    "PayrollConfig",
]
