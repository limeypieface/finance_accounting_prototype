"""Mapping engine: pure field mapping and type coercion (ERP_INGESTION_PLAN Phase 4)."""

from finance_ingestion.mapping.engine import (
    CoercionResult,
    MappingResult,
    apply_mapping,
    apply_transform,
    coerce_from_string,
)

__all__ = [
    "apply_mapping",
    "apply_transform",
    "coerce_from_string",
    "MappingResult",
    "CoercionResult",
]
