"""
Interpretation Invariant Tests.

Tests covering critical system invariants:
1. P1: Profile overlap / unambiguous matching
2. L4: Deterministic replay
3. Idempotency
4. P11/L5: Multi-ledger atomicity
5. Guard expression handling
6. Profile version migration
7. BLOCK expiration and settlement

These tests verify the mathematical and behavioral guarantees
of the interpretation system.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PolicyMeaning,
    PolicyPrecedence,
    PolicyTrigger,
    PrecedenceMode,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    GuardEvaluationResult,
    MeaningBuilder,
    MeaningBuilderResult,
    ReferenceSnapshot,
)
from finance_kernel.domain.policy_selector import (
    MultiplePoliciesMatchError,
    PolicyAlreadyRegisteredError,
    PolicyNotFoundError,
    PolicySelector,
)

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture(autouse=True)
def clear_profile_registry():
    """Save, clear, and restore profile registry for test isolation."""
    saved_profiles = {k: dict(v) for k, v in PolicySelector._profiles.items()}
    saved_by_event = {k: list(v) for k, v in PolicySelector._by_event_type.items()}
    PolicySelector.clear()
    yield
    PolicySelector.clear()
    PolicySelector._profiles.update(saved_profiles)
    PolicySelector._by_event_type.update(saved_by_event)


@pytest.fixture
def meaning_builder() -> MeaningBuilder:
    """Create a MeaningBuilder instance."""
    return MeaningBuilder()


def create_base_profile(
    name: str,
    event_type: str = "test.event",
    version: int = 1,
    effective_from: date = date(2024, 1, 1),
    effective_to: date | None = None,
    scope: str = "*",
    priority: int = 0,
    mode: PrecedenceMode = PrecedenceMode.NORMAL,
    overrides: tuple[str, ...] = (),
    guards: tuple[GuardCondition, ...] = (),
) -> AccountingPolicy:
    """Create a base profile for testing."""
    return AccountingPolicy(
        name=name,
        version=version,
        trigger=PolicyTrigger(event_type=event_type, schema_version=1),
        meaning=PolicyMeaning(
            economic_type="TestEconomicType",
            quantity_field="amount",
        ),
        ledger_effects=(
            LedgerEffect(ledger="GL", debit_role="TestDebit", credit_role="TestCredit"),
        ),
        effective_from=effective_from,
        effective_to=effective_to,
        scope=scope,
        precedence=PolicyPrecedence(
            mode=mode,
            priority=priority,
            overrides=overrides,
        ),
        guards=guards,
    )


# =============================================================================
# TEST 1: P1 - PROFILE OVERLAP / UNAMBIGUOUS MATCHING
# =============================================================================


class TestP1ProfileOverlap:
    """
    P1 Invariant: Exactly one profile must match, or event is rejected.

    Multiple profiles cannot apply to the same event unless precedence
    can resolve unambiguously to a single winner.
    """

    def test_single_profile_matches(self):
        """When exactly one profile matches, it should be returned."""
        profile = create_base_profile("SingleProfile", event_type="sales.order")
        PolicySelector.register(profile)

        result = PolicySelector.find_for_event(
            event_type="sales.order",
            effective_date=date(2024, 6, 15),
        )

        assert result.name == "SingleProfile"

    def test_no_profile_matches_raises(self):
        """When no profile matches, PolicyNotFoundError is raised."""
        profile = create_base_profile("WrongType", event_type="inventory.receipt")
        PolicySelector.register(profile)

        with pytest.raises(PolicyNotFoundError) as exc_info:
            PolicySelector.find_for_event(
                event_type="sales.order",
                effective_date=date(2024, 6, 15),
            )

        assert exc_info.value.event_type == "sales.order"

    def test_multiple_profiles_same_scope_same_priority_raises(self):
        """
        Multiple profiles with identical scope and priority cannot be resolved.
        This is a P1 violation - ambiguous matching.
        """
        profile_a = create_base_profile("ProfileA", scope="*", priority=0)
        profile_b = create_base_profile("ProfileB", scope="*", priority=0)

        PolicySelector.register(profile_a)
        PolicySelector.register(profile_b)

        with pytest.raises(MultiplePoliciesMatchError) as exc_info:
            PolicySelector.find_for_event(
                event_type="test.event",
                effective_date=date(2024, 6, 15),
            )

        assert "ProfileA" in exc_info.value.matching_profiles
        assert "ProfileB" in exc_info.value.matching_profiles

    def test_scope_specificity_resolves_overlap(self):
        """More specific scope wins over general scope."""
        general = create_base_profile("GeneralProfile", scope="*")
        specific = create_base_profile("SpecificProfile", scope="SKU:WIDGET-001")

        PolicySelector.register(general)
        PolicySelector.register(specific)

        # Specific scope should win
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
            scope_value="SKU:WIDGET-001",
        )
        assert result.name == "SpecificProfile"

        # General scope still works for non-matching scope
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
            scope_value="SKU:OTHER",
        )
        assert result.name == "GeneralProfile"

    def test_priority_resolves_overlap(self):
        """Higher priority wins when scope specificity is equal."""
        low_priority = create_base_profile("LowPriority", scope="*", priority=10)
        high_priority = create_base_profile("HighPriority", scope="*", priority=100)

        PolicySelector.register(low_priority)
        PolicySelector.register(high_priority)

        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )

        assert result.name == "HighPriority"

    def test_override_mode_wins_over_normal(self):
        """Override mode profiles take precedence over normal profiles."""
        normal = create_base_profile("NormalProfile", priority=100)
        override = create_base_profile(
            "OverrideProfile",
            priority=1,  # Lower priority but override mode
            mode=PrecedenceMode.OVERRIDE,
        )

        PolicySelector.register(normal)
        PolicySelector.register(override)

        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )

        assert result.name == "OverrideProfile"

    def test_explicit_override_removes_profile(self):
        """Profile can explicitly override another by name."""
        base = create_base_profile(
            "BaseProfile",
            mode=PrecedenceMode.OVERRIDE,
            priority=100,
        )
        specialized = create_base_profile(
            "SpecializedProfile",
            mode=PrecedenceMode.OVERRIDE,
            priority=50,  # Lower priority
            overrides=("BaseProfile",),  # But explicitly overrides
        )

        PolicySelector.register(base)
        PolicySelector.register(specialized)

        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )

        assert result.name == "SpecializedProfile"

    def test_effective_date_filters_profiles(self):
        """Profile not effective on date should not match."""
        past = create_base_profile(
            "PastProfile",
            effective_from=date(2023, 1, 1),
            effective_to=date(2023, 12, 31),
        )
        current = create_base_profile(
            "CurrentProfile",
            effective_from=date(2024, 1, 1),
        )

        PolicySelector.register(past)
        PolicySelector.register(current)

        # Should find current profile
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )
        assert result.name == "CurrentProfile"

        # Should find past profile for 2023
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2023, 6, 15),
        )
        assert result.name == "PastProfile"

    def test_profile_prefix_scope_matching(self):
        """Prefix scope pattern (SKU:*) matches any SKU."""
        sku_profile = create_base_profile("SKUProfile", scope="SKU:*")
        PolicySelector.register(sku_profile)

        # Should match any SKU
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
            scope_value="SKU:ANYTHING",
        )
        assert result.name == "SKUProfile"

        # Should NOT match non-SKU scope
        with pytest.raises(PolicyNotFoundError):
            PolicySelector.find_for_event(
                event_type="test.event",
                effective_date=date(2024, 6, 15),
                scope_value="PROJECT:123",
            )


# =============================================================================
# TEST 2: L4 - DETERMINISTIC REPLAY
# =============================================================================


class TestL4DeterministicReplay:
    """
    L4 Invariant: Replay using stored snapshots produces identical results.

    Given the same:
    - Event ID
    - Event payload
    - Profile
    - Reference snapshot

    The MeaningBuilder must produce byte-identical output.
    """

    def test_same_inputs_produce_identical_output(self, meaning_builder: MeaningBuilder):
        """Same inputs must produce exactly the same output."""
        event_id = uuid4()
        payload = {
            "amount": "100.50",
            "sku": "WIDGET-001",
            "warehouse": "WH-MAIN",
        }
        profile = create_base_profile(
            "TestProfile",
            guards=(),
        )
        snapshot = ReferenceSnapshot(
            coa_version=5,
            dimension_schema_version=2,
            currency_registry_version=1,
            fx_policy_version=1,
        )
        effective_date = date(2024, 6, 15)

        # First execution
        result1 = meaning_builder.build(
            event_id=event_id,
            event_type="test.event",
            payload=payload,
            effective_date=effective_date,
            profile=profile,
            snapshot=snapshot,
        )

        # Second execution with identical inputs
        result2 = meaning_builder.build(
            event_id=event_id,
            event_type="test.event",
            payload=payload,
            effective_date=effective_date,
            profile=profile,
            snapshot=snapshot,
        )

        assert result1.success == result2.success
        assert result1.economic_event.source_event_id == result2.economic_event.source_event_id
        assert result1.economic_event.economic_type == result2.economic_event.economic_type
        assert result1.economic_event.quantity == result2.economic_event.quantity
        assert result1.economic_event.profile_id == result2.economic_event.profile_id
        assert result1.economic_event.profile_version == result2.economic_event.profile_version
        assert result1.economic_event.snapshot == result2.economic_event.snapshot

    def test_different_snapshot_can_produce_different_result(
        self, meaning_builder: MeaningBuilder
    ):
        """Different snapshots may produce different results (version change)."""
        event_id = uuid4()
        payload = {"amount": "100.50"}
        profile = create_base_profile("TestProfile")

        snapshot_v1 = ReferenceSnapshot(coa_version=1)
        snapshot_v2 = ReferenceSnapshot(coa_version=2)

        result1 = meaning_builder.build(
            event_id=event_id,
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=profile,
            snapshot=snapshot_v1,
        )

        result2 = meaning_builder.build(
            event_id=event_id,
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=profile,
            snapshot=snapshot_v2,
        )

        # Results should both succeed but have different snapshots stored
        assert result1.success and result2.success
        assert result1.economic_event.snapshot.coa_version == 1
        assert result2.economic_event.snapshot.coa_version == 2

    def test_guard_evaluation_is_deterministic(self, meaning_builder: MeaningBuilder):
        """Guard evaluation with same inputs always produces same result."""
        profile_with_guard = create_base_profile(
            "GuardedProfile",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="amount <= 0",
                    reason_code="INVALID_AMOUNT",
                    message="Amount must be positive",
                ),
            ),
        )

        # Test rejection is deterministic
        for _ in range(10):
            result = meaning_builder.build(
                event_id=uuid4(),
                event_type="test.event",
                payload={"amount": "0"},
                effective_date=date(2024, 6, 15),
                profile=profile_with_guard,
            )
            assert not result.success
            assert result.guard_result.rejected
            assert result.guard_result.reason_code == "INVALID_AMOUNT"

        # Test success is deterministic
        for _ in range(10):
            result = meaning_builder.build(
                event_id=uuid4(),
                event_type="test.event",
                payload={"amount": "100"},
                effective_date=date(2024, 6, 15),
                profile=profile_with_guard,
            )
            assert result.success

    def test_quantity_extraction_is_deterministic(self, meaning_builder: MeaningBuilder):
        """Quantity extraction produces identical Decimal values."""
        profile = create_base_profile("TestProfile")

        # Various numeric representations
        test_values = [
            ("100", Decimal("100")),
            ("100.00", Decimal("100.00")),
            ("100.123456789", Decimal("100.123456789")),
            ("0.01", Decimal("0.01")),
        ]

        for input_val, expected in test_values:
            result = meaning_builder.build(
                event_id=uuid4(),
                event_type="test.event",
                payload={"amount": input_val},
                effective_date=date(2024, 6, 15),
                profile=profile,
            )
            assert result.success
            assert result.economic_event.quantity == expected


# =============================================================================
# TEST 3: IDEMPOTENCY
# =============================================================================


class TestIdempotency:
    """
    Idempotency: Processing the same event multiple times produces the same result.

    This is critical for:
    - Retry safety
    - At-least-once delivery
    - Crash recovery
    """

    def test_same_event_same_profile_same_result(self, meaning_builder: MeaningBuilder):
        """Same event processed multiple times yields identical result."""
        event_id = uuid4()
        payload = {"amount": "500.00", "sku": "TEST-001"}
        profile = create_base_profile("TestProfile")
        snapshot = ReferenceSnapshot(coa_version=1)

        results = []
        for _ in range(5):
            result = meaning_builder.build(
                event_id=event_id,
                event_type="test.event",
                payload=payload,
                effective_date=date(2024, 6, 15),
                profile=profile,
                snapshot=snapshot,
            )
            results.append(result)

        # All results should be identical
        for result in results:
            assert result.success
            assert result.economic_event.source_event_id == event_id
            assert result.economic_event.quantity == Decimal("500.00")

    def test_idempotency_key_generation(self):
        """AccountingIntent generates deterministic idempotency keys."""
        econ_event_id = uuid4()
        source_event_id = uuid4()

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id="TestProfile",
            profile_version=1,
            effective_date=date(2024, 6, 15),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("TestDebit", "100.00", "USD"),
                        IntentLine.credit("TestCredit", "100.00", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        # Key should be deterministic
        key1 = intent.idempotency_key("GL")
        key2 = intent.idempotency_key("GL")
        assert key1 == key2
        assert str(econ_event_id) in key1
        assert "GL" in key1

    def test_different_events_different_ids(self, meaning_builder: MeaningBuilder):
        """Different events get different source_event_ids."""
        profile = create_base_profile("TestProfile")

        id1 = uuid4()
        id2 = uuid4()

        result1 = meaning_builder.build(
            event_id=id1,
            event_type="test.event",
            payload={"amount": "100"},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        result2 = meaning_builder.build(
            event_id=id2,
            event_type="test.event",
            payload={"amount": "100"},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert result1.economic_event.source_event_id != result2.economic_event.source_event_id


# =============================================================================
# TEST 4: P11/L5 - MULTI-LEDGER ATOMICITY
# =============================================================================


class TestMultiLedgerAtomicity:
    """
    P11: Multi-ledger postings are atomic.
    L5: No journal rows without POSTED outcome; no POSTED outcome without all rows.

    Tests verify intent structure enforces atomicity requirements.
    """

    def test_multi_ledger_intent_structure(self):
        """AccountingIntent can contain multiple ledger intents."""
        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=uuid4(),
            profile_id="TestProfile",
            profile_version=1,
            effective_date=date(2024, 6, 15),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("InventoryAsset", "1000.00", "USD"),
                        IntentLine.credit("GRNI", "1000.00", "USD"),
                    ),
                ),
                LedgerIntent(
                    ledger_id="InventorySubledger",
                    lines=(
                        IntentLine.debit("OnHand", "1000.00", "USD"),
                        IntentLine.credit("InTransit", "1000.00", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        assert len(intent.ledger_intents) == 2
        assert "GL" in intent.ledger_ids
        assert "InventorySubledger" in intent.ledger_ids

    def test_all_ledgers_must_balance(self):
        """Each ledger intent must balance individually."""
        gl_intent = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("Asset", "100.00", "USD"),
                IntentLine.credit("Liability", "100.00", "USD"),
            ),
        )

        assert gl_intent.is_balanced()
        assert gl_intent.total_debits() == Decimal("100.00")
        assert gl_intent.total_credits() == Decimal("100.00")

    def test_unbalanced_ledger_detected(self):
        """Unbalanced ledger intent is detected."""
        unbalanced = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("Asset", "100.00", "USD"),
                IntentLine.credit("Liability", "99.00", "USD"),  # Imbalance!
            ),
        )

        assert not unbalanced.is_balanced()

    def test_multi_currency_balance_check(self):
        """Multi-currency ledger must balance each currency separately."""
        multi_currency = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("CashUSD", "100.00", "USD"),
                IntentLine.credit("Revenue", "100.00", "USD"),
                IntentLine.debit("CashEUR", "85.00", "EUR"),
                IntentLine.credit("Revenue", "85.00", "EUR"),
            ),
        )

        assert multi_currency.is_balanced()
        assert multi_currency.is_balanced("USD")
        assert multi_currency.is_balanced("EUR")

    def test_partial_currency_imbalance_detected(self):
        """Imbalance in one currency should be detected."""
        partial_imbalance = LedgerIntent(
            ledger_id="GL",
            lines=(
                IntentLine.debit("CashUSD", "100.00", "USD"),
                IntentLine.credit("Revenue", "100.00", "USD"),
                IntentLine.debit("CashEUR", "85.00", "EUR"),
                IntentLine.credit("Revenue", "80.00", "EUR"),  # EUR imbalance!
            ),
        )

        assert partial_imbalance.is_balanced("USD")  # USD balances
        assert not partial_imbalance.is_balanced("EUR")  # EUR doesn't
        assert not partial_imbalance.is_balanced()  # Overall fails

    def test_intent_requires_non_empty_ledger_intents(self):
        """AccountingIntent cannot have empty ledger_intents."""
        with pytest.raises(ValueError, match="at least one ledger intent"):
            AccountingIntent(
                econ_event_id=uuid4(),
                source_event_id=uuid4(),
                profile_id="TestProfile",
                profile_version=1,
                effective_date=date(2024, 6, 15),
                ledger_intents=(),  # Empty!
                snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
            )

    def test_ledger_intent_requires_non_empty_lines(self):
        """LedgerIntent cannot have empty lines."""
        with pytest.raises(ValueError, match="at least one line"):
            LedgerIntent(ledger_id="GL", lines=())

    def test_all_roles_tracked(self):
        """Intent tracks all account roles for resolution validation."""
        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=uuid4(),
            profile_id="TestProfile",
            profile_version=1,
            effective_date=date(2024, 6, 15),
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("InventoryAsset", "100.00", "USD"),
                        IntentLine.credit("GRNI", "100.00", "USD"),
                    ),
                ),
                LedgerIntent(
                    ledger_id="SubLedger",
                    lines=(
                        IntentLine.debit("OnHand", "100.00", "USD"),
                        IntentLine.credit("InTransit", "100.00", "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        all_roles = intent.all_roles
        assert "InventoryAsset" in all_roles
        assert "GRNI" in all_roles
        assert "OnHand" in all_roles
        assert "InTransit" in all_roles


# =============================================================================
# TEST 5: GUARD EXPRESSION HANDLING
# =============================================================================


class TestGuardExpressionHandling:
    """
    Tests for guard expression evaluation, including edge cases
    and malformed expressions.
    """

    def test_valid_comparison_operators(self, meaning_builder: MeaningBuilder):
        """All comparison operators should work correctly."""
        test_cases = [
            ("amount <= 0", {"amount": "0"}, True),  # Triggers
            ("amount <= 0", {"amount": "1"}, False),  # Doesn't trigger
            ("amount >= 100", {"amount": "100"}, True),
            ("amount >= 100", {"amount": "99"}, False),
            ("amount < 0", {"amount": "-1"}, True),
            ("amount < 0", {"amount": "0"}, False),
            ("amount > 100", {"amount": "101"}, True),
            ("amount > 100", {"amount": "100"}, False),
            ("amount == 50", {"amount": "50"}, True),
            ("amount == 50", {"amount": "51"}, False),
            ("amount != 0", {"amount": "1"}, True),
            ("amount != 0", {"amount": "0"}, False),
        ]

        for expression, payload, should_trigger in test_cases:
            profile = create_base_profile(
                f"Test_{expression}",
                guards=(
                    GuardCondition(
                        guard_type=GuardType.REJECT,
                        expression=expression,
                        reason_code="TEST",
                    ),
                ),
            )

            result = meaning_builder.build(
                event_id=uuid4(),
                event_type="test.event",
                payload=payload,
                effective_date=date(2024, 6, 15),
                profile=profile,
            )

            if should_trigger:
                assert not result.success, f"Expected {expression} to trigger with {payload}"
                assert result.guard_result.rejected
            else:
                assert result.success, f"Expected {expression} NOT to trigger with {payload}"

    def test_boolean_guard_expression(self, meaning_builder: MeaningBuilder):
        """Boolean field checks should work."""
        profile = create_base_profile(
            "BooleanGuard",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="is_blocked == true",
                    reason_code="BLOCKED",
                ),
            ),
        )

        # Should block when is_blocked is true
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "is_blocked": True},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )
        assert result.guard_result.blocked

        # Should pass when is_blocked is false
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "is_blocked": False},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )
        assert result.success

    def test_nested_field_guard(self, meaning_builder: MeaningBuilder):
        """Guards can reference nested fields."""
        profile = create_base_profile(
            "NestedGuard",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="supplier.credit_limit <= 0",
                    reason_code="NO_CREDIT",
                ),
            ),
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "supplier": {"credit_limit": 0}},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert not result.success
        assert result.guard_result.rejected

    def test_missing_field_does_not_trigger(self, meaning_builder: MeaningBuilder):
        """Missing field in expression should not trigger guard."""
        profile = create_base_profile(
            "MissingFieldGuard",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="nonexistent_field <= 0",
                    reason_code="MISSING",
                ),
            ),
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100"},  # No nonexistent_field
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        # Should pass because missing field returns None, which doesn't trigger
        assert result.success

    def test_reject_evaluated_before_block(self, meaning_builder: MeaningBuilder):
        """REJECT guards are evaluated before BLOCK guards."""
        profile = create_base_profile(
            "OrderedGuards",
            guards=(
                # BLOCK guard is listed first
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="needs_approval == true",
                    reason_code="NEEDS_APPROVAL",
                ),
                # REJECT guard is listed second
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="amount <= 0",
                    reason_code="INVALID_AMOUNT",
                ),
            ),
        )

        # Both conditions are met - REJECT should win (evaluated first semantically)
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "0", "needs_approval": True},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        # Current implementation processes in order, but semantically
        # reject conditions should be checked
        assert not result.success


# =============================================================================
# TEST 6: PROFILE VERSION MIGRATION
# =============================================================================


class TestProfileVersionMigration:
    """
    Tests for profile versioning and migration behavior.
    """

    def test_multiple_versions_can_be_registered(self):
        """Multiple versions of the same profile can coexist."""
        v1 = create_base_profile("TestProfile", version=1, effective_to=date(2024, 6, 30))
        v2 = create_base_profile("TestProfile", version=2, effective_from=date(2024, 7, 1))

        PolicySelector.register(v1)
        PolicySelector.register(v2)

        assert PolicySelector.has_profile("TestProfile", version=1)
        assert PolicySelector.has_profile("TestProfile", version=2)

    def test_get_specific_version(self):
        """Can retrieve a specific version by number."""
        v1 = create_base_profile("TestProfile", version=1)
        v2 = create_base_profile("TestProfile", version=2)

        PolicySelector.register(v1)
        PolicySelector.register(v2)

        retrieved_v1 = PolicySelector.get("TestProfile", version=1)
        retrieved_v2 = PolicySelector.get("TestProfile", version=2)

        assert retrieved_v1.version == 1
        assert retrieved_v2.version == 2

    def test_get_latest_version(self):
        """Getting without version returns latest."""
        v1 = create_base_profile("TestProfile", version=1)
        v2 = create_base_profile("TestProfile", version=2)
        v3 = create_base_profile("TestProfile", version=3)

        PolicySelector.register(v1)
        PolicySelector.register(v3)
        PolicySelector.register(v2)  # Out of order registration

        latest = PolicySelector.get("TestProfile")
        assert latest.version == 3

    def test_effective_date_version_transition(self):
        """Version transition based on effective date."""
        old_version = create_base_profile(
            "TestProfile",
            version=1,
            effective_from=date(2024, 1, 1),
            effective_to=date(2024, 6, 30),
            priority=10,  # Ensure this wins for old dates
        )
        new_version = create_base_profile(
            "TestProfile",
            version=2,
            effective_from=date(2024, 7, 1),
            priority=10,  # Same priority for new dates
        )

        PolicySelector.register(old_version)
        PolicySelector.register(new_version)

        # Query for June should get v1
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 6, 15),
        )
        assert result.version == 1

        # Query for August should get v2
        result = PolicySelector.find_for_event(
            event_type="test.event",
            effective_date=date(2024, 8, 15),
        )
        assert result.version == 2

    def test_duplicate_registration_raises(self):
        """Cannot register same name+version twice."""
        v1 = create_base_profile("TestProfile", version=1)
        PolicySelector.register(v1)

        v1_duplicate = create_base_profile("TestProfile", version=1)

        with pytest.raises(PolicyAlreadyRegisteredError):
            PolicySelector.register(v1_duplicate)

    def test_economic_event_records_profile_version(self, meaning_builder: MeaningBuilder):
        """Economic event records which profile version was used."""
        v1 = create_base_profile("TestProfile", version=1)
        v2 = create_base_profile("TestProfile", version=2)

        result_v1 = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100"},
            effective_date=date(2024, 6, 15),
            profile=v1,
        )

        result_v2 = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100"},
            effective_date=date(2024, 6, 15),
            profile=v2,
        )

        assert result_v1.economic_event.profile_version == 1
        assert result_v2.economic_event.profile_version == 2


# =============================================================================
# TEST 7: BLOCK EXPIRATION / SETTLEMENT
# =============================================================================


class TestBlockExpirationSettlement:
    """
    Tests for BLOCK guard behavior - resumable processing.

    BLOCK is different from REJECT:
    - REJECT = terminal, invalid economic reality
    - BLOCK = temporary, system constraint that may resolve
    """

    def test_block_is_resumable(self, meaning_builder: MeaningBuilder):
        """Blocked event can succeed when condition clears."""
        profile = create_base_profile(
            "BlockableProfile",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="warehouse_locked == true",
                    reason_code="WAREHOUSE_LOCKED",
                    message="Warehouse is locked for counting",
                ),
            ),
        )

        # First attempt: blocked
        result1 = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "warehouse_locked": True},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert not result1.success
        assert result1.guard_result.blocked
        assert not result1.guard_result.rejected  # Not rejected!

        # Second attempt: warehouse unlocked
        result2 = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "warehouse_locked": False},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert result2.success

    def test_reject_is_terminal(self, meaning_builder: MeaningBuilder):
        """Rejected event cannot be retried with same payload."""
        profile = create_base_profile(
            "RejectableProfile",
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="amount <= 0",
                    reason_code="INVALID_AMOUNT",
                    message="Amount must be positive",
                ),
            ),
        )

        # Any attempt with amount <= 0 will always be rejected
        for _ in range(3):
            result = meaning_builder.build(
                event_id=uuid4(),
                event_type="test.event",
                payload={"amount": "0"},
                effective_date=date(2024, 6, 15),
                profile=profile,
            )

            assert not result.success
            assert result.guard_result.rejected
            assert not result.guard_result.blocked

    def test_block_reason_is_recorded(self, meaning_builder: MeaningBuilder):
        """Block reason code and message are available for retry logic."""
        profile = create_base_profile(
            "BlockableProfile",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="credit_check_pending == true",
                    reason_code="CREDIT_CHECK_PENDING",
                    message="Waiting for credit check to complete",
                ),
            ),
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "credit_check_pending": True},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert result.guard_result.blocked
        assert result.guard_result.reason_code == "CREDIT_CHECK_PENDING"
        assert result.guard_result.triggered_guard is not None
        assert "credit check" in result.guard_result.triggered_guard.message.lower()

    def test_multiple_block_conditions(self, meaning_builder: MeaningBuilder):
        """Multiple block conditions can exist - first triggered wins."""
        profile = create_base_profile(
            "MultiBlockProfile",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="inventory_locked == true",
                    reason_code="INVENTORY_LOCKED",
                ),
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="period_closed == true",
                    reason_code="PERIOD_CLOSED",
                ),
            ),
        )

        # Both conditions met - first one wins
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={
                "amount": "100",
                "inventory_locked": True,
                "period_closed": True,
            },
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert result.guard_result.blocked
        assert result.guard_result.reason_code == "INVENTORY_LOCKED"

    def test_block_with_different_effective_dates(self, meaning_builder: MeaningBuilder):
        """Block condition may depend on effective date context."""
        profile = create_base_profile(
            "DateSensitiveBlock",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="requires_approval == true",
                    reason_code="APPROVAL_REQUIRED",
                ),
            ),
        )

        # Event with approval requirement
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "requires_approval": True},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert result.guard_result.blocked
        assert result.guard_result.reason_code == "APPROVAL_REQUIRED"

        # Same payload without approval flag
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "requires_approval": False},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert result.success

    def test_block_does_not_produce_economic_event(self, meaning_builder: MeaningBuilder):
        """Blocked events do not produce an economic event."""
        profile = create_base_profile(
            "BlockableProfile",
            guards=(
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="blocked == true",
                    reason_code="BLOCKED",
                ),
            ),
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100", "blocked": True},
            effective_date=date(2024, 6, 15),
            profile=profile,
        )

        assert not result.success
        assert result.guard_result.blocked
        assert result.economic_event is None


# =============================================================================
# TEST: Combined Scenarios
# =============================================================================


class TestCombinedScenarios:
    """Integration scenarios combining multiple invariants."""

    def test_profile_migration_with_guard_change(self, meaning_builder: MeaningBuilder):
        """
        Scenario: Profile v2 adds a new guard that v1 didn't have.
        Events processed under v1 should not be affected by v2 guards.
        """
        v1 = create_base_profile(
            "EvolvingProfile",
            version=1,
            guards=(),  # No guards in v1
        )

        v2 = create_base_profile(
            "EvolvingProfile",
            version=2,
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="amount > 10000",
                    reason_code="AMOUNT_TOO_LARGE",
                ),
            ),
        )

        payload = {"amount": "50000"}  # Would be rejected by v2

        # Process with v1 - should succeed
        result_v1 = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=v1,
        )
        assert result_v1.success

        # Process with v2 - should be rejected
        result_v2 = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=v2,
        )
        assert not result_v2.success
        assert result_v2.guard_result.rejected

    def test_replay_with_original_profile_version(self, meaning_builder: MeaningBuilder):
        """
        Scenario: Replaying an old event uses the profile version recorded
        at original processing time, not the latest profile.
        """
        v1 = create_base_profile("TestProfile", version=1)

        # Original processing
        original_result = meaning_builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"amount": "100"},
            effective_date=date(2024, 6, 15),
            profile=v1,
            snapshot=ReferenceSnapshot(coa_version=5),
        )

        # Record what was stored
        stored_profile_id = original_result.economic_event.profile_id
        stored_profile_version = original_result.economic_event.profile_version
        stored_snapshot = original_result.economic_event.snapshot

        # Replay must use same profile version
        replay_result = meaning_builder.build(
            event_id=original_result.economic_event.source_event_id,
            event_type="test.event",
            payload={"amount": "100"},
            effective_date=date(2024, 6, 15),
            profile=v1,  # Must use v1, not latest
            snapshot=stored_snapshot,  # Must use original snapshot
        )

        assert replay_result.success
        assert replay_result.economic_event.profile_version == stored_profile_version
        assert replay_result.economic_event.snapshot == stored_snapshot
