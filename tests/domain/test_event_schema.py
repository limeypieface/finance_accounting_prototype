"""
Tests for event schema registry and validation.

Tests cover:
- Schema data structures (EventFieldSchema, EventSchema)
- Registry operations (register, get, has_schema)
- Field path resolution
- Payload validation against schemas
- Field reference validation (P10 compliance)
"""

from decimal import Decimal

import pytest

from finance_kernel.domain.dtos import ValidationError
from finance_kernel.domain.event_validator import (
    validate_field_references,
    validate_payload_against_schema,
)
from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import (
    EventSchemaRegistry,
    SchemaAlreadyRegisteredError,
    SchemaNotFoundError,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the registry before and after each test."""
    EventSchemaRegistry.clear()
    yield
    EventSchemaRegistry.clear()


@pytest.fixture
def simple_schema() -> EventSchema:
    """Simple schema for basic tests."""
    return EventSchema(
        event_type="test.simple",
        version=1,
        fields=(
            EventFieldSchema(
                name="amount",
                field_type=EventFieldType.DECIMAL,
                required=True,
            ),
            EventFieldSchema(
                name="currency",
                field_type=EventFieldType.CURRENCY,
                required=True,
            ),
            EventFieldSchema(
                name="description",
                field_type=EventFieldType.STRING,
                required=False,
                nullable=True,
            ),
        ),
    )


@pytest.fixture
def nested_schema() -> EventSchema:
    """Schema with nested objects and arrays."""
    return EventSchema(
        event_type="test.nested",
        version=1,
        fields=(
            EventFieldSchema(
                name="header",
                field_type=EventFieldType.OBJECT,
                required=True,
                nested_fields=(
                    EventFieldSchema(
                        name="reference",
                        field_type=EventFieldType.STRING,
                        required=True,
                    ),
                    EventFieldSchema(
                        name="date",
                        field_type=EventFieldType.DATE,
                        required=True,
                    ),
                ),
            ),
            EventFieldSchema(
                name="items",
                field_type=EventFieldType.ARRAY,
                required=True,
                item_type=EventFieldType.OBJECT,
                item_schema=(
                    EventFieldSchema(
                        name="sku",
                        field_type=EventFieldType.STRING,
                        required=True,
                    ),
                    EventFieldSchema(
                        name="quantity",
                        field_type=EventFieldType.DECIMAL,
                        required=True,
                        min_value=Decimal("0.0001"),
                    ),
                    EventFieldSchema(
                        name="unit_price",
                        field_type=EventFieldType.DECIMAL,
                        required=True,
                        min_value=Decimal("0"),
                    ),
                ),
            ),
        ),
    )


# ============================================================================
# Schema Data Structure Tests
# ============================================================================


class TestEventFieldSchema:
    """Tests for EventFieldSchema dataclass."""

    def test_basic_field(self):
        """Field with minimal configuration."""
        field = EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
        )
        assert field.name == "amount"
        assert field.field_type == EventFieldType.DECIMAL
        assert field.required is True
        assert field.nullable is False

    def test_optional_field(self):
        """Optional nullable field."""
        field = EventFieldSchema(
            name="memo",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
        )
        assert field.required is False
        assert field.nullable is True

    def test_field_with_constraints(self):
        """Field with validation constraints."""
        field = EventFieldSchema(
            name="quantity",
            field_type=EventFieldType.DECIMAL,
            min_value=Decimal("0.0001"),
            max_value=Decimal("999999"),
        )
        assert field.min_value == Decimal("0.0001")
        assert field.max_value == Decimal("999999")

    def test_string_field_constraints(self):
        """String field with length and pattern constraints."""
        field = EventFieldSchema(
            name="code",
            field_type=EventFieldType.STRING,
            min_length=1,
            max_length=50,
            pattern=r"^[A-Z0-9]+$",
        )
        assert field.min_length == 1
        assert field.max_length == 50
        assert field.pattern == r"^[A-Z0-9]+$"

    def test_allowed_values(self):
        """Field with enum-like allowed values."""
        field = EventFieldSchema(
            name="status",
            field_type=EventFieldType.STRING,
            allowed_values=frozenset({"PENDING", "APPROVED", "REJECTED"}),
        )
        assert "PENDING" in field.allowed_values
        assert "INVALID" not in field.allowed_values

    def test_object_field_requires_nested_fields(self):
        """OBJECT type must have nested_fields."""
        with pytest.raises(ValueError, match="must have nested_fields"):
            EventFieldSchema(
                name="header",
                field_type=EventFieldType.OBJECT,
            )

    def test_array_field_requires_item_type_or_schema(self):
        """ARRAY type must have item_type or item_schema."""
        with pytest.raises(ValueError, match="must have item_type or item_schema"):
            EventFieldSchema(
                name="items",
                field_type=EventFieldType.ARRAY,
            )

    def test_field_is_frozen(self):
        """Fields are immutable."""
        field = EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
        )
        with pytest.raises(AttributeError):
            field.name = "changed"


class TestEventSchema:
    """Tests for EventSchema dataclass."""

    def test_basic_schema(self, simple_schema):
        """Schema with basic fields."""
        assert simple_schema.event_type == "test.simple"
        assert simple_schema.version == 1
        assert len(simple_schema.fields) == 3

    def test_schema_key(self, simple_schema):
        """Schema key is event_type:vN."""
        assert simple_schema.schema_key == "test.simple:v1"

    def test_schema_requires_namespaced_event_type(self):
        """Event type must contain a dot."""
        with pytest.raises(ValueError, match="must be namespaced"):
            EventSchema(
                event_type="noDot",
                version=1,
                fields=(),
            )

    def test_schema_requires_positive_version(self):
        """Version must be >= 1."""
        with pytest.raises(ValueError, match="version must be >= 1"):
            EventSchema(
                event_type="test.event",
                version=0,
                fields=(),
            )

    def test_schema_is_frozen(self, simple_schema):
        """Schemas are immutable."""
        with pytest.raises(AttributeError):
            simple_schema.event_type = "changed"


class TestFieldPaths:
    """Tests for field path resolution."""

    def test_simple_field_paths(self, simple_schema):
        """Top-level fields are in path set."""
        paths = simple_schema.all_field_paths()
        assert "amount" in paths
        assert "currency" in paths
        assert "description" in paths

    def test_nested_field_paths(self, nested_schema):
        """Nested object fields use dot notation."""
        paths = nested_schema.all_field_paths()
        assert "header" in paths
        assert "header.reference" in paths
        assert "header.date" in paths

    def test_array_field_paths(self, nested_schema):
        """Array item fields use [*] notation."""
        paths = nested_schema.all_field_paths()
        assert "items" in paths
        assert "items[*].sku" in paths
        assert "items[*].quantity" in paths
        assert "items[*].unit_price" in paths

    def test_has_field(self, nested_schema):
        """has_field checks path existence."""
        assert nested_schema.has_field("header")
        assert nested_schema.has_field("header.reference")
        assert nested_schema.has_field("items[*].sku")
        assert not nested_schema.has_field("nonexistent")
        assert not nested_schema.has_field("header.nonexistent")

    def test_get_field(self, nested_schema):
        """get_field returns schema for path."""
        header = nested_schema.get_field("header")
        assert header is not None
        assert header.name == "header"
        assert header.field_type == EventFieldType.OBJECT

        ref = nested_schema.get_field("header.reference")
        assert ref is not None
        assert ref.name == "reference"

        sku = nested_schema.get_field("items[*].sku")
        assert sku is not None
        assert sku.name == "sku"

    def test_get_field_returns_none_for_invalid(self, nested_schema):
        """get_field returns None for invalid paths."""
        assert nested_schema.get_field("nonexistent") is None
        assert nested_schema.get_field("header.nonexistent") is None


# ============================================================================
# Registry Tests
# ============================================================================


class TestEventSchemaRegistry:
    """Tests for EventSchemaRegistry."""

    def test_register_and_get(self, simple_schema):
        """Register and retrieve a schema."""
        EventSchemaRegistry.register(simple_schema)

        retrieved = EventSchemaRegistry.get("test.simple", version=1)
        assert retrieved is simple_schema

    def test_get_latest_version(self):
        """get() without version returns latest."""
        v1 = EventSchema(event_type="test.versioned", version=1, fields=())
        v2 = EventSchema(event_type="test.versioned", version=2, fields=())

        EventSchemaRegistry.register(v1)
        EventSchemaRegistry.register(v2)

        latest = EventSchemaRegistry.get("test.versioned")
        assert latest.version == 2

    def test_has_schema(self, simple_schema):
        """has_schema checks registration."""
        assert not EventSchemaRegistry.has_schema("test.simple")

        EventSchemaRegistry.register(simple_schema)

        assert EventSchemaRegistry.has_schema("test.simple")
        assert EventSchemaRegistry.has_schema("test.simple", version=1)
        assert not EventSchemaRegistry.has_schema("test.simple", version=2)

    def test_get_all_versions(self):
        """get_all_versions returns sorted list."""
        EventSchemaRegistry.register(
            EventSchema(event_type="test.multi", version=3, fields=())
        )
        EventSchemaRegistry.register(
            EventSchema(event_type="test.multi", version=1, fields=())
        )
        EventSchemaRegistry.register(
            EventSchema(event_type="test.multi", version=2, fields=())
        )

        versions = EventSchemaRegistry.get_all_versions("test.multi")
        assert versions == [1, 2, 3]

    def test_list_event_types(self, simple_schema, nested_schema):
        """list_event_types returns sorted list."""
        EventSchemaRegistry.register(simple_schema)
        EventSchemaRegistry.register(nested_schema)

        types = EventSchemaRegistry.list_event_types()
        assert types == ["test.nested", "test.simple"]

    def test_duplicate_registration_raises(self, simple_schema):
        """Cannot register same event_type + version twice."""
        EventSchemaRegistry.register(simple_schema)

        with pytest.raises(SchemaAlreadyRegisteredError):
            EventSchemaRegistry.register(simple_schema)

    def test_get_nonexistent_raises(self):
        """get() raises for unregistered schemas."""
        with pytest.raises(SchemaNotFoundError):
            EventSchemaRegistry.get("nonexistent.event")

    def test_get_nonexistent_version_raises(self, simple_schema):
        """get() raises for unregistered version."""
        EventSchemaRegistry.register(simple_schema)

        with pytest.raises(SchemaNotFoundError):
            EventSchemaRegistry.get("test.simple", version=99)

    def test_clear(self, simple_schema):
        """clear() removes all registrations."""
        EventSchemaRegistry.register(simple_schema)
        assert EventSchemaRegistry.has_schema("test.simple")

        EventSchemaRegistry.clear()
        assert not EventSchemaRegistry.has_schema("test.simple")


# ============================================================================
# Payload Validation Tests
# ============================================================================


class TestPayloadValidation:
    """Tests for validate_payload_against_schema."""

    def test_valid_payload(self, simple_schema):
        """Valid payload returns no errors."""
        payload = {
            "amount": "100.50",
            "currency": "USD",
            "description": "Test payment",
        }
        errors = validate_payload_against_schema(payload, simple_schema)
        assert len(errors) == 0

    def test_missing_required_field(self, simple_schema):
        """Missing required field returns error."""
        payload = {
            "currency": "USD",
            # missing amount
        }
        errors = validate_payload_against_schema(payload, simple_schema)
        assert len(errors) == 1
        assert errors[0].code == "MISSING_REQUIRED_FIELD"
        assert "amount" in errors[0].field

    def test_invalid_type(self, simple_schema):
        """Invalid type returns error."""
        payload = {
            "amount": "not a number",
            "currency": "USD",
        }
        errors = validate_payload_against_schema(payload, simple_schema)
        assert len(errors) == 1
        assert errors[0].code == "INVALID_TYPE"

    def test_invalid_currency(self, simple_schema):
        """Invalid currency code returns error."""
        payload = {
            "amount": "100.00",
            "currency": "INVALID",
        }
        errors = validate_payload_against_schema(payload, simple_schema)
        assert len(errors) == 1
        assert errors[0].code == "INVALID_CURRENCY"

    def test_optional_field_can_be_missing(self, simple_schema):
        """Optional fields don't require a value."""
        payload = {
            "amount": "100.00",
            "currency": "USD",
            # description is optional
        }
        errors = validate_payload_against_schema(payload, simple_schema)
        assert len(errors) == 0

    def test_nullable_field_can_be_null(self, simple_schema):
        """Nullable fields accept null values."""
        payload = {
            "amount": "100.00",
            "currency": "USD",
            "description": None,
        }
        errors = validate_payload_against_schema(payload, simple_schema)
        assert len(errors) == 0

    def test_min_value_constraint(self):
        """Values below min_value return error."""
        schema = EventSchema(
            event_type="test.minmax",
            version=1,
            fields=(
                EventFieldSchema(
                    name="quantity",
                    field_type=EventFieldType.DECIMAL,
                    min_value=Decimal("1"),
                ),
            ),
        )
        payload = {"quantity": "0.5"}
        errors = validate_payload_against_schema(payload, schema)
        assert len(errors) == 1
        assert errors[0].code == "VALUE_TOO_SMALL"

    def test_max_value_constraint(self):
        """Values above max_value return error."""
        schema = EventSchema(
            event_type="test.minmax",
            version=1,
            fields=(
                EventFieldSchema(
                    name="quantity",
                    field_type=EventFieldType.DECIMAL,
                    max_value=Decimal("100"),
                ),
            ),
        )
        payload = {"quantity": "150"}
        errors = validate_payload_against_schema(payload, schema)
        assert len(errors) == 1
        assert errors[0].code == "VALUE_TOO_LARGE"

    def test_string_length_constraints(self):
        """String length constraints are enforced."""
        schema = EventSchema(
            event_type="test.strlen",
            version=1,
            fields=(
                EventFieldSchema(
                    name="code",
                    field_type=EventFieldType.STRING,
                    min_length=3,
                    max_length=10,
                ),
            ),
        )

        # Too short
        errors = validate_payload_against_schema({"code": "AB"}, schema)
        assert len(errors) == 1
        assert errors[0].code == "STRING_TOO_SHORT"

        # Too long
        errors = validate_payload_against_schema({"code": "ABCDEFGHIJK"}, schema)
        assert len(errors) == 1
        assert errors[0].code == "STRING_TOO_LONG"

        # Just right
        errors = validate_payload_against_schema({"code": "ABCDE"}, schema)
        assert len(errors) == 0

    def test_pattern_constraint(self):
        """Pattern constraint validates regex."""
        schema = EventSchema(
            event_type="test.pattern",
            version=1,
            fields=(
                EventFieldSchema(
                    name="code",
                    field_type=EventFieldType.STRING,
                    pattern=r"^[A-Z]{3}$",
                ),
            ),
        )

        errors = validate_payload_against_schema({"code": "abc"}, schema)
        assert len(errors) == 1
        assert errors[0].code == "PATTERN_MISMATCH"

        errors = validate_payload_against_schema({"code": "ABC"}, schema)
        assert len(errors) == 0

    def test_allowed_values_constraint(self):
        """Allowed values constraint enforces enum."""
        schema = EventSchema(
            event_type="test.enum",
            version=1,
            fields=(
                EventFieldSchema(
                    name="status",
                    field_type=EventFieldType.STRING,
                    allowed_values=frozenset({"A", "B", "C"}),
                ),
            ),
        )

        errors = validate_payload_against_schema({"status": "X"}, schema)
        assert len(errors) == 1
        assert errors[0].code == "VALUE_NOT_ALLOWED"

        errors = validate_payload_against_schema({"status": "A"}, schema)
        assert len(errors) == 0

    def test_nested_object_validation(self, nested_schema):
        """Nested objects are validated recursively."""
        payload = {
            "header": {
                "reference": "REF-001",
                "date": "2024-01-15",
            },
            "items": [
                {"sku": "SKU-1", "quantity": "10", "unit_price": "5.00"},
            ],
        }
        errors = validate_payload_against_schema(payload, nested_schema)
        assert len(errors) == 0

    def test_nested_object_missing_field(self, nested_schema):
        """Missing nested fields return errors."""
        payload = {
            "header": {
                # missing reference
                "date": "2024-01-15",
            },
            "items": [],
        }
        errors = validate_payload_against_schema(payload, nested_schema)
        assert len(errors) == 1
        assert "header.reference" in errors[0].field

    def test_array_item_validation(self, nested_schema):
        """Array items are validated."""
        payload = {
            "header": {"reference": "REF-001", "date": "2024-01-15"},
            "items": [
                {"sku": "SKU-1", "quantity": "10", "unit_price": "5.00"},
                {"sku": "SKU-2", "quantity": "-5", "unit_price": "3.00"},  # negative qty
            ],
        }
        errors = validate_payload_against_schema(payload, nested_schema)
        assert len(errors) == 1
        assert "items[1].quantity" in errors[0].field
        assert errors[0].code == "VALUE_TOO_SMALL"

    def test_date_format_validation(self):
        """Date format is validated."""
        schema = EventSchema(
            event_type="test.date",
            version=1,
            fields=(
                EventFieldSchema(name="date", field_type=EventFieldType.DATE),
            ),
        )

        # Invalid format
        errors = validate_payload_against_schema({"date": "01-15-2024"}, schema)
        assert len(errors) == 1
        assert errors[0].code == "INVALID_DATE_FORMAT"

        # Valid format
        errors = validate_payload_against_schema({"date": "2024-01-15"}, schema)
        assert len(errors) == 0

    def test_uuid_format_validation(self):
        """UUID format is validated."""
        schema = EventSchema(
            event_type="test.uuid",
            version=1,
            fields=(
                EventFieldSchema(name="id", field_type=EventFieldType.UUID),
            ),
        )

        # Invalid format
        errors = validate_payload_against_schema({"id": "not-a-uuid"}, schema)
        assert len(errors) == 1
        assert errors[0].code == "INVALID_UUID_FORMAT"

        # Valid format
        errors = validate_payload_against_schema(
            {"id": "123e4567-e89b-12d3-a456-426614174000"}, schema
        )
        assert len(errors) == 0


# ============================================================================
# Field Reference Validation Tests (P10)
# ============================================================================


class TestFieldReferenceValidation:
    """Tests for validate_field_references (P10 compliance)."""

    def test_valid_references(self, nested_schema):
        """Valid field paths return no errors."""
        paths = ["header", "header.reference", "items", "items[*].sku"]
        errors = validate_field_references(paths, nested_schema)
        assert len(errors) == 0

    def test_invalid_reference(self, nested_schema):
        """Invalid field path returns error."""
        paths = ["header.nonexistent", "items[*].invalid_field"]
        errors = validate_field_references(paths, nested_schema)
        assert len(errors) == 2
        assert all(e.code == "INVALID_FIELD_REFERENCE" for e in errors)

    def test_mixed_valid_invalid(self, nested_schema):
        """Mix of valid and invalid paths."""
        paths = ["header.reference", "nonexistent", "items[*].sku"]
        errors = validate_field_references(paths, nested_schema)
        assert len(errors) == 1
        assert "nonexistent" in errors[0].field


# ============================================================================
# Generic Posting Schema Tests
# ============================================================================


class TestGenericPostingSchema:
    """Tests for the generic.posting schema definition."""

    def test_schema_is_registered(self):
        """generic.posting schema is auto-registered."""
        # Import and register explicitly (registry was cleared by fixture)
        from finance_kernel.domain.schemas.definitions.generic import GENERIC_POSTING_V1

        if not EventSchemaRegistry.has_schema("generic.posting", version=1):
            EventSchemaRegistry.register(GENERIC_POSTING_V1)
        assert EventSchemaRegistry.has_schema("generic.posting", version=1)

    def test_valid_generic_posting_payload(self):
        """Valid generic.posting payload passes validation."""
        from finance_kernel.domain.schemas.definitions.generic import GENERIC_POSTING_V1

        if not EventSchemaRegistry.has_schema("generic.posting", version=1):
            EventSchemaRegistry.register(GENERIC_POSTING_V1)
        schema = EventSchemaRegistry.get("generic.posting", version=1)
        payload = {
            "description": "Test journal entry",
            "currency": "USD",
            "lines": [
                {"account_code": "1000", "debit": "100.00"},
                {"account_code": "2000", "credit": "100.00"},
            ],
        }
        errors = validate_payload_against_schema(payload, schema)
        assert len(errors) == 0

    def test_generic_posting_missing_description(self):
        """Missing description fails validation."""
        from finance_kernel.domain.schemas.definitions.generic import GENERIC_POSTING_V1

        if not EventSchemaRegistry.has_schema("generic.posting", version=1):
            EventSchemaRegistry.register(GENERIC_POSTING_V1)
        schema = EventSchemaRegistry.get("generic.posting", version=1)
        payload = {
            "currency": "USD",
            "lines": [
                {"account_code": "1000", "debit": "100.00"},
            ],
        }
        errors = validate_payload_against_schema(payload, schema)
        assert len(errors) == 1
        assert "description" in errors[0].field
