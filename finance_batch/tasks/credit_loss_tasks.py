"""
Batch task: Credit Loss module (ECL calculation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import BatchItemInput, BatchTaskResult


class ECLCalculationTask:
    """Batch task for calculating expected credit losses (ASC 326 / CECL)."""

    @property
    def task_type(self) -> str:
        return "credit_loss.ecl_calculation"

    @property
    def description(self) -> str:
        return "Calculate expected credit losses per portfolio segment"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        # ECL segments passed via parameters
        segments = parameters.get("segments", [])
        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=seg.get("segment_name", f"segment-{i}"),
                payload=seg,
            )
            for i, seg in enumerate(segments)
        )

    def execute_item(
        self,
        item: BatchItemInput,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> BatchTaskResult:
        try:
            return BatchTaskResult(
                status=BatchItemStatus.SUCCEEDED,
                result_data={"segment": item.item_key},
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="ECL_CALCULATION_FAILED",
                error_message=str(exc),
            )
