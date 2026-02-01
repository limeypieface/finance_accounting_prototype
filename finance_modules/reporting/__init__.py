"""
Financial Reporting Module (``finance_modules.reporting``).

Responsibility
--------------
Read-only module that generates financial statements from the ledger:
trial balance, balance sheet (ASC 210 / IAS 1, classified), income
statement (single-step and multi-step), cash flow statement (indirect
method, ASC 230 / IAS 7), statement of changes in equity, segment
report (dimension-based), and multi-currency trial balance.

Architecture position
---------------------
**Modules layer** -- pure read-only service with NO posting profiles.
Unlike other modules, reporting does NOT post journal entries and has
no ``AccountingPolicy`` registrations.  All statement generation is
implemented as pure functions.

Invariants enforced
-------------------
* No journal entries are created by this module (read-only guarantee).
* Statement computations derive entirely from the immutable journal
  (R6 -- no stored balances).

Failure modes
-------------
* Missing account hierarchy data -> incomplete classification.
* Period not found -> empty report with zero balances.

Audit relevance
---------------
Financial statements are the primary output of the accounting system.
Statement generation is deterministic and reproducible from the
immutable journal.  Report metadata includes generation timestamp and
period identifiers for audit trail purposes.
"""

from finance_modules.reporting.config import ReportingConfig
from finance_modules.reporting.models import (
    BalanceSheetFormat,
    BalanceSheetReport,
    BalanceSheetSection,
    CashFlowLineItem,
    CashFlowSection,
    CashFlowStatementReport,
    EquityChangesReport,
    EquityMovement,
    IncomeStatementFormat,
    IncomeStatementReport,
    IncomeStatementSection,
    MultiCurrencyTrialBalance,
    ReportMetadata,
    ReportType,
    SegmentData,
    SegmentReport,
    TrialBalanceLineItem,
    TrialBalanceReport,
)
from finance_modules.reporting.profiles import REPORTING_PROFILES
from finance_modules.reporting.service import ReportingService

__all__ = [
    # Service
    "ReportingService",
    # Config
    "ReportingConfig",
    # Models
    "ReportType",
    "IncomeStatementFormat",
    "BalanceSheetFormat",
    "ReportMetadata",
    "TrialBalanceLineItem",
    "TrialBalanceReport",
    "BalanceSheetSection",
    "BalanceSheetReport",
    "IncomeStatementSection",
    "IncomeStatementReport",
    "CashFlowLineItem",
    "CashFlowSection",
    "CashFlowStatementReport",
    "EquityMovement",
    "EquityChangesReport",
    "SegmentData",
    "SegmentReport",
    "MultiCurrencyTrialBalance",
    # Profiles (empty — read-only module)
    "REPORTING_PROFILES",
]
