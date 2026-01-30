"""
Integration tests for Balance Sheet report generation.

Verifies A = L + E with real posted journal entries.
Uses module_accounts fixture to avoid duplicate account code conflicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finance_modules.reporting.service import ReportingService


@pytest.fixture
def bs_service(session, deterministic_clock):
    return ReportingService(session=session, clock=deterministic_clock)


class TestBalanceSheetIntegration:
    """Integration tests for balance sheet generation."""

    def test_empty_ledger_is_balanced(self, bs_service, module_accounts):
        report = bs_service.balance_sheet(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        assert report.total_assets == Decimal("0")
        assert report.total_liabilities_and_equity == Decimal("0")

    def test_simple_equity_posting_balanced(
        self,
        bs_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Dr Cash / Cr Retained Earnings produces balanced BS."""
        result = post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("50000.00"),
        )
        assert result.success

        report = bs_service.balance_sheet(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        assert report.total_assets == Decimal("50000.00")

    def test_revenue_affects_equity_via_net_income(
        self,
        bs_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Revenue posting increases equity via net income."""
        # Capital (via retained earnings)
        result = post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("100000.00"),
        )
        assert result.success

        # Revenue earned
        result = post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("25000.00"),
        )
        assert result.success

        report = bs_service.balance_sheet(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        # Assets: Cash = 100k + 25k = 125k
        assert report.total_assets == Decimal("125000.00")

    def test_current_vs_non_current_section_labels(
        self,
        bs_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """BS should have current/non-current section labels."""
        post_via_coordinator(
            debit_role="CASH",
            credit_role="RETAINED_EARNINGS",
            amount=Decimal("100000.00"),
        )

        report = bs_service.balance_sheet(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        assert report.current_assets.label == "Current Assets"
        assert report.non_current_assets.label == "Non-Current Assets"
        assert report.current_liabilities.label == "Current Liabilities"
        assert report.non_current_liabilities.label == "Non-Current Liabilities"
        assert report.equity.label == "Equity"
