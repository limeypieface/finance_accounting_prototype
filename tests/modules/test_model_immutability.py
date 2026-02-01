"""
Model Immutability Tests.

All domain models are frozen dataclasses.
These tests ensure they cannot be mutated after creation.
"""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.ap.models import Invoice, InvoiceLine, Vendor
from finance_modules.ar.models import Customer, Receipt
from finance_modules.ar.models import Invoice as ARInvoice
from finance_modules.assets.models import Asset, AssetStatus
from finance_modules.cash.models import BankAccount, BankTransaction
from finance_modules.expense.models import (
    ExpenseCategory,
    ExpenseLine,
    ExpenseReport,
    PaymentMethod,
)
from finance_modules.gl.models import Account, AccountType
from finance_modules.inventory.models import InventoryReceipt, Item, ItemType
from finance_modules.payroll.models import Employee, PayFrequency, PayType
from finance_modules.procurement.models import PurchaseOrder, Requisition
from finance_modules.tax.models import TaxJurisdiction, TaxType
from finance_modules.wip.models import Operation, WorkOrder


class TestAPModelImmutability:
    """Test AP models are immutable."""

    def test_vendor_immutable(self):
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name="Test Vendor",
        )
        with pytest.raises(FrozenInstanceError):
            vendor.name = "Modified"

    def test_invoice_immutable(self):
        invoice = Invoice(
            id=uuid4(),
            vendor_id=uuid4(),
            invoice_number="INV-001",
            invoice_date=date.today(),
            due_date=date.today(),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("10.00"),
            total_amount=Decimal("110.00"),
        )
        with pytest.raises(FrozenInstanceError):
            invoice.total_amount = Decimal("0")


class TestARModelImmutability:
    """Test AR models are immutable."""

    def test_customer_immutable(self):
        customer = Customer(
            id=uuid4(),
            code="C001",
            name="Test Customer",
        )
        with pytest.raises(FrozenInstanceError):
            customer.credit_limit = Decimal("50000")

    def test_receipt_immutable(self):
        receipt = Receipt(
            id=uuid4(),
            customer_id=uuid4(),
            receipt_date=date.today(),
            amount=Decimal("1000.00"),
            currency="USD",
            payment_method="ach",
            reference="CHK-001",
        )
        with pytest.raises(FrozenInstanceError):
            receipt.amount = Decimal("2000.00")


class TestInventoryModelImmutability:
    """Test Inventory models are immutable."""

    def test_item_immutable(self):
        item = Item(
            id=uuid4(),
            code="ITM001",
            description="Test Item",
            item_type=ItemType.FINISHED_GOODS,
            unit_of_measure="EA",
            standard_cost=Decimal("25.00"),
        )
        with pytest.raises(FrozenInstanceError):
            item.standard_cost = Decimal("30.00")


class TestWIPModelImmutability:
    """Test WIP models are immutable."""

    def test_work_order_immutable(self):
        wo = WorkOrder(
            id=uuid4(),
            order_number="WO-001",
            item_id=uuid4(),
            quantity_ordered=Decimal("100"),
        )
        with pytest.raises(FrozenInstanceError):
            wo.quantity_completed = Decimal("50")


class TestAssetModelImmutability:
    """Test Asset models are immutable."""

    def test_asset_immutable(self):
        asset = Asset(
            id=uuid4(),
            asset_number="FA-001",
            description="Test Asset",
            category_id=uuid4(),
            acquisition_date=date.today(),
        )
        with pytest.raises(FrozenInstanceError):
            asset.status = AssetStatus.DISPOSED


class TestExpenseModelImmutability:
    """Test Expense models are immutable."""

    def test_expense_report_immutable(self):
        report = ExpenseReport(
            id=uuid4(),
            report_number="EXP-001",
            employee_id=uuid4(),
            report_date=date.today(),
            purpose="Business travel",
        )
        with pytest.raises(FrozenInstanceError):
            report.total_amount = Decimal("500.00")

    def test_expense_line_immutable(self):
        line = ExpenseLine(
            id=uuid4(),
            report_id=uuid4(),
            line_number=1,
            expense_date=date.today(),
            category=ExpenseCategory.TRAVEL,
            description="Flight",
            amount=Decimal("350.00"),
            currency="USD",
            payment_method=PaymentMethod.CORPORATE_CARD,
        )
        with pytest.raises(FrozenInstanceError):
            line.amount = Decimal("400.00")


class TestTaxModelImmutability:
    """Test Tax models are immutable."""

    def test_jurisdiction_immutable(self):
        jurisdiction = TaxJurisdiction(
            id=uuid4(),
            code="CA",
            name="California",
            jurisdiction_type="state",
        )
        with pytest.raises(FrozenInstanceError):
            jurisdiction.tax_type = TaxType.VAT


class TestProcurementModelImmutability:
    """Test Procurement models are immutable."""

    def test_purchase_order_immutable(self):
        po = PurchaseOrder(
            id=uuid4(),
            po_number="PO-001",
            vendor_id=uuid4(),
            order_date=date.today(),
        )
        with pytest.raises(FrozenInstanceError):
            po.total_amount = Decimal("10000.00")


class TestPayrollModelImmutability:
    """Test Payroll models are immutable."""

    def test_employee_immutable(self):
        employee = Employee(
            id=uuid4(),
            employee_number="E001",
            first_name="John",
            last_name="Doe",
            pay_type=PayType.SALARY,
            pay_frequency=PayFrequency.BIWEEKLY,
            base_pay=Decimal("75000.00"),
        )
        with pytest.raises(FrozenInstanceError):
            employee.base_pay = Decimal("80000.00")


class TestGLModelImmutability:
    """Test GL models are immutable."""

    def test_account_immutable(self):
        account = Account(
            id=uuid4(),
            account_code="1000-000",
            name="Cash",
            account_type=AccountType.ASSET,
        )
        with pytest.raises(FrozenInstanceError):
            account.name = "Modified Cash"


class TestCashModelImmutability:
    """Test Cash models are immutable."""

    def test_bank_account_immutable(self):
        bank_account = BankAccount(
            id=uuid4(),
            code="CHASE-001",
            name="Operating Account",
            institution="Chase",
            account_number_masked="****1234",
            gl_account_code="1000-000",
            currency="USD",
        )
        with pytest.raises(FrozenInstanceError):
            bank_account.name = "Modified"
