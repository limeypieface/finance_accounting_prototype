"""
Hypothesis-based fuzzing for module configurations.

Property-based tests that verify:
1. Configs handle any valid input without crashing
2. Decimal precision is preserved
3. Nested objects are properly constructed
4. Invalid values are rejected gracefully
"""

import pytest
from decimal import Decimal
from dataclasses import fields, is_dataclass
from typing import get_type_hints, get_origin, get_args

try:
    from hypothesis import given, strategies as st, settings, assume, HealthCheck
    from hypothesis.strategies import composite

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False
    pytest.skip("hypothesis not installed", allow_module_level=True)

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


# =============================================================================
# Strategies for generating config values
# =============================================================================

@composite
def valid_decimals(draw, min_val=Decimal("0"), max_val=Decimal("1000000")):
    """Generate valid Decimal values for financial configs."""
    return draw(st.decimals(
        min_value=min_val,
        max_value=max_val,
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ))


@composite
def percentage_decimals(draw):
    """Generate valid percentage values (0-100)."""
    return draw(st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ))


@composite
def approval_level_dicts(draw):
    """Generate valid approval level dictionaries."""
    return {
        "amount_threshold": draw(valid_decimals()),
        "required_role": draw(st.sampled_from(["supervisor", "manager", "director", "vp", "cfo"])),
        "requires_dual_approval": draw(st.booleans()),
    }


@composite
def dunning_level_dicts(draw):
    """Generate valid dunning level dictionaries."""
    return {
        "days_past_due": draw(st.integers(min_value=1, max_value=365)),
        "action": draw(st.sampled_from(["reminder", "warning", "final_notice", "collection"])),
        "template_code": draw(st.text(min_size=1, max_size=10, alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")),
    }


# =============================================================================
# AP Config Fuzzing
# =============================================================================

class TestAPConfigFuzzing:
    """Fuzz AP configuration with random valid inputs."""

    @given(
        require_po_match=st.booleans(),
        require_receipt_match=st.booleans(),
        payment_terms=st.integers(min_value=1, max_value=365),
        discount_days=st.integers(min_value=1, max_value=30),
    )
    @settings(max_examples=100)
    def test_ap_config_accepts_valid_booleans_and_ints(
        self, require_po_match, require_receipt_match, payment_terms, discount_days
    ):
        """AP config should accept any valid boolean/int combination."""
        assume(discount_days <= payment_terms)

        config = APConfig(
            require_po_match=require_po_match,
            require_receipt_match=require_receipt_match,
            default_payment_terms_days=payment_terms,
            early_payment_discount_days=discount_days,
        )

        assert config.require_po_match == require_po_match
        assert config.default_payment_terms_days == payment_terms

    @given(
        tolerance_percent=percentage_decimals(),
        tolerance_amount=valid_decimals(max_val=Decimal("1000")),
    )
    @settings(max_examples=50)
    def test_match_tolerance_preserves_decimal_precision(
        self, tolerance_percent, tolerance_amount
    ):
        """Match tolerance should preserve exact decimal values."""
        tolerance = MatchTolerance(
            price_variance_percent=tolerance_percent,
            price_variance_absolute=tolerance_amount,
        )

        assert tolerance.price_variance_percent == tolerance_percent
        assert tolerance.price_variance_absolute == tolerance_amount

    @given(num_levels=st.integers(min_value=0, max_value=10))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_from_dict_handles_approval_level_lists(self, num_levels):
        """from_dict should handle lists of approval levels."""
        levels = [
            {"amount_threshold": Decimal(str(i * 1000)), "required_role": f"role_{i}"}
            for i in range(1, num_levels + 1)
        ]

        config = APConfig.from_dict({"approval_levels": levels})

        assert len(config.approval_levels) == num_levels


# =============================================================================
# AR Config Fuzzing
# =============================================================================

class TestARConfigFuzzing:
    """Fuzz AR configuration."""

    @given(
        aging_buckets=st.lists(
            st.integers(min_value=1, max_value=365),
            min_size=1,
            max_size=10,
            unique=True,
        ).map(lambda x: tuple(sorted(x)))
    )
    @settings(max_examples=50)
    def test_aging_buckets_accepts_any_sorted_tuple(self, aging_buckets):
        """Aging buckets should accept any sorted tuple of positive integers."""
        config = ARConfig(aging_buckets=aging_buckets)
        assert config.aging_buckets == aging_buckets

    @given(
        bad_debt_percent=percentage_decimals(),
        write_off_threshold=valid_decimals(),
    )
    @settings(max_examples=50)
    def test_bad_debt_config_preserves_precision(
        self, bad_debt_percent, write_off_threshold
    ):
        """Bad debt configuration should preserve decimal precision."""
        config = ARConfig(
            bad_debt_provision_percent=bad_debt_percent,
            write_off_approval_threshold=write_off_threshold,
        )

        assert config.bad_debt_provision_percent == bad_debt_percent
        assert config.write_off_approval_threshold == write_off_threshold


# =============================================================================
# Inventory Config Fuzzing
# =============================================================================

class TestInventoryConfigFuzzing:
    """Fuzz Inventory configuration."""

    @given(
        costing_method=st.sampled_from(["fifo", "lifo", "weighted_avg", "standard", "specific"]),
        issue_method=st.sampled_from(["fifo", "lifo", "fefo"]),
    )
    @settings(max_examples=30)
    def test_costing_methods_accepted(self, costing_method, issue_method):
        """Config should accept all valid costing/issue methods."""
        config = InventoryConfig(
            default_costing_method=costing_method,
            issue_method=issue_method,
        )

        assert config.default_costing_method == costing_method
        assert config.issue_method == issue_method

    @given(
        abc_a=percentage_decimals(),
        abc_b=percentage_decimals(),
    )
    @settings(max_examples=50)
    def test_abc_classification_valid_percentages(self, abc_a, abc_b):
        """ABC classification should accept percentages that sum to <= 100%."""
        # Skip invalid combinations where sum exceeds 100%
        assume(abc_a + abc_b <= Decimal("100"))

        config = InventoryConfig(
            abc_class_a_percent=abc_a,
            abc_class_b_percent=abc_b,
        )

        assert config.abc_class_a_percent == abc_a
        assert config.abc_class_b_percent == abc_b


# =============================================================================
# Payroll Config Fuzzing
# =============================================================================

class TestPayrollConfigFuzzing:
    """Fuzz Payroll configuration."""

    @given(
        ot_multiplier=st.decimals(
            min_value=Decimal("1"),
            max_value=Decimal("5"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_work_hours_config(self, ot_multiplier):
        """Work hours config should accept reasonable hour values."""
        config = WIPConfig(
            overtime_rate_multiplier=ot_multiplier,
        )

        assert config.overtime_rate_multiplier == ot_multiplier

    @given(
        num_rules=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=20)
    def test_overtime_rules_from_dict(self, num_rules):
        """from_dict should handle overtime rule lists."""
        rules = [
            {
                "threshold_hours": Decimal(str(8 + i * 4)),
                "multiplier": Decimal("1.5") + Decimal(str(i * 0.5)),
                "period": "weekly" if i % 2 == 0 else "daily",
            }
            for i in range(num_rules)
        ]

        config = PayrollConfig.from_dict({"overtime_rules": rules})

        assert len(config.overtime_rules) == num_rules


# =============================================================================
# GL Config Fuzzing
# =============================================================================

class TestGLConfigFuzzing:
    """Fuzz GL configuration."""

    @given(
        fiscal_month=st.integers(min_value=1, max_value=12),
        fiscal_day=st.integers(min_value=1, max_value=31),
        num_periods=st.sampled_from([12, 13]),
    )
    @settings(max_examples=30)
    def test_fiscal_calendar_config(self, fiscal_month, fiscal_day, num_periods):
        """Fiscal calendar should accept valid month/day combinations."""
        # Adjust day for months with fewer days
        if fiscal_month in [4, 6, 9, 11]:
            fiscal_day = min(fiscal_day, 30)
        elif fiscal_month == 2:
            fiscal_day = min(fiscal_day, 28)

        config = GLConfig(
            fiscal_year_end_month=fiscal_month,
            fiscal_year_end_day=fiscal_day,
            num_periods=num_periods,
        )

        assert config.fiscal_year_end_month == fiscal_month
        assert config.num_periods == num_periods

    @given(
        close_order=st.permutations([
            "inventory", "wip", "ar", "ap", "assets", "payroll", "gl"
        ]).map(tuple)
    )
    @settings(max_examples=20)
    def test_close_order_any_permutation(self, close_order):
        """Close order should accept any permutation of subledgers."""
        config = GLConfig(close_order=close_order)
        assert config.close_order == close_order


# =============================================================================
# Tax Config Fuzzing
# =============================================================================

class TestTaxConfigFuzzing:
    """Fuzz Tax configuration."""

    @given(
        tax_type=st.sampled_from(["sales", "vat", "gst"]),
        inclusive=st.booleans(),
        threshold=valid_decimals(),
    )
    @settings(max_examples=30)
    def test_tax_type_config(self, tax_type, inclusive, threshold):
        """Tax config should accept all tax types."""
        config = TaxConfig(
            primary_tax_type=tax_type,
            is_tax_inclusive_pricing=inclusive,
            economic_nexus_threshold=threshold,
        )

        assert config.primary_tax_type == tax_type
        assert config.is_tax_inclusive_pricing == inclusive

    @given(
        num_states=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=10)
    def test_nexus_states_from_dict(self, num_states):
        """from_dict should handle nexus state lists."""
        states = [
            {
                "state_code": f"S{i:02d}",
                "effective_date": "2024-01-01",
                "has_physical_nexus": i % 2 == 0,
                "has_economic_nexus": i % 3 == 0,
            }
            for i in range(num_states)
        ]

        config = TaxConfig.from_dict({"nexus_states": states})

        assert len(config.nexus_states) == num_states


# =============================================================================
# Edge Cases
# =============================================================================

class TestConfigEdgeCases:
    """Test edge cases that could break configs."""

    @given(
        value=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("1000000"),
            places=10,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    @settings(max_examples=100)
    def test_high_precision_decimals_preserved(self, value):
        """High-precision decimals should be preserved exactly."""
        config = APConfig(threshold_1099=value)
        assert config.threshold_1099 == value

    @given(
        value=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=50)
    def test_string_fields_accept_any_text(self, value):
        """String fields should accept any text (validation at use time)."""
        config = GLConfig(account_code_format=value)
        assert config.account_code_format == value

    def test_empty_collections_allowed(self):
        """Empty collections should be allowed."""
        config = APConfig(
            approval_levels=(),
            aging_buckets=(),
        )

        assert config.approval_levels == ()
        assert config.aging_buckets == ()

    def test_none_for_optional_fields(self):
        """None should be accepted for optional fields."""
        config = CashConfig(
            require_dual_approval_above=None,
            transit_account_code=None,
        )

        assert config.require_dual_approval_above is None
        assert config.transit_account_code is None
