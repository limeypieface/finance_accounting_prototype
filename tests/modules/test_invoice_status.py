"""
Invoice Status Tests.

Tests invoice status changes based on payment activity.

CRITICAL: Invoice status must accurately reflect payment state.

Domain specification tests using self-contained business logic models.
Tests validate correct status transitions (paid, partially paid, overdue, cancelled),
credit note handling, validation rules, and status audit tracking.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from uuid import uuid4

import pytest

# =============================================================================
# Domain Models for Invoice Status
# =============================================================================

class InvoiceStatus(Enum):
    """Invoice payment status."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class PaymentStatus(Enum):
    """Payment transaction status."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


@dataclass
class Invoice:
    """Invoice with payment tracking."""
    invoice_id: str
    party_id: str
    invoice_date: date
    due_date: date
    total_amount: Decimal
    status: InvoiceStatus = InvoiceStatus.DRAFT
    outstanding_amount: Decimal | None = None
    paid_amount: Decimal = Decimal("0")

    def __post_init__(self):
        if self.outstanding_amount is None:
            self.outstanding_amount = self.total_amount
        if self.total_amount < 0:
            raise ValueError("Total amount cannot be negative")

    @property
    def is_fully_paid(self) -> bool:
        return self.outstanding_amount <= Decimal("0")

    @property
    def is_partially_paid(self) -> bool:
        return self.paid_amount > Decimal("0") and not self.is_fully_paid

    @property
    def is_overdue(self) -> bool:
        return (
            not self.is_fully_paid and
            self.due_date < date.today() and
            self.status not in (InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED)
        )


@dataclass
class Payment:
    """Payment against invoice."""
    payment_id: str
    invoice_id: str
    amount: Decimal
    payment_date: date
    status: PaymentStatus = PaymentStatus.DRAFT


@dataclass
class CreditNote:
    """Credit note applied to invoice."""
    credit_note_id: str
    original_invoice_id: str
    amount: Decimal
    credit_date: date
    status: PaymentStatus = PaymentStatus.DRAFT


# =============================================================================
# Invoice Status Manager
# =============================================================================

class InvoiceStatusManager:
    """Manage invoice status transitions."""

    def apply_payment(self, invoice: Invoice, payment: Payment) -> Invoice:
        """
        Apply payment to invoice and update status.

        Returns new invoice with updated amounts and status.
        """
        if payment.status != PaymentStatus.SUBMITTED:
            raise ValueError("Can only apply submitted payments")

        if payment.invoice_id != invoice.invoice_id:
            raise ValueError("Payment does not match invoice")

        if payment.amount > invoice.outstanding_amount:
            raise ValueError(
                f"Payment amount {payment.amount} exceeds outstanding {invoice.outstanding_amount}"
            )

        new_outstanding = invoice.outstanding_amount - payment.amount
        new_paid = invoice.paid_amount + payment.amount

        # Determine new status
        if new_outstanding <= Decimal("0"):
            new_status = InvoiceStatus.PAID
        elif new_paid > Decimal("0"):
            new_status = InvoiceStatus.PARTIALLY_PAID
        else:
            new_status = invoice.status

        return Invoice(
            invoice_id=invoice.invoice_id,
            party_id=invoice.party_id,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            total_amount=invoice.total_amount,
            status=new_status,
            outstanding_amount=new_outstanding,
            paid_amount=new_paid,
        )

    def cancel_payment(self, invoice: Invoice, payment: Payment) -> Invoice:
        """
        Cancel payment and revert invoice status.

        Returns new invoice with reverted amounts and status.
        """
        if payment.invoice_id != invoice.invoice_id:
            raise ValueError("Payment does not match invoice")

        new_outstanding = invoice.outstanding_amount + payment.amount
        new_paid = invoice.paid_amount - payment.amount

        # Cannot go negative
        if new_paid < Decimal("0"):
            raise ValueError("Cancellation would result in negative paid amount")

        # Determine new status
        if new_paid <= Decimal("0"):
            new_status = InvoiceStatus.UNPAID
        else:
            new_status = InvoiceStatus.PARTIALLY_PAID

        return Invoice(
            invoice_id=invoice.invoice_id,
            party_id=invoice.party_id,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            total_amount=invoice.total_amount,
            status=new_status,
            outstanding_amount=new_outstanding,
            paid_amount=new_paid,
        )

    def apply_credit_note(self, invoice: Invoice, credit_note: CreditNote) -> Invoice:
        """
        Apply credit note to reduce outstanding.

        Returns new invoice with reduced outstanding.
        """
        if credit_note.status != PaymentStatus.SUBMITTED:
            raise ValueError("Can only apply submitted credit notes")

        if credit_note.original_invoice_id != invoice.invoice_id:
            raise ValueError("Credit note does not match invoice")

        if credit_note.amount > invoice.outstanding_amount:
            raise ValueError(
                f"Credit note {credit_note.amount} exceeds outstanding {invoice.outstanding_amount}"
            )

        new_outstanding = invoice.outstanding_amount - credit_note.amount
        # Credit notes affect outstanding but not "paid" amount technically
        # They reduce what's owed, not record payment received

        # Determine new status
        if new_outstanding <= Decimal("0"):
            new_status = InvoiceStatus.PAID  # Fully settled
        elif new_outstanding < invoice.total_amount:
            new_status = InvoiceStatus.PARTIALLY_PAID
        else:
            new_status = invoice.status

        return Invoice(
            invoice_id=invoice.invoice_id,
            party_id=invoice.party_id,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            total_amount=invoice.total_amount,
            status=new_status,
            outstanding_amount=new_outstanding,
            paid_amount=invoice.paid_amount,  # Unchanged by credit note
        )

    def check_overdue(self, invoice: Invoice, as_of_date: date) -> Invoice:
        """
        Check if invoice is overdue and update status if needed.
        """
        if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED, InvoiceStatus.DRAFT):
            return invoice

        if invoice.due_date < as_of_date and invoice.outstanding_amount > Decimal("0"):
            return Invoice(
                invoice_id=invoice.invoice_id,
                party_id=invoice.party_id,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                total_amount=invoice.total_amount,
                status=InvoiceStatus.OVERDUE,
                outstanding_amount=invoice.outstanding_amount,
                paid_amount=invoice.paid_amount,
            )

        return invoice


# =============================================================================
# Test: Invoice Status on Payment
# =============================================================================

class TestInvoiceStatusOnPayment:
    """Invoice status changes with payment."""

    @pytest.fixture
    def manager(self):
        return InvoiceStatusManager()

    @pytest.fixture
    def unpaid_invoice(self):
        """Unpaid invoice for testing."""
        return Invoice(
            invoice_id="INV-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=15),
            due_date=date.today() + timedelta(days=15),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

    def test_full_payment_marks_paid(self, manager, unpaid_invoice):
        """Status -> Paid on full payment."""
        payment = Payment(
            payment_id="PMT-001",
            invoice_id="INV-001",
            amount=Decimal("1000.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        updated = manager.apply_payment(unpaid_invoice, payment)

        assert updated.status == InvoiceStatus.PAID
        assert updated.outstanding_amount == Decimal("0")
        assert updated.paid_amount == Decimal("1000.00")
        assert updated.is_fully_paid

    def test_partial_payment_marks_partially_paid(self, manager, unpaid_invoice):
        """Status -> Partially Paid on partial payment."""
        payment = Payment(
            payment_id="PMT-002",
            invoice_id="INV-001",
            amount=Decimal("400.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        updated = manager.apply_payment(unpaid_invoice, payment)

        assert updated.status == InvoiceStatus.PARTIALLY_PAID
        assert updated.outstanding_amount == Decimal("600.00")
        assert updated.paid_amount == Decimal("400.00")
        assert updated.is_partially_paid

    def test_multiple_partial_payments(self, manager, unpaid_invoice):
        """Multiple partials eventually mark as paid."""
        # First partial
        payment1 = Payment(
            payment_id="PMT-003",
            invoice_id="INV-001",
            amount=Decimal("300.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )
        invoice_after_1 = manager.apply_payment(unpaid_invoice, payment1)

        assert invoice_after_1.status == InvoiceStatus.PARTIALLY_PAID
        assert invoice_after_1.outstanding_amount == Decimal("700.00")

        # Second partial
        payment2 = Payment(
            payment_id="PMT-004",
            invoice_id="INV-001",
            amount=Decimal("500.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )
        invoice_after_2 = manager.apply_payment(invoice_after_1, payment2)

        assert invoice_after_2.status == InvoiceStatus.PARTIALLY_PAID
        assert invoice_after_2.outstanding_amount == Decimal("200.00")

        # Final payment
        payment3 = Payment(
            payment_id="PMT-005",
            invoice_id="INV-001",
            amount=Decimal("200.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )
        invoice_after_3 = manager.apply_payment(invoice_after_2, payment3)

        assert invoice_after_3.status == InvoiceStatus.PAID
        assert invoice_after_3.outstanding_amount == Decimal("0")
        assert invoice_after_3.is_fully_paid


class TestPaymentCancellationStatus:
    """Invoice status changes when payment is cancelled."""

    @pytest.fixture
    def manager(self):
        return InvoiceStatusManager()

    def test_payment_cancellation_reverts_status(self, manager):
        """Status -> Unpaid on payment cancel."""
        # Invoice that was paid
        paid_invoice = Invoice(
            invoice_id="INV-002",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=30),
            due_date=date.today() - timedelta(days=15),
            total_amount=Decimal("500.00"),
            status=InvoiceStatus.PAID,
            outstanding_amount=Decimal("0"),
            paid_amount=Decimal("500.00"),
        )

        # Cancel the payment
        cancelled_payment = Payment(
            payment_id="PMT-006",
            invoice_id="INV-002",
            amount=Decimal("500.00"),
            payment_date=date.today(),
            status=PaymentStatus.CANCELLED,
        )

        reverted = manager.cancel_payment(paid_invoice, cancelled_payment)

        assert reverted.status == InvoiceStatus.UNPAID
        assert reverted.outstanding_amount == Decimal("500.00")
        assert reverted.paid_amount == Decimal("0")

    def test_partial_cancellation_reverts_to_partial(self, manager):
        """Cancel one of multiple payments reverts to partially paid."""
        # Invoice with two payments
        invoice = Invoice(
            invoice_id="INV-003",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=30),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.PAID,
            outstanding_amount=Decimal("0"),
            paid_amount=Decimal("1000.00"),
        )

        # Cancel one payment of $400 (of total $1000)
        cancelled_payment = Payment(
            payment_id="PMT-007",
            invoice_id="INV-003",
            amount=Decimal("400.00"),
            payment_date=date.today(),
            status=PaymentStatus.CANCELLED,
        )

        reverted = manager.cancel_payment(invoice, cancelled_payment)

        assert reverted.status == InvoiceStatus.PARTIALLY_PAID
        assert reverted.outstanding_amount == Decimal("400.00")
        assert reverted.paid_amount == Decimal("600.00")


class TestCreditNoteReducesOutstanding:
    """Credit notes reduce outstanding amount."""

    @pytest.fixture
    def manager(self):
        return InvoiceStatusManager()

    def test_credit_note_reduces_outstanding(self, manager):
        """Outstanding reduced by credit note."""
        invoice = Invoice(
            invoice_id="INV-004",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=30),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

        credit_note = CreditNote(
            credit_note_id="CN-001",
            original_invoice_id="INV-004",
            amount=Decimal("200.00"),
            credit_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        updated = manager.apply_credit_note(invoice, credit_note)

        assert updated.outstanding_amount == Decimal("800.00")
        assert updated.status == InvoiceStatus.PARTIALLY_PAID

    def test_credit_note_settles_invoice(self, manager):
        """Credit note can fully settle invoice."""
        invoice = Invoice(
            invoice_id="INV-005",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=30),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("500.00"),
            status=InvoiceStatus.UNPAID,
        )

        credit_note = CreditNote(
            credit_note_id="CN-002",
            original_invoice_id="INV-005",
            amount=Decimal("500.00"),
            credit_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        updated = manager.apply_credit_note(invoice, credit_note)

        assert updated.outstanding_amount == Decimal("0")
        assert updated.status == InvoiceStatus.PAID

    def test_credit_plus_payment_settles(self, manager):
        """Combination of credit and payment settles invoice."""
        invoice = Invoice(
            invoice_id="INV-006",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=30),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

        # Apply credit note first
        credit_note = CreditNote(
            credit_note_id="CN-003",
            original_invoice_id="INV-006",
            amount=Decimal("300.00"),
            credit_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )
        invoice_after_credit = manager.apply_credit_note(invoice, credit_note)

        assert invoice_after_credit.outstanding_amount == Decimal("700.00")

        # Then payment for remainder
        payment = Payment(
            payment_id="PMT-008",
            invoice_id="INV-006",
            amount=Decimal("700.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )
        final = manager.apply_payment(invoice_after_credit, payment)

        assert final.status == InvoiceStatus.PAID
        assert final.outstanding_amount == Decimal("0")


# =============================================================================
# Test: Overdue Status
# =============================================================================

class TestOverdueStatus:
    """Invoice overdue status detection."""

    @pytest.fixture
    def manager(self):
        return InvoiceStatusManager()

    def test_unpaid_past_due_is_overdue(self, manager):
        """Unpaid invoice past due date is overdue."""
        invoice = Invoice(
            invoice_id="INV-OVERDUE-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=45),
            due_date=date.today() - timedelta(days=15),  # 15 days overdue
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

        checked = manager.check_overdue(invoice, date.today())

        assert checked.status == InvoiceStatus.OVERDUE

    def test_partially_paid_past_due_is_overdue(self, manager):
        """Partially paid invoice past due is overdue."""
        invoice = Invoice(
            invoice_id="INV-OVERDUE-002",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=45),
            due_date=date.today() - timedelta(days=15),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.PARTIALLY_PAID,
            outstanding_amount=Decimal("500.00"),
            paid_amount=Decimal("500.00"),
        )

        checked = manager.check_overdue(invoice, date.today())

        assert checked.status == InvoiceStatus.OVERDUE

    def test_paid_invoice_not_overdue(self, manager):
        """Paid invoice is never overdue regardless of dates."""
        invoice = Invoice(
            invoice_id="INV-PAID-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=60),
            due_date=date.today() - timedelta(days=30),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.PAID,
            outstanding_amount=Decimal("0"),
            paid_amount=Decimal("1000.00"),
        )

        checked = manager.check_overdue(invoice, date.today())

        assert checked.status == InvoiceStatus.PAID  # Unchanged

    def test_unpaid_not_yet_due(self, manager):
        """Unpaid invoice not yet due is not overdue."""
        invoice = Invoice(
            invoice_id="INV-NOTDUE-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=15),
            due_date=date.today() + timedelta(days=15),  # Still 15 days to pay
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

        checked = manager.check_overdue(invoice, date.today())

        assert checked.status == InvoiceStatus.UNPAID  # Unchanged

    def test_due_today_not_overdue(self, manager):
        """Invoice due today is not overdue."""
        invoice = Invoice(
            invoice_id="INV-DUETODAY-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=30),
            due_date=date.today(),  # Due today
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

        checked = manager.check_overdue(invoice, date.today())

        # Due date is not less than today, so not overdue
        assert checked.status == InvoiceStatus.UNPAID


# =============================================================================
# Test: Validation
# =============================================================================

class TestInvoiceStatusValidation:
    """Validation rules for status transitions."""

    @pytest.fixture
    def manager(self):
        return InvoiceStatusManager()

    def test_cannot_overpay(self, manager):
        """Cannot apply payment exceeding outstanding."""
        invoice = Invoice(
            invoice_id="INV-VAL-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("500.00"),
            status=InvoiceStatus.UNPAID,
        )

        overpayment = Payment(
            payment_id="PMT-OVER-001",
            invoice_id="INV-VAL-001",
            amount=Decimal("600.00"),  # $100 more than invoice
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        with pytest.raises(ValueError, match="exceeds outstanding"):
            manager.apply_payment(invoice, overpayment)

    def test_cannot_over_credit(self, manager):
        """Cannot apply credit note exceeding outstanding."""
        invoice = Invoice(
            invoice_id="INV-VAL-002",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("500.00"),
            status=InvoiceStatus.UNPAID,
        )

        over_credit = CreditNote(
            credit_note_id="CN-OVER-001",
            original_invoice_id="INV-VAL-002",
            amount=Decimal("600.00"),
            credit_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        with pytest.raises(ValueError, match="exceeds outstanding"):
            manager.apply_credit_note(invoice, over_credit)

    def test_cannot_apply_draft_payment(self, manager):
        """Cannot apply draft payment."""
        invoice = Invoice(
            invoice_id="INV-VAL-003",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("500.00"),
            status=InvoiceStatus.UNPAID,
        )

        draft_payment = Payment(
            payment_id="PMT-DRAFT-001",
            invoice_id="INV-VAL-003",
            amount=Decimal("500.00"),
            payment_date=date.today(),
            status=PaymentStatus.DRAFT,  # Not submitted!
        )

        with pytest.raises(ValueError, match="submitted"):
            manager.apply_payment(invoice, draft_payment)

    def test_payment_invoice_mismatch(self, manager):
        """Payment must match invoice ID."""
        invoice = Invoice(
            invoice_id="INV-VAL-004",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("500.00"),
            status=InvoiceStatus.UNPAID,
        )

        mismatched_payment = Payment(
            payment_id="PMT-MISMATCH-001",
            invoice_id="INV-DIFFERENT-999",  # Wrong invoice!
            amount=Decimal("500.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        with pytest.raises(ValueError, match="does not match"):
            manager.apply_payment(invoice, mismatched_payment)

    def test_negative_invoice_rejected(self):
        """Invoice cannot have negative total."""
        with pytest.raises(ValueError, match="negative"):
            Invoice(
                invoice_id="INV-NEG-001",
                party_id="CUSTOMER-001",
                invoice_date=date.today(),
                due_date=date.today() + timedelta(days=30),
                total_amount=Decimal("-100.00"),
            )


# =============================================================================
# Test: Outstanding Amount Tracking
# =============================================================================

class TestOutstandingAmountTracking:
    """Track outstanding amount changes."""

    @pytest.fixture
    def manager(self):
        return InvoiceStatusManager()

    def test_initial_outstanding_equals_total(self):
        """New invoice outstanding equals total."""
        invoice = Invoice(
            invoice_id="INV-INIT-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("1500.00"),
            status=InvoiceStatus.UNPAID,
        )

        assert invoice.outstanding_amount == Decimal("1500.00")
        assert invoice.paid_amount == Decimal("0")

    def test_outstanding_reduces_with_payment(self, manager):
        """Outstanding reduced by payment amount."""
        invoice = Invoice(
            invoice_id="INV-OUT-001",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("1000.00"),
            status=InvoiceStatus.UNPAID,
        )

        payment = Payment(
            payment_id="PMT-OUT-001",
            invoice_id="INV-OUT-001",
            amount=Decimal("350.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        updated = manager.apply_payment(invoice, payment)

        assert updated.outstanding_amount == Decimal("650.00")
        assert updated.paid_amount == Decimal("350.00")

    def test_outstanding_zero_when_fully_paid(self, manager):
        """Outstanding is zero when fully paid."""
        invoice = Invoice(
            invoice_id="INV-OUT-002",
            party_id="CUSTOMER-001",
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            total_amount=Decimal("750.00"),
            status=InvoiceStatus.UNPAID,
        )

        payment = Payment(
            payment_id="PMT-OUT-002",
            invoice_id="INV-OUT-002",
            amount=Decimal("750.00"),
            payment_date=date.today(),
            status=PaymentStatus.SUBMITTED,
        )

        updated = manager.apply_payment(invoice, payment)

        assert updated.outstanding_amount == Decimal("0")
        assert updated.paid_amount == Decimal("750.00")
        assert updated.is_fully_paid


# =============================================================================
# Test: Status Query
# =============================================================================

class TestInvoiceStatusQuery:
    """Query invoices by status."""

    @pytest.fixture
    def invoice_repo(self):
        return MockInvoiceRepository()

    def test_query_unpaid_invoices(self, invoice_repo):
        """Get all unpaid invoices."""
        unpaid = invoice_repo.find_by_status(InvoiceStatus.UNPAID)

        assert len(unpaid) >= 1
        assert all(inv.status == InvoiceStatus.UNPAID for inv in unpaid)

    def test_query_overdue_invoices(self, invoice_repo):
        """Get all overdue invoices."""
        overdue = invoice_repo.find_by_status(InvoiceStatus.OVERDUE)

        assert all(inv.status == InvoiceStatus.OVERDUE for inv in overdue)

    def test_query_by_party(self, invoice_repo):
        """Get invoices for specific party."""
        party_invoices = invoice_repo.find_by_party("CUSTOMER-001")

        assert all(inv.party_id == "CUSTOMER-001" for inv in party_invoices)


class MockInvoiceRepository:
    """Mock invoice repository for testing queries."""

    def __init__(self):
        self.invoices = [
            Invoice(
                invoice_id="INV-REPO-001",
                party_id="CUSTOMER-001",
                invoice_date=date.today() - timedelta(days=45),
                due_date=date.today() - timedelta(days=15),
                total_amount=Decimal("1000.00"),
                status=InvoiceStatus.OVERDUE,
                outstanding_amount=Decimal("1000.00"),
            ),
            Invoice(
                invoice_id="INV-REPO-002",
                party_id="CUSTOMER-001",
                invoice_date=date.today() - timedelta(days=10),
                due_date=date.today() + timedelta(days=20),
                total_amount=Decimal("500.00"),
                status=InvoiceStatus.UNPAID,
            ),
            Invoice(
                invoice_id="INV-REPO-003",
                party_id="CUSTOMER-002",
                invoice_date=date.today() - timedelta(days=30),
                due_date=date.today(),
                total_amount=Decimal("750.00"),
                status=InvoiceStatus.PAID,
                outstanding_amount=Decimal("0"),
                paid_amount=Decimal("750.00"),
            ),
        ]

    def find_by_status(self, status: InvoiceStatus) -> list[Invoice]:
        return [inv for inv in self.invoices if inv.status == status]

    def find_by_party(self, party_id: str) -> list[Invoice]:
        return [inv for inv in self.invoices if inv.party_id == party_id]


# =============================================================================
# Test: Status History
# =============================================================================

class TestStatusHistory:
    """Track status change history."""

    def test_status_transitions_tracked(self):
        """Status changes should be tracked for audit."""
        tracker = StatusTracker()

        # Simulate status changes
        tracker.record_change("INV-001", InvoiceStatus.DRAFT, InvoiceStatus.SUBMITTED, date.today())
        tracker.record_change("INV-001", InvoiceStatus.SUBMITTED, InvoiceStatus.UNPAID, date.today())
        tracker.record_change("INV-001", InvoiceStatus.UNPAID, InvoiceStatus.PARTIALLY_PAID, date.today())
        tracker.record_change("INV-001", InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.PAID, date.today())

        history = tracker.get_history("INV-001")

        assert len(history) == 4
        # history tuple is (date, from_status, to_status)
        assert history[0][1] == InvoiceStatus.DRAFT  # First transition from DRAFT
        assert history[0][2] == InvoiceStatus.SUBMITTED  # First transition to SUBMITTED
        assert history[-1][2] == InvoiceStatus.PAID  # Final status is PAID

    def test_current_status_from_history(self):
        """Get current status from last history entry."""
        tracker = StatusTracker()

        tracker.record_change("INV-002", InvoiceStatus.DRAFT, InvoiceStatus.UNPAID, date.today())
        tracker.record_change("INV-002", InvoiceStatus.UNPAID, InvoiceStatus.PAID, date.today())

        current = tracker.get_current_status("INV-002")

        assert current == InvoiceStatus.PAID


@dataclass
class StatusChange:
    """Record of status change."""
    from_status: InvoiceStatus
    to_status: InvoiceStatus
    change_date: date


class StatusTracker:
    """Track status change history."""

    def __init__(self):
        self.history: dict[str, list[StatusChange]] = {}

    def record_change(
        self,
        invoice_id: str,
        from_status: InvoiceStatus,
        to_status: InvoiceStatus,
        change_date: date,
    ):
        if invoice_id not in self.history:
            self.history[invoice_id] = []

        self.history[invoice_id].append(StatusChange(
            from_status=from_status,
            to_status=to_status,
            change_date=change_date,
        ))

    def get_history(self, invoice_id: str) -> list[tuple]:
        if invoice_id not in self.history:
            return []
        return [
            (c.change_date, c.from_status, c.to_status)
            for c in self.history[invoice_id]
        ]

    def get_current_status(self, invoice_id: str) -> InvoiceStatus | None:
        if invoice_id not in self.history or not self.history[invoice_id]:
            return None
        return self.history[invoice_id][-1].to_status


# =============================================================================
# Summary
# =============================================================================

class TestInvoiceStatusSummary:
    """Summary of invoice status test coverage."""

    def test_document_coverage(self):
        """
        Invoice Status Test Coverage:

        Status on Payment:
        - Full payment marks paid
        - Partial payment marks partially paid
        - Multiple partial payments tracking
        - Payment cancellation reverts status
        - Partial cancellation handling

        Credit Notes:
        - Credit note reduces outstanding
        - Credit note can settle invoice
        - Credit + payment combination

        Overdue Detection:
        - Unpaid past due is overdue
        - Partially paid past due is overdue
        - Paid invoice never overdue
        - Due today not overdue

        Validation:
        - Cannot overpay
        - Cannot over-credit
        - Cannot apply draft payment
        - Invoice-payment matching
        - Negative invoice rejected

        Outstanding Tracking:
        - Initial outstanding equals total
        - Outstanding reduces with payment
        - Outstanding zero when paid

        Status Query:
        - Query by status
        - Query by party

        Status History:
        - Status transitions tracked
        - Current status from history

        Total: ~30 tests covering invoice status patterns.
        """
        pass
