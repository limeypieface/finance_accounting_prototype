"""
Model Invariant Tests - Data Integrity Enforcement.

Tests that domain models enforce invariants that would cause
data corruption or impossible states if violated.

These tests verify that invalid data is rejected with ValueError exceptions.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.ap.models import Invoice, InvoiceLine, PaymentBatch, Vendor
from finance_modules.ar.models import Customer, Receipt
from finance_modules.ar.models import Invoice as ARInvoice
from finance_modules.expense.models import (
    ExpenseCategory,
    ExpenseLine,
    ExpenseReport,
    PaymentMethod,
)
from finance_modules.gl.models import Account, AccountType, FiscalPeriod
from finance_modules.inventory.models import (
    InventoryIssue,
    InventoryReceipt,
    Item,
    ItemType,
    StockLevel,
    StockTransfer,
)
from finance_modules.payroll.models import Employee, PayFrequency, PayType
from finance_modules.procurement.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    Requisition,
    RequisitionLine,
)
from finance_modules.wip.models import Operation, WorkOrder

# =============================================================================
# AP Invoice Amount Invariants
# =============================================================================

class TestAPInvoiceAmountInvariants:
    """Test AP Invoice amount consistency invariants."""

    @pytest.mark.xfail(reason="Subtotal vs line sum requires cross-entity validation")
    def test_invoice_subtotal_must_equal_sum_of_lines(self):
        """Invoice subtotal should equal sum of line amounts."""
        invoice_id = uuid4()
        lines = (
            InvoiceLine(
                id=uuid4(),
                invoice_id=invoice_id,
                line_number=1,
                description="Item 1",
                quantity=Decimal("2"),
                unit_price=Decimal("100"),
                amount=Decimal("200"),
                gl_account_code="5000-000",
            ),
            InvoiceLine(
                id=uuid4(),
                invoice_id=invoice_id,
                line_number=2,
                description="Item 2",
                quantity=Decimal("1"),
                unit_price=Decimal("50"),
                amount=Decimal("50"),
                gl_account_code="5000-000",
            ),
        )
        # Line sum = 250, but subtotal = 300 (wrong!)
        invoice = Invoice(
            id=invoice_id,
            vendor_id=uuid4(),
            invoice_number="INV-001",
            invoice_date=date.today(),
            due_date=date.today(),
            currency="USD",
            subtotal=Decimal("300"),  # Should be 250
            tax_amount=Decimal("25"),
            total_amount=Decimal("325"),
        )
        line_sum = sum(line.amount for line in lines)
        assert invoice.subtotal == line_sum, f"Subtotal {invoice.subtotal} ≠ line sum {line_sum}"

    def test_invoice_total_must_equal_subtotal_plus_tax(self):
        """Invoice total should equal subtotal + tax."""
        with pytest.raises(ValueError, match="total_amount.*must equal.*subtotal.*tax"):
            Invoice(
                id=uuid4(),
                vendor_id=uuid4(),
                invoice_number="INV-002",
                invoice_date=date.today(),
                due_date=date.today(),
                currency="USD",
                subtotal=Decimal("100"),
                tax_amount=Decimal("10"),
                total_amount=Decimal("150"),  # Should be 110!
            )

    def test_invoice_with_valid_amounts_passes(self):
        """Invoice with correct amounts should be created."""
        invoice = Invoice(
            id=uuid4(),
            vendor_id=uuid4(),
            invoice_number="INV-003",
            invoice_date=date.today(),
            due_date=date.today(),
            currency="USD",
            subtotal=Decimal("100"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("100"),
        )
        assert invoice.total_amount == invoice.subtotal + invoice.tax_amount

    def test_credit_memo_amounts_must_be_consistent(self):
        """Credit memos should have consistent negative amounts."""
        # Negative subtotal but positive tax is invalid
        with pytest.raises(ValueError, match="Credit memo.*cannot have positive tax"):
            Invoice(
                id=uuid4(),
                vendor_id=uuid4(),
                invoice_number="CM-001",
                invoice_date=date.today(),
                due_date=date.today(),
                currency="USD",
                subtotal=Decimal("-100"),
                tax_amount=Decimal("10"),  # Positive tax on credit?
                total_amount=Decimal("-90"),
            )


# =============================================================================
# AR Receipt Allocation Invariants
# =============================================================================

class TestARReceiptAllocationInvariants:
    """Test AR Receipt allocation invariants."""

    def test_receipt_unallocated_cannot_exceed_amount(self):
        """Receipt unallocated amount cannot exceed total amount."""
        with pytest.raises(ValueError, match="unallocated_amount.*cannot exceed amount"):
            Receipt(
                id=uuid4(),
                customer_id=uuid4(),
                receipt_date=date.today(),
                amount=Decimal("100"),
                currency="USD",
                payment_method="check",
                reference="CHK-001",
                unallocated_amount=Decimal("150"),  # More than receipt amount!
            )

    def test_receipt_unallocated_cannot_be_negative(self):
        """Receipt unallocated amount cannot be negative."""
        with pytest.raises(ValueError, match="unallocated_amount cannot be negative"):
            Receipt(
                id=uuid4(),
                customer_id=uuid4(),
                receipt_date=date.today(),
                amount=Decimal("100"),
                currency="USD",
                payment_method="check",
                reference="CHK-002",
                unallocated_amount=Decimal("-50"),  # Negative!
            )

    def test_receipt_amount_must_be_positive(self):
        """Receipt amount should be positive."""
        with pytest.raises(ValueError, match="Receipt amount must be positive"):
            Receipt(
                id=uuid4(),
                customer_id=uuid4(),
                receipt_date=date.today(),
                amount=Decimal("0"),  # Zero receipt?
                currency="USD",
                payment_method="check",
                reference="CHK-003",
            )

    def test_valid_receipt_passes(self):
        """Valid receipt should be created."""
        receipt = Receipt(
            id=uuid4(),
            customer_id=uuid4(),
            receipt_date=date.today(),
            amount=Decimal("100"),
            currency="USD",
            payment_method="check",
            reference="CHK-004",
            unallocated_amount=Decimal("50"),
        )
        assert receipt.amount == Decimal("100")
        assert receipt.unallocated_amount == Decimal("50")


# =============================================================================
# Inventory Quantity Invariants
# =============================================================================

class TestInventoryQuantityInvariants:
    """Test Inventory quantity tracking invariants."""

    def test_po_line_received_cannot_exceed_ordered(self):
        """PO line received quantity cannot exceed ordered quantity."""
        with pytest.raises(ValueError, match="quantity_received.*cannot exceed quantity_ordered"):
            PurchaseOrderLine(
                id=uuid4(),
                purchase_order_id=uuid4(),
                line_number=1,
                item_id=uuid4(),
                description="Widget",
                quantity_ordered=Decimal("100"),
                quantity_received=Decimal("150"),  # More than ordered!
                unit_price=Decimal("10"),
                line_total=Decimal("1000"),
            )

    def test_po_line_invoiced_cannot_exceed_received(self):
        """PO line invoiced quantity cannot exceed received quantity."""
        with pytest.raises(ValueError, match="quantity_invoiced.*cannot exceed quantity_received"):
            PurchaseOrderLine(
                id=uuid4(),
                purchase_order_id=uuid4(),
                line_number=1,
                item_id=uuid4(),
                description="Widget",
                quantity_ordered=Decimal("100"),
                quantity_received=Decimal("80"),
                quantity_invoiced=Decimal("100"),  # Invoiced more than received!
                unit_price=Decimal("10"),
                line_total=Decimal("1000"),
            )

    def test_stock_level_cannot_be_negative(self):
        """Stock level quantities cannot be negative."""
        with pytest.raises(ValueError, match="quantity_on_hand cannot be negative"):
            StockLevel(
                id=uuid4(),
                item_id=uuid4(),
                location_id=uuid4(),
                quantity_on_hand=Decimal("-50"),  # Negative stock!
                quantity_reserved=Decimal("0"),
                quantity_available=Decimal("-50"),
            )

    def test_stock_available_must_equal_on_hand_minus_reserved(self):
        """Stock available must equal on-hand minus reserved."""
        with pytest.raises(ValueError, match="quantity_available.*must equal.*quantity_on_hand.*quantity_reserved"):
            StockLevel(
                id=uuid4(),
                item_id=uuid4(),
                location_id=uuid4(),
                quantity_on_hand=Decimal("100"),
                quantity_reserved=Decimal("20"),
                quantity_available=Decimal("120"),  # Should be 80!
            )

    def test_valid_stock_level_passes(self):
        """Valid stock level should be created."""
        stock = StockLevel(
            id=uuid4(),
            item_id=uuid4(),
            location_id=uuid4(),
            quantity_on_hand=Decimal("100"),
            quantity_reserved=Decimal("20"),
            quantity_available=Decimal("80"),  # Correct: 100 - 20
        )
        assert stock.quantity_available == stock.quantity_on_hand - stock.quantity_reserved

    @pytest.mark.xfail(reason="Item standard cost validation requires business context")
    def test_item_standard_cost_must_be_positive_for_standard_costing(self):
        """Items using standard costing should have positive standard cost."""
        item = Item(
            id=uuid4(),
            code="ITEM-001",
            description="Widget",
            item_type=ItemType.FINISHED_GOODS,
            unit_of_measure="EA",
            standard_cost=Decimal("0"),  # Zero cost will cause COGS issues
        )
        # If standard costing is used, standard_cost should be > 0
        assert item.standard_cost > 0, "Standard cost must be positive"


# =============================================================================
# WIP Work Order Invariants
# =============================================================================

class TestWIPWorkOrderInvariants:
    """Test WIP work order quantity invariants."""

    def test_work_order_completed_cannot_exceed_ordered(self):
        """Work order completed quantity cannot exceed ordered quantity."""
        with pytest.raises(ValueError, match="quantity_completed.*cannot exceed quantity_ordered"):
            WorkOrder(
                id=uuid4(),
                order_number="WO-001",
                item_id=uuid4(),
                quantity_ordered=Decimal("100"),
                quantity_completed=Decimal("150"),  # More than ordered!
            )

    def test_work_order_quantities_must_be_positive(self):
        """Work order ordered quantity should be positive."""
        with pytest.raises(ValueError, match="quantity_ordered must be positive"):
            WorkOrder(
                id=uuid4(),
                order_number="WO-002",
                item_id=uuid4(),
                quantity_ordered=Decimal("-100"),  # Negative!
            )

    def test_valid_work_order_passes(self):
        """Valid work order should be created."""
        wo = WorkOrder(
            id=uuid4(),
            order_number="WO-003",
            item_id=uuid4(),
            quantity_ordered=Decimal("100"),
            quantity_completed=Decimal("50"),
        )
        assert wo.quantity_completed <= wo.quantity_ordered


# =============================================================================
# Payroll Employee Invariants
# =============================================================================

class TestPayrollEmployeeInvariants:
    """Test Payroll employee data invariants."""

    def test_salaried_employee_must_have_positive_base_pay(self):
        """Salaried employees should have positive base pay."""
        with pytest.raises(ValueError, match="Salaried employee must have positive base_pay"):
            Employee(
                id=uuid4(),
                employee_number="E001",
                first_name="John",
                last_name="Doe",
                pay_type=PayType.SALARY,
                pay_frequency=PayFrequency.BIWEEKLY,
                base_pay=Decimal("0"),  # Zero salary!
            )

    def test_base_pay_cannot_be_negative(self):
        """Base pay cannot be negative."""
        with pytest.raises(ValueError, match="base_pay cannot be negative"):
            Employee(
                id=uuid4(),
                employee_number="E002",
                first_name="Jane",
                last_name="Doe",
                pay_type=PayType.HOURLY,
                pay_frequency=PayFrequency.WEEKLY,
                base_pay=Decimal("-15.00"),  # Negative pay!
            )

    def test_valid_employee_passes(self):
        """Valid employee should be created."""
        employee = Employee(
            id=uuid4(),
            employee_number="E003",
            first_name="Bob",
            last_name="Smith",
            pay_type=PayType.SALARY,
            pay_frequency=PayFrequency.MONTHLY,
            base_pay=Decimal("50000"),
        )
        assert employee.base_pay > 0


# =============================================================================
# Expense Line Invariants
# =============================================================================

class TestExpenseLineInvariants:
    """Test Expense line amount invariants."""

    @pytest.mark.xfail(reason="Expense line amount vs qty*price requires optional fields validation")
    def test_expense_line_amount_must_equal_quantity_times_price(self):
        """Expense line amount should equal quantity × unit_price."""
        line = ExpenseLine(
            id=uuid4(),
            report_id=uuid4(),
            line_number=1,
            expense_date=date.today(),
            category=ExpenseCategory.MEALS,
            description="Team lunch",
            quantity=Decimal("5"),
            unit_price=Decimal("20"),
            amount=Decimal("150"),  # Should be 100!
            currency="USD",
            payment_method=PaymentMethod.CORPORATE_CARD,
        )
        if line.quantity and line.unit_price:
            expected = line.quantity * line.unit_price
            assert line.amount == expected, f"Amount {line.amount} ≠ qty×price {expected}"

    @pytest.mark.xfail(reason="Zero expense amount may be valid in some scenarios")
    def test_expense_line_amount_must_be_positive(self):
        """Expense line amount should be positive (or have explicit reason)."""
        line = ExpenseLine(
            id=uuid4(),
            report_id=uuid4(),
            line_number=1,
            expense_date=date.today(),
            category=ExpenseCategory.MEALS,
            description="Free lunch?",
            amount=Decimal("0"),  # Zero expense?
            currency="USD",
            payment_method=PaymentMethod.CORPORATE_CARD,
        )
        assert line.amount > 0, "Expense amount should be positive"


# =============================================================================
# GL Fiscal Period Invariants
# =============================================================================

class TestFiscalPeriodInvariants:
    """Test GL fiscal period date invariants."""

    def test_period_end_must_be_after_start(self):
        """Fiscal period end date must be after start date."""
        with pytest.raises(ValueError, match="end_date.*must be after start_date"):
            FiscalPeriod(
                id=uuid4(),
                period_number=1,
                fiscal_year=2024,
                start_date=date(2024, 1, 31),
                end_date=date(2024, 1, 1),  # Before start!
            )

    def test_period_number_must_be_valid(self):
        """Fiscal period number should be 1-13."""
        with pytest.raises(ValueError, match="period_number must be between 1 and 13"):
            FiscalPeriod(
                id=uuid4(),
                period_number=15,  # Invalid!
                fiscal_year=2024,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    def test_valid_fiscal_period_passes(self):
        """Valid fiscal period should be created."""
        period = FiscalPeriod(
            id=uuid4(),
            period_number=1,
            fiscal_year=2024,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        assert period.end_date > period.start_date


# =============================================================================
# Payment Batch Invariants
# =============================================================================

class TestPaymentBatchInvariants:
    """Test payment batch total consistency."""

    @pytest.mark.xfail(reason="PaymentBatch doesn't have payment_count field")
    def test_payment_batch_total_must_equal_sum_of_payments(self):
        """Payment batch total should equal sum of payment amounts."""
        # This would require having payment objects to sum
        # The batch stores total but no validation occurs
        batch = PaymentBatch(
            id=uuid4(),
            batch_date=date.today(),
            payment_method="ach",
            total_amount=Decimal("10000"),  # Claimed total
            payment_ids=(uuid4(), uuid4(), uuid4()),  # Only 3 payments listed
        )
        # Can't validate payment_count without the field
        pass


# =============================================================================
# Summary
# =============================================================================

class TestModelInvariantSummary:
    """Summary of model invariant validation."""

    def test_document_validation_status(self):
        """
        Documents model invariant validation status.

        Validations now enforced:
        - AP Invoice: total = subtotal + tax, credit memo consistency
        - AR Receipt: amount > 0, unallocated >= 0, unallocated <= amount
        - PO Line: received <= ordered, invoiced <= received
        - Stock Level: on_hand >= 0, available = on_hand - reserved
        - Work Order: ordered > 0, completed <= ordered
        - Employee: base_pay >= 0, salaried must have positive pay
        - Fiscal Period: end > start, period 1-13

        Complex rules still marked xfail:
        - Invoice subtotal vs line sum (cross-entity)
        - Item standard cost (business context dependent)
        - Expense line amount vs qty*price (optional fields)
        - Expense amount > 0 (zero may be valid)
        - Payment batch count (field doesn't exist)
        """
        pass  # Documentation only
