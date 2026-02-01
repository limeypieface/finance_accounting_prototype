"""
Accounts Payable Module Service (``finance_modules.ap.service``).

Responsibility
--------------
Orchestrates AP operations -- vendor invoices, payments, three-way matching,
aging, accruals, prepayments, and batch payment runs -- by delegating pure
computation to ``finance_engines`` and journal persistence to
``finance_kernel.services.module_posting_service``.

Architecture position
---------------------
**Modules layer** -- thin ERP glue.  ``APService`` is the sole public entry
point for AP operations.  It composes stateless engines (``AllocationEngine``,
``MatchingEngine``, ``AgingCalculator``), stateful engines
(``ReconciliationManager``, ``LinkGraphService``), and the kernel
``ModulePostingService``.

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

    service = APService(session, role_resolver, clock)
    result = service.record_invoice(
        invoice_id=uuid4(), vendor_id=uuid4(),
        amount=Decimal("5000.00"),
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
from finance_engines.matching import (
    MatchCandidate,
    MatchingEngine,
    MatchResult,
    MatchTolerance,
    MatchType,
)
from finance_engines.aging import AgingCalculator, AgingReport
from finance_modules.ap.models import (
    HoldStatus,
    PaymentRun,
    PaymentRunLine,
    PaymentRunStatus,
    VendorHold,
)
from finance_modules.ap.orm import (
    APInvoiceModel,
    APPaymentModel,
    APPaymentRunModel,
    APVendorHoldModel,
)

logger = get_logger("modules.ap.service")


class APService:
    """
    Orchestrates accounts payable operations through engines and kernel.

    Contract
    --------
    * Every posting method returns ``ModulePostingResult``; callers inspect
      ``result.is_success`` to determine outcome.
    * Non-posting helpers (``calculate_aging``, ``hold_vendor``, etc.) return
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
    - ReconciliationManager: payment matching and application
    - AllocationEngine: distributing amounts across invoice lines
    - MatchingEngine: 3-way PO/receipt/invoice matching
    - AgingCalculator: AP aging analysis

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
        self._matching = MatchingEngine()
        self._aging = AgingCalculator()

    # =========================================================================
    # Invoices
    # =========================================================================

    def record_invoice(
        self,
        invoice_id: UUID,
        vendor_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        invoice_number: str | None = None,
        tax_amount: Decimal | None = None,
        po_number: str | None = None,
        due_date: date | None = None,
        lines: list[dict] | None = None,
    ) -> ModulePostingResult:
        """
        Record a vendor invoice.

        Determines event type based on whether a PO is present:
        - With PO: "ap.invoice_received" (where-clause dispatches to PO-matched profile)
        - Without PO: "ap.invoice_received" (where-clause dispatches to expense profile)

        Links: Creates INVOICE artifact ref for downstream payment matching.

        Preconditions:
            - ``amount`` must be a positive ``Decimal`` (guard enforced by profile).
            - ``currency`` must be a valid ISO 4217 code (R16).
        Postconditions:
            - On success: one POSTED journal entry, session committed.
            - On failure: session rolled back, result describes rejection reason.
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            # Build payload
            payload: dict = {
                "vendor_id": str(vendor_id),
                "invoice_number": invoice_number,
                "gross_amount": str(amount),
                "due_date": due_date.isoformat() if due_date else None,
            }
            if tax_amount is not None:
                payload["tax_amount"] = str(tax_amount)
            if po_number is not None:
                payload["po_number"] = po_number
            if lines:
                payload["invoice_lines"] = lines

            logger.info("ap_record_invoice_started", extra={
                "invoice_id": str(invoice_id),
                "vendor_id": str(vendor_id),
                "amount": str(amount),
                "has_po": po_number is not None,
            })

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="ap.invoice_received",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                orm_invoice = APInvoiceModel(
                    id=invoice_id,
                    vendor_id=vendor_id,
                    invoice_number=invoice_number or str(invoice_id),
                    invoice_date=effective_date,
                    due_date=due_date or effective_date,
                    currency=currency,
                    subtotal=amount - (tax_amount or Decimal("0")),
                    tax_amount=tax_amount or Decimal("0"),
                    total_amount=amount,
                    status="draft",
                    po_id=None,
                    created_by_id=actor_id,
                )
                self._session.add(orm_invoice)
                self._session.commit()
                logger.info("ap_record_invoice_committed", extra={
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
        invoice_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        vendor_id: UUID,
        currency: str = "USD",
        payment_method: str | None = None,
        reference: str | None = None,
        discount_amount: Decimal | None = None,
        bank_account_id: UUID | None = None,
    ) -> ModulePostingResult:
        """
        Record a payment to a vendor.

        Engine: ReconciliationManager.apply_payment() creates PAID_BY link
                between invoice and payment artifacts.
        Profile: ap.payment or ap.payment_with_discount (if discount present)

        Preconditions:
            - ``amount`` > 0 (guard enforced by profile).
            - ``invoice_id`` references a previously recorded invoice.
        Postconditions:
            - PAID_BY economic link persisted in same transaction as journal entry.
            - On success: session committed with link + journal atomically.
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            invoice_ref = ArtifactRef.invoice(invoice_id)
            payment_ref = ArtifactRef.payment(payment_id)

            # Engine: create payment application link
            payment_money = Money.of(amount, currency)
            invoice_original = Money.of(amount, currency)  # caller should supply full amount

            self._reconciliation.apply_payment(
                invoice_ref=invoice_ref,
                payment_ref=payment_ref,
                amount=payment_money,
                invoice_original_amount=invoice_original,
                creating_event_id=payment_id,
                applied_date=effective_date,
            )

            logger.info("ap_record_payment_link_created", extra={
                "payment_id": str(payment_id),
                "invoice_id": str(invoice_id),
                "amount": str(amount),
            })

            # Build payload
            has_discount = discount_amount is not None and discount_amount > 0
            event_type = "ap.payment_with_discount" if has_discount else "ap.payment"

            payload: dict = {
                "vendor_id": str(vendor_id) if vendor_id else None,
                "invoice_id": str(invoice_id),
                "payment_amount": str(amount),
                "payment_method": payment_method,
                "reference": reference,
                "bank_account_id": str(bank_account_id) if bank_account_id else None,
            }
            if has_discount:
                payload["discount_amount"] = str(discount_amount)

            # Posting amount: full invoice cleared (payment + discount)
            posting_amount = amount + discount_amount if has_discount else amount

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type=event_type,
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=posting_amount,
                currency=currency,
            )

            if result.is_success:
                import json as _json
                orm_payment = APPaymentModel(
                    id=payment_id,
                    vendor_id=vendor_id,
                    payment_date=effective_date,
                    payment_method=payment_method or "ach",
                    amount=amount,
                    currency=currency,
                    reference=reference or str(payment_id),
                    status="draft",
                    invoice_ids_json=_json.dumps([str(invoice_id)]),
                    discount_taken=discount_amount or Decimal("0"),
                    bank_account_id=bank_account_id,
                    created_by_id=actor_id,
                )
                self._session.add(orm_payment)
                self._session.commit()
                logger.info("ap_record_payment_committed", extra={
                    "payment_id": str(payment_id),
                    "event_type": event_type,
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # 3-Way Matching
    # =========================================================================

    def match_invoice_to_po(
        self,
        invoice_id: UUID,
        po_id: UUID,
        receipt_ids: list[UUID],
        effective_date: date,
        actor_id: UUID,
        invoice_amount: Decimal | None = None,
        po_amount: Decimal | None = None,
        currency: str = "USD",
        tolerance: MatchTolerance | None = None,
    ) -> ModulePostingResult:
        """
        Match a vendor invoice to a purchase order and receipts (3-way match).

        Engine: MatchingEngine.create_match() evaluates PO/receipt/invoice match.
        Engine: ReconciliationManager creates FULFILLED_BY links in the link graph.
        Profile: ap.invoice_received (PO-matched variant via where-clause dispatch)

        Preconditions:
            - ``receipt_ids`` must be non-empty for a valid 3-way match.
            - ``po_id`` references an existing purchase order.
        Postconditions:
            - FULFILLED_BY links created: PO -> Receipt(s) -> Invoice.
            - Match result recorded; journal entry posted if match succeeds.
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            invoice_ref = ArtifactRef.invoice(invoice_id)
            po_ref = ArtifactRef.purchase_order(po_id)

            # Build match candidates for the matching engine
            invoice_candidate = MatchCandidate(
                document_type="INVOICE",
                document_id=invoice_id,
                amount=Money.of(invoice_amount, currency) if invoice_amount else None,
                date=effective_date,
            )
            po_candidate = MatchCandidate(
                document_type="PO",
                document_id=po_id,
                amount=Money.of(po_amount, currency) if po_amount else None,
            )
            receipt_candidates = [
                MatchCandidate(
                    document_type="RECEIPT",
                    document_id=rid,
                )
                for rid in receipt_ids
            ]

            # Engine: create the 3-way match
            all_documents = [po_candidate, *receipt_candidates, invoice_candidate]
            match_result: MatchResult = self._matching.create_match(
                documents=all_documents,
                match_type=MatchType.THREE_WAY,
                as_of_date=effective_date,
                tolerance=tolerance,
            )

            # Engine: establish FULFILLED_BY links in the graph
            for receipt_id in receipt_ids:
                receipt_ref = ArtifactRef.receipt(receipt_id)
                # PO -> Receipt link
                self._link_graph.establish_link_simple(
                    parent_ref=po_ref,
                    child_ref=receipt_ref,
                    link_type="fulfilled_by",
                    creating_event_id=invoice_id,
                )
                # Receipt -> Invoice link
                self._link_graph.establish_link_simple(
                    parent_ref=receipt_ref,
                    child_ref=invoice_ref,
                    link_type="fulfilled_by",
                    creating_event_id=invoice_id,
                )

            logger.info("ap_match_invoice_to_po_linked", extra={
                "invoice_id": str(invoice_id),
                "po_id": str(po_id),
                "receipt_count": len(receipt_ids),
                "match_status": match_result.status.value,
                "has_variance": match_result.has_variance,
            })

            # Build payload
            payload: dict = {
                "invoice_id": str(invoice_id),
                "po_number": str(po_id),
                "receipt_ids": [str(rid) for rid in receipt_ids],
                "match_status": match_result.status.value,
                "matched_amount": str(match_result.matched_amount.amount),
                "has_variance": match_result.has_variance,
            }

            # Kernel: post journal entry
            result = self._poster.post_event(
                event_type="ap.invoice_received",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=match_result.matched_amount.amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
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
        Calculate AP aging analysis.

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
            logger.info("ap_aging_no_invoices", extra={
                "as_of_date": as_of_date.isoformat(),
            })
            return self._aging.generate_report(
                items=(),
                as_of_date=as_of_date,
                buckets=buckets,
                report_type="AP",
            )

        logger.info("ap_aging_started", extra={
            "as_of_date": as_of_date.isoformat(),
            "invoice_count": len(open_invoices),
        })

        report = self._aging.generate_report_from_documents(
            documents=open_invoices,
            as_of_date=as_of_date,
            buckets=buckets,
            report_type="AP",
            use_due_date=True,
        )

        logger.info("ap_aging_completed", extra={
            "as_of_date": as_of_date.isoformat(),
            "item_count": report.item_count,
            "total_amount": str(report.total_amount().amount),
        })

        return report

    # =========================================================================
    # Invoice Cancellation
    # =========================================================================

    def cancel_invoice(
        self,
        invoice_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        reason: str | None = None,
        tax_amount: Decimal | None = None,
        lines: list[dict] | None = None,
    ) -> ModulePostingResult:
        """
        Cancel/reverse a vendor invoice.

        Profile: ap.invoice_cancelled -> APInvoiceCancelled
        Reverses the original invoice GL entries.

        Preconditions:
            - ``amount`` matches the original invoice total.
        Postconditions:
            - Reversal journal entry posted (R10 -- original entry unchanged).
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            payload: dict = {
                "invoice_id": str(invoice_id),
                "reason": reason,
            }
            if tax_amount is not None:
                payload["tax_amount"] = str(tax_amount)
            if lines:
                payload["invoice_lines"] = lines

            logger.info("ap_cancel_invoice_started", extra={
                "invoice_id": str(invoice_id),
                "amount": str(amount),
                "reason": reason,
            })

            result = self._poster.post_event(
                event_type="ap.invoice_cancelled",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                existing = self._session.get(APInvoiceModel, invoice_id)
                if existing is not None:
                    existing.status = "cancelled"
                self._session.commit()
                logger.info("ap_cancel_invoice_committed", extra={
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
    # Inventory Invoice
    # =========================================================================

    def record_inventory_invoice(
        self,
        invoice_id: UUID,
        vendor_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        po_number: str | None = None,
        tax_amount: Decimal | None = None,
        lines: list[dict] | None = None,
    ) -> ModulePostingResult:
        """
        Record a vendor invoice for inventory items.

        Profile: ap.invoice_received_inventory -> APInvoiceInventory
        Debits Inventory (not Expense) and credits AP.

        Preconditions:
            - ``amount`` > 0, ``currency`` valid ISO 4217.
        Postconditions:
            - INVENTORY role debited (resolved to COA at posting time, L1).
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            payload: dict = {
                "vendor_id": str(vendor_id),
                "gross_amount": str(amount),
            }
            if po_number is not None:
                payload["po_number"] = po_number
            if tax_amount is not None:
                payload["tax_amount"] = str(tax_amount)
            if lines:
                payload["invoice_lines"] = lines

            logger.info("ap_record_inventory_invoice_started", extra={
                "invoice_id": str(invoice_id),
                "vendor_id": str(vendor_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ap.invoice_received_inventory",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                orm_invoice = APInvoiceModel(
                    id=invoice_id,
                    vendor_id=vendor_id,
                    invoice_number=str(invoice_id),
                    invoice_date=effective_date,
                    due_date=effective_date,
                    currency=currency,
                    subtotal=amount - (tax_amount or Decimal("0")),
                    tax_amount=tax_amount or Decimal("0"),
                    total_amount=amount,
                    status="draft",
                    po_id=None,
                    created_by_id=actor_id,
                )
                self._session.add(orm_invoice)
                self._session.commit()
                logger.info("ap_record_inventory_invoice_committed", extra={
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
    # Accruals
    # =========================================================================

    def record_accrual(
        self,
        accrual_id: UUID,
        vendor_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        period_id: str | None = None,
        description: str | None = None,
    ) -> ModulePostingResult:
        """
        Record a period-end AP accrual for uninvoiced receipts.

        Profile: ap.accrual_recorded -> APAccrualRecorded

        Preconditions:
            - ``amount`` > 0; period must be OPEN (R12 enforced by kernel).
        Postconditions:
            - Dr Expense / Cr Accrued Liability posted.
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            payload: dict = {
                "vendor_id": str(vendor_id),
                "period_id": period_id,
                "description": description,
            }

            logger.info("ap_record_accrual_started", extra={
                "accrual_id": str(accrual_id),
                "vendor_id": str(vendor_id),
                "amount": str(amount),
                "period_id": period_id,
            })

            result = self._poster.post_event(
                event_type="ap.accrual_recorded",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ap_record_accrual_committed", extra={
                    "accrual_id": str(accrual_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    def reverse_accrual(
        self,
        reversal_id: UUID,
        original_accrual_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        period_id: str | None = None,
    ) -> ModulePostingResult:
        """
        Reverse a previously recorded AP accrual.

        Profile: ap.accrual_reversed -> APAccrualReversed

        Preconditions:
            - ``amount`` matches the original accrual amount.
        Postconditions:
            - Dr Accrued Liability / Cr Expense posted (mirror of accrual).
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            payload: dict = {
                "original_accrual_id": str(original_accrual_id),
                "period_id": period_id,
            }

            logger.info("ap_reverse_accrual_started", extra={
                "reversal_id": str(reversal_id),
                "original_accrual_id": str(original_accrual_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ap.accrual_reversed",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ap_reverse_accrual_committed", extra={
                    "reversal_id": str(reversal_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Prepayments
    # =========================================================================

    def record_prepayment(
        self,
        prepayment_id: UUID,
        vendor_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Record an advance payment to a vendor.

        Profile: ap.prepayment_recorded -> APPrepaymentRecorded

        Preconditions:
            - ``amount`` > 0.
        Postconditions:
            - Dr Prepaid Expense / Cr Cash posted.
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            payload: dict = {
                "vendor_id": str(vendor_id),
                "prepayment_amount": str(amount),
            }

            logger.info("ap_record_prepayment_started", extra={
                "prepayment_id": str(prepayment_id),
                "vendor_id": str(vendor_id),
                "amount": str(amount),
            })

            result = self._poster.post_event(
                event_type="ap.prepayment_recorded",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ap_record_prepayment_committed", extra={
                    "prepayment_id": str(prepayment_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    def apply_prepayment(
        self,
        application_id: UUID,
        prepayment_id: UUID,
        invoice_id: UUID,
        amount: Decimal,
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
    ) -> ModulePostingResult:
        """
        Apply a vendor prepayment against an invoice.

        Engine: ReconciliationManager creates PAID_BY link between
                prepayment and invoice artifacts.
        Profile: ap.prepayment_applied -> APPrepaymentApplied

        Preconditions:
            - ``amount`` <= outstanding prepayment balance.
            - ``invoice_id`` references a recorded invoice.
        Postconditions:
            - PAID_BY link persisted; Dr AP / Cr Prepaid Expense posted.
        Raises:
            Exception: re-raised after rollback for unexpected failures.
        """
        try:
            # Engine: create link between prepayment and invoice
            prepayment_ref = ArtifactRef.payment(prepayment_id)
            invoice_ref = ArtifactRef.invoice(invoice_id)
            applied_money = Money.of(amount, currency)

            self._reconciliation.apply_payment(
                invoice_ref=invoice_ref,
                payment_ref=prepayment_ref,
                amount=applied_money,
                invoice_original_amount=applied_money,
                creating_event_id=application_id,
                applied_date=effective_date,
            )

            logger.info("ap_apply_prepayment_link_created", extra={
                "application_id": str(application_id),
                "prepayment_id": str(prepayment_id),
                "invoice_id": str(invoice_id),
                "amount": str(amount),
            })

            payload: dict = {
                "prepayment_id": str(prepayment_id),
                "invoice_id": str(invoice_id),
                "applied_amount": str(amount),
            }

            result = self._poster.post_event(
                event_type="ap.prepayment_applied",
                payload=payload,
                effective_date=effective_date,
                actor_id=actor_id,
                amount=amount,
                currency=currency,
            )

            if result.is_success:
                self._session.commit()
                logger.info("ap_apply_prepayment_committed", extra={
                    "application_id": str(application_id),
                    "status": result.status.value,
                })
            else:
                self._session.rollback()
            return result

        except Exception:
            self._session.rollback()
            raise

    # =========================================================================
    # Batch Payment Runs
    # =========================================================================

    def create_payment_run(
        self,
        run_id: UUID,
        payment_date: date,
        invoices: Sequence[dict],
        actor_id: UUID,
        currency: str = "USD",
    ) -> PaymentRun:
        """
        Create a payment run selecting invoices for batch payment.

        No posting -- pure domain operation. Produces a PaymentRun with
        lines ready for execution.

        Engine: AgingCalculator used upstream by caller to select invoices.

        Preconditions:
            - ``invoices`` is a non-empty sequence of dicts with ``amount`` keys.
        Postconditions:
            - Returns a ``PaymentRun`` in DRAFT status (no DB side-effects).
        """
        from uuid import uuid4

        total = sum(Decimal(str(inv.get("amount", "0"))) for inv in invoices)

        logger.info("ap_create_payment_run", extra={
            "run_id": str(run_id),
            "payment_date": payment_date.isoformat(),
            "invoice_count": len(invoices),
            "total_amount": str(total),
        })

        run = PaymentRun(
            id=run_id,
            payment_date=payment_date,
            currency=currency,
            status=PaymentRunStatus.DRAFT,
            total_amount=total,
            line_count=len(invoices),
            created_by=actor_id,
        )

        orm_run = APPaymentRunModel(
            id=run_id,
            payment_date=payment_date,
            currency=currency,
            status="draft",
            total_amount=total,
            line_count=len(invoices),
            created_by=actor_id,
            created_by_id=actor_id,
        )
        self._session.add(orm_run)

        return run

    def execute_payment_run(
        self,
        run: PaymentRun,
        lines: Sequence[PaymentRunLine],
        actor_id: UUID,
    ) -> list[ModulePostingResult]:
        """
        Execute a payment run, posting each line via record_payment().

        Reuses existing APPayment profile for each individual payment.
        Returns list of posting results for each line.

        Preconditions:
            - ``run`` is in DRAFT or APPROVED status.
            - Each ``PaymentRunLine`` references a valid invoice.
        Postconditions:
            - One ``ModulePostingResult`` per line; each line posts independently.
        """
        from uuid import uuid4

        results: list[ModulePostingResult] = []

        logger.info("ap_execute_payment_run_started", extra={
            "run_id": str(run.id),
            "line_count": len(lines),
            "total_amount": str(run.total_amount),
        })

        for line in lines:
            payment_id = uuid4()
            result = self.record_payment(
                payment_id=payment_id,
                invoice_id=line.invoice_id,
                amount=line.amount,
                effective_date=run.payment_date,
                actor_id=actor_id,
                currency=run.currency,
                vendor_id=line.vendor_id,
                discount_amount=line.discount_amount if line.discount_amount > 0 else None,
            )
            results.append(result)

        logger.info("ap_execute_payment_run_completed", extra={
            "run_id": str(run.id),
            "total_posted": sum(1 for r in results if r.is_success),
            "total_failed": sum(1 for r in results if not r.is_success),
        })

        return results

    # =========================================================================
    # Auto-Matching
    # =========================================================================

    def auto_match_invoices(
        self,
        candidates: Sequence[dict],
        effective_date: date,
        actor_id: UUID,
        currency: str = "USD",
        tolerance: MatchTolerance | None = None,
    ) -> list[MatchResult]:
        """
        Batch auto-match invoices to POs/receipts.

        Engine: MatchingEngine.create_match() for each candidate set.
        Engine: ReconciliationManager for link creation.
        No new profile — uses existing ap.invoice_received dispatch.

        Returns list of MatchResults for each attempted match.
        """
        results: list[MatchResult] = []

        logger.info("ap_auto_match_started", extra={
            "candidate_count": len(candidates),
            "effective_date": effective_date.isoformat(),
        })

        for candidate in candidates:
            invoice_id = candidate.get("invoice_id")
            po_id = candidate.get("po_id")
            invoice_amount = candidate.get("invoice_amount")
            po_amount = candidate.get("po_amount")

            invoice_candidate = MatchCandidate(
                document_type="INVOICE",
                document_id=invoice_id,
                amount=Money.of(Decimal(str(invoice_amount)), currency) if invoice_amount else None,
                date=effective_date,
            )
            po_candidate = MatchCandidate(
                document_type="PO",
                document_id=po_id,
                amount=Money.of(Decimal(str(po_amount)), currency) if po_amount else None,
            )

            match_result = self._matching.create_match(
                documents=[po_candidate, invoice_candidate],
                match_type=MatchType.TWO_WAY,
                as_of_date=effective_date,
                tolerance=tolerance,
            )
            results.append(match_result)

        from finance_engines.matching import MatchStatus
        matched_statuses = {MatchStatus.MATCHED, MatchStatus.VARIANCE}
        logger.info("ap_auto_match_completed", extra={
            "total_matched": sum(1 for r in results if r.status in matched_statuses),
            "total_unmatched": sum(1 for r in results if r.status not in matched_statuses),
        })

        return results

    # =========================================================================
    # Vendor Hold/Release
    # =========================================================================

    def hold_vendor(
        self,
        hold_id: UUID,
        vendor_id: UUID,
        reason: str,
        hold_date: date,
        actor_id: UUID,
    ) -> VendorHold:
        """
        Place a payment hold on a vendor.

        No posting -- pure domain operation. Returns VendorHold record.
        Downstream payment methods should check for active holds.

        Preconditions:
            - ``reason`` is non-empty.
        Postconditions:
            - Returns ``VendorHold`` with ``status == ACTIVE`` (no DB write).
        """
        logger.info("ap_vendor_hold_placed", extra={
            "vendor_id": str(vendor_id),
            "reason": reason,
            "hold_date": hold_date.isoformat(),
        })

        hold = VendorHold(
            id=hold_id,
            vendor_id=vendor_id,
            reason=reason,
            hold_date=hold_date,
            held_by=actor_id,
            status=HoldStatus.ACTIVE,
        )

        orm_hold = APVendorHoldModel(
            id=hold_id,
            vendor_id=vendor_id,
            reason=reason,
            hold_date=hold_date,
            held_by=actor_id,
            status="active",
            created_by_id=actor_id,
        )
        self._session.add(orm_hold)

        return hold

    def release_vendor_hold(
        self,
        hold: VendorHold,
        release_date: date,
        actor_id: UUID,
    ) -> VendorHold:
        """
        Release a vendor payment hold.

        No posting -- returns updated VendorHold with RELEASED status.
        Since VendorHold is frozen, creates a new instance via ``dataclasses.replace``.

        Preconditions:
            - ``hold.status`` is ``ACTIVE``.
        Postconditions:
            - Returns new ``VendorHold`` with ``status == RELEASED`` (no DB write).
        """
        logger.info("ap_vendor_hold_released", extra={
            "hold_id": str(hold.id),
            "vendor_id": str(hold.vendor_id),
            "release_date": release_date.isoformat(),
        })

        existing = self._session.get(APVendorHoldModel, hold.id)
        if existing is not None:
            existing.status = "released"
            existing.released_date = release_date
            existing.released_by = actor_id

        from dataclasses import replace
        return replace(
            hold,
            status=HoldStatus.RELEASED,
            released_date=release_date,
            released_by=actor_id,
        )
