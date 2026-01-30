"""
Payment Term and Early Payment Discount Tests.

Tests payment allocation across invoice terms and early payment discount calculations.

CRITICAL: Payment terms affect cash flow and vendor relationships.

Domain specification tests using self-contained business logic models.
Tests validate FIFO/proportional allocation, early payment discounts (2/10 Net 30),
schedule validation, due date calculation, and rounding correctness.
"""

import pytest
from decimal import Decimal
from datetime import date, timedelta
from uuid import uuid4
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


# =============================================================================
# Domain Models for Payment Terms
# =============================================================================

class DiscountType(Enum):
    """Type of early payment discount."""
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


@dataclass(frozen=True)
class PaymentTerm:
    """A single payment term in a schedule."""
    term_id: str
    description: str
    due_days: int  # Days from invoice date
    percentage: Decimal  # Percentage of total due on this term
    discount_percentage: Decimal = Decimal("0")  # Early payment discount
    discount_days: int = 0  # Days within which discount applies

    def __post_init__(self):
        if self.percentage < 0 or self.percentage > 100:
            raise ValueError("Percentage must be between 0 and 100")
        if self.due_days < 0:
            raise ValueError("Due days cannot be negative")
        if self.discount_percentage < 0 or self.discount_percentage > 100:
            raise ValueError("Discount percentage must be between 0 and 100")
        if self.discount_days < 0:
            raise ValueError("Discount days cannot be negative")
        if self.discount_days > self.due_days:
            raise ValueError("Discount days cannot exceed due days")


@dataclass
class PaymentSchedule:
    """Collection of payment terms for an invoice."""
    schedule_id: str
    terms: List[PaymentTerm]

    def __post_init__(self):
        total_percentage = sum(t.percentage for t in self.terms)
        if total_percentage != Decimal("100"):
            raise ValueError(f"Term percentages must sum to 100, got {total_percentage}")

    @property
    def term_count(self) -> int:
        return len(self.terms)


@dataclass
class Invoice:
    """Invoice with payment terms."""
    invoice_id: str
    party_id: str
    invoice_date: date
    gross_amount: Decimal
    tax_amount: Decimal
    schedule: PaymentSchedule
    currency: str = "USD"

    @property
    def total_amount(self) -> Decimal:
        return self.gross_amount + self.tax_amount


@dataclass
class InvoiceTerm:
    """Calculated term amount for an invoice."""
    term_id: str
    due_date: date
    amount_due: Decimal
    amount_paid: Decimal = Decimal("0")
    discount_date: Optional[date] = None
    discount_amount: Decimal = Decimal("0")

    @property
    def outstanding(self) -> Decimal:
        return self.amount_due - self.amount_paid

    @property
    def is_paid(self) -> bool:
        return self.outstanding <= Decimal("0")


@dataclass
class PaymentAllocation:
    """Allocation of payment to invoice term."""
    term_id: str
    allocated_amount: Decimal
    discount_taken: Decimal = Decimal("0")

    @property
    def net_payment(self) -> Decimal:
        """Payment amount after discount."""
        return self.allocated_amount - self.discount_taken


# =============================================================================
# Payment Term Calculator
# =============================================================================

class PaymentTermCalculator:
    """Calculate payment term amounts and due dates."""

    def calculate_terms(self, invoice: Invoice) -> List[InvoiceTerm]:
        """Calculate term amounts and due dates for invoice."""
        terms = []
        for term in invoice.schedule.terms:
            # Calculate due date
            due_date = invoice.invoice_date + timedelta(days=term.due_days)

            # Calculate amount due for this term
            amount_due = (invoice.total_amount * term.percentage / Decimal("100")).quantize(
                Decimal("0.01")
            )

            # Calculate discount date and amount
            discount_date = None
            discount_amount = Decimal("0")
            if term.discount_percentage > 0 and term.discount_days > 0:
                discount_date = invoice.invoice_date + timedelta(days=term.discount_days)
                discount_amount = (amount_due * term.discount_percentage / Decimal("100")).quantize(
                    Decimal("0.01")
                )

            terms.append(InvoiceTerm(
                term_id=term.term_id,
                due_date=due_date,
                amount_due=amount_due,
                discount_date=discount_date,
                discount_amount=discount_amount,
            ))

        # Handle rounding - adjust last term to match total
        total_calculated = sum(t.amount_due for t in terms)
        if total_calculated != invoice.total_amount:
            diff = invoice.total_amount - total_calculated
            terms[-1] = InvoiceTerm(
                term_id=terms[-1].term_id,
                due_date=terms[-1].due_date,
                amount_due=terms[-1].amount_due + diff,
                discount_date=terms[-1].discount_date,
                discount_amount=terms[-1].discount_amount,
            )

        return terms


# =============================================================================
# Payment Allocator
# =============================================================================

class PaymentAllocator:
    """Allocate payments to invoice terms."""

    class AllocationStrategy(Enum):
        """Strategy for allocating payments."""
        FIFO = "fifo"  # Oldest term first
        PROPORTIONAL = "proportional"  # Pro-rata across all terms
        SPECIFIC = "specific"  # User-specified allocation

    def allocate_fifo(
        self,
        payment_amount: Decimal,
        terms: List[InvoiceTerm],
        payment_date: date,
    ) -> List[PaymentAllocation]:
        """
        Allocate payment to oldest term first (FIFO).

        Also applies early payment discount if within discount window.
        """
        allocations = []
        remaining = payment_amount

        # Sort by due date (oldest first)
        sorted_terms = sorted(terms, key=lambda t: t.due_date)

        for term in sorted_terms:
            if remaining <= Decimal("0"):
                break

            if term.is_paid:
                continue

            # Check if discount applies
            discount_available = Decimal("0")
            if term.discount_date and payment_date <= term.discount_date:
                discount_available = term.discount_amount

            # Calculate amount to allocate
            amount_needed = term.outstanding - discount_available
            if amount_needed <= Decimal("0"):
                # Discount covers the outstanding amount
                continue

            allocated = min(remaining, amount_needed)
            discount_taken = Decimal("0")

            # If paying full term amount, take the discount
            if allocated >= amount_needed and discount_available > 0:
                discount_taken = discount_available
                allocated = term.outstanding - discount_taken

            allocations.append(PaymentAllocation(
                term_id=term.term_id,
                allocated_amount=allocated,
                discount_taken=discount_taken,
            ))

            remaining -= allocated

        return allocations

    def allocate_proportional(
        self,
        payment_amount: Decimal,
        terms: List[InvoiceTerm],
    ) -> List[PaymentAllocation]:
        """Allocate payment proportionally across all outstanding terms."""
        allocations = []

        # Calculate total outstanding
        total_outstanding = sum(t.outstanding for t in terms)
        if total_outstanding <= Decimal("0"):
            return allocations

        for term in terms:
            if term.is_paid:
                continue

            # Calculate pro-rata share
            ratio = term.outstanding / total_outstanding
            allocated = (payment_amount * ratio).quantize(Decimal("0.01"))

            allocations.append(PaymentAllocation(
                term_id=term.term_id,
                allocated_amount=min(allocated, term.outstanding),
            ))

        return allocations

    def validate_allocation(
        self,
        allocations: List[PaymentAllocation],
        terms: List[InvoiceTerm],
    ) -> None:
        """Validate that allocation doesn't exceed outstanding."""
        term_map = {t.term_id: t for t in terms}

        for alloc in allocations:
            term = term_map.get(alloc.term_id)
            if not term:
                raise ValueError(f"Unknown term: {alloc.term_id}")

            if alloc.allocated_amount > term.outstanding:
                raise ValueError(
                    f"Allocation {alloc.allocated_amount} exceeds "
                    f"outstanding {term.outstanding} for term {term.term_id}"
                )


# =============================================================================
# Early Payment Discount Calculator
# =============================================================================

class DiscountCalculator:
    """Calculate early payment discounts."""

    def calculate_discount(
        self,
        term: InvoiceTerm,
        payment_date: date,
    ) -> Decimal:
        """Calculate available discount for payment date."""
        if not term.discount_date:
            return Decimal("0")

        if payment_date > term.discount_date:
            return Decimal("0")  # Discount expired

        return term.discount_amount

    def is_discount_available(
        self,
        term: InvoiceTerm,
        payment_date: date,
    ) -> bool:
        """Check if discount is available for payment date."""
        if not term.discount_date:
            return False
        return payment_date <= term.discount_date

    def calculate_net_payment(
        self,
        amount_due: Decimal,
        discount_percentage: Decimal,
    ) -> Decimal:
        """Calculate net payment amount after discount."""
        discount = (amount_due * discount_percentage / Decimal("100")).quantize(
            Decimal("0.01")
        )
        return amount_due - discount


# =============================================================================
# Test: Payment Term Allocation
# =============================================================================

class TestPaymentTermAllocation:
    """Allocate payment across invoice terms."""

    @pytest.fixture
    def calculator(self):
        return PaymentTermCalculator()

    @pytest.fixture
    def allocator(self):
        return PaymentAllocator()

    @pytest.fixture
    def net30_schedule(self):
        """Simple Net 30 payment terms."""
        return PaymentSchedule(
            schedule_id="NET30",
            terms=[
                PaymentTerm(
                    term_id="TERM-1",
                    description="Net 30",
                    due_days=30,
                    percentage=Decimal("100"),
                ),
            ],
        )

    @pytest.fixture
    def multi_term_schedule(self):
        """Multiple payment terms (30/30/40 split)."""
        return PaymentSchedule(
            schedule_id="MULTI-TERM",
            terms=[
                PaymentTerm(
                    term_id="TERM-1",
                    description="30% in 30 days",
                    due_days=30,
                    percentage=Decimal("30"),
                ),
                PaymentTerm(
                    term_id="TERM-2",
                    description="30% in 60 days",
                    due_days=60,
                    percentage=Decimal("30"),
                ),
                PaymentTerm(
                    term_id="TERM-3",
                    description="40% in 90 days",
                    due_days=90,
                    percentage=Decimal("40"),
                ),
            ],
        )

    def test_allocate_to_oldest_term_first(self, calculator, allocator, multi_term_schedule):
        """FIFO allocation to due dates."""
        invoice = Invoice(
            invoice_id="INV-001",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=35),  # 35 days ago
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=multi_term_schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # First term is now overdue, second due in 25 days, third in 55 days
        # Payment of $500 should go to first term fully, then second term partially
        allocations = allocator.allocate_fifo(
            payment_amount=Decimal("500.00"),
            terms=terms,
            payment_date=date.today(),
        )

        # First allocation to oldest (30% = $300)
        assert allocations[0].term_id == "TERM-1"
        assert allocations[0].allocated_amount == Decimal("300.00")

        # Remaining $200 to second term
        assert allocations[1].term_id == "TERM-2"
        assert allocations[1].allocated_amount == Decimal("200.00")

    def test_partial_payment_allocation(self, calculator, allocator, net30_schedule):
        """Partial against first term."""
        invoice = Invoice(
            invoice_id="INV-002",
            party_id="PARTY-001",
            invoice_date=date.today(),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("100.00"),
            schedule=net30_schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # Partial payment of $500 against $1100 total
        allocations = allocator.allocate_fifo(
            payment_amount=Decimal("500.00"),
            terms=terms,
            payment_date=date.today(),
        )

        assert len(allocations) == 1
        assert allocations[0].allocated_amount == Decimal("500.00")

        # $600 still outstanding
        terms[0] = InvoiceTerm(
            term_id=terms[0].term_id,
            due_date=terms[0].due_date,
            amount_due=terms[0].amount_due,
            amount_paid=Decimal("500.00"),
        )
        assert terms[0].outstanding == Decimal("600.00")

    def test_overallocation_rejected(self, calculator, allocator, net30_schedule):
        """Cannot allocate more than outstanding."""
        invoice = Invoice(
            invoice_id="INV-003",
            party_id="PARTY-001",
            invoice_date=date.today(),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=net30_schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # Try to allocate more than total
        allocations = [
            PaymentAllocation(
                term_id="TERM-1",
                allocated_amount=Decimal("1500.00"),  # More than $1000!
            )
        ]

        with pytest.raises(ValueError, match="exceeds outstanding"):
            allocator.validate_allocation(allocations, terms)

    def test_proportional_allocation(self, calculator, allocator, multi_term_schedule):
        """Pro-rata allocation across all terms."""
        invoice = Invoice(
            invoice_id="INV-004",
            party_id="PARTY-001",
            invoice_date=date.today(),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=multi_term_schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # $500 proportional across all terms
        allocations = allocator.allocate_proportional(
            payment_amount=Decimal("500.00"),
            terms=terms,
        )

        # Should allocate 50% to each term proportionally
        # Term 1: $300 * 50% = $150
        # Term 2: $300 * 50% = $150
        # Term 3: $400 * 50% = $200
        assert len(allocations) == 3
        total_allocated = sum(a.allocated_amount for a in allocations)
        assert total_allocated == Decimal("500.00")


# =============================================================================
# Test: Early Payment Discount
# =============================================================================

class TestEarlyPaymentDiscount:
    """Early payment discount calculations."""

    @pytest.fixture
    def calculator(self):
        return PaymentTermCalculator()

    @pytest.fixture
    def allocator(self):
        return PaymentAllocator()

    @pytest.fixture
    def discount_calc(self):
        return DiscountCalculator()

    @pytest.fixture
    def two_ten_net_thirty(self):
        """2/10 Net 30 - 2% discount if paid within 10 days."""
        return PaymentSchedule(
            schedule_id="2/10-NET30",
            terms=[
                PaymentTerm(
                    term_id="TERM-1",
                    description="2/10 Net 30",
                    due_days=30,
                    percentage=Decimal("100"),
                    discount_percentage=Decimal("2"),
                    discount_days=10,
                ),
            ],
        )

    def test_discount_within_window(self, calculator, discount_calc, two_ten_net_thirty):
        """2/10 Net 30 - pay in 10 days gets 2% discount."""
        invoice = Invoice(
            invoice_id="INV-D-001",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=5),  # 5 days ago
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=two_ten_net_thirty,
        )

        terms = calculator.calculate_terms(invoice)

        # Payment within discount window
        discount = discount_calc.calculate_discount(terms[0], date.today())

        assert discount == Decimal("20.00")  # 2% of $1000
        assert discount_calc.is_discount_available(terms[0], date.today())

    def test_discount_expired(self, calculator, discount_calc, two_ten_net_thirty):
        """No discount after window expires."""
        invoice = Invoice(
            invoice_id="INV-D-002",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=15),  # 15 days ago
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=two_ten_net_thirty,
        )

        terms = calculator.calculate_terms(invoice)

        # Payment after discount window (day 15 > day 10)
        discount = discount_calc.calculate_discount(terms[0], date.today())

        assert discount == Decimal("0")
        assert not discount_calc.is_discount_available(terms[0], date.today())

    def test_discount_on_last_day(self, calculator, discount_calc, two_ten_net_thirty):
        """Discount available on last day of window."""
        invoice = Invoice(
            invoice_id="INV-D-003",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=10),  # Exactly 10 days
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=two_ten_net_thirty,
        )

        terms = calculator.calculate_terms(invoice)

        # Payment on exact discount date
        assert discount_calc.is_discount_available(terms[0], date.today())

    def test_discount_reduces_payment_amount(self, discount_calc):
        """Discount reduces net payment amount."""
        net_payment = discount_calc.calculate_net_payment(
            amount_due=Decimal("1000.00"),
            discount_percentage=Decimal("2"),
        )

        assert net_payment == Decimal("980.00")

    def test_discount_taken_in_allocation(self, calculator, allocator, two_ten_net_thirty):
        """Discount is taken when paying full term within window."""
        invoice = Invoice(
            invoice_id="INV-D-004",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=5),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=two_ten_net_thirty,
        )

        terms = calculator.calculate_terms(invoice)

        # Pay $980 to satisfy $1000 with 2% discount
        allocations = allocator.allocate_fifo(
            payment_amount=Decimal("980.00"),
            terms=terms,
            payment_date=date.today(),
        )

        assert len(allocations) == 1
        assert allocations[0].discount_taken == Decimal("20.00")
        assert allocations[0].net_payment == Decimal("960.00")

    def test_partial_payment_no_discount(self, calculator, allocator, two_ten_net_thirty):
        """Partial payment typically doesn't get discount."""
        invoice = Invoice(
            invoice_id="INV-D-005",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=5),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=two_ten_net_thirty,
        )

        terms = calculator.calculate_terms(invoice)

        # Partial payment of $500 - no discount
        allocations = allocator.allocate_fifo(
            payment_amount=Decimal("500.00"),
            terms=terms,
            payment_date=date.today(),
        )

        assert len(allocations) == 1
        assert allocations[0].allocated_amount == Decimal("500.00")
        # Discount only applies when paying full amount
        # For partial, typically no discount (business policy varies)


class TestMultiTermDiscount:
    """Multi-term discount scenarios."""

    @pytest.fixture
    def calculator(self):
        return PaymentTermCalculator()

    @pytest.fixture
    def allocator(self):
        return PaymentAllocator()

    @pytest.fixture
    def multi_discount_schedule(self):
        """Multiple terms with different discount windows."""
        return PaymentSchedule(
            schedule_id="MULTI-DISCOUNT",
            terms=[
                PaymentTerm(
                    term_id="TERM-1",
                    description="50% in 30 days, 2% if in 10",
                    due_days=30,
                    percentage=Decimal("50"),
                    discount_percentage=Decimal("2"),
                    discount_days=10,
                ),
                PaymentTerm(
                    term_id="TERM-2",
                    description="50% in 60 days, 1% if in 45",
                    due_days=60,
                    percentage=Decimal("50"),
                    discount_percentage=Decimal("1"),
                    discount_days=45,
                ),
            ],
        )

    def test_cascading_term_discounts(self, calculator, allocator, multi_discount_schedule):
        """Different discount rates on each term."""
        invoice = Invoice(
            invoice_id="INV-MD-001",
            party_id="PARTY-001",
            invoice_date=date.today() - timedelta(days=5),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=multi_discount_schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # Term 1: $500, 2% discount = $10
        # Term 2: $500, 1% discount = $5
        assert terms[0].discount_amount == Decimal("10.00")
        assert terms[1].discount_amount == Decimal("5.00")

    def test_different_discount_windows(self, calculator, multi_discount_schedule):
        """Each term has its own discount window."""
        invoice = Invoice(
            invoice_id="INV-MD-002",
            party_id="PARTY-001",
            invoice_date=date.today(),
            gross_amount=Decimal("2000.00"),
            tax_amount=Decimal("0"),
            schedule=multi_discount_schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # Term 1 discount window: 10 days
        # Term 2 discount window: 45 days
        assert terms[0].discount_date == date.today() + timedelta(days=10)
        assert terms[1].discount_date == date.today() + timedelta(days=45)


class TestDiscountTaxImpact:
    """Discount impact on tax calculations."""

    @pytest.fixture
    def calculator(self):
        return PaymentTermCalculator()

    def test_discount_reduces_taxable_amount(self):
        """Discount should reduce the taxable base (in some jurisdictions)."""
        # Invoice: $1000 gross + $100 tax = $1100
        # 2% discount = $20 off gross
        # Some jurisdictions: tax recalculated on $980
        # New tax: $98, total: $1078

        gross = Decimal("1000.00")
        tax_rate = Decimal("10")
        discount_rate = Decimal("2")

        # Original amounts
        original_tax = (gross * tax_rate / Decimal("100")).quantize(Decimal("0.01"))
        assert original_tax == Decimal("100.00")

        # Discounted amounts
        discounted_gross = gross - (gross * discount_rate / Decimal("100"))
        discounted_tax = (discounted_gross * tax_rate / Decimal("100")).quantize(Decimal("0.01"))

        assert discounted_gross == Decimal("980.00")
        assert discounted_tax == Decimal("98.00")

        # Net payment
        net_payment = discounted_gross + discounted_tax
        assert net_payment == Decimal("1078.00")

    def test_discount_on_gross_only(self):
        """Discount on gross amount only, tax unchanged."""
        # Alternative: discount only on gross, original tax remains
        # Invoice: $1000 + $100 tax = $1100
        # 2% discount on gross = $20
        # Payment: $980 + $100 = $1080

        gross = Decimal("1000.00")
        tax = Decimal("100.00")
        discount_rate = Decimal("2")

        discounted_gross = gross - (gross * discount_rate / Decimal("100"))
        net_payment = discounted_gross + tax

        assert net_payment == Decimal("1080.00")


# =============================================================================
# Test: Payment Term Schedule Validation
# =============================================================================

class TestPaymentScheduleValidation:
    """Validate payment term schedules."""

    def test_terms_must_sum_to_100(self):
        """Payment term percentages must sum to 100."""
        with pytest.raises(ValueError, match="sum to 100"):
            PaymentSchedule(
                schedule_id="INVALID",
                terms=[
                    PaymentTerm(
                        term_id="T1",
                        description="50%",
                        due_days=30,
                        percentage=Decimal("50"),
                    ),
                    PaymentTerm(
                        term_id="T2",
                        description="40%",
                        due_days=60,
                        percentage=Decimal("40"),
                    ),
                    # Missing 10%!
                ],
            )

    def test_valid_schedule_accepted(self):
        """Valid schedule with 100% total accepted."""
        schedule = PaymentSchedule(
            schedule_id="VALID",
            terms=[
                PaymentTerm(
                    term_id="T1",
                    description="50%",
                    due_days=30,
                    percentage=Decimal("50"),
                ),
                PaymentTerm(
                    term_id="T2",
                    description="50%",
                    due_days=60,
                    percentage=Decimal("50"),
                ),
            ],
        )
        assert schedule.term_count == 2

    def test_single_term_100_percent(self):
        """Single term at 100% is valid."""
        schedule = PaymentSchedule(
            schedule_id="SINGLE",
            terms=[
                PaymentTerm(
                    term_id="T1",
                    description="100%",
                    due_days=30,
                    percentage=Decimal("100"),
                ),
            ],
        )
        assert schedule.term_count == 1

    def test_percentage_bounds(self):
        """Percentage must be 0-100."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            PaymentTerm(
                term_id="INVALID",
                description="Invalid",
                due_days=30,
                percentage=Decimal("150"),
            )

        with pytest.raises(ValueError, match="between 0 and 100"):
            PaymentTerm(
                term_id="INVALID",
                description="Invalid",
                due_days=30,
                percentage=Decimal("-10"),
            )

    def test_due_days_non_negative(self):
        """Due days cannot be negative."""
        with pytest.raises(ValueError, match="negative"):
            PaymentTerm(
                term_id="INVALID",
                description="Invalid",
                due_days=-5,
                percentage=Decimal("100"),
            )

    def test_discount_days_cannot_exceed_due_days(self):
        """Discount window cannot exceed payment due date."""
        with pytest.raises(ValueError, match="exceed due days"):
            PaymentTerm(
                term_id="INVALID",
                description="Invalid",
                due_days=10,
                percentage=Decimal("100"),
                discount_percentage=Decimal("2"),
                discount_days=15,  # > 10!
            )


# =============================================================================
# Test: Due Date Calculation
# =============================================================================

class TestDueDateCalculation:
    """Calculate due dates from invoice date."""

    @pytest.fixture
    def calculator(self):
        return PaymentTermCalculator()

    def test_due_date_from_invoice_date(self, calculator):
        """Due date calculated from invoice date."""
        schedule = PaymentSchedule(
            schedule_id="NET30",
            terms=[
                PaymentTerm(
                    term_id="T1",
                    description="Net 30",
                    due_days=30,
                    percentage=Decimal("100"),
                ),
            ],
        )

        invoice = Invoice(
            invoice_id="INV-001",
            party_id="PARTY-001",
            invoice_date=date(2024, 1, 15),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=schedule,
        )

        terms = calculator.calculate_terms(invoice)

        assert terms[0].due_date == date(2024, 2, 14)

    def test_multiple_due_dates(self, calculator):
        """Multiple terms have different due dates."""
        schedule = PaymentSchedule(
            schedule_id="MULTI",
            terms=[
                PaymentTerm(term_id="T1", description="30 days", due_days=30, percentage=Decimal("50")),
                PaymentTerm(term_id="T2", description="60 days", due_days=60, percentage=Decimal("50")),
            ],
        )

        invoice = Invoice(
            invoice_id="INV-002",
            party_id="PARTY-001",
            invoice_date=date(2024, 1, 1),
            gross_amount=Decimal("1000.00"),
            tax_amount=Decimal("0"),
            schedule=schedule,
        )

        terms = calculator.calculate_terms(invoice)

        assert terms[0].due_date == date(2024, 1, 31)
        assert terms[1].due_date == date(2024, 3, 1)

    def test_immediate_due(self, calculator):
        """Due on receipt (0 days)."""
        schedule = PaymentSchedule(
            schedule_id="DUE-ON-RECEIPT",
            terms=[
                PaymentTerm(
                    term_id="T1",
                    description="Due on Receipt",
                    due_days=0,
                    percentage=Decimal("100"),
                ),
            ],
        )

        invoice = Invoice(
            invoice_id="INV-003",
            party_id="PARTY-001",
            invoice_date=date(2024, 1, 15),
            gross_amount=Decimal("500.00"),
            tax_amount=Decimal("0"),
            schedule=schedule,
        )

        terms = calculator.calculate_terms(invoice)

        assert terms[0].due_date == date(2024, 1, 15)  # Same as invoice date


# =============================================================================
# Test: Rounding in Term Calculations
# =============================================================================

class TestTermRounding:
    """Handle rounding in term amount calculations."""

    @pytest.fixture
    def calculator(self):
        return PaymentTermCalculator()

    def test_rounding_adjustment_on_last_term(self, calculator):
        """Last term adjusted to match invoice total exactly."""
        schedule = PaymentSchedule(
            schedule_id="THIRDS",
            terms=[
                PaymentTerm(term_id="T1", description="1/3", due_days=30, percentage=Decimal("33.33")),
                PaymentTerm(term_id="T2", description="1/3", due_days=60, percentage=Decimal("33.33")),
                PaymentTerm(term_id="T3", description="1/3", due_days=90, percentage=Decimal("33.34")),
            ],
        )

        invoice = Invoice(
            invoice_id="INV-ROUND",
            party_id="PARTY-001",
            invoice_date=date.today(),
            gross_amount=Decimal("100.00"),
            tax_amount=Decimal("0"),
            schedule=schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # Sum should equal invoice total exactly
        total = sum(t.amount_due for t in terms)
        assert total == invoice.total_amount

    def test_penny_rounding(self, calculator):
        """Handle penny differences in splitting."""
        schedule = PaymentSchedule(
            schedule_id="SPLIT",
            terms=[
                PaymentTerm(term_id="T1", description="60%", due_days=30, percentage=Decimal("60")),
                PaymentTerm(term_id="T2", description="40%", due_days=60, percentage=Decimal("40")),
            ],
        )

        # $33.33 - doesn't split evenly
        invoice = Invoice(
            invoice_id="INV-PENNY",
            party_id="PARTY-001",
            invoice_date=date.today(),
            gross_amount=Decimal("33.33"),
            tax_amount=Decimal("0"),
            schedule=schedule,
        )

        terms = calculator.calculate_terms(invoice)

        # T1: 60% of $33.33 = $19.998 -> $20.00
        # T2: 40% of $33.33 = $13.332 -> $13.33
        # Total: $33.33 (adjusted)
        total = sum(t.amount_due for t in terms)
        assert total == Decimal("33.33")


# =============================================================================
# Summary
# =============================================================================

class TestPaymentTermsSummary:
    """Summary of payment term test coverage."""

    def test_document_coverage(self):
        """
        Payment Terms Test Coverage:

        Term Allocation:
        - FIFO allocation to oldest term first
        - Partial payment allocation
        - Over-allocation rejection
        - Proportional allocation across terms

        Early Payment Discounts:
        - Discount within window (2/10 Net 30)
        - Discount expired after window
        - Discount on last day of window
        - Discount reduces payment amount
        - Multi-term cascading discounts
        - Tax impact of discounts

        Schedule Validation:
        - Terms must sum to 100%
        - Percentage bounds (0-100)
        - Due days non-negative
        - Discount days <= due days

        Due Date Calculation:
        - Due date from invoice date
        - Multiple due dates
        - Immediate due (0 days)

        Rounding:
        - Last term adjustment for rounding
        - Penny rounding handling

        Total: ~30 tests covering payment term patterns.
        """
        pass
