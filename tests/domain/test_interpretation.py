"""
Tests for Phase 2C Interpretation Engine components.

Tests cover:
- MeaningBuilder: Transforms events into economic meaning
- ValuationResolver: Computes values using registered models
- Guard evaluation (P12: reject vs block semantics)
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PolicyMeaning,
    PolicyTrigger,
)
from finance_kernel.domain.meaning_builder import (
    MeaningBuilder,
    ReferenceSnapshot,
)
from finance_kernel.domain.valuation import (
    ValuationModel,
    ValuationModelRegistry,
    ValuationResolver,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def clear_registries():
    """Clear registries before and after each test."""
    ValuationModelRegistry.clear()
    yield
    ValuationModelRegistry.clear()


@pytest.fixture
def simple_profile() -> AccountingPolicy:
    """Simple profile for testing."""
    return AccountingPolicy(
        name="TestProfile",
        version=1,
        trigger=PolicyTrigger(event_type="test.event"),
        meaning=PolicyMeaning(
            economic_type="TestType",
            quantity_field="payload.quantity",
            dimensions=("payload.sku", "location"),
        ),
        ledger_effects=(
            LedgerEffect(ledger="GL", debit_role="Asset", credit_role="Liability"),
        ),
        effective_from=date(2024, 1, 1),
    )


@pytest.fixture
def profile_with_guards() -> AccountingPolicy:
    """Profile with reject and block guards."""
    return AccountingPolicy(
        name="GuardedProfile",
        version=1,
        trigger=PolicyTrigger(event_type="test.event"),
        meaning=PolicyMeaning(economic_type="TestType"),
        ledger_effects=(
            LedgerEffect(ledger="GL", debit_role="Asset", credit_role="Liability"),
        ),
        effective_from=date(2024, 1, 1),
        guards=(
            GuardCondition(
                guard_type=GuardType.REJECT,
                expression="payload.quantity <= 0",
                reason_code="INVALID_QUANTITY",
                message="Quantity must be positive",
            ),
            GuardCondition(
                guard_type=GuardType.BLOCK,
                expression="payload.pending_approval == true",
                reason_code="PENDING_APPROVAL",
                message="Waiting for approval",
            ),
        ),
    )


# ============================================================================
# MeaningBuilder Tests
# ============================================================================


class TestMeaningBuilder:
    """Tests for MeaningBuilder."""

    def test_build_simple_event(self, simple_profile):
        """Build economic meaning from a simple event."""
        builder = MeaningBuilder()
        event_id = uuid4()
        payload = {
            "quantity": "100.5",
            "sku": "SKU-001",
            "location": "WH-A",
        }

        result = builder.build(
            event_id=event_id,
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=simple_profile,
        )

        assert result.success
        assert result.economic_event is not None
        assert result.economic_event.source_event_id == event_id
        assert result.economic_event.economic_type == "TestType"
        assert result.economic_event.quantity == Decimal("100.5")
        assert result.economic_event.effective_date == date(2024, 6, 15)
        assert result.economic_event.profile_id == "TestProfile"
        assert result.economic_event.profile_version == 1

    def test_build_extracts_dimensions(self, simple_profile):
        """Dimensions are extracted from payload."""
        builder = MeaningBuilder()
        payload = {
            "quantity": "50",
            "sku": "SKU-002",
            "location": "WH-B",
        }

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 1, 1),
            profile=simple_profile,
        )

        assert result.success
        assert result.economic_event.dimensions is not None
        assert result.economic_event.dimensions.get("sku") == "SKU-002"
        assert result.economic_event.dimensions.get("location") == "WH-B"

    def test_build_with_snapshot(self, simple_profile):
        """Reference snapshot is captured."""
        builder = MeaningBuilder()
        snapshot = ReferenceSnapshot(
            coa_version=1,
            dimension_schema_version=2,
        )

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"quantity": "10"},
            effective_date=date(2024, 1, 1),
            profile=simple_profile,
            snapshot=snapshot,
        )

        assert result.success
        assert result.economic_event.snapshot is snapshot

    def test_build_profile_mismatch_fails(self, simple_profile):
        """Building with wrong event type fails."""
        builder = MeaningBuilder()

        result = builder.build(
            event_id=uuid4(),
            event_type="wrong.event",  # Profile expects test.event
            payload={},
            effective_date=date(2024, 1, 1),
            profile=simple_profile,
        )

        assert not result.success
        assert len(result.validation_errors) == 1
        assert result.validation_errors[0].code == "PROFILE_EVENT_MISMATCH"


class TestGuardEvaluation:
    """Tests for guard evaluation (P12)."""

    def test_reject_guard_triggers(self, profile_with_guards):
        """REJECT guard triggers for invalid quantity."""
        builder = MeaningBuilder()
        payload = {"quantity": "-5"}  # Triggers: quantity <= 0

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 1, 1),
            profile=profile_with_guards,
        )

        assert not result.success
        assert result.guard_result is not None
        assert result.guard_result.rejected
        assert not result.guard_result.blocked
        assert result.guard_result.reason_code == "INVALID_QUANTITY"

    def test_block_guard_triggers(self, profile_with_guards):
        """BLOCK guard triggers for pending approval."""
        builder = MeaningBuilder()
        payload = {
            "quantity": "10",
            "pending_approval": True,
        }

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 1, 1),
            profile=profile_with_guards,
        )

        assert not result.success
        assert result.guard_result is not None
        assert result.guard_result.blocked
        assert not result.guard_result.rejected
        assert result.guard_result.reason_code == "PENDING_APPROVAL"

    def test_guards_pass_when_conditions_not_met(self, profile_with_guards):
        """Guards pass when conditions are not met."""
        builder = MeaningBuilder()
        payload = {
            "quantity": "10",  # Positive, doesn't trigger reject
            "pending_approval": False,  # False, doesn't trigger block
        }

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 1, 1),
            profile=profile_with_guards,
        )

        assert result.success
        assert result.guard_result.passed

    def test_reject_evaluated_before_block(self):
        """REJECT guards are evaluated in order with BLOCK."""
        # When both could trigger, the first matching guard wins
        profile = AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="A", credit_role="B"),
            ),
            effective_from=date(2024, 1, 1),
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression="payload.invalid == true",
                    reason_code="REJECT_FIRST",
                ),
                GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="payload.block == true",
                    reason_code="BLOCK_SECOND",
                ),
            ),
        )

        builder = MeaningBuilder()
        payload = {"invalid": True, "block": True}

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload=payload,
            effective_date=date(2024, 1, 1),
            profile=profile,
        )

        # First guard (REJECT) should trigger
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "REJECT_FIRST"


class TestGuardExpressions:
    """Tests for guard expression parsing."""

    def _make_profile_with_guard(self, expression: str) -> AccountingPolicy:
        """Helper to create profile with a single guard."""
        return AccountingPolicy(
            name="Test",
            version=1,
            trigger=PolicyTrigger(event_type="test.event"),
            meaning=PolicyMeaning(economic_type="Test"),
            ledger_effects=(
                LedgerEffect(ledger="GL", debit_role="A", credit_role="B"),
            ),
            effective_from=date(2024, 1, 1),
            guards=(
                GuardCondition(
                    guard_type=GuardType.REJECT,
                    expression=expression,
                    reason_code="TEST",
                ),
            ),
        )

    def test_less_than_or_equal(self):
        """Test <= operator."""
        profile = self._make_profile_with_guard("payload.value <= 0")
        builder = MeaningBuilder()

        # Should trigger
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"value": 0},
            effective_date=date(2024, 1, 1),
            profile=profile,
        )
        assert result.guard_result.rejected

        # Should not trigger
        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"value": 1},
            effective_date=date(2024, 1, 1),
            profile=profile,
        )
        assert result.guard_result.passed

    def test_greater_than(self):
        """Test > operator."""
        profile = self._make_profile_with_guard("payload.value > 100")
        builder = MeaningBuilder()

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"value": 150},
            effective_date=date(2024, 1, 1),
            profile=profile,
        )
        assert result.guard_result.rejected

    def test_equality(self):
        """Test == operator."""
        profile = self._make_profile_with_guard("payload.status == INVALID")
        builder = MeaningBuilder()

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"status": "INVALID"},
            effective_date=date(2024, 1, 1),
            profile=profile,
        )
        assert result.guard_result.rejected

    def test_nested_field(self):
        """Test nested field access."""
        profile = self._make_profile_with_guard("payload.item.quantity <= 0")
        builder = MeaningBuilder()

        result = builder.build(
            event_id=uuid4(),
            event_type="test.event",
            payload={"item": {"quantity": -1}},
            effective_date=date(2024, 1, 1),
            profile=profile,
        )
        assert result.guard_result.rejected


# ============================================================================
# ValuationResolver Tests
# ============================================================================


class TestValuationResolver:
    """Tests for ValuationResolver."""

    def test_resolve_with_registered_model(self):
        """Resolve valuation using registered model."""
        # Register a model
        model = ValuationModel(
            model_id="test_model",
            version=1,
            description="Test model",
            currency_field="currency",
            uses_fields=("quantity", "unit_price", "currency"),
            compute=lambda p: Decimal(str(p["quantity"])) * Decimal(str(p["unit_price"])),
        )
        ValuationModelRegistry.register(model)

        resolver = ValuationResolver()
        payload = {
            "quantity": "10",
            "unit_price": "5.50",
            "currency": "USD",
        }

        result = resolver.resolve("test_model", payload)

        assert result.success
        assert result.value == Decimal("55.00")
        assert result.currency == "USD"
        assert result.model_id == "test_model"
        assert result.model_version == 1

    def test_resolve_model_not_found(self):
        """Resolve fails when model not found."""
        resolver = ValuationResolver()

        result = resolver.resolve("nonexistent_model", {})

        assert not result.success
        assert "not found" in result.error.lower()

    def test_resolve_currency_missing(self):
        """Resolve fails when currency field missing."""
        model = ValuationModel(
            model_id="test_model",
            version=1,
            description="Test",
            currency_field="currency",
            uses_fields=("amount",),
            compute=lambda p: Decimal(str(p.get("amount", 0))),
        )
        ValuationModelRegistry.register(model)

        resolver = ValuationResolver()
        payload = {"amount": "100"}  # Missing currency

        result = resolver.resolve("test_model", payload)

        assert not result.success
        assert "currency" in result.error.lower()

    def test_resolve_specific_version(self):
        """Resolve using specific model version."""
        v1 = ValuationModel(
            model_id="versioned",
            version=1,
            description="V1",
            currency_field="currency",
            uses_fields=("amount",),
            compute=lambda p: Decimal(str(p.get("amount", 0))),
        )
        v2 = ValuationModel(
            model_id="versioned",
            version=2,
            description="V2",
            currency_field="currency",
            uses_fields=("amount",),
            compute=lambda p: Decimal(str(p.get("amount", 0))) * 2,  # Different computation
        )
        ValuationModelRegistry.register(v1)
        ValuationModelRegistry.register(v2)

        resolver = ValuationResolver()
        payload = {"amount": "100", "currency": "USD"}

        # Latest version
        result = resolver.resolve("versioned", payload)
        assert result.value == Decimal("200")

        # Specific version
        result = resolver.resolve("versioned", payload, model_version=1)
        assert result.value == Decimal("100")


class TestValuationModelRegistry:
    """Tests for ValuationModelRegistry."""

    def test_register_and_get(self):
        """Register and retrieve a model."""
        model = ValuationModel(
            model_id="test",
            version=1,
            description="Test",
            currency_field="currency",
            uses_fields=(),
            compute=lambda p: Decimal("0"),
        )
        ValuationModelRegistry.register(model)

        retrieved = ValuationModelRegistry.get("test", version=1)
        assert retrieved is model

    def test_get_latest_version(self):
        """get() without version returns latest."""
        v1 = ValuationModel(
            model_id="test",
            version=1,
            description="V1",
            currency_field="currency",
            uses_fields=(),
            compute=lambda p: Decimal("1"),
        )
        v2 = ValuationModel(
            model_id="test",
            version=2,
            description="V2",
            currency_field="currency",
            uses_fields=(),
            compute=lambda p: Decimal("2"),
        )
        ValuationModelRegistry.register(v1)
        ValuationModelRegistry.register(v2)

        latest = ValuationModelRegistry.get("test")
        assert latest.version == 2

    def test_has_model(self):
        """Check model existence."""
        assert not ValuationModelRegistry.has_model("test")

        ValuationModelRegistry.register(
            ValuationModel(
                model_id="test",
                version=1,
                description="Test",
                currency_field="currency",
                uses_fields=(),
                compute=lambda p: Decimal("0"),
            )
        )

        assert ValuationModelRegistry.has_model("test")
        assert ValuationModelRegistry.has_model("test", version=1)
        assert not ValuationModelRegistry.has_model("test", version=2)
