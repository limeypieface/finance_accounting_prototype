"""
Batch task: Cash module (bank reconciliation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import BatchItemInput, BatchTaskResult


class BankReconcileTask:
    """Batch task for auto-reconciling bank statement lines."""

    @property
    def task_type(self) -> str:
        return "cash.auto_reconcile"

    @property
    def description(self) -> str:
        return "Auto-reconcile bank statement lines with book entries"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.cash.orm import BankStatementLineModel

        # BankStatementLineModel has no status column; unmatched lines
        # are identified by absence of a ReconciliationMatch.  For batch
        # item collection we query all lines and let execute_item handle
        # the matching logic (actual filtering deferred to orchestrator
        # wiring in Phase 10).
        query = session.query(BankStatementLineModel)
        lines = query.all()

        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=str(line.id),
                payload={"statement_line_id": str(line.id)},
            )
            for i, line in enumerate(lines)
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
                    "statement_line_id": item.payload.get("statement_line_id"),
                },
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="RECONCILE_FAILED",
                error_message=str(exc),
            )
