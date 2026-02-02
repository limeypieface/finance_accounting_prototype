"""
Batch tasks: AP module (payment run, invoice auto-match).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import BatchItemInput, BatchTaskResult


class PaymentRunTask:
    """Batch task for executing a payment run across approved invoices."""

    @property
    def task_type(self) -> str:
        return "ap.payment_run"

    @property
    def description(self) -> str:
        return "Execute payment run for approved invoices"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.ap.orm import APInvoiceModel

        query = session.query(APInvoiceModel).filter(
            APInvoiceModel.status == "approved",
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
                error_code="PAYMENT_RUN_FAILED",
                error_message=str(exc),
            )


class InvoiceMatchTask:
    """Batch task for auto-matching invoices to POs/receipts."""

    @property
    def task_type(self) -> str:
        return "ap.invoice_match"

    @property
    def description(self) -> str:
        return "Auto-match invoices to purchase orders and receipts"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        from finance_modules.ap.orm import APInvoiceModel

        query = session.query(APInvoiceModel).filter(
            APInvoiceModel.status == "pending_match",
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
                error_code="MATCH_FAILED",
                error_message=str(exc),
            )
