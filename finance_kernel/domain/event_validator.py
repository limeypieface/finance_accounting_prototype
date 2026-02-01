"""EventValidator -- Pure event validation functions."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.domain.dtos import ValidationError, ValidationResult
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.event_validator")

if TYPE_CHECKING:
    from collections.abc import Iterable

    from finance_kernel.domain.schemas.base import EventFieldSchema, EventSchema


# Supported schema versions - pure constant
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


def validate_event(
    event_type: str,
    payload: dict[str, Any],
    schema_version: int,
    supported_versions: frozenset[int] | None = None,
) -> ValidationResult:
    """Validate an event at the domain boundary."""
    supported = supported_versions or SUPPORTED_SCHEMA_VERSIONS
    errors: list[ValidationError] = []

    logger.debug(
        "validation_started",
        extra={
            "event_type": event_type,
            "schema_version": schema_version,
            "payload_keys": sorted(payload.keys()),
        },
    )

    # Check schema version
    schema_errors = validate_schema_version(schema_version, supported)
    errors.extend(schema_errors)

    # Check event type format
    event_type_errors = validate_event_type(event_type)
    errors.extend(event_type_errors)

    # Validate currencies in payload
    currency_errors = validate_currencies_in_payload(payload)
    errors.extend(currency_errors)

    if errors:
        logger.warning(
            "validation_failed",
            extra={
                "event_type": event_type,
                "schema_version": schema_version,
                "error_count": len(errors),
                "error_codes": [e.code for e in errors],
            },
        )
        return ValidationResult.failure(*errors)

    logger.info(
        "validation_passed",
        extra={
            "event_type": event_type,
            "schema_version": schema_version,
        },
    )
    return ValidationResult.success()


def validate_schema_version(
    schema_version: int,
    supported_versions: frozenset[int],
) -> list[ValidationError]:
    """Validate schema version is supported."""
    if schema_version not in supported_versions:
        logger.debug(
            "schema_version_unsupported",
            extra={
                "schema_version": schema_version,
                "supported_versions": sorted(supported_versions),
            },
        )
        return [
            ValidationError(
                code="UNSUPPORTED_SCHEMA",
                message=f"Schema version {schema_version} not supported",
                details={"supported": sorted(supported_versions)},
            )
        ]
    return []


def validate_event_type(event_type: str) -> list[ValidationError]:
    """Validate event type format."""
    if not event_type:
        return [
            ValidationError(
                code="INVALID_EVENT_TYPE",
                message="Event type is required",
                field="event_type",
            )
        ]

    if "." not in event_type:
        return [
            ValidationError(
                code="INVALID_EVENT_TYPE",
                message="Event type must be namespaced (e.g., 'module.action')",
                field="event_type",
            )
        ]

    return []


def validate_currencies_in_payload(
    payload: dict[str, Any],
    path: str = "",
) -> list[ValidationError]:
    """Recursively validate any currency codes found in the payload (R16)."""
    errors: list[ValidationError] = []
    currency_keys = {"currency", "from_currency", "to_currency", "currency_code"}

    for key, value in payload.items():
        current_path = f"{path}.{key}" if path else key

        if key.lower() in currency_keys and isinstance(value, str):
            if not CurrencyRegistry.is_valid(value):
                errors.append(
                    ValidationError(
                        code="INVALID_CURRENCY",
                        message=f"Invalid ISO 4217 currency code: {value}",
                        field=current_path,
                    )
                )

        elif isinstance(value, dict):
            errors.extend(validate_currencies_in_payload(value, current_path))

        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    errors.extend(
                        validate_currencies_in_payload(item, f"{current_path}[{i}]")
                    )

    return errors


def validate_payload_required_fields(
    payload: dict[str, Any],
    required_fields: frozenset[str],
) -> list[ValidationError]:
    """Validate that required fields are present in payload."""
    errors = []
    for field in required_fields:
        if field not in payload:
            errors.append(
                ValidationError(
                    code="MISSING_REQUIRED_FIELD",
                    message=f"Required field missing: {field}",
                    field=field,
                )
            )
    return errors


def validate_amount(
    amount: Any,
    field_name: str = "amount",
    allow_zero: bool = True,
    allow_negative: bool = False,
) -> list[ValidationError]:
    """Validate an amount value."""
    from decimal import Decimal, InvalidOperation

    errors = []

    if amount is None:
        errors.append(
            ValidationError(
                code="MISSING_AMOUNT",
                message=f"{field_name} is required",
                field=field_name,
            )
        )
        return errors

    try:
        decimal_amount = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        errors.append(
            ValidationError(
                code="INVALID_AMOUNT",
                message=f"{field_name} must be a valid decimal",
                field=field_name,
            )
        )
        return errors

    if not allow_zero and decimal_amount == Decimal("0"):
        errors.append(
            ValidationError(
                code="ZERO_AMOUNT",
                message=f"{field_name} cannot be zero",
                field=field_name,
            )
        )

    if not allow_negative and decimal_amount < Decimal("0"):
        errors.append(
            ValidationError(
                code="NEGATIVE_AMOUNT",
                message=f"{field_name} cannot be negative",
                field=field_name,
            )
        )

    return errors


# Schema validation functions


def validate_payload_against_schema(
    payload: dict[str, Any],
    schema: "EventSchema",
) -> list[ValidationError]:
    """Validate event payload against a schema."""
    from finance_kernel.domain.schemas.base import EventFieldType

    errors: list[ValidationError] = []

    logger.debug(
        "schema_validation_started",
        extra={
            "event_type": schema.event_type,
            "schema_version": schema.version,
            "field_count": len(schema.fields),
        },
    )

    def validate_field(
        value: Any,
        field: "EventFieldSchema",
        path: str,
    ) -> list[ValidationError]:
        """Validate a single field value against its schema."""
        field_errors: list[ValidationError] = []

        # Check required
        if value is None:
            if field.required and not field.nullable:
                field_errors.append(
                    ValidationError(
                        code="MISSING_REQUIRED_FIELD",
                        message=f"Required field missing: {path}",
                        field=path,
                    )
                )
            return field_errors

        # Type validation
        type_error = validate_field_type(value, field.field_type, path)
        if type_error:
            field_errors.append(type_error)
            return field_errors  # Skip further validation if type is wrong

        # Constraint validation
        field_errors.extend(validate_field_constraints(value, field, path))

        # Nested validation for OBJECT type
        if field.field_type == EventFieldType.OBJECT and field.nested_fields:
            if isinstance(value, dict):
                for nested_field in field.nested_fields:
                    nested_value = value.get(nested_field.name)
                    nested_path = f"{path}.{nested_field.name}"
                    field_errors.extend(
                        validate_field(nested_value, nested_field, nested_path)
                    )

        # Array item validation
        if field.field_type == EventFieldType.ARRAY and isinstance(value, list):
            for i, item in enumerate(value):
                item_path = f"{path}[{i}]"

                if field.item_schema:
                    # Array of objects
                    if isinstance(item, dict):
                        for item_field in field.item_schema:
                            item_value = item.get(item_field.name)
                            nested_path = f"{item_path}.{item_field.name}"
                            field_errors.extend(
                                validate_field(item_value, item_field, nested_path)
                            )
                    else:
                        field_errors.append(
                            ValidationError(
                                code="INVALID_TYPE",
                                message=f"Expected object at {item_path}, got {type(item).__name__}",
                                field=item_path,
                            )
                        )
                elif field.item_type:
                    # Array of primitives
                    type_error = validate_field_type(item, field.item_type, item_path)
                    if type_error:
                        field_errors.append(type_error)

        return field_errors

    # Validate all top-level fields
    for field in schema.fields:
        value = payload.get(field.name)
        errors.extend(validate_field(value, field, field.name))

    if errors:
        logger.warning(
            "schema_validation_failed",
            extra={
                "event_type": schema.event_type,
                "schema_version": schema.version,
                "error_count": len(errors),
                "error_codes": [e.code for e in errors],
            },
        )
    else:
        logger.debug(
            "schema_validation_passed",
            extra={
                "event_type": schema.event_type,
                "schema_version": schema.version,
            },
        )

    return errors


def validate_field_type(
    value: Any,
    field_type: "EventFieldType",
    path: str,
) -> ValidationError | None:
    """Validate that a value matches the expected type."""
    from finance_kernel.domain.schemas.base import EventFieldType

    if field_type == EventFieldType.STRING:
        if not isinstance(value, str):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected string at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected integer at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.DECIMAL:
        # Accept int, float, str, Decimal
        try:
            Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected decimal at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.BOOLEAN:
        if not isinstance(value, bool):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected boolean at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.DATE:
        if isinstance(value, str):
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return ValidationError(
                    code="INVALID_DATE_FORMAT",
                    message=f"Invalid date format at {path}: expected YYYY-MM-DD",
                    field=path,
                )
        elif not isinstance(value, date) or isinstance(value, datetime):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected date at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.DATETIME:
        if isinstance(value, str):
            # Try ISO 8601 format
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return ValidationError(
                    code="INVALID_DATETIME_FORMAT",
                    message=f"Invalid datetime format at {path}: expected ISO 8601",
                    field=path,
                )
        elif not isinstance(value, datetime):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected datetime at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.UUID:
        if isinstance(value, str):
            try:
                UUID(value)
            except ValueError:
                return ValidationError(
                    code="INVALID_UUID_FORMAT",
                    message=f"Invalid UUID format at {path}",
                    field=path,
                )
        elif not isinstance(value, UUID):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected UUID at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.CURRENCY:
        if not isinstance(value, str):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected currency code (string) at {path}, got {type(value).__name__}",
                field=path,
            )
        if not CurrencyRegistry.is_valid(value):
            return ValidationError(
                code="INVALID_CURRENCY",
                message=f"Invalid ISO 4217 currency code at {path}: {value}",
                field=path,
            )

    elif field_type == EventFieldType.OBJECT:
        if not isinstance(value, dict):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected object at {path}, got {type(value).__name__}",
                field=path,
            )

    elif field_type == EventFieldType.ARRAY:
        if not isinstance(value, list):
            return ValidationError(
                code="INVALID_TYPE",
                message=f"Expected array at {path}, got {type(value).__name__}",
                field=path,
            )

    return None


def validate_field_constraints(
    value: Any,
    field: "EventFieldSchema",
    path: str,
) -> list[ValidationError]:
    """Validate field constraints (min/max, length, pattern, allowed_values)."""
    from finance_kernel.domain.schemas.base import EventFieldType

    errors: list[ValidationError] = []

    # Numeric constraints
    if field.field_type in (EventFieldType.INTEGER, EventFieldType.DECIMAL):
        try:
            numeric_value = Decimal(str(value))

            if field.min_value is not None:
                min_val = Decimal(str(field.min_value))
                if numeric_value < min_val:
                    errors.append(
                        ValidationError(
                            code="VALUE_TOO_SMALL",
                            message=f"Value at {path} is {value}, minimum is {field.min_value}",
                            field=path,
                        )
                    )

            if field.max_value is not None:
                max_val = Decimal(str(field.max_value))
                if numeric_value > max_val:
                    errors.append(
                        ValidationError(
                            code="VALUE_TOO_LARGE",
                            message=f"Value at {path} is {value}, maximum is {field.max_value}",
                            field=path,
                        )
                    )
        except (InvalidOperation, ValueError, TypeError):
            pass  # Type error already caught

    # String constraints
    if field.field_type == EventFieldType.STRING and isinstance(value, str):
        if field.min_length is not None and len(value) < field.min_length:
            errors.append(
                ValidationError(
                    code="STRING_TOO_SHORT",
                    message=f"String at {path} is {len(value)} chars, minimum is {field.min_length}",
                    field=path,
                )
            )

        if field.max_length is not None and len(value) > field.max_length:
            errors.append(
                ValidationError(
                    code="STRING_TOO_LONG",
                    message=f"String at {path} is {len(value)} chars, maximum is {field.max_length}",
                    field=path,
                )
            )

        if field.pattern is not None:
            if not re.match(field.pattern, value):
                errors.append(
                    ValidationError(
                        code="PATTERN_MISMATCH",
                        message=f"String at {path} does not match pattern: {field.pattern}",
                        field=path,
                    )
                )

    # Allowed values (enum constraint)
    if field.allowed_values is not None:
        if value not in field.allowed_values:
            errors.append(
                ValidationError(
                    code="VALUE_NOT_ALLOWED",
                    message=f"Value '{value}' at {path} not in allowed values: {sorted(field.allowed_values)}",
                    field=path,
                )
            )

    return errors


def validate_field_references(
    field_paths: "Iterable[str]",
    schema: "EventSchema",
) -> list[ValidationError]:
    """Validate that field paths exist in schema (P10)."""
    errors: list[ValidationError] = []
    valid_paths = schema.all_field_paths()

    for path in field_paths:
        if path not in valid_paths:
            logger.debug(
                "invalid_field_reference",
                extra={
                    "field_path": path,
                    "event_type": schema.event_type,
                    "schema_version": schema.version,
                },
            )
            errors.append(
                ValidationError(
                    code="INVALID_FIELD_REFERENCE",
                    message=f"Field '{path}' does not exist in schema for {schema.event_type}",
                    field=path,
                    details={"event_type": schema.event_type, "version": schema.version},
                )
            )

    return errors
