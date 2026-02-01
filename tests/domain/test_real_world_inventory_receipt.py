"""
Real-world test: Inventory Receipt interpretation flow.

Scenario: A manufacturing company receives raw materials from a supplier.
When goods are received at the warehouse:
1. Inventory asset increases (Debit: Inventory)
2. Liability is recorded for goods not yet invoiced (Credit: GRNI)

This test exercises the complete interpretation pipeline:
- Event schema definition and validation
- Economic profile with guards
- MeaningBuilder to create economic events
- AccountingIntent generation for multi-ledger posting

Business rules enforced:
- REJECT if quantity <= 0 (invalid economic reality)
- REJECT if unit_cost <= 0 (invalid pricing)
- BLOCK if warehouse is inactive (system constraint)
"""

from dataclasses import dataclass
from datetime import date, datetime
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
)
from finance_kernel.domain.event_validator import (
    validate_payload_against_schema,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    GuardEvaluationResult,
    MeaningBuilder,
    MeaningBuilderResult,
    ReferenceSnapshot,
)
from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import (
    EventSchemaRegistry,
)

# =============================================================================
# SCHEMA DEFINITION
# =============================================================================

INVENTORY_RECEIPT_SCHEMA = EventSchema(
    event_type="warehouse.inventory_receipt",
    version=1,
    fields=(
        EventFieldSchema(
            name="receipt_number",
            field_type=EventFieldType.STRING,
            required=True,
            description="Unique receipt reference number",
        ),
        EventFieldSchema(
            name="supplier_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Supplier identifier",
        ),
        EventFieldSchema(
            name="purchase_order_id",
            field_type=EventFieldType.UUID,
            required=False,
            description="Related purchase order (if any)",
        ),
        EventFieldSchema(
            name="sku",
            field_type=EventFieldType.STRING,
            required=True,
            description="Stock keeping unit",
        ),
        EventFieldSchema(
            name="quantity",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.001"),
            description="Quantity received",
        ),
        EventFieldSchema(
            name="unit_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Cost per unit in receipt currency",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of unit cost",
        ),
        EventFieldSchema(
            name="warehouse_code",
            field_type=EventFieldType.STRING,
            required=True,
            description="Receiving warehouse identifier",
        ),
        EventFieldSchema(
            name="warehouse_active",
            field_type=EventFieldType.BOOLEAN,
            required=True,
            description="Whether warehouse is active for receiving",
        ),
        EventFieldSchema(
            name="received_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date goods were physically received",
        ),
        EventFieldSchema(
            name="lot_number",
            field_type=EventFieldType.STRING,
            required=False,
            description="Lot or batch number for traceability",
        ),
        EventFieldSchema(
            name="notes",
            field_type=EventFieldType.STRING,
            required=False,
            description="Additional notes",
        ),
    ),
    description="Inventory receipt from supplier delivery",
)


# =============================================================================
# ECONOMIC PROFILE DEFINITION
# =============================================================================

INVENTORY_RECEIPT_PROFILE = AccountingPolicy(
    name="InventoryReceipt_Standard",
    version=1,
    description="""
    Standard inventory receipt profile for raw materials.

    Creates GL entries:
    - Dr: Inventory Asset (at extended cost = qty * unit_cost)
    - Cr: Goods Received Not Invoiced (GRNI)

    Also posts to Inventory Subledger for detailed tracking.
    """,
    trigger=PolicyTrigger(
        event_type="warehouse.inventory_receipt",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="InventoryIncrease",
        quantity_field="quantity",
        dimensions=(
            "sku",
            "warehouse_code",
            "lot_number",
        ),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="InventoryAsset",
            credit_role="GRNI",
        ),
        LedgerEffect(
            ledger="InventorySubledger",
            debit_role="InventoryOnHand",
            credit_role="InventoryInTransit",
        ),
    ),
    effective_from=date(2024, 1, 1),
    effective_to=None,  # Open-ended
    scope="SKU:*",  # Matches all SKUs
    valuation_model="STANDARD_RECEIPT_V1",
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Quantity must be greater than zero",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="unit_cost <= 0",
            reason_code="INVALID_UNIT_COST",
            message="Unit cost must be greater than zero",
        ),
        GuardCondition(
            guard_type=GuardType.BLOCK,
            expression="warehouse_active == false",
            reason_code="WAREHOUSE_INACTIVE",
            message="Cannot receive into inactive warehouse",
        ),
    ),
)


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture(autouse=True)
def clear_schema_registry():
    """Clear schema registry before and after each test."""
    EventSchemaRegistry.clear()
    yield
    EventSchemaRegistry.clear()


@pytest.fixture
def schema_registry() -> EventSchemaRegistry:
    """Create registry with inventory receipt schema."""
    registry = EventSchemaRegistry()
    registry.register(INVENTORY_RECEIPT_SCHEMA)
    return registry


@pytest.fixture
def meaning_builder() -> MeaningBuilder:
    """Create a MeaningBuilder instance."""
    return MeaningBuilder()


def create_valid_receipt_payload(
    quantity: Decimal = Decimal("100.00"),
    unit_cost: Decimal = Decimal("25.50"),
    warehouse_active: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a valid inventory receipt payload."""
    payload = {
        "receipt_number": "RCV-2024-001234",
        "supplier_id": str(uuid4()),
        "purchase_order_id": str(uuid4()),
        "sku": "RAW-STEEL-001",
        "quantity": str(quantity),
        "unit_cost": str(unit_cost),
        "currency": "USD",
        "warehouse_code": "WH-MAIN",
        "warehouse_active": warehouse_active,
        "received_date": "2024-06-15",
        "lot_number": "LOT-2024-Q2-001",
        "notes": "Standard delivery, no damage observed",
    }
    payload.update(overrides)
    return payload


# =============================================================================
# TESTS: Schema Validation
# =============================================================================

class TestInventoryReceiptSchema:
    """Test schema validation for inventory receipts."""

    def test_valid_payload_passes_validation(self, schema_registry: EventSchemaRegistry):
        """Valid receipt payload should pass schema validation."""
        payload = create_valid_receipt_payload()
        errors = validate_payload_against_schema(payload, INVENTORY_RECEIPT_SCHEMA)

        assert len(errors) == 0, f"Expected valid but got errors: {errors}"

    def test_missing_required_field_fails(self, schema_registry: EventSchemaRegistry):
        """Missing required field should fail validation."""
        payload = create_valid_receipt_payload()
        del payload["quantity"]

        errors = validate_payload_against_schema(payload, INVENTORY_RECEIPT_SCHEMA)

        assert len(errors) > 0
        assert any("quantity" in str(e) for e in errors)

    def test_invalid_currency_fails(self, schema_registry: EventSchemaRegistry):
        """Invalid currency code should fail validation."""
        payload = create_valid_receipt_payload(currency="INVALID")

        errors = validate_payload_against_schema(payload, INVENTORY_RECEIPT_SCHEMA)

        assert len(errors) > 0
        assert any("currency" in str(e).lower() for e in errors)

    def test_negative_quantity_fails_schema_min(self, schema_registry: EventSchemaRegistry):
        """Quantity below minimum should fail schema validation."""
        payload = create_valid_receipt_payload()
        payload["quantity"] = "-5.00"

        errors = validate_payload_against_schema(payload, INVENTORY_RECEIPT_SCHEMA)

        assert len(errors) > 0


# =============================================================================
# TESTS: Economic Profile Configuration
# =============================================================================

class TestInventoryReceiptProfile:
    """Test the inventory receipt profile configuration."""

    def test_profile_is_valid(self):
        """Profile should be properly configured."""
        profile = INVENTORY_RECEIPT_PROFILE

        assert profile.name == "InventoryReceipt_Standard"
        assert profile.version == 1
        assert profile.trigger.event_type == "warehouse.inventory_receipt"
        assert profile.meaning.economic_type == "InventoryIncrease"

    def test_profile_has_correct_ledger_effects(self):
        """Profile should define GL and subledger effects."""
        effects = INVENTORY_RECEIPT_PROFILE.ledger_effects

        assert len(effects) == 2

        gl_effect = next(e for e in effects if e.ledger == "GL")
        assert gl_effect.debit_role == "InventoryAsset"
        assert gl_effect.credit_role == "GRNI"

        sub_effect = next(e for e in effects if e.ledger == "InventorySubledger")
        assert sub_effect.debit_role == "InventoryOnHand"

    def test_profile_has_guards(self):
        """Profile should have reject and block guards."""
        profile = INVENTORY_RECEIPT_PROFILE

        reject_guards = profile.get_reject_guards()
        block_guards = profile.get_block_guards()

        assert len(reject_guards) == 2  # quantity and unit_cost
        assert len(block_guards) == 1   # warehouse_active

    def test_profile_effective_date_range(self):
        """Profile should be effective for current dates."""
        profile = INVENTORY_RECEIPT_PROFILE

        assert profile.is_effective_on(date(2024, 6, 15))
        assert profile.is_effective_on(date(2025, 1, 1))
        assert not profile.is_effective_on(date(2023, 12, 31))

    def test_profile_scope_matching(self):
        """Profile scope should match SKU patterns."""
        profile = INVENTORY_RECEIPT_PROFILE

        assert profile.matches_scope("SKU:RAW-STEEL-001")
        assert profile.matches_scope("SKU:anything")
        assert not profile.matches_scope("PROJECT:123")


# =============================================================================
# TESTS: MeaningBuilder - Successful Interpretation
# =============================================================================

class TestMeaningBuilderSuccess:
    """Test successful economic event creation."""

    def test_build_creates_economic_event(self, meaning_builder: MeaningBuilder):
        """Valid payload should produce an economic event."""
        event_id = uuid4()
        payload = create_valid_receipt_payload(
            quantity=Decimal("100.00"),
        )

        result = meaning_builder.build(
            event_id=event_id,
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert result.success
        assert result.economic_event is not None
        assert result.economic_event.source_event_id == event_id
        assert result.economic_event.economic_type == "InventoryIncrease"
        assert result.economic_event.quantity == Decimal("100.00")

    def test_build_extracts_dimensions(self, meaning_builder: MeaningBuilder):
        """Builder should extract configured dimensions from payload."""
        payload = create_valid_receipt_payload()
        payload["sku"] = "RAW-STEEL-001"
        payload["warehouse_code"] = "WH-EAST"
        payload["lot_number"] = "LOT-ABC-123"

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert result.success
        dimensions = result.economic_event.dimensions
        assert dimensions is not None
        assert dimensions["sku"] == "RAW-STEEL-001"
        assert dimensions["warehouse_code"] == "WH-EAST"
        assert dimensions["lot_number"] == "LOT-ABC-123"

    def test_build_includes_profile_info(self, meaning_builder: MeaningBuilder):
        """Economic event should include profile identification."""
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=create_valid_receipt_payload(),
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert result.success
        assert result.economic_event.profile_id == "InventoryReceipt_Standard"
        assert result.economic_event.profile_version == 1

    def test_build_with_reference_snapshot(self, meaning_builder: MeaningBuilder):
        """Builder should preserve reference snapshot for replay."""
        snapshot = ReferenceSnapshot(
            coa_version=42,
            dimension_schema_version=3,
            currency_registry_version=1,
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=create_valid_receipt_payload(),
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
            snapshot=snapshot,
        )

        assert result.success
        assert result.economic_event.snapshot is not None
        assert result.economic_event.snapshot.coa_version == 42


# =============================================================================
# TESTS: MeaningBuilder - Guard Rejections
# =============================================================================

class TestMeaningBuilderRejections:
    """Test guard rejections for invalid economic reality."""

    def test_reject_zero_quantity(self, meaning_builder: MeaningBuilder):
        """Zero quantity should be rejected."""
        payload = create_valid_receipt_payload()
        payload["quantity"] = "0"

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert not result.success
        assert result.guard_result is not None
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_QUANTITY"

    def test_reject_negative_quantity(self, meaning_builder: MeaningBuilder):
        """Negative quantity should be rejected."""
        payload = create_valid_receipt_payload()
        payload["quantity"] = "-50"

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_QUANTITY"

    def test_reject_zero_unit_cost(self, meaning_builder: MeaningBuilder):
        """Zero unit cost should be rejected."""
        payload = create_valid_receipt_payload()
        payload["unit_cost"] = "0"

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert not result.success
        assert result.guard_result.rejected
        assert result.guard_result.reason_code == "INVALID_UNIT_COST"


# =============================================================================
# TESTS: MeaningBuilder - Guard Blocks
# =============================================================================

class TestMeaningBuilderBlocks:
    """Test guard blocks for system constraints."""

    def test_block_inactive_warehouse(self, meaning_builder: MeaningBuilder):
        """Inactive warehouse should trigger a block (not reject)."""
        payload = create_valid_receipt_payload(warehouse_active=False)

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert not result.success
        assert result.guard_result is not None
        assert result.guard_result.blocked
        assert not result.guard_result.rejected  # Block, not reject
        assert result.guard_result.reason_code == "WAREHOUSE_INACTIVE"

    def test_block_is_resumable(self, meaning_builder: MeaningBuilder):
        """Blocked event should succeed once warehouse is active."""
        # First attempt: warehouse inactive -> blocked
        payload = create_valid_receipt_payload(warehouse_active=False)
        result1 = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )
        assert result1.guard_result.blocked

        # Second attempt: warehouse now active -> success
        payload["warehouse_active"] = True
        result2 = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )
        assert result2.success


# =============================================================================
# TESTS: AccountingIntent Generation
# =============================================================================

class TestAccountingIntentGeneration:
    """Test accounting intent generation from economic events."""

    def test_create_accounting_intent_from_receipt(self, meaning_builder: MeaningBuilder):
        """Generate accounting intent from successful interpretation."""
        payload = create_valid_receipt_payload(
            quantity=Decimal("100"),
            unit_cost=Decimal("25.50"),
        )

        # Step 1: Build economic event
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )
        assert result.success

        # Step 2: Create accounting intent
        # (In production, this would be done by an IntentBuilder service)
        econ_event = result.economic_event
        extended_cost = Decimal(payload["quantity"]) * Decimal(payload["unit_cost"])

        intent = AccountingIntent(
            econ_event_id=uuid4(),  # Would be assigned after persistence
            source_event_id=econ_event.source_event_id,
            profile_id=econ_event.profile_id,
            profile_version=econ_event.profile_version,
            effective_date=econ_event.effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit(
                            role="InventoryAsset",
                            amount=extended_cost,
                            currency="USD",
                            dimensions={"warehouse": "WH-MAIN"},
                        ),
                        IntentLine.credit(
                            role="GRNI",
                            amount=extended_cost,
                            currency="USD",
                        ),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
            description=f"Inventory receipt: {payload['receipt_number']}",
        )

        # Verify intent structure
        assert intent.all_balanced()
        assert "GL" in intent.ledger_ids
        assert "InventoryAsset" in intent.all_roles
        assert "GRNI" in intent.all_roles

        gl_intent = intent.get_ledger_intent("GL")
        assert gl_intent.total_debits() == extended_cost
        assert gl_intent.total_credits() == extended_cost

    def test_multi_ledger_intent(self, meaning_builder: MeaningBuilder):
        """Create intent posting to both GL and subledger."""
        extended_cost = Decimal("2550.00")  # 100 * 25.50

        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=uuid4(),
            profile_id="InventoryReceipt_Standard",
            profile_version=1,
            effective_date=date(2024, 6, 15),
            ledger_intents=(
                # General Ledger entry
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("InventoryAsset", extended_cost, "USD"),
                        IntentLine.credit("GRNI", extended_cost, "USD"),
                    ),
                ),
                # Inventory Subledger entry
                LedgerIntent(
                    ledger_id="InventorySubledger",
                    lines=(
                        IntentLine.debit(
                            "InventoryOnHand",
                            extended_cost,
                            "USD",
                            dimensions={"sku": "RAW-STEEL-001", "warehouse": "WH-MAIN"},
                        ),
                        IntentLine.credit("InventoryInTransit", extended_cost, "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=1,
                dimension_schema_version=1,
            ),
        )

        assert len(intent.ledger_intents) == 2
        assert intent.all_balanced()
        assert "GL" in intent.ledger_ids
        assert "InventorySubledger" in intent.ledger_ids


# =============================================================================
# TESTS: Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_large_quantity_receipt(self, meaning_builder: MeaningBuilder):
        """Handle receipts with large quantities."""
        payload = create_valid_receipt_payload(
            quantity=Decimal("999999999.999"),
            unit_cost=Decimal("0.01"),
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert result.success
        assert result.economic_event.quantity == Decimal("999999999.999")

    def test_fractional_quantity(self, meaning_builder: MeaningBuilder):
        """Handle fractional quantities (e.g., bulk materials)."""
        payload = create_valid_receipt_payload(
            quantity=Decimal("123.456789"),
        )

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert result.success
        assert result.economic_event.quantity == Decimal("123.456789")

    def test_missing_optional_lot_number(self, meaning_builder: MeaningBuilder):
        """Handle receipts without lot tracking."""
        payload = create_valid_receipt_payload()
        del payload["lot_number"]

        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert result.success
        # Dimension should not include lot_number
        assert "lot_number" not in (result.economic_event.dimensions or {})

    def test_profile_event_type_mismatch(self, meaning_builder: MeaningBuilder):
        """Wrong event type should fail validation."""
        result = meaning_builder.build(
            event_id=uuid4(),
            event_type="sales.shipment",  # Wrong type!
            payload=create_valid_receipt_payload(),
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
        )

        assert not result.success
        assert len(result.validation_errors) > 0
        assert any("mismatch" in str(e).lower() for e in result.validation_errors)


# =============================================================================
# INTEGRATION TEST: Full Pipeline
# =============================================================================

class TestFullInterpretationPipeline:
    """Integration test for the complete interpretation flow."""

    def test_receipt_interpretation_end_to_end(
        self,
        schema_registry: EventSchemaRegistry,
        meaning_builder: MeaningBuilder,
    ):
        """
        Full pipeline: Event -> Schema Validation -> Profile Match ->
        Guard Evaluation -> Economic Event -> Accounting Intent
        """
        # 1. Create the business event payload
        payload = {
            "receipt_number": "RCV-2024-001234",
            "supplier_id": str(uuid4()),
            "purchase_order_id": str(uuid4()),
            "sku": "RAW-ALUMINUM-002",
            "quantity": "500",
            "unit_cost": "12.75",
            "currency": "USD",
            "warehouse_code": "WH-WEST",
            "warehouse_active": True,
            "received_date": "2024-06-15",
            "lot_number": "LOT-2024-AL-007",
            "notes": "Partial shipment - remaining 500 units expected next week",
        }

        # 2. Validate against schema
        schema_errors = validate_payload_against_schema(payload, INVENTORY_RECEIPT_SCHEMA)
        assert len(schema_errors) == 0, f"Schema validation failed: {schema_errors}"

        # 3. Build economic meaning
        event_id = uuid4()
        snapshot = ReferenceSnapshot(
            coa_version=5,
            dimension_schema_version=2,
            currency_registry_version=1,
        )

        meaning_result = meaning_builder.build(
            event_id=event_id,
            event_type="warehouse.inventory_receipt",
            payload=payload,
            effective_date=date(2024, 6, 15),
            profile=INVENTORY_RECEIPT_PROFILE,
            snapshot=snapshot,
            trace_id=uuid4(),
        )

        assert meaning_result.success
        econ_event = meaning_result.economic_event

        # 4. Verify economic event
        assert econ_event.economic_type == "InventoryIncrease"
        assert econ_event.quantity == Decimal("500")
        assert econ_event.dimensions["sku"] == "RAW-ALUMINUM-002"
        assert econ_event.dimensions["warehouse_code"] == "WH-WEST"
        assert econ_event.snapshot.coa_version == 5

        # 5. Generate accounting intent
        extended_cost = Decimal("500") * Decimal("12.75")  # 6375.00

        intent = AccountingIntent(
            econ_event_id=uuid4(),
            source_event_id=econ_event.source_event_id,
            profile_id=econ_event.profile_id,
            profile_version=econ_event.profile_version,
            effective_date=econ_event.effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit(
                            role="InventoryAsset",
                            amount=extended_cost,
                            currency="USD",
                            dimensions={"warehouse": "WH-WEST"},
                        ),
                        IntentLine.credit(
                            role="GRNI",
                            amount=extended_cost,
                            currency="USD",
                        ),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(
                coa_version=5,
                dimension_schema_version=2,
            ),
            description=f"Inventory receipt: {payload['receipt_number']} - {payload['sku']}",
            trace_id=econ_event.trace_id,
        )

        # 6. Verify accounting intent
        assert intent.all_balanced()
        gl = intent.get_ledger_intent("GL")
        assert gl.total_debits() == Decimal("6375.00")
        assert gl.total_credits() == Decimal("6375.00")

        # 7. Verify idempotency key
        idem_key = intent.idempotency_key("GL")
        assert "GL" in idem_key
        assert str(intent.econ_event_id) in idem_key
