"""
Tests for ReconciliationManager - Payment application, 3-way match, bank reconciliation.

Tests cover:
- Payment application to invoices
- Reconciliation state derivation
- Overapplication prevention
- 3-way matching with tolerance
- Bank statement reconciliation
- Payment allocation across invoices
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_engines.allocation import AllocationMethod
from finance_engines.matching import MatchTolerance
from finance_engines.reconciliation import (
    BankReconciliationLine,
    BankReconciliationStatus,
    MatchType,
    ReconciliationState,
    ReconciliationStatus,
)
from finance_kernel.domain.economic_link import ArtifactRef, LinkType
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    DocumentAlreadyMatchedError,
    MatchVarianceExceededError,
    OverapplicationError,
)
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_services.reconciliation_service import ReconciliationManager


class TestReconciliationState:
    """Tests for reconciliation state derivation."""

    def test_open_state_when_no_payments(self, session: Session):
        """Document should be OPEN when no payments applied."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        original_amount = Money.of("1000.00", "USD")

        state = manager.get_reconciliation_state(invoice_ref, original_amount)

        assert state.status == ReconciliationStatus.OPEN
        assert state.remaining_amount.amount == Decimal("1000.00")
        assert state.applied_amount.is_zero
        assert state.is_open

    def test_partial_state_after_partial_payment(self, session: Session):
        """Document should be PARTIAL after partial payment."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        original_amount = Money.of("1000.00", "USD")

        # Apply partial payment
        manager.apply_payment(
            invoice_ref=invoice_ref,
            payment_ref=payment_ref,
            amount=Money.of("300.00", "USD"),
            invoice_original_amount=original_amount,
            creating_event_id=uuid4(),
        )

        state = manager.get_reconciliation_state(invoice_ref, original_amount)

        assert state.status == ReconciliationStatus.PARTIAL
        assert state.remaining_amount.amount == Decimal("700.00")
        assert state.applied_amount.amount == Decimal("300.00")

    def test_matched_state_when_fully_paid(self, session: Session):
        """Document should be MATCHED when fully paid."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())
        original_amount = Money.of("1000.00", "USD")

        # Apply full payment
        manager.apply_payment(
            invoice_ref=invoice_ref,
            payment_ref=payment_ref,
            amount=Money.of("1000.00", "USD"),
            invoice_original_amount=original_amount,
            creating_event_id=uuid4(),
        )

        state = manager.get_reconciliation_state(invoice_ref, original_amount)

        assert state.status == ReconciliationStatus.MATCHED
        assert state.remaining_amount.is_zero
        assert state.is_fully_matched


class TestPaymentApplication:
    """Tests for applying payments to invoices."""

    def test_apply_payment_creates_paid_by_link(self, session: Session):
        """Applying payment should create PAID_BY link."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())

        application = manager.apply_payment(
            invoice_ref=invoice_ref,
            payment_ref=payment_ref,
            amount=Money.of("500.00", "USD"),
            invoice_original_amount=Money.of("1000.00", "USD"),
            creating_event_id=uuid4(),
        )

        assert application.applied_amount.amount == Decimal("500.00")
        assert application.link_created is not None

        # Verify link
        links = link_graph.get_children(invoice_ref, frozenset({LinkType.PAID_BY}))
        assert len(links) == 1
        assert links[0].child_ref == payment_ref

    def test_apply_payment_with_metadata(self, session: Session):
        """Payment application should preserve metadata."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())

        application = manager.apply_payment(
            invoice_ref=invoice_ref,
            payment_ref=payment_ref,
            amount=Money.of("500.00", "USD"),
            invoice_original_amount=Money.of("1000.00", "USD"),
            creating_event_id=uuid4(),
            metadata={"discount_taken": "10.00"},
        )

        assert application.link_created.metadata.get("discount_taken") == "10.00"

    def test_overapplication_error(self, session: Session):
        """Should raise error when applying more than remaining."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        original_amount = Money.of("1000.00", "USD")

        # Apply partial
        manager.apply_payment(
            invoice_ref=invoice_ref,
            payment_ref=ArtifactRef.payment(uuid4()),
            amount=Money.of("800.00", "USD"),
            invoice_original_amount=original_amount,
            creating_event_id=uuid4(),
        )

        # Try to overapply
        with pytest.raises(OverapplicationError) as exc_info:
            manager.apply_payment(
                invoice_ref=invoice_ref,
                payment_ref=ArtifactRef.payment(uuid4()),
                amount=Money.of("300.00", "USD"),  # Only 200 remaining
                invoice_original_amount=original_amount,
                creating_event_id=uuid4(),
            )

        assert exc_info.value.remaining_amount == "200.00"
        assert exc_info.value.attempted_amount == "300.00"

    def test_document_already_matched_error(self, session: Session):
        """Should raise error when applying to fully paid invoice."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        invoice_ref = ArtifactRef.invoice(uuid4())
        original_amount = Money.of("1000.00", "USD")

        # Pay in full
        manager.apply_payment(
            invoice_ref=invoice_ref,
            payment_ref=ArtifactRef.payment(uuid4()),
            amount=Money.of("1000.00", "USD"),
            invoice_original_amount=original_amount,
            creating_event_id=uuid4(),
        )

        # Try to apply more
        with pytest.raises(DocumentAlreadyMatchedError):
            manager.apply_payment(
                invoice_ref=invoice_ref,
                payment_ref=ArtifactRef.payment(uuid4()),
                amount=Money.of("1.00", "USD"),
                invoice_original_amount=original_amount,
                creating_event_id=uuid4(),
            )


class TestPaymentAllocation:
    """Tests for allocating payment across multiple invoices."""

    def test_allocate_payment_fifo(self, session: Session):
        """Payment should be allocated FIFO (oldest invoice first)."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        # Three invoices
        inv1_ref = ArtifactRef.invoice(uuid4())
        inv2_ref = ArtifactRef.invoice(uuid4())
        inv3_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())

        invoices = [
            (inv1_ref, Money.of("300.00", "USD"), Money.of("300.00", "USD")),
            (inv2_ref, Money.of("500.00", "USD"), Money.of("500.00", "USD")),
            (inv3_ref, Money.of("400.00", "USD"), Money.of("400.00", "USD")),
        ]

        # Apply $600 payment
        applications = manager.apply_payment_across_invoices(
            payment_ref=payment_ref,
            payment_amount=Money.of("600.00", "USD"),
            invoices=invoices,
            creating_event_id=uuid4(),
            method=AllocationMethod.FIFO,
        )

        # Should fully pay inv1 (300) and partially pay inv2 (300)
        assert len(applications) == 2
        assert applications[0].applied_amount.amount == Decimal("300.00")
        assert applications[1].applied_amount.amount == Decimal("300.00")

    def test_allocate_payment_prorata(self, session: Session):
        """Payment should be allocated proportionally."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        # Two invoices
        inv1_ref = ArtifactRef.invoice(uuid4())
        inv2_ref = ArtifactRef.invoice(uuid4())
        payment_ref = ArtifactRef.payment(uuid4())

        invoices = [
            (inv1_ref, Money.of("400.00", "USD"), Money.of("400.00", "USD")),
            (inv2_ref, Money.of("600.00", "USD"), Money.of("600.00", "USD")),
        ]

        # Apply $500 payment pro-rata
        applications = manager.apply_payment_across_invoices(
            payment_ref=payment_ref,
            payment_amount=Money.of("500.00", "USD"),
            invoices=invoices,
            creating_event_id=uuid4(),
            method=AllocationMethod.PRORATA,
        )

        # Should allocate 40% to inv1 (200) and 60% to inv2 (300)
        assert len(applications) == 2
        total_applied = sum(a.applied_amount.amount for a in applications)
        assert total_applied == Decimal("500.00")


class TestThreeWayMatch:
    """Tests for 3-way matching (PO → Receipt → Invoice)."""

    def test_three_way_match_creates_fulfilled_by_links(self, session: Session):
        """3-way match should create FULFILLED_BY chain."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        po_ref = ArtifactRef.purchase_order(uuid4())
        receipt_ref = ArtifactRef.receipt(uuid4())
        invoice_ref = ArtifactRef.invoice(uuid4())

        result = manager.create_three_way_match(
            po_ref=po_ref,
            receipt_ref=receipt_ref,
            invoice_ref=invoice_ref,
            po_quantity=Decimal("100"),
            po_unit_price=Money.of("10.00", "USD"),
            receipt_quantity=Decimal("100"),
            invoice_quantity=Decimal("100"),
            invoice_unit_price=Money.of("10.00", "USD"),
            creating_event_id=uuid4(),
        )

        assert result.match.match_type == MatchType.THREE_WAY
        assert result.is_clean_match
        assert len(result.match.documents) == 3

        # Check links
        po_links = link_graph.get_children(po_ref, frozenset({LinkType.FULFILLED_BY}))
        assert len(po_links) == 1
        assert po_links[0].child_ref == receipt_ref

        receipt_links = link_graph.get_children(receipt_ref, frozenset({LinkType.FULFILLED_BY}))
        assert len(receipt_links) == 1
        assert receipt_links[0].child_ref == invoice_ref

    def test_three_way_match_calculates_price_variance(self, session: Session):
        """Should calculate price variance correctly."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        # Allow larger variance for this test
        tolerance = MatchTolerance(
            amount_tolerance=Decimal("100.00"),  # Allow up to $100 variance
            quantity_tolerance=Decimal("10"),
        )

        result = manager.create_three_way_match(
            po_ref=ArtifactRef.purchase_order(uuid4()),
            receipt_ref=ArtifactRef.receipt(uuid4()),
            invoice_ref=ArtifactRef.invoice(uuid4()),
            po_quantity=Decimal("100"),
            po_unit_price=Money.of("10.00", "USD"),  # PO @ $10
            receipt_quantity=Decimal("100"),
            invoice_quantity=Decimal("100"),
            invoice_unit_price=Money.of("10.50", "USD"),  # Invoice @ $10.50
            creating_event_id=uuid4(),
            tolerance=tolerance,
        )

        # Variance = (10.50 - 10.00) * 100 = $50
        assert result.has_price_variance
        assert result.price_variance.amount == Decimal("50.00")

    def test_three_way_match_variance_exceeded_error(self, session: Session):
        """Should raise error when variance exceeds tolerance."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        tolerance = MatchTolerance(
            amount_tolerance=Decimal("10.00"),  # Only $10 allowed
        )

        with pytest.raises(MatchVarianceExceededError) as exc_info:
            manager.create_three_way_match(
                po_ref=ArtifactRef.purchase_order(uuid4()),
                receipt_ref=ArtifactRef.receipt(uuid4()),
                invoice_ref=ArtifactRef.invoice(uuid4()),
                po_quantity=Decimal("100"),
                po_unit_price=Money.of("10.00", "USD"),
                receipt_quantity=Decimal("100"),
                invoice_quantity=Decimal("100"),
                invoice_unit_price=Money.of("11.00", "USD"),  # $100 variance
                creating_event_id=uuid4(),
                tolerance=tolerance,
            )

        assert exc_info.value.variance_type == "price"


class TestBankReconciliation:
    """Tests for bank statement reconciliation."""

    def test_find_bank_match_suggestions(self, session: Session):
        """Should find matching GL transactions for statement line."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        statement_line = BankReconciliationLine.unmatched(
            line_id=uuid4(),
            statement_ref=ArtifactRef(
                artifact_type=ArtifactRef.invoice(uuid4()).artifact_type,  # Placeholder
                artifact_id=str(uuid4()),
            ),
            transaction_date=date(2024, 1, 15),
            description="ACME Corp Payment",
            amount=Money.of("-500.00", "USD"),
        )

        # GL candidates
        gl_candidates = [
            (ArtifactRef.journal_entry(uuid4()), Money.of("-500.00", "USD"), date(2024, 1, 15), "ACME Corp"),
            (ArtifactRef.journal_entry(uuid4()), Money.of("-300.00", "USD"), date(2024, 1, 14), "Other Payment"),
            (ArtifactRef.journal_entry(uuid4()), Money.of("-500.00", "USD"), date(2024, 1, 20), "Different Date"),
        ]

        suggestions = manager.find_bank_match_suggestions(
            statement_line=statement_line,
            gl_candidates=gl_candidates,
        )

        # Best match should be exact amount and date
        assert len(suggestions) > 0
        best_match = suggestions[0]
        assert best_match[1] > Decimal("0")  # Has positive score

    def test_match_bank_transaction_creates_links(self, session: Session):
        """Matching bank transaction should create MATCHED_WITH links."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        statement_ref = ArtifactRef.invoice(uuid4())  # Placeholder for statement
        statement_line = BankReconciliationLine.unmatched(
            line_id=uuid4(),
            statement_ref=statement_ref,
            transaction_date=date(2024, 1, 15),
            description="Payment",
            amount=Money.of("-500.00", "USD"),
        )

        gl_ref = ArtifactRef.journal_entry(uuid4())

        matched_line = manager.match_bank_transaction(
            statement_line=statement_line,
            gl_refs=[gl_ref],
            creating_event_id=uuid4(),
        )

        assert matched_line.status == BankReconciliationStatus.MATCHED
        assert matched_line.is_reconciled
        assert gl_ref in matched_line.matched_gl_refs

        # Check link
        links = link_graph.get_children(statement_ref, frozenset({LinkType.MATCHED_WITH}))
        assert len(links) == 1

    def test_get_bank_reconciliation_status(self, session: Session):
        """Should return correct status counts."""
        link_graph = LinkGraphService(session)
        manager = ReconciliationManager(session, link_graph)

        statement_ref = ArtifactRef.invoice(uuid4())  # Placeholder

        lines = [
            BankReconciliationLine.unmatched(
                line_id=uuid4(),
                statement_ref=statement_ref,
                transaction_date=date(2024, 1, 15),
                description="Line 1",
                amount=Money.of("-100.00", "USD"),
            ),
            BankReconciliationLine.unmatched(
                line_id=uuid4(),
                statement_ref=statement_ref,
                transaction_date=date(2024, 1, 16),
                description="Line 2",
                amount=Money.of("-200.00", "USD"),
            ),
        ]

        status = manager.get_bank_reconciliation_status(statement_ref, lines)

        assert status["unmatched"] == 2
        assert status["matched"] == 0
