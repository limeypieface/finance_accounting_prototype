"""
Cross-module reporting integration tests.

Posts events through the full interpretation pipeline and verifies
that they produce correct, balanced financial statements.

This is the primary test file for ensuring that events from multiple
ERP modules (AP, AR, cash, payroll, inventory, GL) translate into
fully qualified financial reports.
Uses module_accounts fixture to avoid duplicate account code conflicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finance_modules.reporting.models import IncomeStatementFormat
from finance_modules.reporting.service import ReportingService


@pytest.fixture
def full_reporting_svc(session, deterministic_clock):
    return ReportingService(session=session, clock=deterministic_clock)


class TestCrossModuleTrialBalance:
    """Full business cycle trial balance tests."""

    def test_initial_investment_tb_balanced(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Owner invest + first revenue: TB must balance."""
        # Owner invests
        post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("100000.00"),
        )
        # First sale
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("50000.00"),
        )

        report = full_reporting_svc.trial_balance(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        assert report.total_debits == report.total_credits
        assert report.total_debits == Decimal("150000.00")

    def test_multiple_event_types_tb_balanced(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Diverse events from multiple modules: TB must still balance."""
        # Capital contribution
        post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("200000.00"),
        )
        # Revenue
        post_via_coordinator(
            debit_role="ACCOUNTS_RECEIVABLE",
            credit_role="REVENUE",
            amount=Decimal("75000.00"),
        )
        # COGS
        post_via_coordinator(
            debit_role="COGS",
            credit_role="INVENTORY",
            amount=Decimal("30000.00"),
        )
        # Salary expense
        post_via_coordinator(
            debit_role="SALARY_EXPENSE",
            credit_role="ACCRUED_LIABILITY",
            amount=Decimal("15000.00"),
        )

        report = full_reporting_svc.trial_balance(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True


class TestCrossModuleBalanceSheet:
    """Balance sheet from cross-module postings."""

    def test_a_equals_l_plus_e_after_diverse_events(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """A = L + E after capital, revenue, expense events."""
        # Capital
        post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("100000.00"),
        )
        # Revenue
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("50000.00"),
        )
        # Expense: Salary
        post_via_coordinator(
            debit_role="SALARY_EXPENSE",
            credit_role="CASH",
            amount=Decimal("20000.00"),
        )

        report = full_reporting_svc.balance_sheet(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        # Assets: Cash = 100k + 50k - 20k = 130k
        assert report.total_assets == Decimal("130000.00")
        # L = 0, E = Retained Earnings 100k + NI (50k - 20k) = 130k
        assert report.total_equity == Decimal("130000.00")

    def test_ap_increases_liabilities(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """AP invoice increases current liabilities on BS."""
        # Capital
        post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("100000.00"),
        )
        # AP invoice: expense / AP
        post_via_coordinator(
            debit_role="EXPENSE",
            credit_role="ACCOUNTS_PAYABLE",
            amount=Decimal("5000.00"),
        )

        report = full_reporting_svc.balance_sheet(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        assert report.total_liabilities == Decimal("5000.00")
        assert report.current_liabilities.total == Decimal("5000.00")


class TestCrossModuleIncomeStatement:
    """Income statement from cross-module postings."""

    def test_net_income_from_diverse_events(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """NI computed correctly with revenue and multiple expense types."""
        # Revenue
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("100000.00"),
        )
        # COGS
        post_via_coordinator(
            debit_role="COGS",
            credit_role="INVENTORY",
            amount=Decimal("40000.00"),
        )
        # Salary
        post_via_coordinator(
            debit_role="SALARY_EXPENSE",
            credit_role="CASH",
            amount=Decimal("25000.00"),
        )
        # Other expense
        post_via_coordinator(
            debit_role="EXPENSE",
            credit_role="CASH",
            amount=Decimal("10000.00"),
        )

        report = full_reporting_svc.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            format=IncomeStatementFormat.MULTI_STEP,
        )
        # Revenue: 100k
        assert report.total_revenue == Decimal("100000.00")
        # Expenses: 40k + 25k + 10k = 75k
        assert report.total_expenses == Decimal("75000.00")
        # NI: 100k - 75k = 25k
        assert report.net_income == Decimal("25000.00")
        # Multi-step: gross profit = 100k - 40k = 60k
        assert report.gross_profit == Decimal("60000.00")

    def test_ar_creates_revenue(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """AR invoice posts revenue on the income statement."""
        post_via_coordinator(
            debit_role="ACCOUNTS_RECEIVABLE",
            credit_role="REVENUE",
            amount=Decimal("30000.00"),
        )

        report = full_reporting_svc.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        assert report.total_revenue == Decimal("30000.00")
        assert report.net_income == Decimal("30000.00")


class TestCrossModuleConsistency:
    """Cross-report consistency between BS and IS."""

    def test_bs_equity_includes_is_net_income(
        self,
        full_reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """BS equity change matches IS net income."""
        # Capital
        post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("100000.00"),
        )
        # Revenue
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("50000.00"),
        )
        # Expense
        post_via_coordinator(
            debit_role="SALARY_EXPENSE",
            credit_role="CASH",
            amount=Decimal("20000.00"),
        )

        bs = full_reporting_svc.balance_sheet(as_of_date=date(2025, 12, 31))
        is_report = full_reporting_svc.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )

        # BS equity = Retained Earnings + NI = 100k + 30k = 130k
        # IS NI = 50k - 20k = 30k
        assert is_report.net_income == Decimal("30000.00")
        assert bs.total_equity == Decimal("130000.00")
        # Equity - Retained Earnings = NI
        retained_earnings_balance = Decimal("100000.00")
        assert bs.total_equity - retained_earnings_balance == is_report.net_income
