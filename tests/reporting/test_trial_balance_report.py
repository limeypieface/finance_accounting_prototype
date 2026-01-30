"""
Integration tests for Trial Balance report generation.

Uses real database with posted journal entries.
Uses module_accounts fixture from conftest.py to avoid code conflicts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finance_modules.reporting.service import ReportingService


@pytest.fixture
def reporting_svc(session, deterministic_clock):
    """Reporting service for integration tests."""
    return ReportingService(session=session, clock=deterministic_clock)


class TestTrialBalanceIntegration:
    """Integration tests for trial balance generation."""

    def test_empty_ledger_produces_empty_tb(
        self, reporting_svc, module_accounts,
    ):
        report = reporting_svc.trial_balance(as_of_date=date(2025, 12, 31))
        assert len(report.lines) == 0
        assert report.is_balanced is True

    def test_posted_entry_appears_in_tb(
        self,
        reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Post a simple journal entry and verify it appears in the TB."""
        result = post_via_coordinator(
            debit_role="CASH",
            credit_role="REVENUE",
            amount=Decimal("10000.00"),
            currency="USD",
        )
        assert result.success

        report = reporting_svc.trial_balance(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True

        # Find cash and revenue lines
        cash_lines = [l for l in report.lines if l.account_code == "1000"]
        rev_lines = [l for l in report.lines if l.account_code == "4000"]
        assert len(cash_lines) == 1
        assert cash_lines[0].debit_balance == Decimal("10000.00")
        assert len(rev_lines) == 1
        assert rev_lines[0].credit_balance == Decimal("10000.00")

    def test_multiple_entries_aggregate(
        self,
        reporting_svc,
        post_via_coordinator,
        current_period,
        register_modules,
    ):
        """Multiple entries to the same accounts aggregate correctly."""
        for _ in range(3):
            result = post_via_coordinator(
                debit_role="CASH",
                credit_role="REVENUE",
                amount=Decimal("5000.00"),
            )
            assert result.success

        report = reporting_svc.trial_balance(as_of_date=date(2025, 12, 31))
        assert report.is_balanced is True
        assert report.total_debits == Decimal("15000.00")
        assert report.total_credits == Decimal("15000.00")

    def test_to_dict_serializes(self, reporting_svc, module_accounts):
        """Verify to_dict produces valid dict from empty TB."""
        report = reporting_svc.trial_balance(as_of_date=date(2025, 12, 31))
        result = reporting_svc.to_dict(report)
        assert isinstance(result, dict)
        assert result["is_balanced"] is True
        assert isinstance(result["lines"], list)
