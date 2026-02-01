"""
finance_services.reconciliation_service -- Invoice/payment matching, 3-way match, bank reconciliation.

Responsibility:
    Manages document reconciliation by deriving reconciliation state from
    EconomicLink relationships, applying payments to invoices with PAID_BY
    links, creating 3-way matches (PO -> Receipt -> Invoice) with
    FULFILLED_BY chains, and reconciling bank statements with MATCHED_WITH
    links.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Composes AllocationEngine and MatchingEngine (pure engines) with
    LinkGraphService (kernel I/O) for link management and state derivation.

Invariants enforced:
    - LINK_LEGALITY: all links use the immutable EconomicLink model with
      appropriate LinkType (PAID_BY, FULFILLED_BY, MATCHED_WITH).
    - Over-application guard: payment amount must not exceed remaining
      balance (OverapplicationError).
    - Duplicate match guard: fully-matched documents reject further
      application (DocumentAlreadyMatchedError).
    - Tolerance enforcement: 3-way match rejects quantity and price
      variances exceeding configured tolerance (MatchVarianceExceededError).

Failure modes:
    - OverapplicationError: applied amount exceeds remaining balance.
    - DocumentAlreadyMatchedError: document is already fully matched.
    - MatchVarianceExceededError: variance exceeds tolerance threshold.
    - BankReconciliationError: statement line already reconciled.

Audit relevance:
    - Every payment application and match creates immutable EconomicLink
      records with metadata capturing amounts, currencies, and dates.
    - Reconciliation state is fully derivable from the link graph (no
      stored balances), ensuring replay safety (R6).

Usage:
    from finance_services.reconciliation_service import ReconciliationManager
    from finance_kernel.services.link_graph_service import LinkGraphService

    link_service = LinkGraphService(session)
    rec_manager = ReconciliationManager(session, link_service)

    # Apply payment to invoice
    application = rec_manager.apply_payment(
        invoice_ref=ArtifactRef.invoice(invoice_id),
        payment_ref=ArtifactRef.payment(payment_id),
        amount=Money.of("500.00", "USD"),
        creating_event_id=event_id,
    )

    # Get reconciliation state
    state = rec_manager.get_reconciliation_state(
        artifact_ref=ArtifactRef.invoice(invoice_id),
        original_amount=Money.of("1000.00", "USD"),
    )
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

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
)
from finance_engines.matching import (
    MatchType as MatchingMatchType,
)
from finance_engines.reconciliation.domain import (
    BankReconciliationLine,
    BankReconciliationStatus,
    DocumentMatch,
    MatchType,
    PaymentApplication,
    ReconciliationState,
    ReconciliationStatus,
    ThreeWayMatchResult,
)
from finance_kernel.domain.economic_link import (
    ArtifactRef,
    EconomicLink,
    LinkType,
)
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    BankReconciliationError,
    DocumentAlreadyMatchedError,
    MatchVarianceExceededError,
    OverapplicationError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.services.link_graph_service import LinkGraphService

logger = get_logger("services.reconciliation")


class ReconciliationManager:
    """
    Manages document reconciliation and payment application.

    Contract:
        Given artifact references and amounts, derive reconciliation
        state, apply payments, create 3-way matches, and reconcile bank
        statement lines -- all backed by immutable EconomicLink records.

    Guarantees:
        - All state is derived from EconomicLink relationships; no stored
          balances (R6 replay safety).
        - Over-application is prevented by balance checks before link
          creation.
        - 3-way match variances are validated against tolerances before
          any links are established.

    Non-goals:
        - Does NOT manage fiscal-period close or GL posting (separate
          services handle those concerns).
        - Does NOT handle partial reversal of a payment application
          (use CorrectionEngine for that).
    """

    def __init__(
        self,
        session: Session,
        link_graph: LinkGraphService,
    ):
        """
        Initialize the reconciliation manager.

        Args:
            session: SQLAlchemy session for database operations.
            link_graph: LinkGraphService for link operations.
        """
        self.session = session
        self.link_graph = link_graph
        self.allocation = AllocationEngine()
        self.matching = MatchingEngine()

    # =========================================================================
    # State Queries
    # =========================================================================

    def get_reconciliation_state(
        self,
        artifact_ref: ArtifactRef,
        original_amount: Money,
    ) -> ReconciliationState:
        """
        Get the current reconciliation state of a document.

        Derives state from EconomicLink relationships using
        LinkGraphService.get_unconsumed_value().

        Args:
            artifact_ref: The document to query (invoice, payment, etc.).
            original_amount: The original amount of the document.

        Returns:
            ReconciliationState showing current matching status.
        """
        logger.info("reconciliation_state_query_started", extra={
            "artifact_ref": str(artifact_ref),
            "original_amount": str(original_amount.amount),
        })

        # Get unconsumed value via link graph
        unconsumed = self.link_graph.get_unconsumed_value(
            parent_ref=artifact_ref,
            original_amount=original_amount,
            link_types=frozenset({
                LinkType.PAID_BY,
                LinkType.ALLOCATED_TO,
                LinkType.APPLIED_TO,
            }),
            amount_metadata_key="amount_applied",
        )

        # Get child links for match references
        children = self.link_graph.get_children(
            artifact_ref,
            frozenset({
                LinkType.PAID_BY,
                LinkType.ALLOCATED_TO,
                LinkType.APPLIED_TO,
            }),
        )
        match_refs = tuple(link.child_ref for link in children)

        # Get last activity date from links
        last_activity: date | None = None
        if children:
            last_created = max(link.created_at for link in children)
            last_activity = last_created.date()

        state = ReconciliationState.from_amounts(
            artifact_ref=artifact_ref,
            original_amount=original_amount,
            applied_amount=unconsumed.consumed_amount,
            match_references=match_refs,
            last_activity_date=last_activity,
        )

        logger.info("reconciliation_state_query_completed", extra={
            "artifact_ref": str(artifact_ref),
            "status": state.status.value,
            "applied_amount": str(state.applied_amount.amount),
            "remaining_amount": str(state.remaining_amount.amount),
            "match_reference_count": len(match_refs),
        })

        return state

    def is_fully_matched(
        self,
        artifact_ref: ArtifactRef,
        original_amount: Money,
    ) -> bool:
        """Check if a document is fully matched."""
        state = self.get_reconciliation_state(artifact_ref, original_amount)
        return state.is_fully_matched

    # =========================================================================
    # Payment Application
    # =========================================================================

    def apply_payment(
        self,
        invoice_ref: ArtifactRef,
        payment_ref: ArtifactRef,
        amount: Money,
        invoice_original_amount: Money,
        creating_event_id: UUID,
        applied_date: date | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> PaymentApplication:
        """
        Apply a payment to an invoice.

        Creates a PAID_BY link: invoice -> payment

        Args:
            invoice_ref: The invoice being paid.
            payment_ref: The payment being applied.
            amount: Amount being applied.
            invoice_original_amount: Original invoice amount (for validation).
            creating_event_id: Event that triggered this application.
            applied_date: Date of application (defaults to today).
            metadata: Additional application attributes.

        Returns:
            PaymentApplication with application details.

        Raises:
            OverapplicationError: Amount exceeds remaining balance.
            DocumentAlreadyMatchedError: Invoice is already fully paid.
        """
        t0 = time.monotonic()
        logger.info("payment_application_started", extra={
            "invoice_ref": str(invoice_ref),
            "payment_ref": str(payment_ref),
            "amount": str(amount.amount),
            "invoice_original_amount": str(invoice_original_amount.amount),
        })

        # Get current state
        state = self.get_reconciliation_state(invoice_ref, invoice_original_amount)

        # INVARIANT: Over-application guard -- applied amount must not
        # exceed remaining balance.
        if state.is_fully_matched:
            logger.warning("payment_application_already_matched", extra={
                "invoice_ref": str(invoice_ref),
            })
            raise DocumentAlreadyMatchedError(str(invoice_ref))

        # INVARIANT: Amount cannot exceed remaining balance.
        assert amount.amount >= 0, "Payment amount must be non-negative"
        if amount.amount > state.remaining_amount.amount:
            logger.warning("payment_application_overapplication", extra={
                "invoice_ref": str(invoice_ref),
                "remaining_amount": str(state.remaining_amount.amount),
                "attempted_amount": str(amount.amount),
            })
            raise OverapplicationError(
                document_ref=str(invoice_ref),
                remaining_amount=str(state.remaining_amount.amount),
                attempted_amount=str(amount.amount),
                currency=amount.currency.code,
            )

        # Create PAID_BY link
        link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.PAID_BY,
            parent_ref=invoice_ref,
            child_ref=payment_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(UTC),
            metadata={
                "amount_applied": str(amount.amount),
                "currency": amount.currency.code,
                **(dict(metadata) if metadata else {}),
            },
        )
        result = self.link_graph.establish_link(link, allow_duplicate=False)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("payment_application_completed", extra={
            "invoice_ref": str(invoice_ref),
            "payment_ref": str(payment_ref),
            "amount": str(amount.amount),
            "duration_ms": duration_ms,
        })

        return PaymentApplication.create(
            application_id=uuid4(),
            source_ref=invoice_ref,
            payment_ref=payment_ref,
            applied_amount=amount,
            applied_date=applied_date or date.today(),
            link=result.link,
            metadata=metadata,
        )

    def apply_payment_across_invoices(
        self,
        payment_ref: ArtifactRef,
        payment_amount: Money,
        invoices: Sequence[tuple[ArtifactRef, Money, Money]],  # ref, original, remaining
        creating_event_id: UUID,
        method: AllocationMethod = AllocationMethod.FIFO,
        applied_date: date | None = None,
    ) -> list[PaymentApplication]:
        """
        Apply a payment across multiple invoices.

        Uses AllocationEngine to determine how much to apply to each.

        Args:
            payment_ref: The payment being applied.
            payment_amount: Total payment amount.
            invoices: Sequence of (invoice_ref, original_amount, remaining_amount).
            creating_event_id: Event that triggered this application.
            method: Allocation method (FIFO, PRORATA, etc.).
            applied_date: Date of application.

        Returns:
            List of PaymentApplication for each invoice that received payment.
        """
        t0 = time.monotonic()
        logger.info("payment_across_invoices_started", extra={
            "payment_ref": str(payment_ref),
            "payment_amount": str(payment_amount.amount),
            "invoice_count": len(invoices),
            "allocation_method": method.value,
        })

        # Build allocation targets
        targets = [
            AllocationTarget(
                target_id=str(inv_ref),
                target_type="invoice",
                eligible_amount=remaining,
                date=applied_date or date.today(),
            )
            for inv_ref, original, remaining in invoices
        ]

        # Allocate payment
        result = self.allocation.allocate(
            amount=payment_amount,
            targets=targets,
            method=method,
        )

        # Create applications for each allocated amount
        applications: list[PaymentApplication] = []
        for line, (inv_ref, original, remaining) in zip(result.lines, invoices, strict=False):
            if line.allocated.is_zero:
                continue

            app = self.apply_payment(
                invoice_ref=inv_ref,
                payment_ref=payment_ref,
                amount=line.allocated,
                invoice_original_amount=original,
                creating_event_id=creating_event_id,
                applied_date=applied_date,
            )
            applications.append(app)

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("payment_across_invoices_completed", extra={
            "payment_ref": str(payment_ref),
            "invoices_paid": len(applications),
            "total_invoices": len(invoices),
            "duration_ms": duration_ms,
        })

        return applications

    # =========================================================================
    # 3-Way Matching
    # =========================================================================

    def create_three_way_match(
        self,
        po_ref: ArtifactRef,
        receipt_ref: ArtifactRef,
        invoice_ref: ArtifactRef,
        po_quantity: Decimal,
        po_unit_price: Money,
        receipt_quantity: Decimal,
        invoice_quantity: Decimal,
        invoice_unit_price: Money,
        creating_event_id: UUID,
        tolerance: MatchTolerance | None = None,
    ) -> ThreeWayMatchResult:
        """
        Create a 3-way match between PO, Receipt, and Invoice.

        Creates FULFILLED_BY chain: PO -> Receipt -> Invoice

        Args:
            po_ref: Purchase order reference.
            receipt_ref: Goods receipt reference.
            invoice_ref: Vendor invoice reference.
            po_quantity: Quantity on PO.
            po_unit_price: Unit price on PO.
            receipt_quantity: Quantity on receipt.
            invoice_quantity: Quantity on invoice.
            invoice_unit_price: Unit price on invoice.
            creating_event_id: Event that triggered this match.
            tolerance: Match tolerance rules.

        Returns:
            ThreeWayMatchResult with match details and variances.

        Raises:
            MatchVarianceExceededError: Variance exceeds tolerance.
        """
        t0 = time.monotonic()
        logger.info("three_way_match_started", extra={
            "po_ref": str(po_ref),
            "receipt_ref": str(receipt_ref),
            "invoice_ref": str(invoice_ref),
            "po_quantity": str(po_quantity),
            "receipt_quantity": str(receipt_quantity),
            "invoice_quantity": str(invoice_quantity),
        })

        tolerance = tolerance or MatchTolerance()

        # Calculate variances
        quantity_variance = receipt_quantity - po_quantity
        price_variance_amount = (
            (invoice_unit_price.amount - po_unit_price.amount) * invoice_quantity
        )
        price_variance = Money.of(
            price_variance_amount,
            po_unit_price.currency.code,
        )

        # INVARIANT: Tolerance enforcement -- quantity and price variances
        # must be within configured thresholds.
        if tolerance.quantity_tolerance_type.value == "absolute":
            if abs(quantity_variance) > tolerance.quantity_tolerance:
                raise MatchVarianceExceededError(
                    match_type="THREE_WAY",
                    variance_type="quantity",
                    variance_amount=str(quantity_variance),
                    tolerance=str(tolerance.quantity_tolerance),
                )
        else:  # percent
            if po_quantity > 0:
                pct_var = abs(quantity_variance) / po_quantity * 100
                if pct_var > tolerance.quantity_tolerance:
                    raise MatchVarianceExceededError(
                        match_type="THREE_WAY",
                        variance_type="quantity",
                        variance_amount=f"{pct_var:.2f}%",
                        tolerance=f"{tolerance.quantity_tolerance}%",
                    )

        if tolerance.amount_tolerance_type.value == "absolute":
            if abs(price_variance.amount) > tolerance.amount_tolerance:
                raise MatchVarianceExceededError(
                    match_type="THREE_WAY",
                    variance_type="price",
                    variance_amount=str(price_variance.amount),
                    tolerance=str(tolerance.amount_tolerance),
                    currency=price_variance.currency.code,
                )
        else:  # percent
            expected_total = po_unit_price.amount * invoice_quantity
            if expected_total > 0:
                pct_var = abs(price_variance.amount) / expected_total * 100
                if pct_var > tolerance.amount_tolerance:
                    raise MatchVarianceExceededError(
                        match_type="THREE_WAY",
                        variance_type="price",
                        variance_amount=f"{pct_var:.2f}%",
                        tolerance=f"{tolerance.amount_tolerance}%",
                    )

        # Create FULFILLED_BY links
        links: list[EconomicLink] = []

        # PO -> Receipt
        po_receipt_link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=po_ref,
            child_ref=receipt_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(UTC),
            metadata={
                "match_type": "three_way",
                "po_quantity": str(po_quantity),
                "receipt_quantity": str(receipt_quantity),
            },
        )
        result1 = self.link_graph.establish_link(po_receipt_link, allow_duplicate=True)
        if not result1.was_duplicate:
            links.append(result1.link)

        # Receipt -> Invoice
        receipt_invoice_link = EconomicLink.create(
            link_id=uuid4(),
            link_type=LinkType.FULFILLED_BY,
            parent_ref=receipt_ref,
            child_ref=invoice_ref,
            creating_event_id=creating_event_id,
            created_at=datetime.now(UTC),
            metadata={
                "match_type": "three_way",
                "receipt_quantity": str(receipt_quantity),
                "invoice_quantity": str(invoice_quantity),
            },
        )
        result2 = self.link_graph.establish_link(receipt_invoice_link, allow_duplicate=True)
        if not result2.was_duplicate:
            links.append(result2.link)

        # Create the domain result
        matched_amount = Money.of(
            invoice_quantity * invoice_unit_price.amount,
            invoice_unit_price.currency.code,
        )

        match = DocumentMatch(
            match_id=uuid4(),
            match_type=MatchType.THREE_WAY,
            documents=(po_ref, receipt_ref, invoice_ref),
            matched_amount=matched_amount,
            match_date=date.today(),
            variance=price_variance if not price_variance.is_zero else None,
            links_created=tuple(links),
        )

        duration_ms = round((time.monotonic() - t0) * 1000, 2)
        logger.info("three_way_match_completed", extra={
            "match_id": str(match.match_id),
            "matched_amount": str(matched_amount.amount),
            "has_price_variance": not price_variance.is_zero,
            "has_quantity_variance": quantity_variance != Decimal("0"),
            "links_created": len(links),
            "duration_ms": duration_ms,
        })

        return ThreeWayMatchResult.create(
            match=match,
            po_ref=po_ref,
            receipt_ref=receipt_ref,
            invoice_ref=invoice_ref,
            po_quantity=po_quantity,
            receipt_quantity=receipt_quantity,
            invoice_quantity=invoice_quantity,
            po_unit_price=po_unit_price,
            invoice_unit_price=invoice_unit_price,
        )

    # =========================================================================
    # Bank Reconciliation
    # =========================================================================

    def find_bank_match_suggestions(
        self,
        statement_line: BankReconciliationLine,
        gl_candidates: Sequence[tuple[ArtifactRef, Money, date, str]],  # ref, amount, date, description
        tolerance: MatchTolerance | None = None,
    ) -> list[tuple[ArtifactRef, Decimal]]:
        """
        Find potential GL matches for a bank statement line.

        Args:
            statement_line: The bank statement line to match.
            gl_candidates: Potential GL transactions (ref, amount, date, desc).
            tolerance: Match tolerance rules.

        Returns:
            List of (gl_ref, confidence_score) sorted by score descending.
        """
        logger.info("bank_match_suggestion_started", extra={
            "statement_line_id": str(statement_line.line_id),
            "statement_amount": str(statement_line.amount.amount),
            "candidate_count": len(gl_candidates),
        })

        tolerance = tolerance or MatchTolerance(
            amount_tolerance=Decimal("0.01"),
            date_tolerance_days=3,
        )

        # Build match candidates
        statement_candidate = MatchCandidate(
            document_type="STATEMENT",
            document_id=str(statement_line.line_id),
            amount=statement_line.amount,
            date=statement_line.transaction_date,
            reference=statement_line.description,
        )

        gl_match_candidates = [
            MatchCandidate(
                document_type="GL",
                document_id=str(ref.artifact_id),
                amount=amount,
                date=txn_date,
                reference=desc,
            )
            for ref, amount, txn_date, desc in gl_candidates
        ]

        # Find matches
        suggestions = self.matching.find_matches(
            target=statement_candidate,
            candidates=gl_match_candidates,
            tolerance=tolerance,
        )

        # Convert to result format
        result: list[tuple[ArtifactRef, Decimal]] = []
        for suggestion in suggestions:
            # Find original ref
            original_ref = next(
                ref for ref, amount, txn_date, desc in gl_candidates
                if str(ref.artifact_id) == suggestion.candidate.document_id
            )
            result.append((original_ref, suggestion.score))

        logger.info("bank_match_suggestion_completed", extra={
            "statement_line_id": str(statement_line.line_id),
            "suggestions_found": len(result),
            "top_score": str(result[0][1]) if result else "0",
        })

        return result

    def match_bank_transaction(
        self,
        statement_line: BankReconciliationLine,
        gl_refs: Sequence[ArtifactRef],
        creating_event_id: UUID,
    ) -> BankReconciliationLine:
        """
        Match a bank statement line to GL transactions.

        Creates MATCHED_WITH links (symmetric).

        Args:
            statement_line: The bank statement line.
            gl_refs: GL transactions to match.
            creating_event_id: Event that triggered this match.

        Returns:
            Updated BankReconciliationLine with match confirmed.

        Raises:
            BankReconciliationError: Match cannot be created.
        """
        logger.info("bank_transaction_match_started", extra={
            "statement_line_id": str(statement_line.line_id),
            "gl_ref_count": len(gl_refs),
            "amount": str(statement_line.amount.amount),
        })

        if statement_line.is_reconciled:
            logger.warning("bank_transaction_already_reconciled", extra={
                "statement_line_id": str(statement_line.line_id),
            })
            raise BankReconciliationError(
                str(statement_line.line_id),
                "Line is already reconciled",
            )

        links: list[EconomicLink] = []

        for gl_ref in gl_refs:
            link = EconomicLink.create(
                link_id=uuid4(),
                link_type=LinkType.MATCHED_WITH,
                parent_ref=statement_line.statement_ref,
                child_ref=gl_ref,
                creating_event_id=creating_event_id,
                created_at=datetime.now(UTC),
                metadata={
                    "statement_line_id": str(statement_line.line_id),
                    "amount": str(statement_line.amount.amount),
                    "currency": statement_line.amount.currency.code,
                    "transaction_date": statement_line.transaction_date.isoformat(),
                },
            )
            result = self.link_graph.establish_link(link, allow_duplicate=True)
            if not result.was_duplicate:
                links.append(result.link)

        logger.info("bank_transaction_match_completed", extra={
            "statement_line_id": str(statement_line.line_id),
            "matched_gl_count": len(gl_refs),
            "links_created": len(links),
        })

        return statement_line.with_confirmed_match(
            matched_refs=tuple(gl_refs),
            links=tuple(links),
        )

    def get_bank_reconciliation_status(
        self,
        statement_ref: ArtifactRef,
        statement_lines: Sequence[BankReconciliationLine],
    ) -> dict[str, int]:
        """
        Get summary status for bank reconciliation.

        Args:
            statement_ref: The bank statement.
            statement_lines: Lines in the statement.

        Returns:
            Dict with counts: unmatched, suggested, matched, excluded
        """
        status_counts = {
            "unmatched": 0,
            "suggested": 0,
            "matched": 0,
            "excluded": 0,
        }

        for line in statement_lines:
            status_counts[line.status.value] += 1

        logger.info("bank_reconciliation_status_summary", extra={
            "statement_ref": str(statement_ref),
            "total_lines": len(statement_lines),
            **status_counts,
        })

        return status_counts
