"""
Comprehensive tests for DCAA compliance functionality.

Tests cover:
1. DCAA v2 schema validation (allowability fields)
2. DCAA profile routing based on allowability
3. Guard enforcement (unallowable costs cannot charge to contracts)
4. Precedence override behavior
5. Segregation of allowable vs unallowable costs
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.schemas.registry import EventSchemaRegistry
from finance_kernel.domain.policy_selector import PolicySelector

# Import DCAA schemas to trigger registration
import finance_kernel.domain.schemas.definitions.dcaa  # noqa: F401

# DCAA profiles now live in finance_modules.contracts
from finance_modules.contracts.profiles import register as register_contracts
register_contracts()


# ============================================================================
# DCAA Schema Tests
# ============================================================================


class TestDCAASchemaRegistration:
    """Tests for DCAA v2 schema registration."""

    def test_ap_invoice_v2_registered(self):
        """ap.invoice_received v2 schema should be registered."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        assert schema is not None
        assert schema.version == 2
        assert "DCAA" in schema.description

    def test_bank_withdrawal_v2_registered(self):
        """bank.withdrawal v2 schema should be registered."""
        schema = EventSchemaRegistry.get("bank.withdrawal", 2)
        assert schema is not None
        assert schema.version == 2
        assert "DCAA" in schema.description

    def test_payroll_timesheet_v2_registered(self):
        """payroll.timesheet v2 schema should be registered."""
        schema = EventSchemaRegistry.get("payroll.timesheet", 2)
        assert schema is not None
        assert schema.version == 2
        assert "DCAA" in schema.description

    def test_payroll_labor_distribution_v2_registered(self):
        """payroll.labor_distribution v2 schema should be registered."""
        schema = EventSchemaRegistry.get("payroll.labor_distribution", 2)
        assert schema is not None
        assert schema.version == 2
        assert "DCAA" in schema.description


class TestDCAAAllowabilityField:
    """Tests for allowability field validation."""

    @pytest.mark.parametrize("allowability", ["ALLOWABLE", "UNALLOWABLE", "CONDITIONAL"])
    def test_valid_allowability_values(self, allowability):
        """All three allowability values should be valid."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        allowability_field = next(
            (f for f in schema.fields if f.name == "allowability"), None
        )
        assert allowability_field is not None
        assert allowability in allowability_field.allowed_values

    def test_allowability_field_required_on_ap_invoice_v2(self):
        """Allowability should be required on ap.invoice_received v2."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        allowability_field = next(
            (f for f in schema.fields if f.name == "allowability"), None
        )
        assert allowability_field is not None
        assert allowability_field.required is True

    def test_allowability_field_optional_on_bank_withdrawal_v2(self):
        """Allowability should be optional on bank.withdrawal v2."""
        schema = EventSchemaRegistry.get("bank.withdrawal", 2)
        allowability_field = next(
            (f for f in schema.fields if f.name == "allowability"), None
        )
        assert allowability_field is not None
        assert allowability_field.required is False

    def test_allowability_field_required_on_timesheet_v2(self):
        """Allowability should be required on payroll.timesheet v2."""
        schema = EventSchemaRegistry.get("payroll.timesheet", 2)
        allowability_field = next(
            (f for f in schema.fields if f.name == "allowability"), None
        )
        assert allowability_field is not None
        assert allowability_field.required is True

    def test_allowability_field_required_on_labor_distribution_v2(self):
        """Allowability should be required on payroll.labor_distribution v2."""
        schema = EventSchemaRegistry.get("payroll.labor_distribution", 2)
        allowability_field = next(
            (f for f in schema.fields if f.name == "allowability"), None
        )
        assert allowability_field is not None
        assert allowability_field.required is True


class TestDCAAUnallowableReasonField:
    """Tests for unallowable_reason field validation."""

    VALID_REASON_CODES = [
        "ENTERTAINMENT",
        "ALCOHOL",
        "LOBBYING",
        "ADVERTISING",
        "BAD_DEBT",
        "CONTRIBUTIONS",
        "FINES_PENALTIES",
        "INTEREST",
        "ORGANIZATION_COSTS",
        "PATENT_COSTS",
        "GOODWILL",
        "TRAVEL_EXCESS",
        "COMPENSATION_EXCESS",
        "OTHER",
    ]

    @pytest.mark.parametrize("reason_code", VALID_REASON_CODES)
    def test_valid_reason_codes(self, reason_code):
        """All FAR 31.205 unallowable reason codes should be valid."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        reason_field = next(
            (f for f in schema.fields if f.name == "unallowable_reason"), None
        )
        assert reason_field is not None
        assert reason_code in reason_field.allowed_values

    def test_unallowable_reason_is_optional(self):
        """Unallowable reason should be optional."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        reason_field = next(
            (f for f in schema.fields if f.name == "unallowable_reason"), None
        )
        assert reason_field is not None
        assert reason_field.required is False
        assert reason_field.nullable is True


class TestDCAAContractFields:
    """Tests for contract-related fields on DCAA schemas."""

    def test_contract_id_field_on_ap_invoice_v2(self):
        """contract_id should be present on ap.invoice_received v2."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        contract_field = next(
            (f for f in schema.fields if f.name == "contract_id"), None
        )
        assert contract_field is not None
        assert contract_field.required is False
        assert contract_field.nullable is True

    def test_contract_line_number_field_on_ap_invoice_v2(self):
        """contract_line_number should be present on ap.invoice_received v2."""
        schema = EventSchemaRegistry.get("ap.invoice_received", 2)
        clin_field = next(
            (f for f in schema.fields if f.name == "contract_line_number"), None
        )
        assert clin_field is not None
        assert clin_field.required is False

    def test_labor_category_field_on_timesheet_v2(self):
        """labor_category should be present on payroll.timesheet v2."""
        schema = EventSchemaRegistry.get("payroll.timesheet", 2)
        labor_cat_field = next(
            (f for f in schema.fields if f.name == "labor_category"), None
        )
        assert labor_cat_field is not None
        assert labor_cat_field.required is False

    def test_indirect_rate_type_field_on_labor_distribution_v2(self):
        """indirect_rate_type should be present on payroll.labor_distribution v2."""
        schema = EventSchemaRegistry.get("payroll.labor_distribution", 2)
        rate_type_field = next(
            (f for f in schema.fields if f.name == "indirect_rate_type"), None
        )
        assert rate_type_field is not None
        assert rate_type_field.required is False
        # Verify allowed values for indirect rate pools
        assert "FRINGE" in rate_type_field.allowed_values
        assert "OVERHEAD" in rate_type_field.allowed_values
        assert "G_AND_A" in rate_type_field.allowed_values
        assert "MATERIAL_HANDLING" in rate_type_field.allowed_values


# ============================================================================
# DCAA Profile Tests
# ============================================================================


class TestDCAAProfileRegistration:
    """Tests for DCAA profile registration."""

    @pytest.mark.parametrize(
        "profile_name",
        [
            "APInvoiceAllowable",
            "APInvoiceUnallowable",
            "APInvoiceConditional",
            "TimesheetAllowable",
            "TimesheetUnallowable",
            "LaborDistDirectAllowable",
            "LaborDistDirectUnallowable",
            "LaborDistIndirectAllowable",
            "LaborDistIndirectUnallowable",
            "BankWithdrawalExpenseAllowable",
            "BankWithdrawalExpenseUnallowable",
        ],
    )
    def test_dcaa_profile_registered(self, profile_name):
        """All DCAA profiles should be registered."""
        profile = PolicySelector.get(profile_name)
        assert profile is not None, f"Profile {profile_name} not found"


class TestDCAAPolicyTriggers:
    """Tests for DCAA profile trigger conditions."""

    def test_ap_invoice_allowable_trigger(self):
        """APInvoiceAllowable should trigger on ALLOWABLE allowability."""
        profile = PolicySelector.get("APInvoiceAllowable")
        assert profile.trigger.event_type == "ap.invoice_received"
        assert profile.trigger.schema_version == 2
        assert ("payload.allowability", "ALLOWABLE") in profile.trigger.where

    def test_ap_invoice_unallowable_trigger(self):
        """APInvoiceUnallowable should trigger on UNALLOWABLE allowability."""
        profile = PolicySelector.get("APInvoiceUnallowable")
        assert profile.trigger.event_type == "ap.invoice_received"
        assert profile.trigger.schema_version == 2
        assert ("payload.allowability", "UNALLOWABLE") in profile.trigger.where

    def test_ap_invoice_conditional_trigger(self):
        """APInvoiceConditional should trigger on CONDITIONAL allowability."""
        profile = PolicySelector.get("APInvoiceConditional")
        assert profile.trigger.event_type == "ap.invoice_received"
        assert profile.trigger.schema_version == 2
        assert ("payload.allowability", "CONDITIONAL") in profile.trigger.where

    def test_timesheet_allowable_trigger(self):
        """TimesheetAllowable should trigger on ALLOWABLE allowability."""
        profile = PolicySelector.get("TimesheetAllowable")
        assert profile.trigger.event_type == "payroll.timesheet"
        assert profile.trigger.schema_version == 2
        assert ("payload.allowability", "ALLOWABLE") in profile.trigger.where

    def test_timesheet_unallowable_trigger(self):
        """TimesheetUnallowable should trigger on UNALLOWABLE allowability."""
        profile = PolicySelector.get("TimesheetUnallowable")
        assert profile.trigger.event_type == "payroll.timesheet"
        assert profile.trigger.schema_version == 2
        assert ("payload.allowability", "UNALLOWABLE") in profile.trigger.where

    def test_labor_dist_direct_allowable_trigger(self):
        """LaborDistDirectAllowable should trigger on DIRECT + ALLOWABLE."""
        profile = PolicySelector.get("LaborDistDirectAllowable")
        assert profile.trigger.event_type == "payroll.labor_distribution"
        assert profile.trigger.schema_version == 2
        assert ("payload.labor_type", "DIRECT") in profile.trigger.where
        assert ("payload.allowability", "ALLOWABLE") in profile.trigger.where

    def test_labor_dist_indirect_unallowable_trigger(self):
        """LaborDistIndirectUnallowable should trigger on INDIRECT + UNALLOWABLE."""
        profile = PolicySelector.get("LaborDistIndirectUnallowable")
        assert profile.trigger.event_type == "payroll.labor_distribution"
        assert profile.trigger.schema_version == 2
        assert ("payload.labor_type", "INDIRECT") in profile.trigger.where
        assert ("payload.allowability", "UNALLOWABLE") in profile.trigger.where

    def test_bank_withdrawal_expense_allowable_trigger(self):
        """BankWithdrawalExpenseAllowable should trigger on EXPENSE + ALLOWABLE."""
        profile = PolicySelector.get("BankWithdrawalExpenseAllowable")
        assert profile.trigger.event_type == "bank.withdrawal"
        assert profile.trigger.schema_version == 2
        assert ("payload.destination_type", "EXPENSE") in profile.trigger.where
        assert ("payload.allowability", "ALLOWABLE") in profile.trigger.where


class TestDCAALedgerEffects:
    """Tests for DCAA ledger effects - verifying cost segregation."""

    def test_allowable_expense_posts_to_allowable_account(self):
        """Allowable costs should post to EXPENSE_ALLOWABLE."""
        profile = PolicySelector.get("APInvoiceAllowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "EXPENSE_ALLOWABLE"
        assert gl_effect.credit_role == "ACCOUNTS_PAYABLE"

    def test_unallowable_expense_posts_to_unallowable_account(self):
        """Unallowable costs should post to EXPENSE_UNALLOWABLE."""
        profile = PolicySelector.get("APInvoiceUnallowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "EXPENSE_UNALLOWABLE"
        assert gl_effect.credit_role == "ACCOUNTS_PAYABLE"

    def test_conditional_expense_posts_to_conditional_account(self):
        """Conditional costs should post to EXPENSE_CONDITIONAL."""
        profile = PolicySelector.get("APInvoiceConditional")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "EXPENSE_CONDITIONAL"
        assert gl_effect.credit_role == "ACCOUNTS_PAYABLE"

    def test_allowable_labor_posts_to_allowable_account(self):
        """Allowable labor should post to LABOR_ALLOWABLE."""
        profile = PolicySelector.get("TimesheetAllowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "LABOR_ALLOWABLE"
        assert gl_effect.credit_role == "ACCRUED_PAYROLL"

    def test_unallowable_labor_posts_to_unallowable_account(self):
        """Unallowable labor should post to LABOR_UNALLOWABLE."""
        profile = PolicySelector.get("TimesheetUnallowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "LABOR_UNALLOWABLE"
        assert gl_effect.credit_role == "ACCRUED_PAYROLL"

    def test_allowable_direct_labor_posts_to_wip(self):
        """Allowable direct labor should post to WIP_DIRECT_LABOR."""
        profile = PolicySelector.get("LaborDistDirectAllowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "WIP_DIRECT_LABOR"
        assert gl_effect.credit_role == "LABOR_CLEARING"

    def test_unallowable_direct_labor_excluded_from_wip(self):
        """Unallowable direct labor should NOT post to WIP."""
        profile = PolicySelector.get("LaborDistDirectUnallowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "LABOR_UNALLOWABLE"
        assert gl_effect.credit_role == "LABOR_CLEARING"

    def test_allowable_indirect_labor_posts_to_overhead_pool(self):
        """Allowable indirect labor should post to OVERHEAD_POOL_ALLOWABLE."""
        profile = PolicySelector.get("LaborDistIndirectAllowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "OVERHEAD_POOL_ALLOWABLE"
        assert gl_effect.credit_role == "LABOR_CLEARING"

    def test_unallowable_indirect_labor_excluded_from_rate_pool(self):
        """Unallowable indirect labor should NOT be in overhead pool."""
        profile = PolicySelector.get("LaborDistIndirectUnallowable")
        gl_effect = next(
            (e for e in profile.ledger_effects if e.ledger == "GL"), None
        )
        assert gl_effect is not None
        assert gl_effect.debit_role == "OVERHEAD_UNALLOWABLE"
        assert gl_effect.credit_role == "LABOR_CLEARING"


class TestDCAAGuardConditions:
    """Tests for DCAA guard conditions."""

    def test_unallowable_invoice_rejects_contract_charge(self):
        """Unallowable AP invoice should reject if contract_id is set."""
        profile = PolicySelector.get("APInvoiceUnallowable")
        contract_guard = next(
            (
                g
                for g in profile.guards
                if g.reason_code == "UNALLOWABLE_TO_CONTRACT"
            ),
            None,
        )
        assert contract_guard is not None
        assert "contract_id" in contract_guard.expression
        assert contract_guard.message == "Unallowable costs cannot be charged to a contract"

    def test_unallowable_timesheet_rejects_contract_charge(self):
        """Unallowable timesheet should reject if contract_id is set."""
        profile = PolicySelector.get("TimesheetUnallowable")
        contract_guard = next(
            (
                g
                for g in profile.guards
                if g.reason_code == "UNALLOWABLE_TO_CONTRACT"
            ),
            None,
        )
        assert contract_guard is not None
        assert "contract_id" in contract_guard.expression

    def test_unallowable_direct_labor_rejects_contract_charge(self):
        """Unallowable direct labor should reject if contract_id is set."""
        profile = PolicySelector.get("LaborDistDirectUnallowable")
        contract_guard = next(
            (
                g
                for g in profile.guards
                if g.reason_code == "UNALLOWABLE_TO_CONTRACT"
            ),
            None,
        )
        assert contract_guard is not None
        assert "contract_id" in contract_guard.expression

    def test_unallowable_bank_withdrawal_rejects_contract_charge(self):
        """Unallowable bank withdrawal should reject if contract_id is set."""
        profile = PolicySelector.get("BankWithdrawalExpenseUnallowable")
        contract_guard = next(
            (
                g
                for g in profile.guards
                if g.reason_code == "UNALLOWABLE_TO_CONTRACT"
            ),
            None,
        )
        assert contract_guard is not None
        assert "contract_id" in contract_guard.expression

    def test_conditional_invoice_requires_reason_code(self):
        """Conditional AP invoice should block if unallowable_reason is missing."""
        profile = PolicySelector.get("APInvoiceConditional")
        reason_guard = next(
            (
                g
                for g in profile.guards
                if g.reason_code == "CONDITIONAL_REQUIRES_REASON"
            ),
            None,
        )
        assert reason_guard is not None
        assert "unallowable_reason" in reason_guard.expression

    def test_frozen_supplier_guard_on_allowable_profiles(self):
        """All AP invoice profiles should have frozen supplier guard."""
        for profile_name in ["APInvoiceAllowable", "APInvoiceUnallowable", "APInvoiceConditional"]:
            profile = PolicySelector.get(profile_name)
            frozen_guard = next(
                (g for g in profile.guards if g.reason_code == "SUPPLIER_FROZEN"),
                None,
            )
            assert frozen_guard is not None, f"{profile_name} missing frozen supplier guard"


class TestDCAAPolicyPrecedence:
    """Tests for DCAA profile precedence override behavior."""

    def test_allowable_ap_invoice_overrides_base(self):
        """APInvoiceAllowable should override base APInvoiceExpense."""
        profile = PolicySelector.get("APInvoiceAllowable")
        assert profile.precedence is not None
        assert profile.precedence.priority == 100
        assert "APInvoiceExpense" in profile.precedence.overrides

    def test_unallowable_ap_invoice_overrides_base(self):
        """APInvoiceUnallowable should override base APInvoiceExpense."""
        profile = PolicySelector.get("APInvoiceUnallowable")
        assert profile.precedence is not None
        assert profile.precedence.priority == 100
        assert "APInvoiceExpense" in profile.precedence.overrides

    def test_allowable_timesheet_overrides_base(self):
        """TimesheetAllowable should override base timesheet profiles."""
        profile = PolicySelector.get("TimesheetAllowable")
        assert profile.precedence is not None
        assert profile.precedence.priority == 100
        assert "TimesheetRegular" in profile.precedence.overrides or \
               "TimesheetOvertime" in profile.precedence.overrides

    def test_direct_labor_allowable_overrides_base(self):
        """LaborDistDirectAllowable should override base LaborDistributionDirect."""
        profile = PolicySelector.get("LaborDistDirectAllowable")
        assert profile.precedence is not None
        assert profile.precedence.priority == 100
        assert "LaborDistributionDirect" in profile.precedence.overrides


class TestDCAADimensionCapture:
    """Tests for dimension capture in DCAA profiles."""

    def test_allowable_expense_captures_contract_dimension(self):
        """Allowable expenses should capture contract_id dimension."""
        profile = PolicySelector.get("APInvoiceAllowable")
        assert "contract_id" in profile.meaning.dimensions

    def test_unallowable_expense_excludes_contract_dimension(self):
        """Unallowable expenses should NOT capture contract_id dimension."""
        profile = PolicySelector.get("APInvoiceUnallowable")
        assert "contract_id" not in profile.meaning.dimensions

    def test_allowable_direct_labor_captures_labor_category(self):
        """Allowable direct labor should capture labor_category dimension."""
        profile = PolicySelector.get("LaborDistDirectAllowable")
        assert "labor_category" in profile.meaning.dimensions

    def test_allowable_indirect_labor_captures_rate_type(self):
        """Allowable indirect labor should capture indirect_rate_type dimension."""
        profile = PolicySelector.get("LaborDistIndirectAllowable")
        assert "indirect_rate_type" in profile.meaning.dimensions


# ============================================================================
# DCAA Integration Tests
# ============================================================================


class TestDCAASchemaProfileAlignment:
    """Tests verifying DCAA schemas and profiles work together."""

    def test_all_allowability_values_have_ap_invoice_profiles(self):
        """Each allowability value should have a matching AP invoice profile."""
        for allowability in ["ALLOWABLE", "UNALLOWABLE", "CONDITIONAL"]:
            profile_name = f"APInvoice{allowability.capitalize()}"
            profile = PolicySelector.get(profile_name)
            assert profile is not None, f"Missing profile for {allowability}"
            # Verify trigger matches
            assert any(
                cond == ("payload.allowability", allowability)
                for cond in profile.trigger.where
            )

    def test_all_allowability_values_have_timesheet_profiles(self):
        """Each allowability value should have a matching timesheet profile."""
        for allowability in ["ALLOWABLE", "UNALLOWABLE"]:
            profile_name = f"Timesheet{allowability.capitalize()}"
            profile = PolicySelector.get(profile_name)
            assert profile is not None, f"Missing profile for {allowability}"

    def test_all_labor_types_have_allowable_unallowable_profiles(self):
        """Each labor type should have allowable and unallowable profiles."""
        for labor_type in ["Direct", "Indirect"]:
            for allowability in ["Allowable", "Unallowable"]:
                profile_name = f"LaborDist{labor_type}{allowability}"
                profile = PolicySelector.get(profile_name)
                assert profile is not None, f"Missing profile {profile_name}"


class TestDCAAComplianceRequirements:
    """High-level tests verifying DCAA compliance requirements are met."""

    def test_unallowable_costs_segregated(self):
        """DCAA Req: Unallowable costs must be segregated in accounting system."""
        # Verify unallowable expense accounts are distinct from allowable
        allowable_profile = PolicySelector.get("APInvoiceAllowable")
        unallowable_profile = PolicySelector.get("APInvoiceUnallowable")

        allowable_debit = next(
            e.debit_role for e in allowable_profile.ledger_effects if e.ledger == "GL"
        )
        unallowable_debit = next(
            e.debit_role for e in unallowable_profile.ledger_effects if e.ledger == "GL"
        )

        assert allowable_debit != unallowable_debit
        assert "ALLOWABLE" in allowable_debit
        assert "UNALLOWABLE" in unallowable_debit

    def test_unallowable_costs_excluded_from_indirect_pools(self):
        """DCAA Req: Unallowable costs cannot be included in indirect rate pools."""
        allowable_profile = PolicySelector.get("LaborDistIndirectAllowable")
        unallowable_profile = PolicySelector.get("LaborDistIndirectUnallowable")

        allowable_debit = next(
            e.debit_role for e in allowable_profile.ledger_effects if e.ledger == "GL"
        )
        unallowable_debit = next(
            e.debit_role for e in unallowable_profile.ledger_effects if e.ledger == "GL"
        )

        # Allowable goes to overhead pool, unallowable does not
        assert "OVERHEAD_POOL" in allowable_debit or "OVERHEAD" in allowable_debit
        assert "UNALLOWABLE" in unallowable_debit

    def test_conditional_costs_tracked_separately(self):
        """DCAA Req: Conditional costs must be tracked separately for audit."""
        conditional_profile = PolicySelector.get("APInvoiceConditional")

        conditional_debit = next(
            e.debit_role for e in conditional_profile.ledger_effects if e.ledger == "GL"
        )

        assert "CONDITIONAL" in conditional_debit

    def test_contract_charging_blocked_for_unallowable(self):
        """DCAA Req: Unallowable costs cannot be charged to contracts."""
        unallowable_profiles = [
            "APInvoiceUnallowable",
            "TimesheetUnallowable",
            "LaborDistDirectUnallowable",
            "BankWithdrawalExpenseUnallowable",
        ]

        for profile_name in unallowable_profiles:
            profile = PolicySelector.get(profile_name)
            contract_guards = [
                g for g in profile.guards
                if "contract_id" in g.expression
            ]
            assert len(contract_guards) > 0, \
                f"{profile_name} missing contract charge rejection"
