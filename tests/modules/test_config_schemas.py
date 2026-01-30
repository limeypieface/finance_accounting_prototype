"""
Configuration Schema Tests.

Tests that all module configs:
1. Have sensible defaults
2. Can be overridden at instantiation
3. Can be loaded from dictionaries
4. Are properly typed
"""

import pytest
from decimal import Decimal

from finance_modules.ap.config import APConfig, MatchTolerance, ApprovalLevel
from finance_modules.ar.config import ARConfig, DunningLevel
from finance_modules.inventory.config import InventoryConfig
from finance_modules.wip.config import WIPConfig
from finance_modules.assets.config import AssetConfig
from finance_modules.expense.config import ExpenseConfig, CategoryLimit
from finance_modules.tax.config import TaxConfig, NexusState
from finance_modules.procurement.config import ProcurementConfig
from finance_modules.payroll.config import PayrollConfig, OvertimeRule
from finance_modules.gl.config import GLConfig
from finance_modules.cash.config import CashConfig


class TestConfigDefaults:
    """Test that all configs can be instantiated with defaults."""

    def test_ap_config_defaults(self):
        config = APConfig.with_defaults()
        assert config.require_po_match is True
        assert config.default_payment_terms_days == 30
        assert config.threshold_1099 == Decimal("600.00")

    def test_ar_config_defaults(self):
        config = ARConfig.with_defaults()
        assert config.enforce_credit_limits is True
        assert config.default_payment_terms_days == 30
        assert config.aging_buckets == (30, 60, 90, 120)

    def test_inventory_config_defaults(self):
        config = InventoryConfig.with_defaults()
        assert config.default_costing_method == "weighted_avg"
        assert config.allow_negative_inventory is False

    def test_wip_config_defaults(self):
        config = WIPConfig.with_defaults()
        assert config.costing_method == "standard"
        assert config.overtime_rate_multiplier == Decimal("1.5")

    def test_asset_config_defaults(self):
        config = AssetConfig.with_defaults()
        assert config.capitalization_threshold == Decimal("5000.00")
        assert config.default_depreciation_method == "straight_line"

    def test_expense_config_defaults(self):
        config = ExpenseConfig.with_defaults()
        assert config.receipt_required_above == Decimal("25.00")
        assert config.mileage_rate_per_mile == Decimal("0.67")

    def test_tax_config_defaults(self):
        config = TaxConfig.with_defaults()
        assert config.primary_tax_type == "sales"
        assert config.economic_nexus_threshold == Decimal("100000.00")

    def test_procurement_config_defaults(self):
        config = ProcurementConfig.with_defaults()
        assert config.require_requisition is True
        assert config.enable_three_way_match is True

    def test_payroll_config_defaults(self):
        config = PayrollConfig.with_defaults()
        assert config.default_pay_frequency == "biweekly"
        assert config.standard_hours_per_week == Decimal("40")

    def test_gl_config_defaults(self):
        config = GLConfig.with_defaults()
        assert config.fiscal_year_end_month == 12
        assert config.allow_unbalanced_entries is False

    def test_cash_config_defaults(self):
        config = CashConfig.with_defaults()
        assert config.reconciliation_tolerance == Decimal("0.01")


class TestConfigOverrides:
    """Test that defaults can be overridden at instantiation."""

    def test_ap_config_override(self):
        config = APConfig(
            require_po_match=False,
            default_payment_terms_days=45,
        )
        assert config.require_po_match is False
        assert config.default_payment_terms_days == 45
        # Other defaults unchanged
        assert config.threshold_1099 == Decimal("600.00")

    def test_ar_config_override(self):
        config = ARConfig(
            enforce_credit_limits=False,
            apply_receipts_by="oldest_first",
        )
        assert config.enforce_credit_limits is False
        assert config.apply_receipts_by == "oldest_first"

    def test_gl_config_override_fiscal_year(self):
        config = GLConfig(
            fiscal_year_end_month=6,
            fiscal_year_end_day=30,
            functional_currency="EUR",
        )
        assert config.fiscal_year_end_month == 6
        assert config.functional_currency == "EUR"


class TestConfigFromDict:
    """Test loading configs from dictionaries."""

    def test_ap_config_from_dict(self):
        data = {
            "require_po_match": False,
            "default_payment_terms_days": 60,
            "match_tolerance": {
                "price_variance_percent": Decimal("0.10"),
                "quantity_variance_percent": Decimal("0.05"),
            },
            "approval_levels": [
                {"amount_threshold": Decimal("5000"), "required_role": "manager"},
            ],
        }
        config = APConfig.from_dict(data)
        assert config.require_po_match is False
        assert config.match_tolerance.price_variance_percent == Decimal("0.10")
        assert len(config.approval_levels) == 1

    def test_ar_config_from_dict_with_dunning(self):
        data = {
            "dunning_levels": [
                {"days_past_due": 30, "action": "reminder", "template_code": "DUN01"},
                {"days_past_due": 60, "action": "warning", "template_code": "DUN02"},
            ],
        }
        config = ARConfig.from_dict(data)
        assert len(config.dunning_levels) == 2
        assert config.dunning_levels[0].days_past_due == 30

    def test_tax_config_from_dict_with_nexus(self):
        data = {
            "primary_tax_type": "vat",
            "is_tax_inclusive_pricing": True,
            "nexus_states": [
                {"state_code": "CA", "effective_date": "2024-01-01", "has_physical_nexus": True},
            ],
        }
        config = TaxConfig.from_dict(data)
        assert config.primary_tax_type == "vat"
        assert len(config.nexus_states) == 1

    def test_payroll_config_from_dict_with_overtime(self):
        data = {
            "california_overtime": True,
            "overtime_rules": [
                {"threshold_hours": Decimal("8"), "multiplier": Decimal("1.5"), "period": "daily"},
                {"threshold_hours": Decimal("12"), "multiplier": Decimal("2.0"), "period": "daily"},
            ],
        }
        config = PayrollConfig.from_dict(data)
        assert config.california_overtime is True
        assert len(config.overtime_rules) == 2


class TestConfigImmutabilityOfDefaults:
    """Ensure modifying one config doesn't affect others."""

    def test_default_factory_isolation(self):
        config1 = APConfig.with_defaults()
        config2 = APConfig.with_defaults()

        # Modify config1's mutable default
        config1.account_mappings["test"] = "1234"

        # config2 should not be affected
        assert "test" not in config2.account_mappings

    def test_nested_object_isolation(self):
        config1 = APConfig.with_defaults()
        config2 = APConfig.with_defaults()

        # These are separate objects
        assert config1.match_tolerance is not config2.match_tolerance
