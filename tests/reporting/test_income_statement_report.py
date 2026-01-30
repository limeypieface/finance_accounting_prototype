"""
Integration tests for Income Statement report generation.

Verifies net income = revenue - expenses with real posted entries.
Uses module_accounts fixture to avoid duplicate account code conflicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finance_modules.reporting.models import IncomeStatementFormat
from finance_modules.reporting.service import ReportingService


@pytest.fixture
def is_service(session, deterministic_clock):
    return ReportingService(session=session, clock=deterministic_clock)


class TestIncomeStatementIntegration:
    """Integration tests for income statement generation."""

    def test_empty_ledger(self, is_service, module_accounts):
        report = is_service.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        assert report.net_income == Decimal("0")
        assert report.total_revenue == Decimal("0")
        assert report.total_expenses == Decimal("0")

    def test_revenue_only(
        self,
        is_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Revenue with no expenses produces positive NI."""
        result = post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("50000.00"),
        )
        assert result.success

        report = is_service.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        assert report.net_income == Decimal("50000.00")
        assert report.total_revenue == Decimal("50000.00")

    def test_multi_step_format(
        self,
        is_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Multi-step IS shows sections."""
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("100000.00"),
        )

        report = is_service.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            format=IncomeStatementFormat.MULTI_STEP,
        )
        assert report.format == IncomeStatementFormat.MULTI_STEP
        assert report.revenue_section is not None

    def test_single_step_format(
        self,
        is_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Single-step IS has no sub-sections."""
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("50000.00"),
        )

        report = is_service.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            format=IncomeStatementFormat.SINGLE_STEP,
        )
        assert report.format == IncomeStatementFormat.SINGLE_STEP
        assert report.revenue_section is None
        assert report.net_income == Decimal("50000.00")

    def test_net_income_formula(
        self,
        is_service,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """NI always equals total_revenue - total_expenses."""
        post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("80000.00"),
        )
        post_via_coordinator(
            debit_role="COGS",
            credit_role="INVENTORY",
            amount=Decimal("30000.00"),
        )

        report = is_service.income_statement(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )
        assert report.net_income == report.total_revenue - report.total_expenses
        assert report.net_income == Decimal("50000.00")
