"""
Tests for reporting configuration.

Verifies config validation, defaults, and factory methods.
NO database required.
"""

from __future__ import annotations

import pytest

from finance_modules.reporting.config import AccountClassification, ReportingConfig


class TestAccountClassification:
    """Tests for AccountClassification."""

    def test_default_prefixes(self):
        clf = AccountClassification()
        assert "10" in clf.current_asset_prefixes
        assert "15" in clf.non_current_asset_prefixes
        assert "20" in clf.current_liability_prefixes
        assert "25" in clf.non_current_liability_prefixes
        assert "30" in clf.equity_prefixes
        assert "40" in clf.revenue_prefixes
        assert "50" in clf.cogs_prefixes
        assert "51" in clf.operating_expense_prefixes

    def test_matches_prefix(self):
        clf = AccountClassification()
        assert clf.matches_prefix("1000", ("10",)) is True
        assert clf.matches_prefix("2500", ("25",)) is True
        assert clf.matches_prefix("9999", ("10",)) is False

    def test_cash_account_prefixes(self):
        clf = AccountClassification()
        assert clf.matches_prefix("1000", clf.cash_account_prefixes) is True
        assert clf.matches_prefix("1020", clf.cash_account_prefixes) is True
        assert clf.matches_prefix("1100", clf.cash_account_prefixes) is False


class TestReportingConfig:
    """Tests for ReportingConfig."""

    def test_defaults(self):
        config = ReportingConfig.with_defaults()
        assert config.default_currency == "USD"
        assert config.entity_name == "Company"
        assert config.display_precision == 2
        assert config.include_zero_balances is False
        assert config.include_inactive is False

    def test_custom_values(self):
        config = ReportingConfig(
            default_currency="EUR",
            entity_name="Test GmbH",
            display_precision=4,
            include_zero_balances=True,
        )
        assert config.default_currency == "EUR"
        assert config.entity_name == "Test GmbH"
        assert config.display_precision == 4
        assert config.include_zero_balances is True

    def test_negative_precision_rejected(self):
        with pytest.raises(ValueError, match="display_precision"):
            ReportingConfig(display_precision=-1)

    def test_invalid_currency_rejected(self):
        with pytest.raises(ValueError, match="3-letter ISO"):
            ReportingConfig(default_currency="US")

    def test_from_dict(self):
        config = ReportingConfig.from_dict({
            "default_currency": "GBP",
            "entity_name": "UK Ltd",
        })
        assert config.default_currency == "GBP"
        assert config.entity_name == "UK Ltd"

    def test_from_dict_with_classification(self):
        config = ReportingConfig.from_dict({
            "classification": {
                "current_asset_prefixes": ("10", "11"),
            },
        })
        assert config.classification.current_asset_prefixes == ("10", "11")
