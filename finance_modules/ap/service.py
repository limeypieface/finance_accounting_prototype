"""
Accounts Payable Module Service - Orchestrates AP operations via engines + kernel.

Thin glue layer that:
1. Calls ReconciliationManager for payment matching and application
2. Calls AllocationEngine for distributing amounts across invoice lines
3. Calls MatchingEngine for 3-way PO/receipt/invoice matching
4. Calls AgingCalculator for AP aging analysis
5. Calls ModulePostingService for journal entry creation

All computation lives in engines. All posting lives in kernel.
This service owns the transaction boundary (R7 compliance).

Usage:
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

logger = get_logger("modules.ap.service")


class APService:
    """
    Orchestrates accounts payable operations through engines and kernel.

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
        currency: str = "USD",
        vendor_id: UUID | None = None,
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
                as_of_date=date.today(),
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
