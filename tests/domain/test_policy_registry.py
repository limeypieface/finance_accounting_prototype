"""
Tests for Policy Registry.

Tests the governance layer that controls economic authority -
which modules can perform which actions on which ledgers.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from finance_kernel.domain.policy_authority import (
    EconomicCapability,
    EconomicTypeConstraint,
    LedgerRoleMapping,
    ModuleAuthorization,
    ModuleType,
    PolicyAuthority,
    PolicyAuthorityBuilder,
    PolicyViolation,
    create_default_policy_registry,
    create_standard_ap_authorization,
    create_standard_ar_authorization,
    create_standard_inventory_authorization,
)


class TestEconomicCapability:
    """Tests for EconomicCapability enum."""

    def test_all_capabilities_defined(self):
        """Should have all expected capabilities."""
        expected = {
            "create_balance",
            "clear_balance",
            "recognize_revenue",
            "recognize_expense",
            "recognize_gain",
            "recognize_loss",
            "capitalize",
            "depreciate",
            "impair",
            "dispose",
            "accrue",
            "settle",
            "write_off",
            "write_down",
            "adjust",
            "correct",
            "reclassify",
            "intercompany_transfer",
        }

        actual = {cap.value for cap in EconomicCapability}
        assert actual == expected

    def test_capabilities_unique(self):
        """Should have unique values."""
        values = [cap.value for cap in EconomicCapability]
        assert len(values) == len(set(values))


class TestModuleType:
    """Tests for ModuleType enum."""

    def test_all_module_types_defined(self):
        """Should have all expected module types."""
        expected = {
            "gl",
            "ap",
            "ar",
            "inventory",
            "fixed_assets",
            "payroll",
            "bank",
            "tax",
            "manufacturing",
            "projects",
            "intercompany",
        }

        actual = {mt.value for mt in ModuleType}
        assert actual == expected


class TestModuleAuthorization:
    """Tests for ModuleAuthorization."""

    @pytest.fixture
    def ap_authorization(self) -> ModuleAuthorization:
        """Create AP authorization for testing."""
        return ModuleAuthorization(
            module_type=ModuleType.AP,
            capabilities=frozenset({
                EconomicCapability.CREATE_BALANCE,
                EconomicCapability.CLEAR_BALANCE,
                EconomicCapability.RECOGNIZE_EXPENSE,
                EconomicCapability.SETTLE,
            }),
            allowed_ledgers=frozenset({"AP", "GL"}),
            restricted_account_roles=frozenset({"REVENUE", "EQUITY"}),
            effective_from=datetime(2024, 1, 1),
            effective_to=None,
        )

    def test_has_capability_true(self, ap_authorization):
        """Should return True for granted capabilities."""
        assert ap_authorization.has_capability(EconomicCapability.CREATE_BALANCE)
        assert ap_authorization.has_capability(EconomicCapability.SETTLE)

    def test_has_capability_false(self, ap_authorization):
        """Should return False for non-granted capabilities."""
        assert not ap_authorization.has_capability(EconomicCapability.RECOGNIZE_REVENUE)
        assert not ap_authorization.has_capability(EconomicCapability.CAPITALIZE)

    def test_can_post_to_ledger_true(self, ap_authorization):
        """Should return True for allowed ledgers."""
        assert ap_authorization.can_post_to_ledger("AP")
        assert ap_authorization.can_post_to_ledger("GL")

    def test_can_post_to_ledger_false(self, ap_authorization):
        """Should return False for non-allowed ledgers."""
        assert not ap_authorization.can_post_to_ledger("AR")
        assert not ap_authorization.can_post_to_ledger("INVENTORY")

    def test_can_use_role_true(self, ap_authorization):
        """Should return True for non-restricted roles."""
        assert ap_authorization.can_use_role("EXPENSE")
        assert ap_authorization.can_use_role("AP_CONTROL")

    def test_can_use_role_false(self, ap_authorization):
        """Should return False for restricted roles."""
        assert not ap_authorization.can_use_role("REVENUE")
        assert not ap_authorization.can_use_role("EQUITY")

    def test_is_effective_within_window(self, ap_authorization):
        """Should be effective within date window."""
        assert ap_authorization.is_effective(datetime(2024, 6, 15))
        assert ap_authorization.is_effective(datetime(2025, 1, 1))

    def test_is_effective_before_start(self, ap_authorization):
        """Should not be effective before start date."""
        assert not ap_authorization.is_effective(datetime(2023, 12, 31))

    def test_is_effective_with_end_date(self):
        """Should respect end date."""
        auth = ModuleAuthorization(
            module_type=ModuleType.AP,
            capabilities=frozenset({EconomicCapability.CREATE_BALANCE}),
            allowed_ledgers=frozenset({"AP"}),
            effective_from=datetime(2024, 1, 1),
            effective_to=datetime(2024, 12, 31),
        )

        assert auth.is_effective(datetime(2024, 6, 15))
        assert not auth.is_effective(datetime(2025, 1, 1))


class TestEconomicTypeConstraint:
    """Tests for EconomicTypeConstraint."""

    @pytest.fixture
    def ap_invoice_constraint(self) -> EconomicTypeConstraint:
        """Create AP invoice constraint for testing."""
        return EconomicTypeConstraint(
            economic_type="ap.invoice",
            required_ledgers=frozenset({"AP"}),
            optional_ledgers=frozenset({"GL"}),
            forbidden_ledgers=frozenset({"AR", "INVENTORY"}),
        )

    def test_validate_required_present(self, ap_invoice_constraint):
        """Should pass when required ledgers present."""
        errors = ap_invoice_constraint.validate_ledgers(frozenset({"AP", "GL"}))
        assert len(errors) == 0

    def test_validate_required_missing(self, ap_invoice_constraint):
        """Should fail when required ledgers missing."""
        errors = ap_invoice_constraint.validate_ledgers(frozenset({"GL"}))
        assert len(errors) == 1
        assert "Missing required ledgers" in errors[0]

    def test_validate_forbidden_present(self, ap_invoice_constraint):
        """Should fail when forbidden ledgers present."""
        errors = ap_invoice_constraint.validate_ledgers(frozenset({"AP", "AR"}))
        assert len(errors) == 1
        assert "Forbidden ledgers" in errors[0]

    def test_validate_unknown_ledger(self, ap_invoice_constraint):
        """Should fail for unknown ledgers."""
        errors = ap_invoice_constraint.validate_ledgers(frozenset({"AP", "UNKNOWN"}))
        assert len(errors) == 1
        assert "Unknown ledgers" in errors[0]

    def test_validate_multiple_violations(self, ap_invoice_constraint):
        """Should return multiple errors for multiple violations."""
        # Missing required, has forbidden, has unknown
        errors = ap_invoice_constraint.validate_ledgers(
            frozenset({"GL", "AR", "UNKNOWN"})
        )
        assert len(errors) == 3


class TestLedgerRoleMapping:
    """Tests for LedgerRoleMapping."""

    def test_create_mapping(self):
        """Should create ledger role mapping."""
        mapping = LedgerRoleMapping(
            ledger_id="AP",
            role_code="AP_CONTROL",
            control_account_code="2100",
            is_debit_normal=False,
            effective_from=datetime(2024, 1, 1),
        )

        assert mapping.ledger_id == "AP"
        assert mapping.role_code == "AP_CONTROL"
        assert mapping.control_account_code == "2100"
        assert not mapping.is_debit_normal

    def test_mapping_immutable(self):
        """Should be immutable."""
        mapping = LedgerRoleMapping(
            ledger_id="AP",
            role_code="AP_CONTROL",
            control_account_code="2100",
            is_debit_normal=False,
        )

        with pytest.raises(AttributeError):
            mapping.control_account_code = "2200"


class TestPolicyAuthority:
    """Tests for PolicyAuthority."""

    @pytest.fixture
    def policy_registry(self) -> PolicyAuthority:
        """Create policy registry for testing."""
        return create_default_policy_registry()

    def test_get_module_authorization(self, policy_registry):
        """Should retrieve module authorization."""
        ap_auth = policy_registry.get_module_authorization(ModuleType.AP)

        assert ap_auth is not None
        assert ap_auth.module_type == ModuleType.AP
        assert ap_auth.has_capability(EconomicCapability.RECOGNIZE_EXPENSE)

    def test_get_module_authorization_missing(self, policy_registry):
        """Should return None for missing module."""
        auth = policy_registry.get_module_authorization(ModuleType.PROJECTS)
        assert auth is None

    def test_get_ledger_role_mapping(self, policy_registry):
        """Should retrieve ledger role mapping."""
        mapping = policy_registry.get_ledger_role_mapping("AP", "AP_CONTROL")

        assert mapping is not None
        assert mapping.control_account_code == "2100"

    def test_get_ledger_role_mapping_missing(self, policy_registry):
        """Should return None for missing mapping."""
        mapping = policy_registry.get_ledger_role_mapping("UNKNOWN", "UNKNOWN")
        assert mapping is None

    def test_get_economic_type_constraint(self, policy_registry):
        """Should retrieve economic type constraint."""
        constraint = policy_registry.get_economic_type_constraint("ap.invoice")

        assert constraint is not None
        assert "AP" in constraint.required_ledgers

    def test_validate_module_action_success(self, policy_registry):
        """Should pass for valid module actions."""
        violations = policy_registry.validate_module_action(
            module_type=ModuleType.AP,
            capability=EconomicCapability.RECOGNIZE_EXPENSE,
            ledger_id="AP",
        )

        assert len(violations) == 0

    def test_validate_module_action_no_authorization(self, policy_registry):
        """Should fail for unauthorized module."""
        violations = policy_registry.validate_module_action(
            module_type=ModuleType.PROJECTS,  # No authorization
            capability=EconomicCapability.CREATE_BALANCE,
            ledger_id="GL",
        )

        assert len(violations) == 1
        assert violations[0].policy_type == "module_authorization"

    def test_validate_module_action_missing_capability(self, policy_registry):
        """Should fail for missing capability."""
        violations = policy_registry.validate_module_action(
            module_type=ModuleType.AP,
            capability=EconomicCapability.RECOGNIZE_REVENUE,  # AP can't do this
            ledger_id="AP",
        )

        assert len(violations) >= 1
        capability_violations = [v for v in violations if v.policy_type == "capability"]
        assert len(capability_violations) == 1

    def test_validate_module_action_wrong_ledger(self, policy_registry):
        """Should fail for unauthorized ledger."""
        violations = policy_registry.validate_module_action(
            module_type=ModuleType.AP,
            capability=EconomicCapability.RECOGNIZE_EXPENSE,
            ledger_id="AR",  # AP can't post to AR
        )

        assert len(violations) >= 1
        ledger_violations = [v for v in violations if v.policy_type == "ledger_access"]
        assert len(ledger_violations) == 1

    def test_validate_economic_type_posting_success(self, policy_registry):
        """Should pass for valid economic type posting."""
        violations = policy_registry.validate_economic_type_posting(
            economic_type="ap.invoice",
            target_ledgers=frozenset({"AP", "GL"}),
        )

        assert len(violations) == 0

    def test_validate_economic_type_posting_missing_required(self, policy_registry):
        """Should fail when required ledger missing."""
        violations = policy_registry.validate_economic_type_posting(
            economic_type="ap.invoice",
            target_ledgers=frozenset({"GL"}),  # Missing AP
        )

        assert len(violations) == 1
        assert "Missing required ledgers" in violations[0].message


class TestPolicyAuthorityBuilder:
    """Tests for PolicyAuthorityBuilder."""

    def test_build_empty_registry(self):
        """Should build empty registry."""
        registry = PolicyAuthorityBuilder().build()

        assert registry.version == 1
        assert len(registry.module_authorizations) == 0
        assert len(registry.ledger_role_mappings) == 0
        assert len(registry.economic_type_constraints) == 0

    def test_build_with_module_authorization(self):
        """Should add module authorization."""
        registry = (
            PolicyAuthorityBuilder(version=2)
            .authorize_module(
                module_type=ModuleType.AP,
                capabilities=frozenset({EconomicCapability.CREATE_BALANCE}),
                allowed_ledgers=frozenset({"AP"}),
            )
            .build()
        )

        assert registry.version == 2
        assert len(registry.module_authorizations) == 1

        ap_auth = registry.get_module_authorization(ModuleType.AP)
        assert ap_auth is not None

    def test_build_with_ledger_mapping(self):
        """Should add ledger role mapping."""
        registry = (
            PolicyAuthorityBuilder()
            .map_ledger_role("AP", "AP_CONTROL", "2100", is_debit_normal=False)
            .build()
        )

        mapping = registry.get_ledger_role_mapping("AP", "AP_CONTROL")
        assert mapping is not None
        assert mapping.control_account_code == "2100"

    def test_build_with_economic_constraint(self):
        """Should add economic type constraint."""
        registry = (
            PolicyAuthorityBuilder()
            .constrain_economic_type(
                economic_type="custom.event",
                required_ledgers=frozenset({"GL"}),
                optional_ledgers=frozenset({"AP"}),
                forbidden_ledgers=frozenset({"AR"}),
            )
            .build()
        )

        constraint = registry.get_economic_type_constraint("custom.event")
        assert constraint is not None
        assert "GL" in constraint.required_ledgers

    def test_fluent_chain(self):
        """Should support fluent method chaining."""
        registry = (
            PolicyAuthorityBuilder(version=3)
            .effective_from(datetime(2024, 1, 1))
            .authorize_module(ModuleType.GL, frozenset(), frozenset())
            .authorize_module(ModuleType.AP, frozenset(), frozenset())
            .map_ledger_role("GL", "CASH", "1000", True)
            .constrain_economic_type("test.event", frozenset({"GL"}))
            .build()
        )

        assert registry.version == 3
        assert len(registry.module_authorizations) == 2
        assert len(registry.ledger_role_mappings) == 1
        assert len(registry.economic_type_constraints) == 1


class TestStandardAuthorizations:
    """Tests for standard module authorization factories."""

    def test_ap_authorization(self):
        """Should create valid AP authorization."""
        auth = create_standard_ap_authorization()

        assert auth.module_type == ModuleType.AP
        assert auth.has_capability(EconomicCapability.RECOGNIZE_EXPENSE)
        assert auth.has_capability(EconomicCapability.SETTLE)
        assert not auth.has_capability(EconomicCapability.RECOGNIZE_REVENUE)
        assert auth.can_post_to_ledger("AP")
        assert auth.can_post_to_ledger("GL")
        assert not auth.can_post_to_ledger("AR")

    def test_ar_authorization(self):
        """Should create valid AR authorization."""
        auth = create_standard_ar_authorization()

        assert auth.module_type == ModuleType.AR
        assert auth.has_capability(EconomicCapability.RECOGNIZE_REVENUE)
        assert auth.has_capability(EconomicCapability.WRITE_OFF)
        assert not auth.has_capability(EconomicCapability.CAPITALIZE)
        assert auth.can_post_to_ledger("AR")
        assert auth.can_post_to_ledger("GL")
        assert not auth.can_post_to_ledger("AP")

    def test_inventory_authorization(self):
        """Should create valid Inventory authorization."""
        auth = create_standard_inventory_authorization()

        assert auth.module_type == ModuleType.INVENTORY
        assert auth.has_capability(EconomicCapability.CAPITALIZE)
        assert auth.has_capability(EconomicCapability.RECOGNIZE_EXPENSE)  # COGS
        assert not auth.has_capability(EconomicCapability.RECOGNIZE_REVENUE)
        assert auth.can_post_to_ledger("INVENTORY")
        assert auth.can_post_to_ledger("GL")


class TestDefaultPolicyAuthority:
    """Tests for the default policy registry configuration."""

    def test_gl_has_full_access(self):
        """GL module should have full capabilities."""
        registry = create_default_policy_registry()
        gl_auth = registry.get_module_authorization(ModuleType.GL)

        assert gl_auth is not None
        # GL should have all capabilities
        for cap in EconomicCapability:
            assert gl_auth.has_capability(cap)

    def test_modules_have_separation_of_duties(self):
        """Modules should have proper separation of duties."""
        registry = create_default_policy_registry()

        ap = registry.get_module_authorization(ModuleType.AP)
        ar = registry.get_module_authorization(ModuleType.AR)
        inv = registry.get_module_authorization(ModuleType.INVENTORY)

        # AP cannot recognize revenue
        assert not ap.has_capability(EconomicCapability.RECOGNIZE_REVENUE)

        # AR cannot touch inventory
        assert not ar.can_use_role("INVENTORY")

        # Inventory cannot book revenue directly
        assert "REVENUE" in inv.restricted_account_roles

    def test_standard_control_account_mappings(self):
        """Should have standard control account mappings."""
        registry = create_default_policy_registry()

        ap_control = registry.get_ledger_role_mapping("AP", "AP_CONTROL")
        ar_control = registry.get_ledger_role_mapping("AR", "AR_CONTROL")
        inv_control = registry.get_ledger_role_mapping("INVENTORY", "INV_CONTROL")

        assert ap_control is not None
        assert ar_control is not None
        assert inv_control is not None

        # Verify normal balance sides
        assert not ap_control.is_debit_normal  # Liability
        assert ar_control.is_debit_normal  # Asset
        assert inv_control.is_debit_normal  # Asset
