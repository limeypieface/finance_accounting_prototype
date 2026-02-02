"""
Mapping test harness: validate mapping against sample data without staging (Phase 7b).

Pure function. Runs mapping + validation on sample rows; returns a detailed report.
No DB, no staging tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from finance_kernel.domain.dtos import ValidationError

from finance_ingestion.domain.types import ImportMapping
from finance_ingestion.domain.validators import (
    ENTITY_VALIDATORS,
    validate_batch_uniqueness,
    validate_currency_codes,
    validate_date_ranges_simple,
    validate_decimal_precision,
    validate_field_types,
    validate_required_fields,
)
from finance_ingestion.mapping.engine import apply_mapping


@dataclass(frozen=True)
class MappingTestRow:
    """Result for one sample row."""

    source_row: int
    success: bool
    raw_data: dict[str, Any]
    mapped_data: dict[str, Any] | None = None
    errors: tuple[ValidationError, ...] = ()


@dataclass(frozen=True)
class MappingTestReport:
    """Report from test_mapping run."""

    mapping_name: str
    mapping_version: int
    sample_count: int
    success_count: int
    error_count: int
    rows: tuple[MappingTestRow, ...] = ()
    summary_errors: tuple[str, ...] = ()


def run_mapping_test(
    mapping: ImportMapping,
    sample_rows: Sequence[dict[str, Any]],
) -> MappingTestReport:
    """
    Test a mapping configuration against sample data. Pure function.

    Runs the full mapping + validation pipeline on sample rows
    without writing to staging tables. Returns a detailed report.

    Aliased as test_mapping for backward compatibility; do not rename to test_*
    (pytest would collect it as a test).
    """
    rows: list[MappingTestRow] = []
    all_errors: set[str] = set()
    success_count = 0

    currency_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name == "CURRENCY")
    decimal_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name == "DECIMAL")
    date_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name in ("DATE", "DATETIME"))
    batch_unique_rules = [r for r in mapping.validations if r.rule_type == "unique" and r.scope == "batch"]
    batch_unique_fields = tuple(f for r in batch_unique_rules for f in r.fields) if batch_unique_rules else ()

    mapped_list: list[dict[str, Any]] = []
    for i, raw in enumerate(sample_rows):
        source_row = i + 1
        result = apply_mapping(dict(raw), mapping.field_mappings)
        errors_list: list[ValidationError] = list(result.errors)
        mapped = result.mapped_data if result.success else None
        if result.success and mapped is not None:
            mapped_list.append(mapped)
            errors_list.extend(validate_required_fields(mapped, mapping.field_mappings))
            errors_list.extend(validate_field_types(mapped, mapping.field_mappings))
            if currency_fields:
                errors_list.extend(validate_currency_codes(mapped, currency_fields))
            if decimal_fields:
                errors_list.extend(validate_decimal_precision(mapped, decimal_fields))
            if date_fields:
                errors_list.extend(validate_date_ranges_simple(mapped, date_fields))
            for validator in ENTITY_VALIDATORS.get(mapping.entity_type, ()):
                errors_list.extend(validator(mapped))

        if batch_unique_fields and mapped_list:
            batch_errors = validate_batch_uniqueness(mapped_list, batch_unique_fields)
            # Current row's index in mapped_list is len(mapped_list)-1 (we just appended on success)
            idx_in_mapped = len(mapped_list) - 1
            row_errors = batch_errors.get(idx_in_mapped, [])
            errors_list.extend(row_errors)

        for e in errors_list:
            all_errors.add(f"{e.code}: {e.message}")

        success = len(errors_list) == 0
        if success:
            success_count += 1
        rows.append(
            MappingTestRow(
                source_row=source_row,
                success=success,
                raw_data=dict(raw),
                mapped_data=mapped,
                errors=tuple(errors_list),
            )
        )

    return MappingTestReport(
        mapping_name=mapping.name,
        mapping_version=mapping.version,
        sample_count=len(sample_rows),
        success_count=success_count,
        error_count=len(sample_rows) - success_count,
        rows=tuple(rows),
        summary_errors=tuple(sorted(all_errors)),
    )


# Public API alias (plan/docs refer to test_mapping; do not name the function test_* or pytest collects it)
test_mapping = run_mapping_test
