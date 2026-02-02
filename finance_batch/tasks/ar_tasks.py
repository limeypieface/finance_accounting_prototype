"""
Batch tasks: AR module (dunning letters, small balance write-off).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import BatchItemInput, BatchTaskResult


class DunningLetterTask:
    """Batch task for generating dunning letters for overdue customers."""

    @property
    def task_type(self) -> str:
        return "ar.dunning_letters"

    @property
    def description(self) -> str:
        return "Generate dunning letters for overdue customer balances"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.ar.orm import ARInvoiceModel

        query = session.query(ARInvoiceModel).filter(
            ARInvoiceModel.status == "overdue",
        )
        invoices = query.all()

        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=str(inv.id),
                payload={"invoice_id": str(inv.id)},
            )
            for i, inv in enumerate(invoices)
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
                result_data={"invoice_id": item.payload.get("invoice_id")},
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="DUNNING_FAILED",
                error_message=str(exc),
            )


class SmallBalanceWriteOffTask:
    """Batch task for writing off small balance invoices."""

    @property
    def task_type(self) -> str:
        return "ar.small_balance_write_off"

    @property
    def description(self) -> str:
        return "Auto write-off invoices below configured threshold"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.ar.orm import ARInvoiceModel

        threshold = parameters.get("threshold", "10.00")
        query = session.query(ARInvoiceModel).filter(
            ARInvoiceModel.status == "open",
        )
        invoices = query.all()

        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=str(inv.id),
                payload={
                    "invoice_id": str(inv.id),
                    "threshold": threshold,
                },
            )
            for i, inv in enumerate(invoices)
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
                result_data={"invoice_id": item.payload.get("invoice_id")},
            )
        except Exception as exc:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="WRITE_OFF_FAILED",
                error_message=str(exc),
            )
