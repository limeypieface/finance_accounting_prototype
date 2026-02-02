"""
finance_ingestion.domain.types -- Pure frozen dataclasses for the import system.

Phase 0 of ERP_INGESTION_PLAN. ZERO I/O. Imports only from finance_kernel/domain/.

Reuses:
    - EventFieldType from finance_kernel.domain.schemas.base
    - ValidationError from finance_kernel.domain.dtos
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from finance_kernel.domain.dtos import ValidationError
from finance_kernel.domain.schemas.base import EventFieldType


# =============================================================================
# Status enums (IM-6: per-record status; no transient VALIDATING/PROMOTING in v1)
# =============================================================================


class ImportRecordStatus(str, Enum):
    """Per-record lifecycle status."""

    STAGED = "staged"  # Raw data loaded to staging
    VALID = "valid"  # All validations passed
    INVALID = "invalid"  # One or more validations failed
    PROMOTED = "promoted"  # Successfully promoted to live tables
    PROMOTION_FAILED = "promotion_failed"  # Promotion error
    SKIPPED = "skipped"  # Intentionally skipped (e.g., duplicate or blocked)


class ImportBatchStatus(str, Enum):
    """Batch-level lifecycle status."""

    LOADING = "loading"  # Source file being read
    STAGED = "staged"  # All records loaded to staging
    VALIDATED = "validated"  # All records validated (some may be invalid)
    COMPLETED = "completed"  # All promotable records promoted
    FAILED = "failed"  # Batch-level failure (e.g., file read error)


# =============================================================================
# Batch and record DTOs
# =============================================================================


@dataclass(frozen=True)
class ImportBatch:
    """Immutable snapshot of an import batch (IM-3, IM-11)."""

    batch_id: UUID
    mapping_name: str  # References ImportMappingDef.name
    entity_type: str  # Target entity type
    source_filename: str
    status: ImportBatchStatus
    total_records: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    promoted_records: int = 0
    skipped_records: int = 0
    created_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ImportRecord:
    """Immutable snapshot of a single staged record (IM-6, IM-9)."""

    record_id: UUID
    batch_id: UUID
    source_row: int  # Row number in source file (1-indexed)
    entity_type: str
    status: ImportRecordStatus
    raw_data: dict[str, Any]  # Original source data (IM-9)
    mapped_data: dict[str, Any] | None = None  # After field mapping
    validation_errors: tuple[ValidationError, ...] = ()
    promoted_entity_id: UUID | None = None  # ID in live table after promotion
    promoted_at: datetime | None = None


# =============================================================================
# Field mapping (reuses EventFieldType from kernel)
# =============================================================================


@dataclass(frozen=True)
class FieldMapping:
    """Single field mapping: source column -> target field with type and transform."""

    source: str  # Source field name (e.g., CSV column)
    target: str  # Target field name (e.g., entity attribute)
    field_type: EventFieldType  # Reuses kernel's type enum
    required: bool = False
    default: Any = None
    format: str | None = None  # e.g., date format "MM/DD/YYYY"
    transform: str | None = None  # e.g., "upper", "strip", "to_decimal"


# =============================================================================
# Validation rule (domain shape; config layer will have ImportValidationDef)
# =============================================================================


@dataclass(frozen=True)
class ImportValidationRule:
    """Single validation rule for import records (batch/system/record scope)."""

    rule_type: str  # "unique", "exists", "expression", "cross_field"
    fields: tuple[str, ...] = ()
    scope: str = "batch"  # "batch", "system", "record"
    reference_entity: str | None = None  # For "exists" rules
    expression: str | None = None  # For "expression" rules
    message: str = ""


# =============================================================================
# Import mapping (compiled shape used by mapping engine and services)
# =============================================================================


@dataclass(frozen=True)
class ImportMapping:
    """Compiled import mapping: source format, field mappings, validations, tier."""

    name: str
    version: int
    entity_type: str  # Target entity type
    source_format: str  # "csv", "json"
    source_options: dict[str, Any] = field(default_factory=dict)
    field_mappings: tuple[FieldMapping, ...] = ()
    validations: tuple[ImportValidationRule, ...] = ()
    dependency_tier: int = 0  # For promotion ordering (IM-5)
