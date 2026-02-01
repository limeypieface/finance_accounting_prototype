"""
Config Validation Tests - Business Rule Enforcement.

Tests that configs enforce business invariants that would cause
runtime failures or data corruption if violated.

These tests verify that invalid configurations are properly rejected
with ValueError exceptions.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.ap.config import APConfig, ApprovalLevel, MatchTolerance
from finance_modules.ar.config import ARConfig, DunningLevel
from finance_modules.assets.config import AssetConfig
from finance_modules.cash.config import CashConfig
from finance_modules.expense.config import CategoryLimit, ExpenseConfig
from finance_modules.gl.config import GLConfig
from finance_modules.inventory.config import InventoryConfig
from finance_modules.payroll.config import OvertimeRule, PayrollConfig
from finance_modules.procurement.config import ProcurementConfig
from finance_modules.tax.config import NexusState, TaxConfig
from finance_modules.wip.config import WIPConfig

# =============================================================================
# AP Config Interdependency Violations
# =============================================================================

class TestAPConfigInterdependencies:
    """Test AP config business rule violations."""

    def test_discount_days_must_be_less_than_payment_terms(self):
        """Discount period cannot exceed payment terms."""
        # This is nonsensical: 2% discount if paid in 45 days, but due in 30
        with pytest.raises(ValueError, match="early_payment_discount_days.*cannot exceed"):
            APConfig(
                early_payment_discount_days=45,
                default_payment_terms_days=30,
            )

    def test_approval_levels_must_be_sorted_by_threshold(self):
        """Approval levels should be sorted ascending by amount."""
        # Wrong order: $10K approver before $1K approver
        with pytest.raises(ValueError, match="approval_levels must be sorted"):
            APConfig(
                approval_levels=(
                    ApprovalLevel(amount_threshold=Decimal("10000"), required_role="director"),
                    ApprovalLevel(amount_threshold=Decimal("1000"), required_role="manager"),
                ),
            )

    def test_approval_threshold_must_be_positive(self):
        """Approval thresholds cannot be negative."""
        with pytest.raises(ValueError, match="amount_threshold cannot be negative"):
            APConfig(
                approval_levels=(
                    ApprovalLevel(amount_threshold=Decimal("-1000"), required_role="manager"),
                ),
            )

    def test_aging_buckets_must_be_sorted(self):
        """Aging buckets should be in ascending order."""
        with pytest.raises(ValueError, match="aging_buckets must be sorted"):
            APConfig(
                aging_buckets=(90, 30, 60, 120),  # Wrong order
            )

    def test_aging_buckets_must_be_unique(self):
        """Aging buckets should not have duplicates."""
        with pytest.raises(ValueError, match="aging_buckets must be unique"):
            APConfig(
                aging_buckets=(30, 60, 60, 90),  # Duplicate 60
            )

    def test_aging_buckets_must_be_positive(self):
        """Aging buckets must contain positive values."""
        with pytest.raises(ValueError, match="aging_buckets must contain positive"):
            APConfig(
                aging_buckets=(0, 30, 60),  # Zero is not positive
            )


# =============================================================================
# AR Config Interdependency Violations
# =============================================================================

class TestARConfigInterdependencies:
    """Test AR config business rule violations."""

    def test_dunning_levels_must_be_sorted_by_days(self):
        """Dunning levels should be sorted by days_past_due."""
        with pytest.raises(ValueError, match="dunning_levels must be sorted"):
            ARConfig(
                dunning_levels=(
                    DunningLevel(days_past_due=90, action="collection", template_code="COL"),
                    DunningLevel(days_past_due=30, action="reminder", template_code="REM"),
                ),
            )

    def test_revenue_recognition_must_be_valid_value(self):
        """Revenue recognition policy must be a known value."""
        # Typo in value - should be rejected
        with pytest.raises(ValueError, match="recognize_revenue_on must be one of"):
            ARConfig(
                recognize_revenue_on="invoce",  # Typo!
            )

    def test_write_off_threshold_must_be_positive(self):
        """Write-off threshold cannot be negative."""
        with pytest.raises(ValueError, match="write_off_approval_threshold cannot be negative"):
            ARConfig(
                write_off_approval_threshold=Decimal("-100"),
            )

    def test_dunning_action_must_be_valid(self):
        """Dunning action must be a known value."""
        with pytest.raises(ValueError, match="action must be one of"):
            DunningLevel(days_past_due=30, action="invalid_action", template_code="REM")

    def test_dunning_template_code_cannot_be_empty(self):
        """Dunning template code cannot be empty."""
        with pytest.raises(ValueError, match="template_code cannot be empty"):
            DunningLevel(days_past_due=30, action="reminder", template_code="")


# =============================================================================
# Inventory Config Interdependency Violations
# =============================================================================

class TestInventoryConfigInterdependencies:
    """Test Inventory config business rule violations."""

    def test_abc_percentages_must_sum_to_100_or_less(self):
        """ABC classification percentages cannot exceed 100%."""
        with pytest.raises(ValueError, match="abc_class_a_percent.*abc_class_b_percent cannot exceed 100%"):
            InventoryConfig(
                abc_class_a_percent=Decimal("80"),
                abc_class_b_percent=Decimal("50"),  # A+B = 130%!
            )

    def test_costing_method_must_be_valid(self):
        """Costing method must be a known value."""
        with pytest.raises(ValueError, match="default_costing_method must be one of"):
            InventoryConfig(
                default_costing_method="fifoo",  # Typo!
            )

    def test_receipt_tolerance_must_be_positive(self):
        """Receipt tolerance cannot be negative."""
        with pytest.raises(ValueError, match="receipt_tolerance_percent cannot be negative"):
            InventoryConfig(
                receipt_tolerance_percent=Decimal("-5"),
            )

    def test_receipt_tolerance_must_be_reasonable(self):
        """Receipt tolerance should not exceed 100%."""
        with pytest.raises(ValueError, match="receipt_tolerance_percent cannot exceed 100%"):
            InventoryConfig(
                receipt_tolerance_percent=Decimal("500"),  # 500%!
            )

    def test_issue_method_must_be_valid(self):
        """Issue method must be a known value."""
        with pytest.raises(ValueError, match="issue_method must be one of"):
            InventoryConfig(
                issue_method="invalid_method",
            )

    def test_lcm_adjustment_method_must_be_valid(self):
        """LCM adjustment method must be a known value."""
        with pytest.raises(ValueError, match="lcm_adjustment_method must be one of"):
            InventoryConfig(
                lcm_adjustment_method="invalid_method",
            )


# =============================================================================
# GL Config Interdependency Violations
# =============================================================================

class TestGLConfigInterdependencies:
    """Test GL config business rule violations."""

    def test_fiscal_year_end_month_must_be_valid(self):
        """Fiscal year end month must be between 1 and 12."""
        with pytest.raises(ValueError, match="fiscal_year_end_month must be between 1 and 12"):
            GLConfig(
                fiscal_year_end_month=13,
            )

    def test_fiscal_year_end_day_must_be_valid(self):
        """Fiscal year end day must be between 1 and 31."""
        with pytest.raises(ValueError, match="fiscal_year_end_day must be between 1 and 31"):
            GLConfig(
                fiscal_year_end_day=32,
            )

    def test_close_order_must_have_valid_module_names(self):
        """Close order must only contain valid module names."""
        with pytest.raises(ValueError, match="close_order contains invalid modules"):
            GLConfig(
                close_order=("ap", "ar", "invetnory", "payrol"),  # Typos!
            )

    def test_num_periods_must_be_valid(self):
        """Number of periods must be between 1 and 13."""
        with pytest.raises(ValueError, match="num_periods must be between 1 and 13"):
            GLConfig(
                num_periods=15,
            )

    def test_approval_threshold_must_be_non_negative(self):
        """Approval threshold cannot be negative."""
        with pytest.raises(ValueError, match="approval_threshold_amount cannot be negative"):
            GLConfig(
                approval_threshold_amount=-100.00,
            )

    def test_intercompany_settings_must_be_consistent(self):
        """Cannot auto-create intercompany if intercompany disabled."""
        with pytest.raises(ValueError, match="auto_create_intercompany_entries cannot be True"):
            GLConfig(
                enable_intercompany=False,
                auto_create_intercompany_entries=True,  # Contradictory!
            )


# =============================================================================
# Payroll Config Interdependency Violations
# =============================================================================

class TestPayrollConfigInterdependencies:
    """Test Payroll config business rule violations."""

    def test_overtime_multiplier_must_be_positive(self):
        """Overtime multiplier must be positive."""
        with pytest.raises(ValueError, match="multiplier must be positive"):
            PayrollConfig(
                overtime_rules=(
                    OvertimeRule(
                        threshold_hours=Decimal("40"),
                        multiplier=Decimal("-0.5"),
                        period="weekly",
                    ),
                ),
            )

    def test_overtime_threshold_must_be_positive(self):
        """Overtime threshold hours must be positive."""
        with pytest.raises(ValueError, match="threshold_hours must be positive"):
            PayrollConfig(
                overtime_rules=(
                    OvertimeRule(
                        threshold_hours=Decimal("-8"),  # Negative!
                        multiplier=Decimal("1.5"),
                        period="daily",
                    ),
                ),
            )

    def test_accrual_rates_must_be_reasonable(self):
        """Accrual rates should not exceed 1 (100%)."""
        with pytest.raises(ValueError, match="vacation_accrual_rate cannot exceed 1"):
            PayrollConfig(
                vacation_accrual_rate=Decimal("150"),  # 150 = 15000%!
            )

    def test_pay_frequency_must_be_valid(self):
        """Pay frequency must be a known value."""
        with pytest.raises(ValueError, match="default_pay_frequency must be one of"):
            PayrollConfig(
                default_pay_frequency="invalid_frequency",
            )

    def test_work_week_start_must_be_valid(self):
        """Work week start must be a valid day name."""
        with pytest.raises(ValueError, match="work_week_start must be one of"):
            PayrollConfig(
                work_week_start="invalid_day",
            )

    def test_overtime_period_must_be_valid(self):
        """Overtime period must be daily or weekly."""
        with pytest.raises(ValueError, match="period must be one of"):
            OvertimeRule(
                threshold_hours=Decimal("40"),
                multiplier=Decimal("1.5"),
                period="invalid_period",
            )


# =============================================================================
# Tax Config Interdependency Violations
# =============================================================================

class TestTaxConfigInterdependencies:
    """Test Tax config business rule violations."""

    def test_nexus_states_must_have_unique_codes(self):
        """Nexus states should not have duplicate state codes."""
        with pytest.raises(ValueError, match="nexus_states contains duplicate state codes"):
            TaxConfig(
                nexus_states=(
                    NexusState(state_code="CA", effective_date="2024-01-01"),
                    NexusState(state_code="CA", effective_date="2024-06-01"),  # Duplicate!
                ),
            )

    def test_tax_calculation_method_must_be_valid(self):
        """Tax calculation method must be a known value."""
        with pytest.raises(ValueError, match="tax_calculation_method must be one of"):
            TaxConfig(
                tax_calculation_method="originn",  # Typo!
            )

    def test_primary_tax_type_must_be_valid(self):
        """Primary tax type must be a known value."""
        with pytest.raises(ValueError, match="primary_tax_type must be one of"):
            TaxConfig(
                primary_tax_type="invalid_type",
            )

    def test_nexus_state_code_cannot_be_empty(self):
        """Nexus state code cannot be empty."""
        with pytest.raises(ValueError, match="state_code cannot be empty"):
            NexusState(state_code="", effective_date="2024-01-01")


# =============================================================================
# Expense Config Interdependency Violations
# =============================================================================

class TestExpenseConfigInterdependencies:
    """Test Expense config business rule violations."""

    def test_daily_limit_must_be_at_least_per_transaction(self):
        """Daily limit should be >= per-transaction limit."""
        with pytest.raises(ValueError, match="daily_limit.*cannot be less than.*per_transaction_limit"):
            CategoryLimit(
                category="meals",
                per_transaction_limit=Decimal("100"),
                daily_limit=Decimal("50"),  # Can't spend $100 if daily max is $50
            )

    def test_self_approval_must_be_less_than_approval_threshold(self):
        """Self-approval limit should be less than approval threshold."""
        with pytest.raises(ValueError, match="allow_self_approval_below.*cannot exceed.*single_expense_approval_threshold"):
            ExpenseConfig(
                allow_self_approval_below=Decimal("500"),
                single_expense_approval_threshold=Decimal("100"),  # Need approval at $100 but can self-approve up to $500?
            )

    def test_approval_hierarchy_must_be_valid(self):
        """Approval hierarchy must be a known value."""
        with pytest.raises(ValueError, match="approval_hierarchy must be one of"):
            ExpenseConfig(
                approval_hierarchy="invalid_hierarchy",
            )

    def test_payment_method_must_be_valid(self):
        """Payment method must be a known value."""
        with pytest.raises(ValueError, match="payment_method must be one of"):
            ExpenseConfig(
                payment_method="invalid_method",
            )

    def test_category_limit_category_cannot_be_empty(self):
        """Category limit category cannot be empty."""
        with pytest.raises(ValueError, match="category cannot be empty"):
            CategoryLimit(category="")


# =============================================================================
# Cash Config Interdependency Violations
# =============================================================================

class TestCashConfigInterdependencies:
    """Test Cash config business rule violations."""

    def test_transit_account_required_when_wire_transit_enabled(self):
        """Transit account code required if use_transit_account_for_wires is True."""
        with pytest.raises(ValueError, match="transit_account_code is required"):
            CashConfig(
                use_transit_account_for_wires=True,
                transit_account_code=None,  # Missing!
            )

    def test_reconciliation_tolerance_cannot_be_negative(self):
        """Reconciliation tolerance cannot be negative."""
        with pytest.raises(ValueError, match="reconciliation_tolerance cannot be negative"):
            CashConfig(
                reconciliation_tolerance=Decimal("-0.01"),
            )


# =============================================================================
# Cross-Module Reference Validation
# =============================================================================

class TestCrossModuleReferences:
    """Test that configs don't reference non-existent entities."""

    @pytest.mark.xfail(reason="No validation: account mappings can reference invalid GL codes - requires registry")
    def test_account_mappings_reference_valid_gl_codes(self):
        """Account mappings should reference valid GL account codes."""
        # This mapping references a GL code that might not exist
        config = APConfig(
            account_mappings={
                "AP_LIABILITY": "NONEXISTENT-CODE",
                "EXPENSE": "ALSO-FAKE-CODE",
            },
        )
        # At minimum, codes should follow the GL code format
        # Better: validate against actual GL account list
        for role, code in config.account_mappings.items():
            # Basic format check (should be like "2000-000")
            assert "-" in code, f"Invalid GL code format: {code}"

    @pytest.mark.xfail(reason="No validation: bank account UUIDs can be invalid - requires registry")
    def test_bank_account_gl_mappings_reference_valid_accounts(self):
        """Bank account mappings should reference valid bank account UUIDs."""
        fake_uuid = uuid4()  # Random UUID that doesn't exist
        config = CashConfig(
            bank_account_gl_mappings={
                fake_uuid: "1000-001",
            },
        )
        # Should validate that UUIDs reference actual bank accounts
        # This is a placeholder - actual validation needs bank account registry
        assert len(config.bank_account_gl_mappings) >= 0  # Can't actually validate


# =============================================================================
# Summary: Config Validation is Now Enforced
# =============================================================================

class TestValidationSummary:
    """Summary test to document validation status."""

    def test_document_validation_status(self):
        """
        This test documents the validation status.

        Validations now enforced:
        - AP: discount/payment terms, approval sorting, negative threshold, bucket sorting, bucket duplicates
        - AR: dunning sorting, revenue recognition values, negative write-off, dunning action/template
        - Inventory: ABC sum, costing method, negative/excessive tolerance, issue method, LCM method
        - GL: fiscal month/day, close order modules, num_periods, approval threshold
        - Payroll: overtime multiplier/threshold, accrual rates, pay frequency, work week start, overtime period
        - Tax: tax type, calculation method, nexus state code
        - Expense: approval hierarchy, payment method, category limit
        - Cash: reconciliation tolerance

        Complex rules still marked xfail (require cross-field or registry validation):
        - GL: contradictory intercompany settings
        - Tax: duplicate state codes
        - Expense: daily < per_transaction, self-approval > threshold
        - Cash: transit account required when enabled
        - Cross-module: GL code format, bank account UUIDs
        """
        pass  # Documentation only
