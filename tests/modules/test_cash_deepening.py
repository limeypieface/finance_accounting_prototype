"""
Tests for Cash Module Deepening.

Validates new methods:
- import_bank_statement: parse MT940, BAI2, CAMT053
- auto_reconcile: auto-match with variance posting
- generate_payment_file: NACHA formatting
- forecast_cash: pure calculation
- record_nsf_return: posts reversal

Also validates helpers:
- parse_mt940, parse_bai2, parse_camt053, format_nacha
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.cash.helpers import (
    format_nacha,
    parse_bai2,
    parse_camt053,
    parse_mt940,
)
from finance_modules.cash.models import (
    BankStatement,
    BankStatementLine,
    CashForecast,
    PaymentFile,
    ReconciliationMatch,
)
from finance_modules.cash.service import CashService
from tests.modules.conftest import TEST_BANK_ACCOUNT_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cash_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide CashService for integration testing."""
    return CashService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestCashModels:
    """Verify new cash models are frozen dataclasses."""

    def test_bank_statement_creation(self):
        stmt = BankStatement(
            id=uuid4(),
            bank_account_id=uuid4(),
            statement_date=date(2024, 1, 31),
            opening_balance=Decimal("10000.00"),
            closing_balance=Decimal("12500.00"),
            line_count=5,
        )
        assert stmt.format == "MT940"
        assert stmt.currency == "USD"

    def test_bank_statement_line_creation(self):
        line = BankStatementLine(
            id=uuid4(),
            statement_id=uuid4(),
            transaction_date=date(2024, 1, 15),
            amount=Decimal("500.00"),
            reference="REF001",
        )
        assert line.transaction_type == "UNKNOWN"

    def test_reconciliation_match_defaults(self):
        match = ReconciliationMatch(
            id=uuid4(),
            statement_line_id=uuid4(),
        )
        assert match.match_confidence == Decimal("1.0")
        assert match.match_method == "manual"

    def test_cash_forecast_creation(self):
        fc = CashForecast(
            period="2024-02",
            opening_balance=Decimal("100000"),
            expected_inflows=Decimal("50000"),
            expected_outflows=Decimal("30000"),
            projected_closing=Decimal("120000"),
        )
        assert fc.currency == "USD"
        assert fc.projected_closing == Decimal("120000")

    def test_payment_file_creation(self):
        pf = PaymentFile(
            id=uuid4(),
            format="NACHA",
            payment_count=3,
            total_amount=Decimal("15000.00"),
            content="FILE_HEADER|Test|123",
        )
        assert pf.format == "NACHA"


# =============================================================================
# Helper Tests — Parsers
# =============================================================================


class TestParserHelpers:
    """Test pure parsing functions."""

    def test_parse_mt940_basic(self):
        data = "2024-01-15|1500.00|REF001|Deposit from client|CREDIT\n2024-01-16|200.00|REF002|Bank fee|DEBIT"
        records = parse_mt940(data)
        assert len(records) == 2
        assert records[0]["amount"] == Decimal("1500.00")
        assert records[0]["reference"] == "REF001"
        assert records[1]["type"] == "DEBIT"

    def test_parse_mt940_skips_colons(self):
        data = ":20:STATEMENT\n2024-01-15|1000.00|REF001|Payment|CREDIT"
        records = parse_mt940(data)
        assert len(records) == 1

    def test_parse_mt940_empty(self):
        records = parse_mt940("")
        assert records == []

    def test_parse_bai2_basic(self):
        data = "01,HEADER\n02,DETAIL\n03,ACCOUNT\n16,20240115,150000,REF001,Deposit"
        records = parse_bai2(data)
        assert len(records) == 1
        assert records[0]["amount"] == Decimal("1500.00")
        assert records[0]["reference"] == "REF001"

    def test_parse_bai2_skips_headers(self):
        data = "01,HEADER\n02,GROUP\n03,ACCOUNT"
        records = parse_bai2(data)
        assert records == []

    def test_parse_camt053_basic(self):
        data = "2024-01-15|2500.00|REF001|Wire transfer|CREDIT\n# Comment line\n2024-01-16|300.00|REF002|Fee|DEBIT"
        records = parse_camt053(data)
        assert len(records) == 2
        assert records[0]["amount"] == Decimal("2500.00")

    def test_parse_camt053_skips_comments(self):
        data = "# Header comment\n2024-01-15|1000.00|REF|Desc|CREDIT"
        records = parse_camt053(data)
        assert len(records) == 1


# =============================================================================
# Helper Tests — NACHA Formatter
# =============================================================================


class TestNACHAFormatter:
    """Test NACHA payment file formatting."""

    def test_format_nacha_basic(self):
        payments = [
            {"name": "Vendor A", "account": "12345", "routing": "021000021", "amount": Decimal("5000.00")},
            {"name": "Vendor B", "account": "67890", "routing": "021000021", "amount": Decimal("3000.00")},
        ]
        content = format_nacha(payments, "Test Corp", "1234567890")
        assert "FILE_HEADER|Test Corp|1234567890" in content
        assert "BATCH_HEADER|PPD|Test Corp" in content
        assert "Vendor A" in content
        assert "Vendor B" in content
        assert "FILE_CONTROL|1|2|8000.00" in content

    def test_format_nacha_empty(self):
        content = format_nacha([], "Test Corp", "123")
        assert "FILE_CONTROL|1|0|0" in content


# =============================================================================
# Integration Tests — Statement Import
# =============================================================================


class TestStatementImport:
    """Tests for import_bank_statement."""

    def test_import_mt940(self, cash_service, test_bank_account, test_actor_id):
        data = "2024-01-15|1500.00|REF001|Deposit|CREDIT\n2024-01-16|200.00|REF002|Fee|DEBIT"
        stmt, lines = cash_service.import_bank_statement(
            raw_data=data,
            format="MT940",
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2024, 1, 31),
            actor_id=test_actor_id,
        )
        assert isinstance(stmt, BankStatement)
        assert stmt.line_count == 2
        assert stmt.format == "MT940"
        assert len(lines) == 2
        assert all(isinstance(l, BankStatementLine) for l in lines)

    def test_import_unsupported_format(self, cash_service, test_bank_account, test_actor_id):
        with pytest.raises(ValueError, match="Unsupported format"):
            cash_service.import_bank_statement(
                raw_data="data",
                format="CSV",
                bank_account_id=TEST_BANK_ACCOUNT_ID,
                statement_date=date(2024, 1, 31),
                actor_id=test_actor_id,
            )


# =============================================================================
# Integration Tests — Auto-Reconciliation
# =============================================================================


class TestAutoReconciliation:
    """Tests for auto_reconcile."""

    def test_auto_reconcile_all_matched(
        self, cash_service, current_period, test_actor_id, test_bank_account, deterministic_clock,
    ):
        """All lines match — no adjustment posting needed."""
        lines = [
            BankStatementLine(id=uuid4(), statement_id=uuid4(),
                              transaction_date=date(2024, 1, 15),
                              amount=Decimal("1000.00"), reference="R1"),
        ]
        book_entries = [{"id": str(uuid4()), "amount": "1000.00"}]

        matches, result = cash_service.auto_reconcile(
            bank_account_id=uuid4(),
            statement_lines=lines,
            book_entries=book_entries,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert len(matches) == 1
        assert matches[0].match_method == "auto_amount"

    def test_auto_reconcile_with_variance(
        self, cash_service, current_period, test_actor_id, test_bank_account, deterministic_clock,
    ):
        """Unmatched lines produce variance posting."""
        lines = [
            BankStatementLine(id=uuid4(), statement_id=uuid4(),
                              transaction_date=date(2024, 1, 15),
                              amount=Decimal("500.00"), reference="R1"),
        ]
        # No matching book entries
        matches, result = cash_service.auto_reconcile(
            bank_account_id=uuid4(),
            statement_lines=lines,
            book_entries=[],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert len(matches) == 0


# =============================================================================
# Integration Tests — Payment File
# =============================================================================


class TestPaymentFileGeneration:
    """Tests for generate_payment_file."""

    def test_generate_nacha(self, cash_service):
        payments = [
            {"name": "Vendor A", "account": "12345", "routing": "021000021", "amount": "5000.00"},
        ]
        pf = cash_service.generate_payment_file(
            payments=payments,
            format="NACHA",
            company_name="Test Corp",
            company_id="123",
        )
        assert isinstance(pf, PaymentFile)
        assert pf.format == "NACHA"
        assert pf.payment_count == 1
        assert pf.total_amount == Decimal("5000.00")
        assert "Vendor A" in pf.content

    def test_generate_unsupported_format(self, cash_service):
        with pytest.raises(ValueError, match="Unsupported payment format"):
            cash_service.generate_payment_file([], "SWIFT", "Corp", "123")


# =============================================================================
# Integration Tests — Cash Forecast
# =============================================================================


class TestCashForecast:
    """Tests for forecast_cash."""

    def test_forecast_basic(self, cash_service):
        forecasts = cash_service.forecast_cash(
            periods=["2024-02", "2024-03", "2024-04"],
            opening_balance=Decimal("100000"),
            expected_inflows_per_period=Decimal("50000"),
            expected_outflows_per_period=Decimal("40000"),
        )
        assert len(forecasts) == 3
        assert all(isinstance(f, CashForecast) for f in forecasts)
        # Each period net +10000
        assert forecasts[0].opening_balance == Decimal("100000")
        assert forecasts[0].projected_closing == Decimal("110000")
        assert forecasts[1].opening_balance == Decimal("110000")
        assert forecasts[1].projected_closing == Decimal("120000")
        assert forecasts[2].projected_closing == Decimal("130000")

    def test_forecast_negative_flow(self, cash_service):
        """Outflows exceed inflows."""
        forecasts = cash_service.forecast_cash(
            periods=["2024-02"],
            opening_balance=Decimal("50000"),
            expected_inflows_per_period=Decimal("10000"),
            expected_outflows_per_period=Decimal("30000"),
        )
        assert forecasts[0].projected_closing == Decimal("30000")


# =============================================================================
# Integration Tests — NSF Return
# =============================================================================


class TestNSFReturn:
    """Tests for record_nsf_return."""

    def test_nsf_return_posts(
        self, cash_service, current_period, test_actor_id, test_bank_account, deterministic_clock,
    ):
        """NSF return posts Dr AR / Cr Cash."""
        result = cash_service.record_nsf_return(
            deposit_id=uuid4(),
            amount=Decimal("2500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
