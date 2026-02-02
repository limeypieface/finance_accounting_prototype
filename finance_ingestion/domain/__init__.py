"""
finance_ingestion.domain -- Pure types and value objects for ingestion.

ZERO I/O. Imports only from finance_kernel/domain/.
"""

from finance_ingestion.domain.types import (
    FieldMapping,
    ImportBatch,
    ImportBatchStatus,
    ImportMapping,
    ImportRecord,
    ImportRecordStatus,
    ImportValidationRule,
)

__all__ = [
    "FieldMapping",
    "ImportBatch",
    "ImportBatchStatus",
    "ImportMapping",
    "ImportRecord",
    "ImportRecordStatus",
    "ImportValidationRule",
]
