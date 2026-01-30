"""
Payroll Module.

Handles payroll processing, deductions, and labor distribution.

Total: ~200 lines of module-specific code.
Tax calculation and benefit engines come from shared engines.
"""

from finance_modules.payroll.models import (
    Employee,
    PayPeriod,
    Timecard,
    PayrollRun,
    Paycheck,
)
from finance_modules.payroll.profiles import PAYROLL_PROFILES
from finance_modules.payroll.workflows import PAYROLL_RUN_WORKFLOW
from finance_modules.payroll.config import PayrollConfig

__all__ = [
    "Employee",
    "PayPeriod",
    "Timecard",
    "PayrollRun",
    "Paycheck",
    "PAYROLL_PROFILES",
    "PAYROLL_RUN_WORKFLOW",
    "PayrollConfig",
]
