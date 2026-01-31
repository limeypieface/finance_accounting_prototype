"""
Tests for AccountingPolicy, PolicySelector, and PolicyCompiler.

Tests cover:
- AccountingPolicy data structure and validation
- PolicySelector registration and lookup
- Precedence resolution (P1)
- PolicyCompiler validation (P1, P7, P10)
"""

from datetime import date
from decimal import Decimal

import pytest

from finance_kernel.domain.dtos import ValidationError
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PrecedenceMode,
    PolicyMeaning,
    PolicyPrecedence,
    PolicyTrigger,
)
from finance_kernel.domain.ledger_registry import LedgerRegistry
from finance_kernel.domain.policy_compiler import PolicyCompiler
from finance_kernel.domain.policy_selector import (
    MultiplePoliciesMatchError,
    PolicyAlreadyRegisteredError,
    PolicyNotFoundError,
    PolicySelector,
)
from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def clear_registries():
    """Save, clear, and restore registries for test isolation."""
    saved_profiles = {k: dict(v) for k, v in PolicySelector._profiles.items()}
    saved_by_event = {k: list(v) for k, v in PolicySelector._by_event_type.items()}
    saved_schemas = {k: dict(v) for k, v in EventSchemaRegistry._schemas.items()}
    PolicySelector.clear()
    EventSchemaRegistry.clear()
    LedgerRegistry.clear()
    yield
    PolicySelector.clear()
    EventSchemaRegistry.clear()
    LedgerRegistry.clear()
    PolicySelector._profiles.update(saved_profiles)
    PolicySelector._by_event_type.update(saved_by_event)
    EventSchemaRegistry._schemas.update(saved_schemas)


@pytest.fixture
def simple_profile() -> AccountingPolicy:
    """Simple profile for basic tests."""
    return AccountingPolicy(
        name="TestProfile",
        version=1,
        trigger=PolicyTrigger(
            event_type="test.event",
            schema_version=1,
        ),
        meaning=PolicyMeaning(
            economic_type="TestType",
            quantity_field="payload.quantity",
        ),
        ledger_effects=(
            LedgerEffect(
                ledger="GL",
                debit_role="Asset",
                credit_role="Liability",
            ),
        ),
        effective_from=date(2024, 1, 1),
    )


@pytest.fixture
def test_schema() -> EventSchema:
    """Event schema for testing field validation."""
    return EventSchema(
        event_type="test.event",
        version=1,
        fields=(
            EventFieldSchema(
                name="quantity",
                field_type=EventFieldType.DECIMAL,
                required=True,
            ),
            EventFieldSchema(
                name="currency",
                field_type=EventFieldType.CURRENCY,
                required=True,
            ),
        ),
    )


# ============================================================================
# AccountingPolicy Tests
# ============================================================================


class TestAccountingPolicy:
    """Tests for AccountingPolicy dataclass."""

    def test_basic_profile(self, simple_profile):
        """Profile with required fields."""
        assert simple_profile.name == "TestProfile"
        assert simple_profile.version == 1
        assert simple_profile.trigger.event_type == "test.event"
        assert simple_profile.meaning.economic_type == "TestType"

    def test_profile_key(self, simple_profile):
        """Profile key is name:vN."""
        assert simple_profile.profile_key == "TestProfile:v1"

    def test_requires_name(self):
        """Profile requires name."""
        with pytest.raises(ValueError, match="name is required"):
            AccountingPolicy(
                name="",
                version=1,
                trigger=PolicyTrigger(event_type="test.event"),
                meaning=PolicyMeaning(economic_type="Test"),
                ledger_effects=(),
                effective_from=date(2024, 1, 1),
            )

    def test_requires_positive_version(self):
        """Version must be >= 1."""
        with pytest.raises(ValueError, match="version must be >= 1"):
            AccountingPolicy(
                name="Test",
                version=0,
                trigger=PolicyTrigger(event_type="test.event"),
                meaning=PolicyMeaning(economic_type="Test"),
                ledger_effects=(),
                effective_from=date(2024, 1, 1),
            )

    def test_is_effective_on(self, simple_profile):
        """Check effective date range."""
        # Before effective_from
        assert not simple_profile.is_effective_on(date(2023, 12, 31))
        # On effective_from
        assert simple_profile.is_effective_on(date(2024, 1, 1))
        # After effective_from (open-ended)
        assert simple_profile.is_effective_on(date(2030, 1, 1))

    def test_is_effective_on_with_end_date(self):
        """Check effective date range with end date."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 12, 31),
        )
        assert not profile.is_effective_on(date(2023, 12, 31))
        assert profile.is_effective_on(date(2024, 6, 15))
        assert not profile.is_effective_on(date(2025, 1, 1))

    def test_matches_scope_wildcard(self, simple_profile):
        """Wildcard scope matches everything."""
        assert simple_profile.matches_scope("anything")
        assert simple_profile.matches_scope("SKU:ABC")

    def test_matches_scope_prefix(self):
        """Prefix scope matches prefix."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            scope="SKU:*",
        )
        assert profile.matches_scope("SKU:ABC")
        assert profile.matches_scope("SKU:123")
        assert not profile.matches_scope("PROJECT:ABC")

    def test_matches_scope_exact(self):
        """Exact scope only matches exact value."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            scope="SKU:ABC",
        )
        assert profile.matches_scope("SKU:ABC")
        assert not profile.matches_scope("SKU:DEF")

    def test_get_field_references(self):
        """Extract field references from profile."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(
                event_type="test.event",
                where=(("payload.status", "ACTIVE"),),
            ),
            meaning=PolicyMeaning(
                economic_type="Test",
                quantity_field="payload.quantity",
                dimensions=("payload.sku", "location"),
            ),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
        )
        refs = profile.get_field_references()
        assert "payload.status" in refs
        assert "payload.quantity" in refs
        assert "payload.sku" in refs

    def test_guards(self):
        """Profile with guards."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="payload.quantity <= 0",
                    reason_code="INVALID_QUANTITY",
                ),
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="reference_data_missing",
                    reason_code="MISSING_REF_DATA",
                ),
            ),
        )
        assert len(profile.get_reject_guards()) == 1
        assert len(profile.get_block_guards()) == 1


# ============================================================================
# PolicySelector Tests
# ============================================================================


class TestPolicySelector:
    """Tests for PolicySelector."""

    def test_register_and_get(self, simple_profile):
        """Register and retrieve a profile."""
        PolicySelector.register(simple_profile)
        retrieved = PolicySelector.get("TestProfile", version=1)
        assert retrieved is simple_profile

    def test_get_latest_version(self):
        """get() without version returns latest."""
        v1 = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
        )
        v2 = AccountingPolicy(
            name="Test",
            version=2,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
        )
        PolicySelector.register(v1)
        PolicySelector.register(v2)

        latest = PolicySelector.get("Test")
        assert latest.version == 2

    def test_duplicate_registration_raises(self, simple_profile):
        """Cannot register same name+version twice."""
        PolicySelector.register(simple_profile)
        with pytest.raises(PolicyAlreadyRegisteredError):
            PolicySelector.register(simple_profile)

    def test_find_for_event(self, simple_profile):
        """Find matching profile for event."""
        PolicySelector.register(simple_profile)

        found = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )
        assert found is simple_profile

    def test_find_for_event_not_found(self):
        """Raises when no profile matches."""
        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                event_type="nonexistent.event",
                effective_date=date(2024, 1, 1),
            )

    def test_find_for_event_not_effective(self, simple_profile):
        """Raises when no profile is effective on date."""
        PolicySelector.register(simple_profile)

        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                event_type="test.event",
                effective_date=date(2023, 1, 1),  # Before effective_from
            )

    def test_precedence_by_scope_specificity(self):
        """More specific scope wins."""
        general = AccountingPolicy(
            name="General",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            scope="*",
        )
        specific = AccountingPolicy(
            name="Specific",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            scope="SKU:*",
        )
        PolicySelector.register(general)
        PolicySelector.register(specific)

        found = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
            scope_value="SKU:ABC",
        )
        assert found.name == "Specific"

    def test_precedence_by_priority(self):
        """Higher priority wins."""
        low = AccountingPolicy(
            name="LowPriority",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            precedence=PolicyPrecedence(priority=0),
        )
        high = AccountingPolicy(
            name="HighPriority",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            precedence=PolicyPrecedence(priority=10),
        )
        PolicySelector.register(low)
        PolicySelector.register(high)

        found = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )
        assert found.name == "HighPriority"

    def test_precedence_by_override(self):
        """Override mode with explicit override wins."""
        normal = AccountingPolicy(
            name="Normal",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            precedence=PolicyPrecedence(mode=PrecedenceMode.NORMAL),
        )
        override = AccountingPolicy(
            name="Override",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
            precedence=PolicyPrecedence(
                mode=PrecedenceMode.OVERRIDE,
                overrides=("Normal",),
            ),
        )
        PolicySelector.register(normal)
        PolicySelector.register(override)

        found = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )
        assert found.name == "Override"


# ============================================================================
# PolicyCompiler Tests
# ============================================================================


class TestPolicyCompiler:
    """Tests for PolicyCompiler."""

    def test_compile_valid_profile(self, simple_profile):
        """Valid profile compiles successfully."""
        compiler = PolicyCompiler(
            check_overlaps=False,
            check_schema=False,
            check_ledger=False,
        )
        result = compiler.compile(simple_profile)
        assert result.success

    def test_compile_invalid_date_range(self):
        """Invalid effective date range fails."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="A", credit_role="B"),
            ),
            effective_from=date(2024, 12, 31),
            effective_to=date(2024, 1, 1),  # Before effective_from
        )
        compiler = PolicyCompiler(
            check_overlaps=False,
            check_schema=False,
            check_ledger=False,
        )
        result = compiler.compile(profile)
        assert not result.success
        assert any(e.code == "INVALID_EFFECTIVE_RANGE" for e in result.errors)

    def test_compile_no_ledger_effects(self):
        """Profile without ledger effects fails."""
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(),
            effective_from=date(2024, 1, 1),
        )
        compiler = PolicyCompiler(
            check_overlaps=False,
            check_schema=False,
            check_ledger=False,
        )
        result = compiler.compile(profile)
        assert not result.success
        assert any(e.code == "NO_LEDGER_EFFECTS" for e in result.errors)

    def test_p1_overlap_detection(self, simple_profile):
        """P1: Detects overlapping profiles."""
        # Register first profile
        PolicySelector.register(simple_profile)

        # Create overlapping profile
        overlapping = AccountingPolicy(
            name="Overlapping",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="A", credit_role="B"),
            ),
            effective_from=date(2024, 1, 1),
            # Same priority, same scope = unresolvable overlap
        )

        compiler = PolicyCompiler(check_schema=False, check_ledger=False)
        result = compiler.compile(overlapping)
        assert not result.success
        assert any(e.code == "PROFILE_OVERLAP" for e in result.errors)

    def test_p10_field_validation(self, simple_profile, test_schema):
        """P10: Validates field references against schema."""
        EventSchemaRegistry.register(test_schema)

        # Profile with invalid field reference
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(
                event_type="test.event",
                schema_version=1,
                where=(("payload.nonexistent", "value"),),  # Invalid field
            ),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="A", credit_role="B"),
            ),
            effective_from=date(2024, 1, 1),
        )

        compiler = PolicyCompiler(check_overlaps=False, check_ledger=False)
        result = compiler.compile(profile)
        assert not result.success
        assert any(e.code == "INVALID_FIELD_REFERENCE" for e in result.errors)

    def test_p10_warns_when_schema_missing(self, simple_profile):
        """P10: Warns when schema not registered."""
        compiler = PolicyCompiler(check_overlaps=False, check_ledger=False)
        result = compiler.compile(simple_profile)
        # Should succeed but with warning
        assert result.success
        assert any(e.code == "SCHEMA_NOT_REGISTERED" for e in result.warnings)

    def test_p7_ledger_requirements(self):
        """P7: Validates ledger requirements."""
        # Register ledger with requirements
        LedgerRegistry.register(
            ledger_id="GL",
            required_roles={
                "InventoryIncrease": ("InventoryAsset", "GRNI"),
            },
        )

        # Profile missing required role
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="InventoryIncrease"),
            ledger_effects=(
                LedgerEffect(
                    ledger="GL",
                    debit_role="InventoryAsset",
                    credit_role="WrongRole",  # Should be GRNI
                ),
            ),
            effective_from=date(2024, 1, 1),
        )

        compiler = PolicyCompiler(check_overlaps=False, check_schema=False)
        result = compiler.compile(profile)
        assert not result.success
        assert any(e.code == "MISSING_REQUIRED_ROLES" for e in result.errors)

    def test_compile_and_register(self, simple_profile):
        """compile_and_register registers on success."""
        compiler = PolicyCompiler(
            check_overlaps=False,
            check_schema=False,
            check_ledger=False,
        )
        result = compiler.compile_and_register(simple_profile)
        assert result.success
        assert PolicySelector.has_profile("TestProfile", version=1)
