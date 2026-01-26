"""
Pure event validation.

All event validation logic lives here - NO I/O, NO ORM, NO time.
This is part of the functional core (R1).
"""

from typing import Any

from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.domain.dtos import ValidationError, ValidationResult


# Supported schema versions - pure constant
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


def validate_event(
    event_type: str,
    payload: dict[str, Any],
    schema_version: int,
    supported_versions: frozenset[int] | None = None,
) -> ValidationResult:
    """
    Validate an event at the domain boundary.

    Pure function - no I/O, no ORM, no time.

    Args:
        event_type: The event type string.
        payload: The event payload.
        schema_version: The schema version.
        supported_versions: Optional set of supported versions.

    Returns:
        ValidationResult with success or failure.
    """
    supported = supported_versions or SUPPORTED_SCHEMA_VERSIONS
    errors: list[ValidationError] = []

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
        return ValidationResult.failure(*errors)
    return ValidationResult.success()


def validate_schema_version(
    schema_version: int,
    supported_versions: frozenset[int],
) -> list[ValidationError]:
    """
    Validate schema version is supported.

    Pure function.
    """
    if schema_version not in supported_versions:
        return [
            ValidationError(
                code="UNSUPPORTED_SCHEMA",
                message=f"Schema version {schema_version} not supported",
                details={"supported": sorted(supported_versions)},
            )
        ]
    return []


def validate_event_type(event_type: str) -> list[ValidationError]:
    """
    Validate event type format.

    Event types must be namespaced (contain a dot).
    Pure function.
    """
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
    """
    Recursively validate any currency codes found in the payload.

    Looks for keys like 'currency', 'from_currency', 'to_currency', etc.
    Pure function - uses CurrencyRegistry which is also pure.

    Args:
        payload: The payload dictionary to validate.
        path: Current path for error reporting.

    Returns:
        List of validation errors (empty if valid).
    """
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
    """
    Validate that required fields are present in payload.

    Pure function.

    Args:
        payload: The payload dictionary.
        required_fields: Set of required field names.

    Returns:
        List of validation errors for missing fields.
    """
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
    """
    Validate an amount value.

    Pure function.

    Args:
        amount: The amount to validate.
        field_name: Field name for error reporting.
        allow_zero: Whether zero is allowed.
        allow_negative: Whether negative amounts are allowed.

    Returns:
        List of validation errors.
    """
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
