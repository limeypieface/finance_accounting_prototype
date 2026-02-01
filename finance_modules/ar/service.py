"""
Accounts Receivable Module Service (``finance_modules.ar.service``).

Responsibility
--------------
Orchestrates AR operations -- customer invoices, payment application,
credit memos, dunning, aging analysis, write-offs, and auto-apply rules
-- by delegating pure computation to ``finance_engines`` and journal
persistence to ``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``ARService`` is the sole public entry
point for AR operations.  It composes stateless engines (``AllocationEngine``,
``AgingCalculator``), stateful engines (``ReconciliationManager``,
``LinkGraphService``), and the kernel ``ModulePostingService``.

Invariants enforced
-------------------
* R7  -- Each public method owns the transaction boundary
          (``commit`` on success, ``rollback`` on failure or exception).
* R14 -- Event type selection is data-driven; no ``if/switch`` on event_type
          inside the posting path.
* L1  -- Account ROLES in profiles; COA resolution deferred to kernel.
* L5  -- Atomicity: link creation and journal posting share a single
          transaction (``auto_commit=False``).

Failure modes
-------------
* Guard rejection or kernel validation  -> ``ModulePostingResult`` with
  ``is_success == False``; session rolled back.
* Unexpected exception  -> session rolled back, exception re-raised.
* Engine errors (e.g., invalid match)  -> propagate before posting attempt.

Audit relevance
---------------
Structured log events emitted at operation start and commit/rollback for
every public method, carrying IDs, amounts, and statuses.  All journal
entries feed the kernel audit chain (R11).

Usage::

    service = ARService(session, role_resolver, clock)
    result = service.record_invoice(
        invoice_id=uuid4(), customer_id=uuid4(),
        amount=Decimal("10000.00"),
        effective_date=date.today(), actor_id=actor_id,
    )
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from finance_engines.aging import AgingCalculator, AgingReport
from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationTarget,
)
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
from finance_modules.ar.models import (
    AutoApplyRule,
    CreditDecision,
    DunningHistory,
    DunningLevel,
)
from finance_modules.ar.orm import (
    ARCreditDecisionModel,
    ARCreditMemoModel,
    ARDunningHistoryModel,
    ARInvoiceModel,
    ARReceiptModel,
)
from finance_services.reconciliation_service import ReconciliationManager

logger = get_logger("modules.ar.service")


class ARService:
    """
    Orchestrates accounts receivable operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``calculate_aging``, ``run_dunning``, etc.) return
      pure domain objects with no side-effects on the journal.

    Guarantees
    ----------
    * Session is committed only on ``result.is_success``; otherwise rolled back.
    * Engine link writes and journal writes share a single transaction
      (``ModulePostingService`` runs with ``auto_commit=False``).
    * Clock is injectable for deterministic testing.

    Non-goals
    ---------
    * Does NOT own account-code resolution (delegated to kernel via ROLES).
    * Does NOT enforce fiscal-period locks directly (kernel ``PeriodService``
      handles R12/R13).

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
                orm_invoice = ARInvoiceModel(
                    id=invoice_id,
                    customer_id=customer_id,
                    invoice_number=invoice_number or str(invoice_id),
                    invoice_date=effective_date,
                    due_date=due_date or effective_date,
                    currency=currency,
                    subtotal=amount - (tax_amount or Decimal("0")),
                    tax_amount=tax_amount or Decimal("0"),
                    total_amount=amount,
                    balance_due=amount,
                    status="draft",
                    sales_order_id=sales_order_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_invoice)
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
                orm_receipt = ARReceiptModel(
                    id=payment_id,
                    customer_id=customer_id,
                    receipt_date=effective_date,
                    amount=amount,
                    currency=currency,
                    payment_method=payment_method or "ach",
                    reference=reference or str(payment_id),
                    status="unallocated",
                    bank_account_id=bank_account_id,
                    unallocated_amount=amount,
                    created_by_id=actor_id,
                )
                self._session.add(orm_receipt)
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
                allocations = list(zip(invoice_ids, invoice_amounts, strict=False))
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
                    for inv_id, line in zip(invoice_ids, alloc_result.lines, strict=False)
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
                orm_receipt = ARReceiptModel(
                    id=receipt_id,
                    customer_id=customer_id,
                    receipt_date=effective_date,
                    amount=amount,
                    currency=currency,
                    payment_method=payment_method or "ach",
                    reference=reference or str(receipt_id),
                    status="unallocated",
                    bank_account_id=bank_account_id,
                    unallocated_amount=amount,
                    created_by_id=actor_id,
                )
                self._session.add(orm_receipt)
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
                orm_memo = ARCreditMemoModel(
                    id=memo_id,
                    customer_id=customer_id,
                    credit_memo_number=str(memo_id),
                    issue_date=effective_date,
                    amount=amount,
                    currency=currency,
                    reason=description or reason_code,
                    status="draft",
                    original_invoice_id=invoice_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_memo)
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
                existing = self._session.get(ARInvoiceModel, invoice_id)
                if existing is not None:
                    existing.status = "written_off"
                    existing.balance_due = Decimal("0")
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

    # =========================================================================
    # Dunning
    # =========================================================================

    def generate_dunning_letters(
        self,
        as_of_date: date,
        overdue_customers: Sequence[dict],
        actor_id: UUID,
        currency: str = "USD",
    ) -> list[DunningHistory]:
        """
        Generate dunning letters for overdue customers.

        No posting — pure domain operation using AgingCalculator.
        Assigns dunning levels based on days overdue.

        Args:
            as_of_date: Date for aging calculation.
            overdue_customers: Sequence of dicts with:
                - customer_id, total_overdue, days_overdue, invoice_count
            actor_id: User generating the dunning run.

        Returns:
            List of DunningHistory records for each customer.
        """
        from uuid import uuid4 as _uuid4

        results: list[DunningHistory] = []

        logger.info("ar_generate_dunning_started", extra={
            "as_of_date": as_of_date.isoformat(),
            "customer_count": len(overdue_customers),
        })

        for customer in overdue_customers:
            days_overdue = int(customer.get("days_overdue", 0))
            if days_overdue <= 0:
                continue

            # Assign dunning level based on days overdue
            if days_overdue <= 30:
                level = DunningLevel.REMINDER
            elif days_overdue <= 60:
                level = DunningLevel.FIRST_NOTICE
            elif days_overdue <= 90:
                level = DunningLevel.SECOND_NOTICE
            elif days_overdue <= 120:
                level = DunningLevel.FINAL_NOTICE
            else:
                level = DunningLevel.COLLECTION

            dunning_id = _uuid4()
            record = DunningHistory(
                id=dunning_id,
                customer_id=customer["customer_id"],
                level=level,
                sent_date=as_of_date,
                as_of_date=as_of_date,
                total_overdue=Decimal(str(customer.get("total_overdue", "0"))),
                invoice_count=int(customer.get("invoice_count", 0)),
                currency=currency,
            )
            results.append(record)

            orm_dunning = ARDunningHistoryModel(
                id=dunning_id,
                customer_id=customer["customer_id"],
                level=level.value,
                sent_date=as_of_date,
                as_of_date=as_of_date,
                total_overdue=Decimal(str(customer.get("total_overdue", "0"))),
                invoice_count=int(customer.get("invoice_count", 0)),
                currency=currency,
                created_by_id=actor_id,
            )
            self._session.add(orm_dunning)

        logger.info("ar_generate_dunning_completed", extra={
            "letters_generated": len(results),
        })

        return results

    # =========================================================================
    # Auto Cash Application
    # =========================================================================

    def auto_apply_payment(
        self,
        payment_id: UUID,
        customer_id: UUID,
        payment_amount: Decimal,
        open_invoices: Sequence[dict],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Automatically apply a payment to open invoices (oldest-first).

        Engine: AllocationEngine (FIFO) to distribute across invoices.
        Engine: ReconciliationManager.apply_payment() for link creation.
        Reuses existing ar.receipt_applied profile — no new profile needed.
        """
        try:
            # Sort invoices oldest-first for FIFO application
            sorted_invoices = sorted(
                open_invoices,
                key=lambda inv: inv.get("due_date", inv.get("invoice_date", "")),
            )

            invoice_ids = [inv["invoice_id"] for inv in sorted_invoices]

            logger.info("ar_auto_apply_payment_started", extra={
                "payment_id": str(payment_id),
                "customer_id": str(customer_id),
                "payment_amount": str(payment_amount),
                "invoice_count": len(invoice_ids),
            })

            # Delegate to existing apply_payment (FIFO allocation + reconciliation)
            result = self.apply_payment(
                payment_id=payment_id,
                invoice_ids=invoice_ids,
                effective_date=effective_date,
                actor_id=actor_id,
                payment_amount=payment_amount,
                currency=currency,
                allocation_method=AllocationMethod.FIFO,
            )

            logger.info("ar_auto_apply_payment_completed", extra={
                "payment_id": str(payment_id),
                "status": result.status.value,
            })

            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Credit Management
    # =========================================================================

    def check_credit_limit(
        self,
        customer_id: UUID,
        order_amount: Decimal,
        current_balance: Decimal,
        credit_limit: Decimal,
        actor_id: UUID,
    ) -> CreditDecision:
        """
        Check if an order would exceed the customer credit limit.

        No posting — pure domain evaluation.

        Returns:
            CreditDecision with approved=True/False.
        """
        from uuid import uuid4 as _uuid4

        projected_balance = current_balance + order_amount
        approved = projected_balance <= credit_limit

        logger.info("ar_check_credit_limit", extra={
            "customer_id": str(customer_id),
            "order_amount": str(order_amount),
            "current_balance": str(current_balance),
            "credit_limit": str(credit_limit),
            "projected_balance": str(projected_balance),
            "approved": approved,
        })

        decision_id = _uuid4()
        decision = CreditDecision(
            id=decision_id,
            customer_id=customer_id,
            decision_date=self._clock.now().date(),
            previous_limit=credit_limit,
            new_limit=None,
            order_amount=order_amount,
            approved=approved,
            reason="Within limit" if approved else f"Projected balance {projected_balance} exceeds limit {credit_limit}",
            decided_by=actor_id,
        )

        orm_decision = ARCreditDecisionModel(
            id=decision_id,
            customer_id=customer_id,
            decision_date=self._clock.now().date(),
            previous_limit=credit_limit,
            new_limit=None,
            order_amount=order_amount,
            approved=approved,
            reason=decision.reason,
            decided_by=actor_id,
            created_by_id=actor_id,
        )
        self._session.add(orm_decision)

        return decision

    def update_credit_limit(
        self,
        customer_id: UUID,
        previous_limit: Decimal,
        new_limit: Decimal,
        actor_id: UUID,
        reason: str | None = None,
    ) -> CreditDecision:
        """
        Update a customer's credit limit.

        No posting — returns CreditDecision record.
        """
        from uuid import uuid4 as _uuid4

        logger.info("ar_update_credit_limit", extra={
            "customer_id": str(customer_id),
            "previous_limit": str(previous_limit),
            "new_limit": str(new_limit),
        })

        decision_id = _uuid4()
        decision = CreditDecision(
            id=decision_id,
            customer_id=customer_id,
            decision_date=self._clock.now().date(),
            previous_limit=previous_limit,
            new_limit=new_limit,
            approved=True,
            reason=reason or f"Credit limit updated from {previous_limit} to {new_limit}",
            decided_by=actor_id,
        )

        orm_decision = ARCreditDecisionModel(
            id=decision_id,
            customer_id=customer_id,
            decision_date=self._clock.now().date(),
            previous_limit=previous_limit,
            new_limit=new_limit,
            approved=True,
            reason=decision.reason,
            decided_by=actor_id,
            created_by_id=actor_id,
        )
        self._session.add(orm_decision)

        return decision

    # =========================================================================
    # Small Balance Write-Off
    # =========================================================================

    def auto_write_off_small_balances(
        self,
        threshold: Decimal,
        small_balance_invoices: Sequence[dict],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> list[ModulePostingResult]:
        """
        Batch write-off small balances below a threshold.

        Loops existing record_write_off() for each qualifying invoice.
        Reuses existing ar.write_off profile — no new profile needed.
        """
        results: list[ModulePostingResult] = []

        logger.info("ar_auto_write_off_started", extra={
            "threshold": str(threshold),
            "candidate_count": len(small_balance_invoices),
        })

        for inv in small_balance_invoices:
            balance = Decimal(str(inv.get("balance", "0")))
            if Decimal("0") < balance <= threshold:
                result = self.record_write_off(
                    write_off_id=inv["invoice_id"],
                    invoice_id=inv["invoice_id"],
                    customer_id=inv["customer_id"],
                    amount=balance,
                    effective_date=effective_date,
                    actor_id=actor_id,
                    currency=currency,
                    reason=f"Small balance write-off (threshold: {threshold})",
                )
                results.append(result)

        logger.info("ar_auto_write_off_completed", extra={
            "total_written_off": sum(1 for r in results if r.is_success),
            "total_failed": sum(1 for r in results if not r.is_success),
        })

        return results

    # =========================================================================
    # Finance Charges
    # =========================================================================

    def record_finance_charge(
        self,
        charge_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        period: str | None = None,
        annual_rate: Decimal | None = None,
    ) -> ModulePostingResult:
        """
        Record a finance charge for late payment.

        Profile: ar.finance_charge -> ARFinanceCharge
        (Dr Accounts Receivable / Cr Interest Income)
        """
        try:
            payload: dict = {
                "customer_id": str(customer_id),
                "charge_amount": str(amount),
                "period": period,
            }
            if annual_rate is not None:
                payload["annual_rate"] = str(annual_rate)

            logger.info("ar_record_finance_charge_started", extra={
                "charge_id": str(charge_id),
                "customer_id": str(customer_id),
                "amount": str(amount),
                "period": period,
            })

            result = self._poster.post_event(
                event_type="ar.finance_charge",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ar_record_finance_charge_committed", extra={
                    "charge_id": str(charge_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise
