"""
Accounts Receivable Module Service - Orchestrates AR operations via engines + kernel.

Thin glue layer that:
1. Calls ReconciliationManager for payment application and matching
2. Calls AllocationEngine for distributing payments across invoices
3. Calls AgingCalculator for AR aging analysis
4. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
    service = ARService(session, role_resolver, clock)
    result = service.record_invoice(
        invoice_id=uuid4(), customer_id=uuid4(),
        amount=Decimal("10000.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.services.journal_writer import RoleResolver
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingService,
    ModulePostingStatus,
)
from finance_services.reconciliation_service import ReconciliationManager
from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationTarget,
)
from finance_engines.aging import AgingCalculator, AgingReport

logger = get_logger("modules.ar.service")


class ARService:
    """
    Orchestrates accounts receivable operations through engines and kernel.

    Engine composition:
    - ReconciliationManager: payment application and receipt matching
    - AllocationEngine: distributing payments across invoices
    - AgingCalculator: AR aging analysis

    Transaction boundary: this service commits on success, rolls back on failure.
    ModulePostingService runs with auto_commit=False so all engine writes
    (links, allocations) and journal writes share a single transaction.
    """

    def __init__(
        self,
        session: Session,
        role_resolver: RoleResolver,
        clock: Clock | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()

        # Kernel posting (auto_commit=False -- we own the boundary)
        self._poster = ModulePostingService(
            session=session,
            role_resolver=role_resolver,
            clock=self._clock,
            auto_commit=False,
        )

        # Stateful engines (share session for atomicity)
        self._link_graph = LinkGraphService(session)
        self._reconciliation = ReconciliationManager(session, self._link_graph)

        # Stateless engines
        self._allocation = AllocationEngine()
        self._aging = AgingCalculator()

    # =========================================================================
    # Invoices
    # =========================================================================

    def record_invoice(
        self,
        invoice_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        invoice_number: str | None = None,
        tax_amount: Decimal | None = None,
        due_date: date | None = None,
        lines: list[dict] | None = None,
        sales_order_id: UUID | None = None,
    ) -> ModulePostingResult:
        """
        Record a customer invoice (revenue recognition).

        Profile: ar.invoice -> ARInvoice (Dr Accounts Receivable / Cr Revenue)
        Links: Creates INVOICE artifact ref for downstream payment application.
        """
        try:
            # Build payload
            payload: dict = {
                "customer_id": str(customer_id),
                "invoice_number": invoice_number,
                "gross_amount": str(amount),
                "due_date": due_date.isoformat() if due_date else None,
            }
            if tax_amount is not None:
                payload["tax_amount"] = str(tax_amount)
            if lines:
                payload["invoice_lines"] = lines
            if sales_order_id is not None:
                payload["sales_order_id"] = str(sales_order_id)

            logger.info("ar_record_invoice_started", extra={
                "invoice_id": str(invoice_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
            })

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="ar.invoice",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_invoice_committed", extra={
                    "invoice_id": str(invoice_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Payments
    # =========================================================================

    def record_payment(
        self,
        payment_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        payment_method: str | None = None,
        reference: str | None = None,
        bank_account_id: UUID | None = None,
    ) -> ModulePostingResult:
        """
        Record a customer payment (direct application to AR balance).

        Profile: ar.payment -> ARPaymentReceived (Dr Cash / Cr AR)
        """
        try:
            payload: dict = {
                "customer_id": str(customer_id),
                "payment_amount": str(amount),
                "payment_method": payment_method,
                "reference": reference,
                "bank_account_id": str(bank_account_id) if bank_account_id else None,
            }

            logger.info("ar_record_payment_started", extra={
                "payment_id": str(payment_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
            })

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="ar.payment",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_payment_committed", extra={
                    "payment_id": str(payment_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Payment Application
    # =========================================================================

    def apply_payment(
        self,
        payment_id: UUID,
        invoice_ids: list[UUID],
        effective_date: date,
        actor_id: UUID,
        payment_amount: Decimal | None = None,
        invoice_amounts: list[Decimal] | None = None,
        currency: str = "USD",
        allocation_method: AllocationMethod = AllocationMethod.FIFO,
        discount_amount: Decimal | None = None,
    ) -> ModulePostingResult:
        """
        Apply a payment to one or more customer invoices.

        Engine: AllocationEngine distributes payment across invoice balances.
        Engine: ReconciliationManager.apply_payment() creates PAID_BY links
                for each invoice that receives payment.
        Profile: ar.receipt_applied (or ar.receipt_applied with has_discount)
        """
        try:
            payment_ref = ArtifactRef.payment(payment_id)

            # If specific per-invoice amounts are provided, apply directly
            if invoice_amounts and len(invoice_amounts) == len(invoice_ids):
                allocations = list(zip(invoice_ids, invoice_amounts))
            elif payment_amount is not None:
                # Engine: allocate payment across invoices
                targets = [
                    AllocationTarget(
                        target_id=str(inv_id),
                        target_type="invoice",
                        date=effective_date,
                    )
                    for inv_id in invoice_ids
                ]
                alloc_result = self._allocation.allocate(
                    amount=Money.of(payment_amount, currency),
                    targets=targets,
                    method=allocation_method,
                )

                allocations = [
                    (inv_id, line.allocated.amount)
                    for inv_id, line in zip(invoice_ids, alloc_result.lines)
                    if not line.allocated.is_zero
                ]

                logger.info("ar_apply_payment_allocated", extra={
                    "payment_id": str(payment_id),
                    "total_allocated": str(alloc_result.total_allocated.amount),
                    "invoices_funded": len(allocations),
                    "method": allocation_method.value,
                })
            else:
                raise ValueError(
                    "Either payment_amount or invoice_amounts must be provided"
                )

            # Engine: create PAID_BY links for each allocation
            total_applied = Decimal("0")
            for inv_id, applied_amt in allocations:
                invoice_ref = ArtifactRef.invoice(inv_id)
                applied_money = Money.of(applied_amt, currency)

                self._reconciliation.apply_payment(
                    invoice_ref=invoice_ref,
                    payment_ref=payment_ref,
                    amount=applied_money,
                    invoice_original_amount=applied_money,  # best-effort; caller owns full amount
                    creating_event_id=payment_id,
                    applied_date=effective_date,
                )
                total_applied += applied_amt

            logger.info("ar_apply_payment_links_created", extra={
                "payment_id": str(payment_id),
                "invoice_count": len(allocations),
                "total_applied": str(total_applied),
            })

            # Build payload
            has_discount = discount_amount is not None and discount_amount > 0
            payload: dict = {
                "payment_id": str(payment_id),
                "invoice_ids": [str(inv_id) for inv_id, _ in allocations],
                "allocations": [
                    {"invoice_id": str(inv_id), "amount": str(amt)}
                    for inv_id, amt in allocations
                ],
                "total_applied": str(total_applied),
            }
            if has_discount:
                payload["has_discount"] = True
                payload["discount_amount"] = str(discount_amount)

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="ar.receipt_applied",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=total_applied,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_apply_payment_committed", extra={
                    "payment_id": str(payment_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Aging
    # =========================================================================

    def calculate_aging(
        self,
        as_of_date: date,
        open_invoices: Sequence[dict] | None = None,
        buckets: Sequence | None = None,
    ) -> AgingReport:
        """
        Calculate AR aging analysis.

        Pure computation -- no posting, no transaction boundary.

        Args:
            as_of_date: Date to age invoices as of.
            open_invoices: Sequence of dicts with invoice details:
                - document_id, document_type, document_date, amount
                - due_date, counterparty_id, counterparty_name (optional)
            buckets: Custom aging buckets (defaults to standard 0/30/60/90+).

        Returns:
            AgingReport with aged items and bucket totals.
        """
        if not open_invoices:
            logger.info("ar_aging_no_invoices", extra={
                "as_of_date": as_of_date.isoformat(),
            })
            return self._aging.generate_report(
                items=(),
                as_of_date=as_of_date,
                buckets=buckets,
                report_type="AR",
            )

        logger.info("ar_aging_started", extra={
            "as_of_date": as_of_date.isoformat(),
            "invoice_count": len(open_invoices),
        })

        report = self._aging.generate_report_from_documents(
            documents=open_invoices,
            as_of_date=as_of_date,
            buckets=buckets,
            report_type="AR",
            use_due_date=True,
        )

        logger.info("ar_aging_completed", extra={
            "as_of_date": as_of_date.isoformat(),
            "item_count": report.item_count,
            "total_amount": str(report.total_amount().amount),
        })

        return report

    # =========================================================================
    # Receipts (unapplied cash)
    # =========================================================================

    def record_receipt(
        self,
        receipt_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_id: UUID | None = None,
        payment_method: str | None = None,
        reference: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a customer receipt as unapplied cash.

        Use apply_payment() to apply the receipt to specific invoices.
        Profile: ar.receipt -> ARReceiptReceived (Dr Cash / Cr Unapplied Cash)
        """
        try:
            payload: dict = {
                "customer_id": str(customer_id),
                "receipt_amount": str(amount),
                "payment_method": payment_method,
                "reference": reference,
                "bank_account_id": str(bank_account_id) if bank_account_id else None,
            }

            logger.info("ar_record_receipt_started", extra={
                "receipt_id": str(receipt_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ar.receipt",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_receipt_committed", extra={
                    "receipt_id": str(receipt_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Credit Memos
    # =========================================================================

    def record_credit_memo(
        self,
        memo_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        reason_code: str,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        invoice_id: UUID | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a credit memo against a customer.

        The reason_code dispatches to different profiles via where-clause:
        - RETURN -> ARCreditMemoReturn
        - PRICE_ADJUSTMENT -> ARCreditMemoPriceAdj
        - SERVICE_CREDIT -> ARCreditMemoService
        - ERROR_CORRECTION -> ARCreditMemoError

        Profile: ar.credit_memo (where payload.reason_code == ...)
        """
        try:
            payload: dict = {
                "customer_id": str(customer_id),
                "reason_code": reason_code,
                "description": description,
            }
            if invoice_id is not None:
                payload["invoice_id"] = str(invoice_id)

            logger.info("ar_record_credit_memo_started", extra={
                "memo_id": str(memo_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
                "reason_code": reason_code,
            })

            result = self._poster.post_event(
                event_type="ar.credit_memo",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_credit_memo_committed", extra={
                    "memo_id": str(memo_id),
                    "reason_code": reason_code,
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Write-offs
    # =========================================================================

    def record_write_off(
        self,
        write_off_id: UUID,
        invoice_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        reason: str | None = None,
    ) -> ModulePostingResult:
        """
        Write off an uncollectible receivable.

        Profile: ar.write_off -> ARWriteOff (Dr Bad Debt Expense / Cr AR)
        """
        try:
            payload: dict = {
                "invoice_id": str(invoice_id),
                "customer_id": str(customer_id),
                "write_off_amount": str(amount),
                "reason": reason,
            }

            logger.info("ar_record_write_off_started", extra={
                "write_off_id": str(write_off_id),
                "invoice_id": str(invoice_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ar.write_off",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_write_off_committed", extra={
                    "write_off_id": str(write_off_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Bad Debt Provision
    # =========================================================================

    def record_bad_debt_provision(
        self,
        provision_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        period_id: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a provision for doubtful accounts.

        Profile: ar.bad_debt_provision -> ARBadDebtProvision
        (Dr Bad Debt Expense / Cr Allowance for Doubtful Accounts)
        """
        try:
            payload: dict = {
                "provision_amount": str(amount),
                "period_id": period_id,
            }

            logger.info("ar_record_bad_debt_provision_started", extra={
                "provision_id": str(provision_id),
                "amount": str(amount),
                "period_id": period_id,
            })

            result = self._poster.post_event(
                event_type="ar.bad_debt_provision",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_bad_debt_provision_committed", extra={
                    "provision_id": str(provision_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Deferred Revenue
    # =========================================================================

    def record_deferred_revenue(
        self,
        deferred_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        bank_account_id: UUID | None = None,
    ) -> ModulePostingResult:
        """
        Record an advance payment as deferred revenue.

        Profile: ar.deferred_revenue_recorded -> ARDeferredRevenueRecorded
        (Dr Cash / Cr Deferred Revenue)
        """
        try:
            payload: dict = {
                "customer_id": str(customer_id),
                "deferred_amount": str(amount),
                "bank_account_id": str(bank_account_id) if bank_account_id else None,
            }

            logger.info("ar_record_deferred_revenue_started", extra={
                "deferred_id": str(deferred_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ar.deferred_revenue_recorded",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_deferred_revenue_committed", extra={
                    "deferred_id": str(deferred_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    def recognize_deferred_revenue(
        self,
        recognition_id: UUID,
        original_deferred_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        period_id: str | None = None,
    ) -> ModulePostingResult:
        """
        Recognize previously deferred revenue as earned.

        Profile: ar.deferred_revenue_recognized -> ARDeferredRevenueRecognized
        (Dr Deferred Revenue / Cr Revenue)
        """
        try:
            payload: dict = {
                "original_deferred_id": str(original_deferred_id),
                "recognized_amount": str(amount),
                "period_id": period_id,
            }

            logger.info("ar_recognize_deferred_revenue_started", extra={
                "recognition_id": str(recognition_id),
                "original_deferred_id": str(original_deferred_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ar.deferred_revenue_recognized",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_recognize_deferred_revenue_committed", extra={
                    "recognition_id": str(recognition_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Refunds
    # =========================================================================

    def record_refund(
        self,
        refund_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        invoice_id: UUID | None = None,
        bank_account_id: UUID | None = None,
    ) -> ModulePostingResult:
        """
        Record a refund issued to a customer.

        Profile: ar.refund -> ARRefundIssued (Dr AR / Cr Cash)
        """
        try:
            payload: dict = {
                "customer_id": str(customer_id),
                "refund_amount": str(amount),
                "bank_account_id": str(bank_account_id) if bank_account_id else None,
            }
            if invoice_id is not None:
                payload["invoice_id"] = str(invoice_id)

            logger.info("ar_record_refund_started", extra={
                "refund_id": str(refund_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ar.refund",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_refund_committed", extra={
                    "refund_id": str(refund_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
