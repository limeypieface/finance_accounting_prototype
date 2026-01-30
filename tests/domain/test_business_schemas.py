"""
Tests for business event schemas (asset, inventory, AP, AR, bank, FX, deferred, payroll).

Tests cover:
- Schema registration and retrieval
- Field configuration validation
- Required field validation
- Allowed values constraints
"""

from decimal import Decimal

import pytest

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import (
    EventSchemaRegistry,
)

# Import definitions to trigger registration
from finance_kernel.domain.schemas.definitions import (
    ap,
    ar,
    asset,
    bank,
    deferred,
    fx,
    inventory,
    payroll,
)


# ============================================================================
# Helper Functions
# ============================================================================


def get_field(schema: EventSchema, field_name: str) -> EventFieldSchema | None:
    """Get a field from a schema by name."""
    for f in schema.fields:
        if f.name == field_name:
            return f
    return None


# ============================================================================
# Asset Schema Tests
# ============================================================================


class TestAssetAcquisitionSchema:
    """Tests for asset.acquisition schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("asset.acquisition", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema is not None
        assert schema.event_type == "asset.acquisition"
        assert schema.version == 1

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "asset_id", "asset_code", "description", "cost", "currency",
            "acquisition_date", "useful_life_months", "depreciation_method",
            "asset_category", "org_unit",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"
            assert field.required, f"Field {field_name} should be required"

    def test_cost_has_minimum_value(self, schema):
        """Cost field should have minimum value constraint."""
        field = get_field(schema, "cost")
        assert field.min_value == Decimal("0.01")

    def test_useful_life_has_range(self, schema):
        """Useful life should have min/max constraints."""
        field = get_field(schema, "useful_life_months")
        assert field.min_value == 1
        assert field.max_value == 600

    def test_depreciation_method_allowed_values(self, schema):
        """Depreciation method should have allowed values."""
        field = get_field(schema, "depreciation_method")
        assert field.allowed_values is not None
        assert "STRAIGHT_LINE" in field.allowed_values
        assert "DECLINING_BALANCE" in field.allowed_values
        assert "UNITS_OF_PRODUCTION" in field.allowed_values


class TestAssetDepreciationSchema:
    """Tests for asset.depreciation schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("asset.depreciation", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "asset.depreciation"

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "asset_id", "period_start", "period_end", "depreciation_amount",
            "currency", "accumulated_depreciation", "net_book_value", "org_unit",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"
            assert field.required, f"Field {field_name} should be required"


class TestAssetDisposalSchema:
    """Tests for asset.disposal schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("asset.disposal", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "asset.disposal"

    def test_disposal_type_allowed_values(self, schema):
        """Disposal type should have allowed values."""
        field = get_field(schema, "disposal_type")
        assert field.allowed_values is not None
        assert "SALE" in field.allowed_values
        assert "RETIREMENT" in field.allowed_values
        assert "WRITE_OFF" in field.allowed_values
        assert "TRANSFER" in field.allowed_values


# ============================================================================
# Inventory Schema Tests
# ============================================================================


class TestInventoryReceiptSchema:
    """Tests for inventory.receipt schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("inventory.receipt", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "inventory.receipt"

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "receipt_id", "item_code", "quantity", "unit_cost",
            "total_cost", "currency", "warehouse_code", "org_unit",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"
            assert field.required, f"Field {field_name} should be required"

    def test_quantity_minimum(self, schema):
        """Quantity should have minimum value."""
        field = get_field(schema, "quantity")
        assert field.min_value == Decimal("0.0001")


class TestInventoryIssueSchema:
    """Tests for inventory.issue schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("inventory.issue", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "inventory.issue"

    def test_issue_type_allowed_values(self, schema):
        """Issue type should have allowed values."""
        field = get_field(schema, "issue_type")
        assert field.allowed_values is not None
        assert "SALE" in field.allowed_values
        assert "PRODUCTION" in field.allowed_values
        assert "TRANSFER" in field.allowed_values
        assert "SCRAP" in field.allowed_values
        assert "SAMPLE" in field.allowed_values


class TestInventoryAdjustmentSchema:
    """Tests for inventory.adjustment schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("inventory.adjustment", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "inventory.adjustment"

    def test_adjustment_reason_allowed_values(self, schema):
        """Adjustment reason should have allowed values."""
        field = get_field(schema, "adjustment_reason")
        assert field.allowed_values is not None
        assert "PHYSICAL_COUNT" in field.allowed_values
        assert "DAMAGE" in field.allowed_values
        assert "THEFT" in field.allowed_values
        assert "OBSOLESCENCE" in field.allowed_values
        assert "ERROR_CORRECTION" in field.allowed_values


# ============================================================================
# AP Schema Tests
# ============================================================================


class TestAPInvoiceReceivedSchema:
    """Tests for ap.invoice_received schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("ap.invoice_received", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "ap.invoice_received"

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "invoice_id", "invoice_number", "supplier_party_code",
            "invoice_date", "due_date", "gross_amount", "net_amount",
            "currency", "org_unit",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"
            assert field.required, f"Field {field_name} should be required"

    def test_lines_is_array_of_objects(self, schema):
        """Lines should be array of objects."""
        field = get_field(schema, "lines")
        assert field.field_type == EventFieldType.ARRAY
        assert field.item_type == EventFieldType.OBJECT
        assert field.item_schema is not None


class TestAPPaymentSchema:
    """Tests for ap.payment schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("ap.payment", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "ap.payment"

    def test_payment_method_allowed_values(self, schema):
        """Payment method should have allowed values."""
        field = get_field(schema, "payment_method")
        assert field.allowed_values is not None
        assert "WIRE" in field.allowed_values
        assert "ACH" in field.allowed_values
        assert "CHECK" in field.allowed_values
        assert "CARD" in field.allowed_values
        assert "CASH" in field.allowed_values

    def test_invoice_allocations_is_required_array(self, schema):
        """Invoice allocations should be required array."""
        field = get_field(schema, "invoice_allocations")
        assert field.required
        assert field.field_type == EventFieldType.ARRAY


# ============================================================================
# AR Schema Tests
# ============================================================================


class TestARInvoiceIssuedSchema:
    """Tests for ar.invoice_issued schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("ar.invoice_issued", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "ar.invoice_issued"

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "invoice_id", "invoice_number", "customer_party_code",
            "invoice_date", "due_date", "gross_amount", "net_amount",
            "currency", "org_unit",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"


class TestARPaymentReceivedSchema:
    """Tests for ar.payment_received schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("ar.payment_received", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "ar.payment_received"


class TestARCreditMemoSchema:
    """Tests for ar.credit_memo schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("ar.credit_memo", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "ar.credit_memo"

    def test_reason_code_allowed_values(self, schema):
        """Reason code should have allowed values."""
        field = get_field(schema, "reason_code")
        assert field.allowed_values is not None
        assert "RETURN" in field.allowed_values
        assert "PRICE_ADJUSTMENT" in field.allowed_values
        assert "SERVICE_CREDIT" in field.allowed_values
        assert "ERROR_CORRECTION" in field.allowed_values


# ============================================================================
# Bank Schema Tests
# ============================================================================


class TestBankDepositSchema:
    """Tests for bank.deposit schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("bank.deposit", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "bank.deposit"

    def test_source_type_allowed_values(self, schema):
        """Source type should have allowed values."""
        field = get_field(schema, "source_type")
        assert field.allowed_values is not None
        assert "CUSTOMER_PAYMENT" in field.allowed_values
        assert "CASH_SALES" in field.allowed_values
        assert "TRANSFER" in field.allowed_values
        assert "OTHER" in field.allowed_values


class TestBankWithdrawalSchema:
    """Tests for bank.withdrawal schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("bank.withdrawal", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "bank.withdrawal"

    def test_destination_type_allowed_values(self, schema):
        """Destination type should have allowed values."""
        field = get_field(schema, "destination_type")
        assert field.allowed_values is not None
        assert "SUPPLIER_PAYMENT" in field.allowed_values
        assert "EXPENSE" in field.allowed_values
        assert "PAYROLL" in field.allowed_values


class TestBankTransferSchema:
    """Tests for bank.transfer schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("bank.transfer", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "bank.transfer"

    def test_has_from_and_to_accounts(self, schema):
        """Should have from and to bank account fields."""
        from_field = get_field(schema, "from_bank_account_code")
        to_field = get_field(schema, "to_bank_account_code")
        assert from_field is not None
        assert from_field.required
        assert to_field is not None
        assert to_field.required


class TestBankReconciliationSchema:
    """Tests for bank.reconciliation schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("bank.reconciliation", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "bank.reconciliation"

    def test_match_type_allowed_values(self, schema):
        """Match type should have allowed values."""
        field = get_field(schema, "match_type")
        assert field.allowed_values is not None
        assert "EXACT" in field.allowed_values
        assert "PARTIAL" in field.allowed_values
        assert "GROUPED" in field.allowed_values
        assert "MANUAL" in field.allowed_values


# ============================================================================
# FX Schema Tests
# ============================================================================


class TestFXRevaluationSchema:
    """Tests for fx.revaluation schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("fx.revaluation", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "fx.revaluation"

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "revaluation_id", "revaluation_date", "account_code",
            "foreign_currency", "functional_currency", "foreign_balance",
            "old_rate", "new_rate", "old_functional_value",
            "new_functional_value", "gain_loss", "is_realized", "org_unit",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"
            assert field.required, f"Field {field_name} should be required"

    def test_rate_fields_have_minimum(self, schema):
        """Rate fields should have minimum value."""
        old_rate = get_field(schema, "old_rate")
        new_rate = get_field(schema, "new_rate")
        assert old_rate.min_value == Decimal("0.000001")
        assert new_rate.min_value == Decimal("0.000001")


# ============================================================================
# Deferred Schema Tests
# ============================================================================


class TestDeferredRecognitionSchema:
    """Tests for deferred.recognition schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("deferred.recognition", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "deferred.recognition"

    def test_recognition_type_allowed_values(self, schema):
        """Recognition type should have allowed values."""
        field = get_field(schema, "recognition_type")
        assert field.allowed_values is not None
        assert "REVENUE" in field.allowed_values
        assert "EXPENSE" in field.allowed_values

    def test_period_fields_required(self, schema):
        """Period start and end should be required."""
        period_start = get_field(schema, "period_start")
        period_end = get_field(schema, "period_end")
        assert period_start.required
        assert period_end.required


# ============================================================================
# Payroll Schema Tests
# ============================================================================


class TestPayrollTimesheetSchema:
    """Tests for payroll.timesheet schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("payroll.timesheet", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "payroll.timesheet"

    def test_required_fields_present(self, schema):
        """All required fields should be defined."""
        required_fields = [
            "timesheet_id", "employee_party_code", "work_date", "hours",
            "pay_code", "hourly_rate", "total_amount", "currency",
            "is_billable", "org_unit", "cost_center",
        ]
        for field_name in required_fields:
            field = get_field(schema, field_name)
            assert field is not None, f"Field {field_name} not found"
            assert field.required, f"Field {field_name} should be required"

    def test_hours_has_range(self, schema):
        """Hours should have min/max constraints."""
        field = get_field(schema, "hours")
        assert field.min_value == Decimal("0.01")
        assert field.max_value == Decimal("24")

    def test_pay_code_allowed_values(self, schema):
        """Pay code should have allowed values."""
        field = get_field(schema, "pay_code")
        assert field.allowed_values is not None
        assert "REGULAR" in field.allowed_values
        assert "OVERTIME" in field.allowed_values
        assert "DOUBLE_TIME" in field.allowed_values
        assert "SICK" in field.allowed_values
        assert "VACATION" in field.allowed_values
        assert "HOLIDAY" in field.allowed_values


class TestPayrollLaborDistributionSchema:
    """Tests for payroll.labor_distribution schema."""

    @pytest.fixture
    def schema(self) -> EventSchema:
        return EventSchemaRegistry.get("payroll.labor_distribution", version=1)

    def test_schema_registered(self, schema):
        """Schema should be registered."""
        assert schema.event_type == "payroll.labor_distribution"

    def test_labor_type_allowed_values(self, schema):
        """Labor type should have allowed values."""
        field = get_field(schema, "labor_type")
        assert field.allowed_values is not None
        assert "DIRECT" in field.allowed_values
        assert "INDIRECT" in field.allowed_values
        assert "OVERHEAD" in field.allowed_values


# ============================================================================
# Schema Count Tests
# ============================================================================


class TestSchemaRegistration:
    """Tests for overall schema registration."""

    def test_total_schema_count(self):
        """All 20 event types should be registered."""
        event_types = EventSchemaRegistry.list_event_types()
        # 8 modules with various schemas
        expected_types = [
            # Asset
            "asset.acquisition", "asset.depreciation", "asset.disposal",
            # Inventory
            "inventory.receipt", "inventory.issue", "inventory.adjustment",
            # AP
            "ap.invoice_received", "ap.payment",
            # AR
            "ar.invoice_issued", "ar.payment_received", "ar.credit_memo",
            # Bank
            "bank.deposit", "bank.withdrawal", "bank.transfer", "bank.reconciliation",
            # FX
            "fx.revaluation",
            # Deferred
            "deferred.recognition",
            # Payroll
            "payroll.timesheet", "payroll.labor_distribution",
            # Generic (pre-existing)
            "generic.posting",
        ]
        for event_type in expected_types:
            assert event_type in event_types, f"Missing event type: {event_type}"

    def test_all_schemas_have_org_unit_dimension(self):
        """All schemas should have org_unit dimension field."""
        event_types = [
            "asset.acquisition", "asset.depreciation", "asset.disposal",
            "inventory.receipt", "inventory.issue", "inventory.adjustment",
            "ap.invoice_received", "ap.payment",
            "ar.invoice_issued", "ar.payment_received", "ar.credit_memo",
            "bank.deposit", "bank.withdrawal", "bank.transfer", "bank.reconciliation",
            "fx.revaluation", "deferred.recognition",
            "payroll.timesheet", "payroll.labor_distribution",
        ]
        for event_type in event_types:
            schema = EventSchemaRegistry.get(event_type)
            field = get_field(schema, "org_unit")
            assert field is not None, f"{event_type} missing org_unit field"
            assert field.required, f"{event_type} org_unit should be required"

    def test_all_monetary_schemas_have_currency(self):
        """All schemas with amounts should have currency field(s)."""
        # Standard schemas with single currency field
        event_types = [
            "asset.acquisition", "asset.depreciation", "asset.disposal",
            "inventory.receipt", "inventory.issue", "inventory.adjustment",
            "ap.invoice_received", "ap.payment",
            "ar.invoice_issued", "ar.payment_received", "ar.credit_memo",
            "bank.deposit", "bank.withdrawal",
            "deferred.recognition",
            "payroll.timesheet", "payroll.labor_distribution",
        ]
        for event_type in event_types:
            schema = EventSchemaRegistry.get(event_type)
            field = get_field(schema, "currency")
            assert field is not None, f"{event_type} missing currency field"
            assert field.field_type == EventFieldType.CURRENCY

        # bank.transfer has from_currency and to_currency (multi-currency support)
        transfer_schema = EventSchemaRegistry.get("bank.transfer")
        from_currency = get_field(transfer_schema, "from_currency")
        to_currency = get_field(transfer_schema, "to_currency")
        assert from_currency is not None, "bank.transfer missing from_currency"
        assert to_currency is not None, "bank.transfer missing to_currency"
        assert from_currency.field_type == EventFieldType.CURRENCY
        assert to_currency.field_type == EventFieldType.CURRENCY

        # fx.revaluation has foreign_currency and functional_currency
        fx_schema = EventSchemaRegistry.get("fx.revaluation")
        foreign_currency = get_field(fx_schema, "foreign_currency")
        functional_currency = get_field(fx_schema, "functional_currency")
        assert foreign_currency is not None, "fx.revaluation missing foreign_currency"
        assert functional_currency is not None, "fx.revaluation missing functional_currency"
        assert foreign_currency.field_type == EventFieldType.CURRENCY
        assert functional_currency.field_type == EventFieldType.CURRENCY
