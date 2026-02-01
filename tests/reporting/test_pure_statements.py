"""
Pure function unit tests for statements.py.

NO database, NO I/O. Tests every pure transformation with synthetic data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from finance_kernel.models.account import AccountType, NormalBalance
from finance_kernel.selectors.ledger_selector import TrialBalanceRow
from finance_modules.reporting.config import ReportingConfig
from finance_modules.reporting.models import (
    BalanceSheetFormat,
    IncomeStatementFormat,
    ReportMetadata,
    ReportType,
)
from finance_modules.reporting.statements import (
    AccountInfo,
    build_balance_sheet,
    build_cash_flow_statement,
    build_equity_changes,
    build_income_statement,
    build_segment_report,
    build_trial_balance,
    classify_for_balance_sheet,
    classify_for_income_statement,
    compute_natural_balance,
    compute_net_income_from_tb,
    enrich_trial_balance,
    render_to_dict,
)

# =========================================================================
# Fixtures / helpers
# =========================================================================

CASH_ID = uuid4()
AR_ID = uuid4()
INVENTORY_ID = uuid4()
EQUIPMENT_ID = uuid4()
AP_ID = uuid4()
NOTES_PAYABLE_ID = uuid4()
COMMON_STOCK_ID = uuid4()
RETAINED_EARNINGS_ID = uuid4()
REVENUE_ID = uuid4()
COGS_ID = uuid4()
SALARY_EXPENSE_ID = uuid4()
RENT_EXPENSE_ID = uuid4()
INTEREST_INCOME_ID = uuid4()
INTEREST_EXPENSE_ID = uuid4()
DEPRECIATION_ID = uuid4()
DIVIDEND_ID = uuid4()


def _config() -> ReportingConfig:
    return ReportingConfig.with_defaults()


def _metadata(
    report_type: ReportType = ReportType.TRIAL_BALANCE,
) -> ReportMetadata:
    return ReportMetadata(
        report_type=report_type,
        entity_name="Test Company",
        currency="USD",
        as_of_date=date(2025, 12, 31),
        generated_at="2025-12-31T23:59:59",
    )


def _accounts() -> dict[UUID, AccountInfo]:
    """Standard set of 16 test accounts covering all types."""
    return {
        CASH_ID: AccountInfo(CASH_ID, "1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT),
        AR_ID: AccountInfo(AR_ID, "1100", "Accounts Receivable", AccountType.ASSET, NormalBalance.DEBIT),
        INVENTORY_ID: AccountInfo(INVENTORY_ID, "1200", "Inventory", AccountType.ASSET, NormalBalance.DEBIT),
        EQUIPMENT_ID: AccountInfo(EQUIPMENT_ID, "1500", "Equipment", AccountType.ASSET, NormalBalance.DEBIT),
        AP_ID: AccountInfo(AP_ID, "2000", "Accounts Payable", AccountType.LIABILITY, NormalBalance.CREDIT),
        NOTES_PAYABLE_ID: AccountInfo(NOTES_PAYABLE_ID, "2500", "Notes Payable", AccountType.LIABILITY, NormalBalance.CREDIT),
        COMMON_STOCK_ID: AccountInfo(COMMON_STOCK_ID, "3000", "Common Stock", AccountType.EQUITY, NormalBalance.CREDIT),
        RETAINED_EARNINGS_ID: AccountInfo(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", AccountType.EQUITY, NormalBalance.CREDIT),
        REVENUE_ID: AccountInfo(REVENUE_ID, "4000", "Sales Revenue", AccountType.REVENUE, NormalBalance.CREDIT),
        COGS_ID: AccountInfo(COGS_ID, "5000", "Cost of Goods Sold", AccountType.EXPENSE, NormalBalance.DEBIT),
        SALARY_EXPENSE_ID: AccountInfo(SALARY_EXPENSE_ID, "5100", "Salary Expense", AccountType.EXPENSE, NormalBalance.DEBIT),
        RENT_EXPENSE_ID: AccountInfo(RENT_EXPENSE_ID, "5200", "Rent Expense", AccountType.EXPENSE, NormalBalance.DEBIT),
        INTEREST_INCOME_ID: AccountInfo(INTEREST_INCOME_ID, "4300", "Interest Income", AccountType.REVENUE, NormalBalance.CREDIT),
        INTEREST_EXPENSE_ID: AccountInfo(INTEREST_EXPENSE_ID, "6000", "Interest Expense", AccountType.EXPENSE, NormalBalance.DEBIT),
        DEPRECIATION_ID: AccountInfo(DEPRECIATION_ID, "5300", "Depreciation Expense", AccountType.EXPENSE, NormalBalance.DEBIT, tags=("depreciation",)),
        DIVIDEND_ID: AccountInfo(DIVIDEND_ID, "3200", "Dividends Declared", AccountType.EQUITY, NormalBalance.DEBIT),
    }


def _tb_row(
    account_id: UUID,
    code: str,
    name: str,
    debit: str,
    credit: str,
) -> TrialBalanceRow:
    return TrialBalanceRow(
        account_id=account_id,
        account_code=code,
        account_name=name,
        currency="USD",
        debit_total=Decimal(debit),
        credit_total=Decimal(credit),
    )


def _balanced_tb() -> list[TrialBalanceRow]:
    """
    A balanced trial balance representing a small company:

    Cash          Dr 50,000
    AR            Dr 20,000
    Inventory     Dr 15,000
    Equipment     Dr 30,000
    AP                       Cr 10,000
    Notes Payable            Cr 25,000
    Common Stock             Cr 50,000
    Retained Earn            Cr 10,000
    Revenue                  Cr 100,000
    COGS          Dr 40,000
    Salary Exp    Dr 25,000
    Rent Exp      Dr 10,000
    Interest Inc             Cr 5,000
    Interest Exp  Dr  5,000
    Depreciation  Dr  5,000
    ---
    Total Dr:     200,000
    Total Cr:     200,000
    """
    return [
        _tb_row(CASH_ID, "1000", "Cash", "50000", "0"),
        _tb_row(AR_ID, "1100", "Accounts Receivable", "20000", "0"),
        _tb_row(INVENTORY_ID, "1200", "Inventory", "15000", "0"),
        _tb_row(EQUIPMENT_ID, "1500", "Equipment", "30000", "0"),
        _tb_row(AP_ID, "2000", "Accounts Payable", "0", "10000"),
        _tb_row(NOTES_PAYABLE_ID, "2500", "Notes Payable", "0", "25000"),
        _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "50000"),
        _tb_row(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", "0", "10000"),
        _tb_row(REVENUE_ID, "4000", "Sales Revenue", "0", "100000"),
        _tb_row(COGS_ID, "5000", "Cost of Goods Sold", "40000", "0"),
        _tb_row(SALARY_EXPENSE_ID, "5100", "Salary Expense", "25000", "0"),
        _tb_row(RENT_EXPENSE_ID, "5200", "Rent Expense", "10000", "0"),
        _tb_row(INTEREST_INCOME_ID, "4300", "Interest Income", "0", "5000"),
        _tb_row(INTEREST_EXPENSE_ID, "6000", "Interest Expense", "5000", "0"),
        _tb_row(DEPRECIATION_ID, "5300", "Depreciation Expense", "5000", "0"),
    ]


# =========================================================================
# TestComputeNaturalBalance
# =========================================================================


class TestComputeNaturalBalance:
    """Tests for compute_natural_balance()."""

    def test_debit_normal_positive(self):
        result = compute_natural_balance(
            Decimal("100"), Decimal("30"), NormalBalance.DEBIT,
        )
        assert result == Decimal("70")

    def test_debit_normal_negative(self):
        result = compute_natural_balance(
            Decimal("30"), Decimal("100"), NormalBalance.DEBIT,
        )
        assert result == Decimal("-70")

    def test_credit_normal_positive(self):
        result = compute_natural_balance(
            Decimal("30"), Decimal("100"), NormalBalance.CREDIT,
        )
        assert result == Decimal("70")

    def test_credit_normal_negative(self):
        result = compute_natural_balance(
            Decimal("100"), Decimal("30"), NormalBalance.CREDIT,
        )
        assert result == Decimal("-70")

    def test_zero_balance(self):
        result = compute_natural_balance(
            Decimal("50"), Decimal("50"), NormalBalance.DEBIT,
        )
        assert result == Decimal("0")

    def test_precision_preserved(self):
        result = compute_natural_balance(
            Decimal("100.123456789"), Decimal("50.000000001"), NormalBalance.DEBIT,
        )
        assert result == Decimal("50.123456788")


# =========================================================================
# TestEnrichTrialBalance
# =========================================================================


class TestEnrichTrialBalance:
    """Tests for enrich_trial_balance()."""

    def test_all_rows_enriched(self):
        rows = _balanced_tb()
        accounts = _accounts()
        config = _config()
        items = enrich_trial_balance(rows, accounts, config)
        # Zero balances are excluded by default; all our rows have non-zero
        assert len(items) == 15

    def test_sorted_by_code(self):
        rows = _balanced_tb()
        accounts = _accounts()
        config = _config()
        items = enrich_trial_balance(rows, accounts, config)
        codes = [item.account_code for item in items]
        assert codes == sorted(codes)

    def test_zero_balance_filtered(self):
        zero_id = uuid4()
        accounts = {
            zero_id: AccountInfo(
                zero_id, "9999", "Zero", AccountType.ASSET, NormalBalance.DEBIT,
            ),
        }
        rows = [_tb_row(zero_id, "9999", "Zero", "100", "100")]
        config = _config()
        items = enrich_trial_balance(rows, accounts, config)
        assert len(items) == 0

    def test_zero_balance_included_when_configured(self):
        zero_id = uuid4()
        accounts = {
            zero_id: AccountInfo(
                zero_id, "9999", "Zero", AccountType.ASSET, NormalBalance.DEBIT,
            ),
        }
        rows = [_tb_row(zero_id, "9999", "Zero", "100", "100")]
        config = ReportingConfig(include_zero_balances=True)
        items = enrich_trial_balance(rows, accounts, config)
        assert len(items) == 1
        assert items[0].net_balance == Decimal("0")

    def test_unknown_account_skipped(self):
        unknown_id = uuid4()
        rows = [_tb_row(unknown_id, "9999", "Unknown", "100", "0")]
        items = enrich_trial_balance(rows, {}, _config())
        assert len(items) == 0

    def test_natural_balance_applied(self):
        accounts = _accounts()
        rows = [_tb_row(AP_ID, "2000", "AP", "0", "10000")]
        items = enrich_trial_balance(rows, accounts, _config())
        # AP is credit-normal: credit - debit = 10000 - 0 = 10000
        assert items[0].net_balance == Decimal("10000")

    def test_debit_balance_and_credit_balance_preserved(self):
        accounts = _accounts()
        rows = [_tb_row(CASH_ID, "1000", "Cash", "50000", "0")]
        items = enrich_trial_balance(rows, accounts, _config())
        assert items[0].debit_balance == Decimal("50000")
        assert items[0].credit_balance == Decimal("0")


# =========================================================================
# TestComputeNetIncome
# =========================================================================


class TestComputeNetIncome:
    """Tests for compute_net_income_from_tb()."""

    def test_positive_net_income(self):
        accounts = _accounts()
        rows = _balanced_tb()
        # Revenue: 100,000 + 5,000 = 105,000
        # Expenses: 40,000 + 25,000 + 10,000 + 5,000 + 5,000 = 85,000
        # NI = 105,000 - 85,000 = 20,000
        ni = compute_net_income_from_tb(rows, accounts)
        assert ni == Decimal("20000")

    def test_negative_net_income(self):
        accounts = _accounts()
        rows = [
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "10000"),
            _tb_row(SALARY_EXPENSE_ID, "5100", "Salary", "50000", "0"),
        ]
        ni = compute_net_income_from_tb(rows, accounts)
        assert ni == Decimal("-40000")

    def test_zero_net_income(self):
        accounts = _accounts()
        rows = [
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "50000"),
            _tb_row(COGS_ID, "5000", "COGS", "50000", "0"),
        ]
        ni = compute_net_income_from_tb(rows, accounts)
        assert ni == Decimal("0")

    def test_only_revenue_and_expense_counted(self):
        accounts = _accounts()
        rows = [
            _tb_row(CASH_ID, "1000", "Cash", "999999", "0"),
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "10000"),
            _tb_row(COGS_ID, "5000", "COGS", "3000", "0"),
        ]
        ni = compute_net_income_from_tb(rows, accounts)
        assert ni == Decimal("7000")

    def test_empty_tb(self):
        ni = compute_net_income_from_tb([], _accounts())
        assert ni == Decimal("0")


# =========================================================================
# TestBuildTrialBalance
# =========================================================================


class TestBuildTrialBalance:
    """Tests for build_trial_balance()."""

    def test_basic_tb(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_trial_balance(rows, accounts, _config(), _metadata())
        assert len(report.lines) == 15
        assert report.total_debits == Decimal("200000")
        assert report.total_credits == Decimal("200000")
        assert report.is_balanced is True

    def test_unbalanced_tb(self):
        accounts = _accounts()
        rows = [_tb_row(CASH_ID, "1000", "Cash", "100", "0")]
        report = build_trial_balance(rows, accounts, _config(), _metadata())
        assert report.is_balanced is False
        assert report.total_debits == Decimal("100")
        assert report.total_credits == Decimal("0")

    def test_comparative(self):
        rows = _balanced_tb()
        accounts = _accounts()
        comp_rows = [_tb_row(CASH_ID, "1000", "Cash", "30000", "0")]
        report = build_trial_balance(
            rows, accounts, _config(), _metadata(), comp_rows,
        )
        assert report.comparative_lines is not None
        assert len(report.comparative_lines) == 1
        assert report.comparative_total_debits == Decimal("30000")

    def test_empty_ledger(self):
        report = build_trial_balance([], _accounts(), _config(), _metadata())
        assert len(report.lines) == 0
        assert report.is_balanced is True  # 0 == 0


# =========================================================================
# TestClassifyForBalanceSheet
# =========================================================================


class TestClassifyForBalanceSheet:
    """Tests for classify_for_balance_sheet()."""

    def test_asset_classification(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_balance_sheet(items, accounts, _config())
        current_codes = [i.account_code for i in classified["current_assets"]]
        non_current_codes = [i.account_code for i in classified["non_current_assets"]]
        assert "1000" in current_codes  # Cash
        assert "1100" in current_codes  # AR
        assert "1200" in current_codes  # Inventory
        assert "1500" in non_current_codes  # Equipment

    def test_liability_classification(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_balance_sheet(items, accounts, _config())
        current_codes = [i.account_code for i in classified["current_liabilities"]]
        non_current_codes = [i.account_code for i in classified["non_current_liabilities"]]
        assert "2000" in current_codes  # AP
        assert "2500" in non_current_codes  # Notes Payable

    def test_equity_classification(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_balance_sheet(items, accounts, _config())
        equity_codes = [i.account_code for i in classified["equity"]]
        assert "3000" in equity_codes  # Common Stock
        assert "3100" in equity_codes  # Retained Earnings

    def test_revenue_expense_excluded(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_balance_sheet(items, accounts, _config())
        all_bs_items = (
            classified["current_assets"]
            + classified["non_current_assets"]
            + classified["current_liabilities"]
            + classified["non_current_liabilities"]
            + classified["equity"]
        )
        all_codes = [i.account_code for i in all_bs_items]
        # Revenue and expense codes should NOT appear
        assert "4000" not in all_codes
        assert "5000" not in all_codes
        assert "5100" not in all_codes


# =========================================================================
# TestBuildBalanceSheet
# =========================================================================


class TestBuildBalanceSheet:
    """Tests for build_balance_sheet()."""

    def test_a_equals_l_plus_e(self):
        rows = _balanced_tb()
        accounts = _accounts()
        metadata = _metadata(ReportType.BALANCE_SHEET)
        report = build_balance_sheet(rows, accounts, _config(), metadata)
        assert report.is_balanced is True
        assert report.total_assets == report.total_liabilities_and_equity

    def test_assets_total(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_balance_sheet(rows, accounts, _config(), _metadata(ReportType.BALANCE_SHEET))
        # Current: Cash 50k + AR 20k + Inventory 15k = 85k
        # Non-current: Equipment 30k
        # Total: 115k
        assert report.total_assets == Decimal("115000")

    def test_net_income_in_equity(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_balance_sheet(rows, accounts, _config(), _metadata(ReportType.BALANCE_SHEET))
        # Equity accounts: Common Stock 50k + Retained Earnings 10k = 60k
        # Plus net income: 20k
        # Total equity: 80k
        assert report.total_equity == Decimal("80000")

    def test_classified_format(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_balance_sheet(rows, accounts, _config(), _metadata(ReportType.BALANCE_SHEET))
        assert report.format == BalanceSheetFormat.CLASSIFIED

    def test_comparative_balance_sheet(self):
        rows = _balanced_tb()
        accounts = _accounts()
        comp_rows = [
            _tb_row(CASH_ID, "1000", "Cash", "30000", "0"),
            _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "30000"),
        ]
        report = build_balance_sheet(
            rows, accounts, _config(), _metadata(ReportType.BALANCE_SHEET), comp_rows,
        )
        assert report.comparative is not None
        assert report.comparative.total_assets == Decimal("30000")

    def test_empty_ledger_balanced(self):
        report = build_balance_sheet(
            [], _accounts(), _config(), _metadata(ReportType.BALANCE_SHEET),
        )
        assert report.is_balanced is True
        assert report.total_assets == Decimal("0")


# =========================================================================
# TestClassifyForIncomeStatement
# =========================================================================


class TestClassifyForIncomeStatement:
    """Tests for classify_for_income_statement()."""

    def test_revenue_classified(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_income_statement(items, accounts, _config())
        revenue_codes = [i.account_code for i in classified["revenue"]]
        assert "4000" in revenue_codes

    def test_other_income_classified(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_income_statement(items, accounts, _config())
        other_income_codes = [i.account_code for i in classified["other_income"]]
        assert "4300" in other_income_codes  # Interest Income (43xx prefix)

    def test_cogs_classified(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_income_statement(items, accounts, _config())
        cogs_codes = [i.account_code for i in classified["cogs"]]
        assert "5000" in cogs_codes

    def test_operating_expenses_classified(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_income_statement(items, accounts, _config())
        opex_codes = [i.account_code for i in classified["operating_expenses"]]
        assert "5100" in opex_codes  # Salary
        assert "5200" in opex_codes  # Rent
        assert "5300" in opex_codes  # Depreciation

    def test_other_expenses_classified(self):
        accounts = _accounts()
        items = enrich_trial_balance(_balanced_tb(), accounts, _config())
        classified = classify_for_income_statement(items, accounts, _config())
        other_exp_codes = [i.account_code for i in classified["other_expenses"]]
        assert "6000" in other_exp_codes  # Interest Expense


# =========================================================================
# TestBuildIncomeStatement
# =========================================================================


class TestBuildIncomeStatement:
    """Tests for build_income_statement()."""

    def test_single_step(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_income_statement(
            rows, accounts, _config(), _metadata(ReportType.INCOME_STATEMENT),
            format=IncomeStatementFormat.SINGLE_STEP,
        )
        assert report.format == IncomeStatementFormat.SINGLE_STEP
        # Revenue: 100k + 5k = 105k
        assert report.total_revenue == Decimal("105000")
        # Expenses: 40k + 25k + 10k + 5k + 5k = 85k
        assert report.total_expenses == Decimal("85000")
        assert report.net_income == Decimal("20000")
        # Single-step should not have multi-step fields
        assert report.revenue_section is None

    def test_multi_step(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_income_statement(
            rows, accounts, _config(), _metadata(ReportType.INCOME_STATEMENT),
            format=IncomeStatementFormat.MULTI_STEP,
        )
        assert report.format == IncomeStatementFormat.MULTI_STEP
        assert report.revenue_section is not None
        assert report.cogs_section is not None
        # Revenue section: 100k (excludes other income)
        assert report.revenue_section.total == Decimal("100000")
        # COGS: 40k
        assert report.cogs_section.total == Decimal("40000")
        # Gross profit: 100k - 40k = 60k
        assert report.gross_profit == Decimal("60000")
        # Operating expenses: 25k + 10k + 5k = 40k
        assert report.operating_expense_section.total == Decimal("40000")
        # Operating income: 60k - 40k = 20k
        assert report.operating_income == Decimal("20000")
        # Other income: 5k (interest income)
        assert report.other_income_section.total == Decimal("5000")
        # Other expense: 5k (interest expense)
        assert report.other_expense_section.total == Decimal("5000")
        # Income before tax: 20k + 5k - 5k = 20k
        assert report.income_before_tax == Decimal("20000")
        assert report.net_income == Decimal("20000")

    def test_comparative_income_statement(self):
        rows = _balanced_tb()
        accounts = _accounts()
        comp_rows = [
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "80000"),
            _tb_row(COGS_ID, "5000", "COGS", "30000", "0"),
        ]
        report = build_income_statement(
            rows, accounts, _config(), _metadata(ReportType.INCOME_STATEMENT),
            comparative_rows=comp_rows,
        )
        assert report.comparative is not None
        assert report.comparative.total_revenue == Decimal("80000")

    def test_net_income_equals_revenue_minus_expenses(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_income_statement(
            rows, accounts, _config(), _metadata(ReportType.INCOME_STATEMENT),
        )
        assert report.net_income == report.total_revenue - report.total_expenses


# =========================================================================
# TestBuildCashFlowStatement
# =========================================================================


class TestBuildCashFlowStatement:
    """Tests for build_cash_flow_statement()."""

    def test_structure(self):
        accounts = _accounts()
        current = _balanced_tb()
        prior = [
            _tb_row(CASH_ID, "1000", "Cash", "40000", "0"),
            _tb_row(AR_ID, "1100", "AR", "15000", "0"),
            _tb_row(INVENTORY_ID, "1200", "Inventory", "10000", "0"),
            _tb_row(EQUIPMENT_ID, "1500", "Equipment", "30000", "0"),
            _tb_row(AP_ID, "2000", "AP", "0", "8000"),
            _tb_row(NOTES_PAYABLE_ID, "2500", "Notes Payable", "0", "25000"),
            _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "50000"),
            _tb_row(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", "0", "10000"),
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "0"),
            _tb_row(COGS_ID, "5000", "COGS", "0", "0"),
            _tb_row(SALARY_EXPENSE_ID, "5100", "Salary", "0", "0"),
            _tb_row(RENT_EXPENSE_ID, "5200", "Rent", "0", "0"),
            _tb_row(DEPRECIATION_ID, "5300", "Depreciation", "0", "0"),
        ]
        report = build_cash_flow_statement(
            current, prior, accounts, _config(), _metadata(ReportType.CASH_FLOW),
        )
        assert report.operating_adjustments is not None
        assert report.working_capital_changes is not None
        assert report.investing_activities is not None
        assert report.financing_activities is not None

    def test_cash_reconciliation(self):
        accounts = _accounts()
        current = _balanced_tb()
        prior = [
            _tb_row(CASH_ID, "1000", "Cash", "40000", "0"),
            _tb_row(AR_ID, "1100", "AR", "15000", "0"),
            _tb_row(INVENTORY_ID, "1200", "Inventory", "10000", "0"),
            _tb_row(EQUIPMENT_ID, "1500", "Equipment", "30000", "0"),
            _tb_row(AP_ID, "2000", "AP", "0", "8000"),
            _tb_row(NOTES_PAYABLE_ID, "2500", "Notes Payable", "0", "25000"),
            _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "50000"),
            _tb_row(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", "0", "10000"),
        ]
        report = build_cash_flow_statement(
            current, prior, accounts, _config(), _metadata(ReportType.CASH_FLOW),
        )
        # Beginning cash: 40k, ending cash: 50k
        assert report.beginning_cash == Decimal("40000")
        assert report.ending_cash == Decimal("50000")

    def test_depreciation_added_back(self):
        accounts = _accounts()
        current = [
            _tb_row(CASH_ID, "1000", "Cash", "50000", "0"),
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "100000"),
            _tb_row(DEPRECIATION_ID, "5300", "Depreciation", "5000", "0"),
        ]
        prior = [
            _tb_row(CASH_ID, "1000", "Cash", "40000", "0"),
        ]
        report = build_cash_flow_statement(
            current, prior, accounts, _config(), _metadata(ReportType.CASH_FLOW),
        )
        # Depreciation should appear in operating adjustments
        dep_lines = [
            line for line in report.operating_adjustments.lines
            if "Depreciation" in line.description
        ]
        assert len(dep_lines) == 1
        assert dep_lines[0].amount == Decimal("5000")


# =========================================================================
# TestBuildEquityChanges
# =========================================================================


class TestBuildEquityChanges:
    """Tests for build_equity_changes()."""

    def test_beginning_to_ending(self):
        accounts = _accounts()
        current = _balanced_tb()
        prior = [
            _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "50000"),
            _tb_row(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", "0", "10000"),
        ]
        report = build_equity_changes(
            current, prior, accounts, _config(), _metadata(ReportType.EQUITY_CHANGES),
        )
        assert report.beginning_equity == Decimal("60000")
        # Ending = equity accounts (50k + 10k) + net income (20k) = 80k
        assert report.ending_equity == Decimal("80000")

    def test_net_income_in_movements(self):
        accounts = _accounts()
        current = _balanced_tb()
        prior = [
            _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "50000"),
            _tb_row(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", "0", "10000"),
        ]
        report = build_equity_changes(
            current, prior, accounts, _config(), _metadata(ReportType.EQUITY_CHANGES),
        )
        assert report.net_income == Decimal("20000")
        ni_movements = [m for m in report.movements if m.description == "Net Income"]
        assert len(ni_movements) == 1
        assert ni_movements[0].amount == Decimal("20000")

    def test_reconciles(self):
        accounts = _accounts()
        current = _balanced_tb()
        prior = [
            _tb_row(COMMON_STOCK_ID, "3000", "Common Stock", "0", "50000"),
            _tb_row(RETAINED_EARNINGS_ID, "3100", "Retained Earnings", "0", "10000"),
        ]
        report = build_equity_changes(
            current, prior, accounts, _config(), _metadata(ReportType.EQUITY_CHANGES),
        )
        assert report.reconciles is True


# =========================================================================
# TestBuildSegmentReport
# =========================================================================


class TestBuildSegmentReport:
    """Tests for build_segment_report()."""

    def test_basic_segmentation(self):
        accounts = _accounts()
        dept_a = [
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "60000"),
            _tb_row(COGS_ID, "5000", "COGS", "20000", "0"),
        ]
        dept_b = [
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "40000"),
            _tb_row(COGS_ID, "5000", "COGS", "15000", "0"),
        ]
        rows_by_segment = {"Department A": dept_a, "Department B": dept_b}
        report = build_segment_report(
            rows_by_segment, accounts, _config(),
            _metadata(ReportType.SEGMENT), "department",
        )
        assert len(report.segments) == 2
        assert report.dimension_name == "department"

    def test_segment_totals(self):
        accounts = _accounts()
        dept_a = [
            _tb_row(REVENUE_ID, "4000", "Revenue", "0", "60000"),
            _tb_row(COGS_ID, "5000", "COGS", "20000", "0"),
        ]
        rows_by_segment = {"Dept A": dept_a}
        report = build_segment_report(
            rows_by_segment, accounts, _config(),
            _metadata(ReportType.SEGMENT), "department",
        )
        seg = report.segments[0]
        assert seg.total_revenue == Decimal("60000")
        assert seg.total_expenses == Decimal("20000")
        assert seg.net_income == Decimal("40000")

    def test_unallocated(self):
        accounts = _accounts()
        rows_by_segment = {"Dept A": [_tb_row(REVENUE_ID, "4000", "Rev", "0", "50000")]}
        unallocated = [_tb_row(RENT_EXPENSE_ID, "5200", "Rent", "5000", "0")]
        report = build_segment_report(
            rows_by_segment, accounts, _config(),
            _metadata(ReportType.SEGMENT), "department", unallocated,
        )
        assert report.unallocated is not None
        assert report.unallocated.total_expenses == Decimal("5000")


# =========================================================================
# TestRenderToDict
# =========================================================================


class TestRenderToDict:
    """Tests for render_to_dict()."""

    def test_decimal_to_str(self):
        result = render_to_dict(Decimal("123.45"))
        assert result == "123.45"
        assert isinstance(result, str)

    def test_uuid_to_str(self):
        uid = uuid4()
        result = render_to_dict(uid)
        assert result == str(uid)

    def test_date_to_iso(self):
        d = date(2025, 6, 15)
        result = render_to_dict(d)
        assert result == "2025-06-15"

    def test_enum_to_value(self):
        result = render_to_dict(ReportType.BALANCE_SHEET)
        assert result == "balance_sheet"

    def test_tuple_to_list(self):
        result = render_to_dict((1, 2, 3))
        assert result == [1, 2, 3]

    def test_nested_dataclass(self):
        metadata = _metadata()
        result = render_to_dict(metadata)
        assert isinstance(result, dict)
        assert result["entity_name"] == "Test Company"
        assert result["as_of_date"] == "2025-12-31"

    def test_none_preserved(self):
        assert render_to_dict(None) is None

    def test_primitives_preserved(self):
        assert render_to_dict("hello") == "hello"
        assert render_to_dict(42) == 42
        assert render_to_dict(True) is True

    def test_dict_keys_to_str(self):
        result = render_to_dict({1: "a", "b": 2})
        assert "1" in result
        assert "b" in result

    def test_full_report_renders(self):
        rows = _balanced_tb()
        accounts = _accounts()
        report = build_trial_balance(rows, accounts, _config(), _metadata())
        result = render_to_dict(report)
        assert isinstance(result, dict)
        assert result["is_balanced"] is True
        assert isinstance(result["lines"], list)
        # All amounts should be strings
        for line in result["lines"]:
            assert isinstance(line["debit_balance"], str)
            assert isinstance(line["credit_balance"], str)
