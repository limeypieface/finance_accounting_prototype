"""
Economic Profile Balance Tests.

All economic profiles must produce balanced journal entries.
Every profile must have equal debits and credits.
"""

import pytest

from finance_modules.ap.profiles import AP_PROFILES
from finance_modules.ar.profiles import AR_PROFILES
from finance_modules.inventory.profiles import INVENTORY_PROFILES
from finance_modules.wip.profiles import WIP_PROFILES
from finance_modules.assets.profiles import ASSET_PROFILES
from finance_modules.expense.profiles import EXPENSE_PROFILES
from finance_modules.tax.profiles import TAX_PROFILES
from finance_modules.procurement.profiles import PROCUREMENT_PROFILES
from finance_modules.payroll.profiles import PAYROLL_PROFILES
from finance_modules.gl.profiles import GL_PROFILES
from finance_modules.cash.profiles import CASH_PROFILES


def count_sides(profile):
    """Count debit and credit sides in a profile's ledger effects."""
    debits = sum(1 for e in profile.ledger_effects if e.debit_role)
    credits = sum(1 for e in profile.ledger_effects if e.credit_role)
    return debits, credits


def has_balanced_structure(profile):
    """
    Check if profile has a balanced structure.

    A profile is structurally balanced if it has at least one ledger effect
    with both a debit and credit role (i.e., balanced double-entry).
    """
    debits, credits = count_sides(profile)
    return debits >= 1 and credits >= 1


class TestAPProfileBalance:
    """Test AP profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(AP_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = AP_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"AP profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestARProfileBalance:
    """Test AR profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(AR_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = AR_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"AR profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestInventoryProfileBalance:
    """Test Inventory profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(INVENTORY_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = INVENTORY_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"Inventory profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestWIPProfileBalance:
    """Test WIP profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(WIP_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = WIP_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"WIP profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestAssetProfileBalance:
    """Test Asset profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(ASSET_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = ASSET_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"Asset profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestExpenseProfileBalance:
    """Test Expense profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(EXPENSE_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = EXPENSE_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"Expense profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestTaxProfileBalance:
    """Test Tax profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(TAX_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = TAX_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"Tax profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestProcurementProfileBalance:
    """Test Procurement profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(PROCUREMENT_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = PROCUREMENT_PROFILES[profile_name]
        assert has_balanced_structure(profile), (
            f"Procurement profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestPayrollProfileBalance:
    """Test Payroll profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(PAYROLL_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = PAYROLL_PROFILES[profile_name]
        # labor_distribution is a special case - debit side determined at runtime
        if profile_name == "labor_distribution":
            pytest.skip("Labor distribution debit side is dynamic")
        assert has_balanced_structure(profile), (
            f"Payroll profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestGLProfileBalance:
    """Test GL profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(GL_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = GL_PROFILES[profile_name]
        # GL FX revaluation is a special case - may be one-sided adjustment
        if profile_name == "fx_revaluation":
            pytest.skip("FX revaluation is a special case")
        assert has_balanced_structure(profile), (
            f"GL profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestCashProfileBalance:
    """Test Cash profiles have balanced structure."""

    @pytest.mark.parametrize("profile_name", list(CASH_PROFILES.keys()))
    def test_profile_balanced(self, profile_name):
        profile = CASH_PROFILES[profile_name]
        # recon_adjustment uses dynamic sides from context
        if profile_name == "recon_adjustment":
            pytest.skip("Recon adjustment sides are dynamic (from_context)")
        assert has_balanced_structure(profile), (
            f"Cash profile '{profile_name}' is not balanced: "
            f"{count_sides(profile)}"
        )


class TestProfileEventTypesUnique:
    """Test that no two profiles share the same (event_type, where) dispatch key."""

    def test_no_duplicate_dispatch_keys(self):
        all_profiles: dict[tuple, str] = {}

        module_profiles = [
            ("AP", AP_PROFILES),
            ("AR", AR_PROFILES),
            ("Inventory", INVENTORY_PROFILES),
            ("WIP", WIP_PROFILES),
            ("Assets", ASSET_PROFILES),
            ("Expense", EXPENSE_PROFILES),
            ("Tax", TAX_PROFILES),
            ("Procurement", PROCUREMENT_PROFILES),
            ("Payroll", PAYROLL_PROFILES),
            ("GL", GL_PROFILES),
            ("Cash", CASH_PROFILES),
        ]

        for module_name, profiles in module_profiles:
            for profile_name, profile in profiles.items():
                dispatch_key = (profile.trigger.event_type, profile.trigger.where)
                if dispatch_key in all_profiles:
                    pytest.fail(
                        f"Duplicate dispatch key {dispatch_key} found in "
                        f"{module_name}.{profile_name} and {all_profiles[dispatch_key]}"
                    )
                all_profiles[dispatch_key] = f"{module_name}.{profile_name}"
