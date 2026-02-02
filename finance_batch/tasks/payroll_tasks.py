"""
Batch task: Payroll module (labor cost allocation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import BatchItemInput, BatchTaskResult


class LaborCostAllocationTask:
    """Batch task for allocating labor costs to projects/contracts."""

    @property
    def task_type(self) -> str:
        return "payroll.labor_allocation"

    @property
    def description(self) -> str:
        return "Allocate labor costs to projects and cost centers"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.payroll.orm import PayrollRunModel

        query = session.query(PayrollRunModel).filter(
            PayrollRunModel.status == "pending_allocation",
        )
        runs = query.all()

        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=str(run.id),
                payload={"payroll_run_id": str(run.id)},
            )
            for i, run in enumerate(runs)
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
                result_data={
                    "payroll_run_id": item.payload.get("payroll_run_id"),
                },
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="ALLOCATION_FAILED",
                error_message=str(exc),
            )
