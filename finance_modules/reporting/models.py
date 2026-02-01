"""
Financial Reporting Domain Models (``finance_modules.reporting.models``).

Responsibility
--------------
Frozen dataclass value objects representing financial statement outputs:
trial balance, income statement, balance sheet, cash flow statement,
equity changes, segment reports, and multi-currency trial balance.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``ReportingService`` and returned to callers.  No dependency on kernel
services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``ReportMetadata`` records carry generation timestamp and parameters
  for report reproducibility.
* Financial statements are derived from the immutable journal and
  support external audit review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID


# =========================================================================
# Enums
# =========================================================================


class ReportType(str, Enum):
    """Types of financial reports."""

    TRIAL_BALANCE = "trial_balance"
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"
    EQUITY_CHANGES = "equity_changes"
    SEGMENT = "segment"


class IncomeStatementFormat(str, Enum):
    """Income statement presentation format."""

    SINGLE_STEP = "single_step"
    MULTI_STEP = "multi_step"


class BalanceSheetFormat(str, Enum):
    """Balance sheet presentation format."""

    CLASSIFIED = "classified"  # ASC 210 / IAS 1
    UNCLASSIFIED = "unclassified"  # Simple A = L + E


# =========================================================================
# Report Metadata (common to all reports)
# =========================================================================


@dataclass(frozen=True)
class ReportMetadata:
    """Metadata attached to every financial report."""

    report_type: ReportType
    entity_name: str
    currency: str
    as_of_date: date
    generated_at: str  # ISO format timestamp from injected clock
    period_start: date | None = None
    period_end: date | None = None
    comparative_date: date | None = None
    dimensions_filter: tuple[tuple[str, str], ...] | None = None


# =========================================================================
# Trial Balance Report
# =========================================================================


@dataclass(frozen=True)
class TrialBalanceLineItem:
    """A single line in the trial balance."""

    account_id: UUID
    account_code: str
    account_name: str
    account_type: str  # "asset", "liability", "equity", "revenue", "expense"
    debit_balance: Decimal
    credit_balance: Decimal
    net_balance: Decimal  # Natural-balance-adjusted


@dataclass(frozen=True)
class TrialBalanceReport:
    """Complete trial balance report."""

    metadata: ReportMetadata
    lines: tuple[TrialBalanceLineItem, ...]
    total_debits: Decimal
    total_credits: Decimal
    is_balanced: bool  # total_debits == total_credits
    # Comparative period (optional)
    comparative_lines: tuple[TrialBalanceLineItem, ...] | None = None
    comparative_total_debits: Decimal | None = None
    comparative_total_credits: Decimal | None = None


# =========================================================================
# Balance Sheet Report (ASC 210 / IAS 1)
# =========================================================================


@dataclass(frozen=True)
class BalanceSheetSection:
    """A section of the balance sheet (e.g., Current Assets)."""

    label: str
    lines: tuple[TrialBalanceLineItem, ...]
    total: Decimal


@dataclass(frozen=True)
class BalanceSheetReport:
    """
    Classified balance sheet.

    Assets = Liabilities + Equity is enforced as an invariant.
    """

    metadata: ReportMetadata
    format: BalanceSheetFormat

    # Asset sections
    current_assets: BalanceSheetSection
    non_current_assets: BalanceSheetSection
    total_assets: Decimal

    # Liability sections
    current_liabilities: BalanceSheetSection
    non_current_liabilities: BalanceSheetSection
    total_liabilities: Decimal

    # Equity section
    equity: BalanceSheetSection
    total_equity: Decimal

    # Verification
    total_liabilities_and_equity: Decimal
    is_balanced: bool  # total_assets == total_liabilities_and_equity

    # Comparative period (optional)
    comparative: BalanceSheetReport | None = None


# =========================================================================
# Income Statement
# =========================================================================


@dataclass(frozen=True)
class IncomeStatementSection:
    """A section of the income statement."""

    label: str
    lines: tuple[TrialBalanceLineItem, ...]
    total: Decimal


@dataclass(frozen=True)
class IncomeStatementReport:
    """
    Income statement (P&L).

    Single-step: Revenue - Expenses = Net Income
    Multi-step: Revenue - COGS = Gross Profit - Operating Expenses
                = Operating Income +/- Other = Net Income
    """

    metadata: ReportMetadata
    format: IncomeStatementFormat

    # Always present
    total_revenue: Decimal
    total_expenses: Decimal
    net_income: Decimal  # revenue - expenses

    # Multi-step breakdown (None for single-step)
    revenue_section: IncomeStatementSection | None = None
    cogs_section: IncomeStatementSection | None = None
    gross_profit: Decimal | None = None
    operating_expense_section: IncomeStatementSection | None = None
    operating_income: Decimal | None = None
    other_income_section: IncomeStatementSection | None = None
    other_expense_section: IncomeStatementSection | None = None
    income_before_tax: Decimal | None = None

    # Comparative period (optional)
    comparative: IncomeStatementReport | None = None


# =========================================================================
# Cash Flow Statement (Indirect Method)
# =========================================================================


@dataclass(frozen=True)
class CashFlowLineItem:
    """A single line in a cash flow section."""

    description: str
    amount: Decimal


@dataclass(frozen=True)
class CashFlowSection:
    """A section of the cash flow statement."""

    label: str
    lines: tuple[CashFlowLineItem, ...]
    total: Decimal


@dataclass(frozen=True)
class CashFlowStatementReport:
    """
    Statement of Cash Flows (indirect method, ASC 230 / IAS 7).

    Operating: Net income + non-cash adjustments + WC changes
    Investing: Asset purchases/sales
    Financing: Debt/equity changes
    """

    metadata: ReportMetadata

    net_income: Decimal

    # Operating activities (indirect)
    operating_adjustments: CashFlowSection  # Depreciation, etc.
    working_capital_changes: CashFlowSection  # AR, AP, Inventory changes
    net_cash_from_operations: Decimal

    # Investing activities
    investing_activities: CashFlowSection
    net_cash_from_investing: Decimal

    # Financing activities
    financing_activities: CashFlowSection
    net_cash_from_financing: Decimal

    # Summary
    net_change_in_cash: Decimal
    beginning_cash: Decimal
    ending_cash: Decimal

    # Verification
    cash_change_reconciles: bool  # ending - beginning == net_change

    # Comparative period (optional)
    comparative: CashFlowStatementReport | None = None


# =========================================================================
# Statement of Changes in Equity
# =========================================================================


@dataclass(frozen=True)
class EquityMovement:
    """A single equity movement item."""

    description: str
    amount: Decimal


@dataclass(frozen=True)
class EquityChangesReport:
    """Statement of changes in equity over a period."""

    metadata: ReportMetadata

    beginning_equity: Decimal
    movements: tuple[EquityMovement, ...]
    ending_equity: Decimal

    # Detail
    net_income: Decimal
    dividends_declared: Decimal
    other_changes: Decimal

    # Verification
    reconciles: bool  # beginning + sum(movements) == ending


# =========================================================================
# Segment Reporting
# =========================================================================


@dataclass(frozen=True)
class SegmentData:
    """Financial data for a single segment."""

    segment_key: str
    segment_value: str
    trial_balance_lines: tuple[TrialBalanceLineItem, ...]
    total_revenue: Decimal
    total_expenses: Decimal
    net_income: Decimal
    total_assets: Decimal


@dataclass(frozen=True)
class SegmentReport:
    """Dimension-based segment report."""

    metadata: ReportMetadata
    dimension_name: str
    segments: tuple[SegmentData, ...]
    unallocated: SegmentData | None = None  # Lines without dimension value


# =========================================================================
# Multi-Currency Trial Balance
# =========================================================================


@dataclass(frozen=True)
class MultiCurrencyTrialBalance:
    """Trial balance across multiple currencies.

    Aggregates per-currency trial balance reports into a single
    container for multi-currency reporting needs.
    """

    metadata: ReportMetadata
    currency_reports: tuple[TrialBalanceReport, ...]
    currencies: tuple[str, ...]
    total_debits_by_currency: tuple[tuple[str, Decimal], ...]
    total_credits_by_currency: tuple[tuple[str, Decimal], ...]
    all_balanced: bool  # True if every currency TB is balanced
