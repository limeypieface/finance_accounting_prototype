"""
Mapping engine: pure transformation from raw source dict to typed mapped dict.

Phase 4 of ERP_INGESTION_PLAN. Reuses validate_field_type() from kernel;
adds string-to-typed coercion for CSV/text sources. ZERO I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from finance_kernel.domain.dtos import ValidationError
from finance_kernel.domain.event_validator import validate_field_type
from finance_kernel.domain.schemas.base import EventFieldType

from finance_ingestion.domain.types import FieldMapping


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CoercionResult:
    """Result of coercing a string to a target type."""

    success: bool
    value: Any = None
    error: ValidationError | None = None


@dataclass(frozen=True)
class MappingResult:
    """Result of applying field mappings to a raw record."""

    success: bool
    mapped_data: dict[str, Any] = field(default_factory=dict)
    errors: tuple[ValidationError, ...] = ()


# -----------------------------------------------------------------------------
# Transforms (pure)
# -----------------------------------------------------------------------------


def apply_transform(value: Any, transform: str) -> Any:
    """Apply a named transform. Pure function."""
    if value is None:
        return None
    t = (transform or "").strip().lower()
    if t in ("strip", "trim"):
        return value.strip() if isinstance(value, str) else value
    if t == "upper":
        return value.upper() if isinstance(value, str) else value
    if t == "lower":
        return value.lower() if isinstance(value, str) else value
    if t == "to_decimal":
        if isinstance(value, (Decimal, int)):
            return Decimal(value)
        s = value.strip() if isinstance(value, str) else str(value)
        try:
            return Decimal(s)
        except (InvalidOperation, ValueError):
            return value  # Caller will get type error from validate_field_type
    if t == "normalize_date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            # Try ISO first
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(value.strip(), fmt).date().isoformat()
                except ValueError:
                    continue
        return value
    return value


# -----------------------------------------------------------------------------
# Coercion: string -> typed (for CSV/text sources)
# -----------------------------------------------------------------------------


def coerce_from_string(
    value: str,
    field_type: EventFieldType,
    format_str: str | None = None,
) -> CoercionResult:
    """
    Coerce a string value to the target type. Pure function.

    CSV/text sources produce strings; this converts to Decimal, date, int, etc.
    After coercion, validate_field_type() confirms the result.
    """
    s = value.strip() if isinstance(value, str) else (str(value) if value is not None else "")
    if not s and field_type != EventFieldType.STRING:
        return CoercionResult(success=False, error=ValidationError(
            code="MISSING_VALUE",
            message="Empty value cannot be coerced to non-string type",
            field="",
        ))

    if field_type == EventFieldType.STRING:
        return CoercionResult(success=True, value=s)

    if field_type == EventFieldType.INTEGER:
        try:
            return CoercionResult(success=True, value=int(Decimal(s)))
        except (ValueError, InvalidOperation):
            return CoercionResult(
                success=False,
                error=ValidationError(code="INVALID_INTEGER", message=f"Cannot coerce to integer: {s!r}", field=""),
            )

    if field_type == EventFieldType.DECIMAL:
        try:
            return CoercionResult(success=True, value=Decimal(s))
        except (ValueError, InvalidOperation):
            return CoercionResult(
                success=False,
                error=ValidationError(code="INVALID_DECIMAL", message=f"Cannot coerce to decimal: {s!r}", field=""),
            )

    if field_type == EventFieldType.BOOLEAN:
        low = s.lower()
        if low in ("true", "yes", "1", "on"):
            return CoercionResult(success=True, value=True)
        if low in ("false", "no", "0", "off", ""):
            return CoercionResult(success=True, value=False)
        return CoercionResult(
            success=False,
            error=ValidationError(code="INVALID_BOOLEAN", message=f"Cannot coerce to boolean: {s!r}", field=""),
        )

    if field_type == EventFieldType.DATE:
        fmt = format_str or "%Y-%m-%d"
        for try_fmt in (fmt, "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return CoercionResult(success=True, value=datetime.strptime(s, try_fmt).date())
            except ValueError:
                continue
        return CoercionResult(
            success=False,
            error=ValidationError(code="INVALID_DATE_FORMAT", message=f"Cannot parse date: {s!r}", field=""),
        )

    if field_type == EventFieldType.DATETIME:
        try:
            return CoercionResult(success=True, value=datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            return CoercionResult(
                success=False,
                error=ValidationError(code="INVALID_DATETIME_FORMAT", message=f"Cannot parse datetime: {s!r}", field=""),
            )

    if field_type == EventFieldType.UUID:
        try:
            return CoercionResult(success=True, value=UUID(s))
        except ValueError:
            return CoercionResult(
                success=False,
                error=ValidationError(code="INVALID_UUID_FORMAT", message=f"Invalid UUID: {s!r}", field=""),
            )

    if field_type == EventFieldType.CURRENCY:
        # Keep as string; validate_field_type checks CurrencyRegistry
        return CoercionResult(success=True, value=s)

    if field_type == EventFieldType.OBJECT:
        try:
            return CoercionResult(success=True, value=json.loads(s) if isinstance(s, str) else s)
        except (json.JSONDecodeError, TypeError):
            return CoercionResult(
                success=False,
                error=ValidationError(code="INVALID_JSON", message=f"Cannot parse object from: {s[:50]!r}", field=""),
            )

    if field_type == EventFieldType.ARRAY:
        try:
            parsed = json.loads(s) if isinstance(s, str) else s
            if not isinstance(parsed, list):
                raise TypeError("not a list")
            return CoercionResult(success=True, value=parsed)
        except (json.JSONDecodeError, TypeError):
            return CoercionResult(
                success=False,
                error=ValidationError(code="INVALID_JSON_ARRAY", message=f"Cannot parse array from: {s[:50]!r}", field=""),
            )

    return CoercionResult(success=False, error=ValidationError(code="UNSUPPORTED_TYPE", message=f"Unsupported field_type: {field_type}", field=""))


# -----------------------------------------------------------------------------
# Apply mapping (pure)
# -----------------------------------------------------------------------------


def apply_mapping(
    raw_data: dict[str, Any],
    field_mappings: tuple[FieldMapping, ...],
) -> MappingResult:
    """
    Apply field mappings to a raw source record. Pure function.

    For each mapping: get source value, apply transform, coerce if string,
    validate with validate_field_type. Missing required -> error; missing optional -> use default.
    """
    errors: list[ValidationError] = []
    mapped: dict[str, Any] = {}

    for fm in field_mappings:
        raw_value = raw_data.get(fm.source) if isinstance(raw_data, dict) else None
        path = fm.target

        # Missing value
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            if fm.required:
                errors.append(ValidationError(
                    code="MISSING_REQUIRED_FIELD",
                    message=f"Required field {fm.source!r} is missing",
                    field=path,
                ))
                continue
            if fm.default is not None:
                mapped[path] = fm.default
            continue

        # Transform first
        value = apply_transform(raw_value, fm.transform) if fm.transform else raw_value

        # When target is STRING, accept int/float (e.g. QBO "num": 1) and coerce to string
        if fm.field_type == EventFieldType.STRING and isinstance(value, (int, float)):
            value = str(value)

        # Coerce string to typed when target is not string
        if isinstance(value, str) and fm.field_type != EventFieldType.STRING:
            coerced = coerce_from_string(value, fm.field_type, fm.format)
            if not coerced.success:
                errors.append(coerced.error)
                continue
            value = coerced.value

        # Post-coercion type validation
        type_error = validate_field_type(value, fm.field_type, path)
        if type_error:
            errors.append(type_error)
            continue

        mapped[path] = value

    return MappingResult(
        success=len(errors) == 0,
        mapped_data=mapped,
        errors=tuple(errors),
    )
