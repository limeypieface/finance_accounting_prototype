"""
Financial Reporting Module.

Read-only module that generates financial statements from the ledger:
- Trial Balance
- Balance Sheet (ASC 210 / IAS 1, classified)
- Income Statement (single-step and multi-step)
- Cash Flow Statement (indirect method, ASC 230 / IAS 7)
- Statement of Changes in Equity
- Segment Report (dimension-based)

Unlike other modules, this does NOT post journal entries.
All transformations are pure functions in statements.py.
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
    # Profiles (empty — read-only module)
    "REPORTING_PROFILES",
]
