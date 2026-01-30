"""
General Ledger Module.

Handles chart of accounts, journal entries, period close, and financial reporting.

Total: ~180 lines of module-specific code.
This module orchestrates the kernel's journal posting and provides GL-specific features.
"""

from finance_modules.gl.models import (
    Account,
    AccountHierarchy,
    FiscalPeriod,
    JournalBatch,
    RecurringEntry,
)
from finance_modules.gl.profiles import GL_PROFILES
from finance_modules.gl.workflows import PERIOD_CLOSE_WORKFLOW
from finance_modules.gl.config import GLConfig

__all__ = [
    "Account",
    "AccountHierarchy",
    "FiscalPeriod",
    "JournalBatch",
    "RecurringEntry",
    "GL_PROFILES",
    "PERIOD_CLOSE_WORKFLOW",
    "GLConfig",
]
