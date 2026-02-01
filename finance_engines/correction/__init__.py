"""
Correction - Pure domain objects for document correction unwind.

Pure domain types only. The stateful CorrectionEngine service has moved
to finance_services.correction_service.
"""

from finance_kernel.logging_config import get_logger

logger = get_logger("engines.correction")

from finance_engines.correction.unwind import (
    AffectedArtifact,
    CompensatingEntry,
    CompensatingLine,
    CorrectionResult,
    CorrectionType,
    UnwindPlan,
    UnwindStrategy,
)

__all__ = [
    "UnwindPlan",
    "AffectedArtifact",
    "CompensatingEntry",
    "CompensatingLine",
    "CorrectionResult",
    "CorrectionType",
    "UnwindStrategy",
]
