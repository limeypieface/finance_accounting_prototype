"""
Travel & Expense (T&E) Module.

Handles expense reports, approvals, reimbursements, and corporate card reconciliation.

Total: ~150 lines of module-specific code.
Policy validation uses shared rules engine.
"""

from finance_modules.expense.models import (
    ExpenseReport,
    ExpenseLine,
    CorporateCard,
    CardTransaction,
    ExpensePolicy,
    PolicyViolation,
    MileageRate,
    PerDiemRate,
)
from finance_modules.expense.profiles import EXPENSE_PROFILES
from finance_modules.expense.workflows import EXPENSE_REPORT_WORKFLOW
from finance_modules.expense.config import ExpenseConfig

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
