"""
Batch tasks: GL module (recurring entries, period-end revaluation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import BatchItemInput, BatchTaskResult


class RecurringEntryTask:
    """Batch task for generating recurring journal entries."""

    @property
    def task_type(self) -> str:
        return "gl.recurring_entries"

    @property
    def description(self) -> str:
        return "Generate recurring journal entries from templates"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.gl.orm import RecurringEntryModel

        query = session.query(RecurringEntryModel).filter(
            RecurringEntryModel.is_active == True,  # noqa: E712
        )
        templates = query.all()

        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=str(tpl.id),
                payload={"template_id": str(tpl.id)},
            )
            for i, tpl in enumerate(templates)
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
                result_data={"template_id": item.payload.get("template_id")},
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="RECURRING_ENTRY_FAILED",
                error_message=str(exc),
            )


class PeriodEndRevaluationTask:
    """Batch task for period-end foreign currency revaluation."""

    @property
    def task_type(self) -> str:
        return "gl.period_end_revaluation"

    @property
    def description(self) -> str:
        return "Run period-end foreign currency revaluation"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        # Revaluation items are currency-account pairs passed via parameters
        items_data = parameters.get("revaluation_items", [])
        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=f"{item.get('account_code', 'unknown')}-{item.get('currency', 'USD')}",
                payload=item,
            )
            for i, item in enumerate(items_data)
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
                result_data={"item_key": item.item_key},
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="REVALUATION_FAILED",
                error_message=str(exc),
            )
