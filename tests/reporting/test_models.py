"""
Tests for reporting model DTOs.

Verifies frozen dataclass construction, immutability, and enum behavior.
NO database required.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

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


class TestReportEnums:
    """Verify enum values and string behavior."""

    def test_report_type_values(self):
        assert ReportType.TRIAL_BALANCE.value == "trial_balance"
        assert ReportType.BALANCE_SHEET.value == "balance_sheet"
        assert ReportType.INCOME_STATEMENT.value == "income_statement"
        assert ReportType.CASH_FLOW.value == "cash_flow"
        assert ReportType.EQUITY_CHANGES.value == "equity_changes"
        assert ReportType.SEGMENT.value == "segment"

    def test_income_statement_format(self):
        assert IncomeStatementFormat.SINGLE_STEP.value == "single_step"
        assert IncomeStatementFormat.MULTI_STEP.value == "multi_step"

    def test_balance_sheet_format(self):
        assert BalanceSheetFormat.CLASSIFIED.value == "classified"
        assert BalanceSheetFormat.UNCLASSIFIED.value == "unclassified"


class TestReportMetadata:
    """Tests for ReportMetadata construction."""

    def test_basic_construction(self):
        meta = ReportMetadata(
            report_type=ReportType.TRIAL_BALANCE,
            entity_name="Test Corp",
            currency="USD",
            as_of_date=date(2025, 12, 31),
            generated_at="2025-12-31T23:59:59",
        )
        assert meta.entity_name == "Test Corp"
        assert meta.currency == "USD"
        assert meta.period_start is None

    def test_frozen(self):
        meta = ReportMetadata(
            report_type=ReportType.TRIAL_BALANCE,
            entity_name="Test",
            currency="USD",
            as_of_date=date(2025, 1, 1),
            generated_at="2025-01-01T00:00:00",
        )
        with pytest.raises(AttributeError):
            meta.entity_name = "Changed"  # type: ignore[misc]


class TestTrialBalanceLineItem:
    """Tests for TrialBalanceLineItem."""

    def test_construction(self):
        item = TrialBalanceLineItem(
            account_id=uuid4(),
            account_code="1000",
            account_name="Cash",
            account_type="asset",
            debit_balance=Decimal("50000"),
            credit_balance=Decimal("0"),
            net_balance=Decimal("50000"),
        )
        assert item.account_code == "1000"
        assert item.net_balance == Decimal("50000")

    def test_frozen(self):
        item = TrialBalanceLineItem(
            account_id=uuid4(),
            account_code="1000",
            account_name="Cash",
            account_type="asset",
            debit_balance=Decimal("100"),
            credit_balance=Decimal("0"),
            net_balance=Decimal("100"),
        )
        with pytest.raises(AttributeError):
            item.net_balance = Decimal("999")  # type: ignore[misc]


class TestTrialBalanceReport:
    """Tests for TrialBalanceReport."""

    def test_construction(self):
        meta = ReportMetadata(
            report_type=ReportType.TRIAL_BALANCE,
            entity_name="Test",
            currency="USD",
            as_of_date=date(2025, 1, 1),
            generated_at="2025-01-01T00:00:00",
        )
        report = TrialBalanceReport(
            metadata=meta,
            lines=(),
            total_debits=Decimal("0"),
            total_credits=Decimal("0"),
            is_balanced=True,
        )
        assert report.is_balanced is True
        assert report.comparative_lines is None


class TestEquityMovement:
    """Tests for EquityMovement."""

    def test_construction(self):
        m = EquityMovement(description="Net Income", amount=Decimal("50000"))
        assert m.description == "Net Income"
        assert m.amount == Decimal("50000")

    def test_frozen(self):
        m = EquityMovement(description="Test", amount=Decimal("0"))
        with pytest.raises(AttributeError):
            m.amount = Decimal("999")  # type: ignore[misc]


class TestCashFlowLineItem:
    """Tests for CashFlowLineItem."""

    def test_construction(self):
        item = CashFlowLineItem(
            description="Depreciation add-back",
            amount=Decimal("5000"),
        )
        assert item.description == "Depreciation add-back"
        assert item.amount == Decimal("5000")
