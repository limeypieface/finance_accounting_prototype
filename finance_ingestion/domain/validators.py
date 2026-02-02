"""
Pre-packaged validators for import records (ERP_INGESTION_PLAN Phase 5).

Record-level validators reuse kernel event_validator where possible.
Cross-record (batch uniqueness) is pure. Referential (entity_exists, system_uniqueness)
require DB session and live in the service layer; not in this module.

Architecture: finance_ingestion/domain. ZERO I/O. Imports only from finance_kernel/domain/.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime as datetime_type
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.domain.dtos import ValidationError
from finance_kernel.domain.event_validator import (
    validate_field_type,
    validate_payload_required_fields,
)

from finance_ingestion.domain.types import FieldMapping

# Kernel uses Numeric(38, 9) for amounts
_MAX_DECIMAL_DIGITS = 38
_MAX_DECIMAL_PLACES = 9


# -----------------------------------------------------------------------------
# Record-level validators (one record at a time)
# -----------------------------------------------------------------------------


def validate_required_fields(
    record: dict[str, Any],
    mappings: tuple[FieldMapping, ...],
) -> list[ValidationError]:
    """Validate that all required mapped fields are present in the record."""
    required = frozenset(fm.target for fm in mappings if fm.required)
    return validate_payload_required_fields(record, required)


def validate_field_types(
    record: dict[str, Any],
    mappings: tuple[FieldMapping, ...],
) -> list[ValidationError]:
    """Validate that each mapped field value matches its declared type."""
    errors: list[ValidationError] = []
    for fm in mappings:
        value = record.get(fm.target)
        err = validate_field_type(value, fm.field_type, fm.target)
        if err:
            errors.append(err)
    return errors


def validate_currency_codes(
    record: dict[str, Any],
    currency_fields: tuple[str, ...],
) -> list[ValidationError]:
    """Validate that listed fields contain valid ISO 4217 currency codes."""
    errors: list[ValidationError] = []
    for field_name in currency_fields:
        value = record.get(field_name)
        if value is None:
            continue
        s = str(value).strip()
        if s and not CurrencyRegistry.is_valid(s):
            errors.append(
                ValidationError(
                    code="INVALID_CURRENCY",
                    message=f"Invalid ISO 4217 currency code at {field_name}: {value}",
                    field=field_name,
                )
            )
    return errors


def validate_decimal_precision(
    record: dict[str, Any],
    decimal_fields: tuple[str, ...],
) -> list[ValidationError]:
    """Validate that decimal fields are within kernel precision (38 digits, 9 decimal places)."""
    errors: list[ValidationError] = []
    for field_name in decimal_fields:
        value = record.get(field_name)
        if value is None:
            continue
        try:
            d = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            continue  # Type already validated elsewhere
        sign, digits, exp = d.as_tuple()
        total_digits = len(digits)
        if exp >= 0:
            if total_digits + exp > _MAX_DECIMAL_DIGITS:
                errors.append(
                    ValidationError(
                        code="DECIMAL_PRECISION_EXCEEDED",
                        message=f"Value at {field_name} exceeds {_MAX_DECIMAL_DIGITS} digits",
                        field=field_name,
                    )
                )
        else:
            decimal_places = -exp
            if decimal_places > _MAX_DECIMAL_PLACES:
                errors.append(
                    ValidationError(
                        code="DECIMAL_SCALE_EXCEEDED",
                        message=f"Value at {field_name} exceeds {_MAX_DECIMAL_PLACES} decimal places",
                        field=field_name,
                    )
                )
            if total_digits + decimal_places > _MAX_DECIMAL_DIGITS:
                errors.append(
                    ValidationError(
                        code="DECIMAL_PRECISION_EXCEEDED",
                        message=f"Value at {field_name} exceeds {_MAX_DECIMAL_DIGITS} digits",
                        field=field_name,
                    )
                )
    return errors


def validate_date_ranges(
    record: dict[str, Any],
    date_fields: tuple[str, ...],
    min_date: date | None = None,
    max_date: date | None = None,
) -> list[ValidationError]:
    """Validate that date fields are within an optional range (e.g. 1900-2100)."""
    min_date = min_date or date(1900, 1, 1)
    max_date = max_date or date(2100, 12, 31)
    errors: list[ValidationError] = []
    for field_name in date_fields:
        value = record.get(field_name)
        if value is None:
            continue
        d: date | None = None
        if isinstance(value, datetime_type):
            d = value.date()
        elif isinstance(value, date):
            d = value
        elif isinstance(value, str):
            try:
                d = date.fromisoformat(value)
            except ValueError:
                continue
        if d is None:
            continue
        if d < min_date or d > max_date:
            errors.append(
                ValidationError(
                    code="DATE_OUT_OF_RANGE",
                    message=f"Date at {field_name} is outside allowed range {min_date} to {max_date}",
                    field=field_name,
                    details={"value": str(d), "min": str(min_date), "max": str(max_date)},
                )
            )
    return errors


def validate_date_ranges_simple(
    record: dict[str, Any],
    date_fields: tuple[str, ...],
) -> list[ValidationError]:
    """Validate date fields are within default range (1900-2100)."""
    return validate_date_ranges(record, date_fields)


# -----------------------------------------------------------------------------
# Cross-record validators (batch context)
# -----------------------------------------------------------------------------


def validate_batch_uniqueness(
    records: Sequence[dict[str, Any]],
    fields: tuple[str, ...],
) -> dict[int, list[ValidationError]]:
    """
    For each field set, ensure values are unique across the batch.
    Returns row index -> list of errors (duplicate value).
    """
    result: dict[int, list[ValidationError]] = defaultdict(list)
    for field_name in fields:
        value_to_indices: dict[Any, list[int]] = defaultdict(list)
        for i, rec in enumerate(records):
            v = rec.get(field_name)
            value_to_indices[v].append(i)
        for value, indices in value_to_indices.items():
            if len(indices) > 1:
                for i in indices:
                    result[i].append(
                        ValidationError(
                            code="DUPLICATE_VALUE_IN_BATCH",
                            message=f"Duplicate value for {field_name!r} in batch",
                            field=field_name,
                            details={"value": value, "row_indices": indices},
                        )
                    )
    return dict(result)


# -----------------------------------------------------------------------------
# Entity-specific validators (pure; referential ones live in service layer)
# -----------------------------------------------------------------------------


def validate_party_code(record: dict[str, Any]) -> list[ValidationError]:
    """Party code must be non-empty string."""
    errors: list[ValidationError] = []
    code = record.get("code")
    if code is None or (isinstance(code, str) and not code.strip()):
        errors.append(ValidationError(code="MISSING_REQUIRED_FIELD", message="Party code is required", field="code"))
    return errors


def validate_party_type(record: dict[str, Any]) -> list[ValidationError]:
    """Party type must be one of customer, supplier, employee, intercompany."""
    allowed = frozenset({"customer", "supplier", "employee", "intercompany", "vendor"})
    errors: list[ValidationError] = []
    t = record.get("party_type") or record.get("type")
    if t is not None and str(t).lower() not in allowed:
        errors.append(
            ValidationError(
                code="INVALID_PARTY_TYPE",
                message=f"Party type must be one of {sorted(allowed)}",
                field="party_type",
            )
        )
    return errors


def validate_account_code_format(record: dict[str, Any]) -> list[ValidationError]:
    """Account code must be non-empty and reasonable format."""
    errors: list[ValidationError] = []
    code = record.get("code")
    if code is None or (isinstance(code, str) and not code.strip()):
        errors.append(ValidationError(code="MISSING_REQUIRED_FIELD", message="Account code is required", field="code"))
    return errors


def validate_item_code(record: dict[str, Any]) -> list[ValidationError]:
    """Item code must be non-empty."""
    errors: list[ValidationError] = []
    code = record.get("code")
    if code is None or (isinstance(code, str) and not code.strip()):
        errors.append(ValidationError(code="MISSING_REQUIRED_FIELD", message="Item code is required", field="code"))
    return errors


def _noop_validator(record: dict[str, Any]) -> list[ValidationError]:
    """Placeholder for validators that need DB (use service layer)."""
    return []


# Pre-packaged profiles per entity type (pure only; referential in service layer)
ENTITY_VALIDATORS: dict[str, tuple[Any, ...]] = {
    "party": (validate_party_code, validate_party_type),
    "vendor": (validate_party_code, validate_party_type, _noop_validator),  # vendor_party_exists in service
    "customer": (validate_party_code, validate_party_type, _noop_validator),
    "employee": (validate_party_code, validate_party_type),
    "account": (validate_account_code_format,),
    "item": (validate_item_code),
    "ap_invoice": (_noop_validator,),  # invoice_total, vendor_exists in service
    "ar_invoice": (_noop_validator,),
    "opening_balance": (_noop_validator,),
}
