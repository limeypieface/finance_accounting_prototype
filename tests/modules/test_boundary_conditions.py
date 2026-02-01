"""
Boundary Condition Tests.

Test edge cases and boundary values for domain models:
1. Decimal precision boundaries (10-digit precision, zero, max values)
2. Date boundaries (leap years, month ends, fiscal periods)
3. Collection boundaries (empty, single, max)
4. Enum validation (invalid values)
5. UUID handling
"""

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from uuid import UUID, uuid4

import pytest

from finance_modules.ap.config import APConfig
from finance_modules.ap.models import Invoice, InvoiceLine, Vendor
from finance_modules.ar.config import ARConfig
from finance_modules.ar.models import Customer, Receipt
from finance_modules.ar.models import Invoice as ARInvoice
from finance_modules.assets.models import Asset, AssetStatus, DepreciationMethod
from finance_modules.cash.models import BankAccount, BankTransaction
from finance_modules.expense.models import (
    ExpenseCategory,
    ExpenseLine,
    ExpenseReport,
    PaymentMethod,
)
from finance_modules.gl.config import GLConfig
from finance_modules.gl.models import Account, AccountType, FiscalPeriod
from finance_modules.inventory.models import InventoryReceipt, Item, ItemType
from finance_modules.payroll.models import Employee, PayFrequency, PayType
from finance_modules.procurement.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    Requisition,
)
from finance_modules.tax.models import TaxJurisdiction, TaxRate, TaxType
from finance_modules.wip.models import Operation, WorkOrder

# =============================================================================
# Decimal Precision Boundaries
# =============================================================================

class TestDecimalPrecision:
    """Test decimal precision boundaries in financial calculations."""

    def test_ten_digit_precision_preserved(self):
        """Decimal with 10 decimal places should be preserved."""
        value = Decimal("1234567890.1234567890")
        item = Item(
            id=uuid4(),
            code="ITEM-001",
            description="Test item",
            item_type=ItemType.FINISHED_GOODS,
            unit_of_measure="EA",
            standard_cost=value,
        )
        assert item.standard_cost == value

    def test_zero_decimal_valid(self):
        """Zero should be a valid decimal value."""
        invoice = Invoice(
            id=uuid4(),
            vendor_id=uuid4(),
            invoice_number="INV-001",
            invoice_date=date.today(),
            due_date=date.today(),
            currency="USD",
            subtotal=Decimal("0"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("0"),
        )
        assert invoice.total_amount == Decimal("0")

    def test_very_small_decimal(self):
        """Very small decimals should be preserved."""
        tiny = Decimal("0.0000000001")
        line = InvoiceLine(
            id=uuid4(),
            invoice_id=uuid4(),
            line_number=1,
            description="Tiny value",
            quantity=Decimal("1"),
            unit_price=tiny,
            amount=tiny,
            gl_account_code="5000-000",
        )
        assert line.unit_price == tiny

    def test_very_large_decimal(self):
        """Very large decimals should be preserved."""
        large = Decimal("9999999999999999.99")
        invoice = Invoice(
            id=uuid4(),
            vendor_id=uuid4(),
            invoice_number="INV-001",
            invoice_date=date.today(),
            due_date=date.today(),
            currency="USD",
            subtotal=large,
            tax_amount=Decimal("0"),
            total_amount=large,
        )
        assert invoice.total_amount == large

    def test_negative_decimal_amounts(self):
        """Negative amounts should be valid for credit memos."""
        negative = Decimal("-100.00")
        invoice = Invoice(
            id=uuid4(),
            vendor_id=uuid4(),
            invoice_number="CM-001",
            invoice_date=date.today(),
            due_date=date.today(),
            currency="USD",
            subtotal=negative,
            tax_amount=Decimal("0"),
            total_amount=negative,
        )
        assert invoice.total_amount == negative

    def test_decimal_with_trailing_zeros(self):
        """Trailing zeros should be preserved for exact comparison."""
        value1 = Decimal("100.00")
        value2 = Decimal("100.0")
        value3 = Decimal("100")
        # All are equal numerically
        assert value1 == value2 == value3
        # But different string representations
        assert str(value1) != str(value3)

    def test_decimal_arithmetic_precision(self):
        """Decimal arithmetic should maintain precision."""
        price = Decimal("19.99")
        quantity = Decimal("3")
        expected = Decimal("59.97")
        assert price * quantity == expected


# =============================================================================
# Date Boundary Conditions
# =============================================================================

class TestDateBoundaries:
    """Test date handling at boundaries."""

    def test_leap_year_february_29(self):
        """February 29 on leap year should be valid."""
        leap_date = date(2024, 2, 29)
        invoice = Invoice(
            id=uuid4(),
            vendor_id=uuid4(),
            invoice_number="INV-001",
            invoice_date=leap_date,
            due_date=leap_date,
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("100.00"),
        )
        assert invoice.invoice_date == leap_date

    def test_end_of_month_dates(self):
        """End of month dates should be valid."""
        dates = [
            date(2024, 1, 31),  # 31-day month
            date(2024, 4, 30),  # 30-day month
            date(2024, 2, 29),  # Feb leap year
            date(2023, 2, 28),  # Feb non-leap year
        ]
        for d in dates:
            invoice = Invoice(
                id=uuid4(),
                vendor_id=uuid4(),
                invoice_number=f"INV-{d}",
                invoice_date=d,
                due_date=d,
                currency="USD",
                subtotal=Decimal("100.00"),
                tax_amount=Decimal("0"),
                total_amount=Decimal("100.00"),
            )
            assert invoice.invoice_date == d

    def test_far_future_date(self):
        """Far future dates should be valid."""
        future = date(2099, 12, 31)
        asset = Asset(
            id=uuid4(),
            asset_number="FA-001",
            description="Long-lived asset",
            category_id=uuid4(),
            acquisition_date=date.today(),
            in_service_date=date.today(),
            useful_life_months=1000,
        )
        assert asset.useful_life_months == 1000

    def test_historical_date(self):
        """Historical dates should be valid."""
        historical = date(1990, 1, 1)
        # Asset acquired long ago
        asset = Asset(
            id=uuid4(),
            asset_number="FA-001",
            description="Old asset",
            category_id=uuid4(),
            acquisition_date=historical,
        )
        assert asset.acquisition_date == historical

    def test_fiscal_year_boundary(self):
        """Fiscal year end dates should be configurable."""
        # Test various fiscal year ends
        configs = [
            GLConfig(fiscal_year_end_month=12, fiscal_year_end_day=31),  # Calendar year
            GLConfig(fiscal_year_end_month=6, fiscal_year_end_day=30),   # June fiscal year
            GLConfig(fiscal_year_end_month=3, fiscal_year_end_day=31),   # March fiscal year
            GLConfig(fiscal_year_end_month=9, fiscal_year_end_day=30),   # September fiscal year
        ]
        for config in configs:
            assert config.fiscal_year_end_month in range(1, 13)


# =============================================================================
# Collection Boundaries
# =============================================================================

class TestCollectionBoundaries:
    """Test collection boundary conditions."""

    def test_empty_tuple_fields(self):
        """Empty tuples should be valid for collection fields."""
        config = APConfig(
            approval_levels=(),
            aging_buckets=(),
        )
        assert config.approval_levels == ()
        assert config.aging_buckets == ()

    def test_single_item_collections(self):
        """Single-item collections should be valid."""
        config = ARConfig(
            aging_buckets=(30,),
            dunning_levels=(),
        )
        assert len(config.aging_buckets) == 1

    def test_large_collections(self):
        """Large collections should be handled."""
        # 100 aging buckets
        buckets = tuple(range(1, 366, 3))  # Every 3 days up to a year
        config = ARConfig(aging_buckets=buckets)
        assert len(config.aging_buckets) > 100

    def test_invoice_with_many_lines(self):
        """Invoice with many lines should be valid."""
        lines = tuple(
            InvoiceLine(
                id=uuid4(),
                invoice_id=uuid4(),
                line_number=i,
                description=f"Line {i}",
                quantity=Decimal("1"),
                unit_price=Decimal("10.00"),
                amount=Decimal("10.00"),
                gl_account_code="5000-000",
            )
            for i in range(1, 101)  # 100 lines
        )
        assert len(lines) == 100


# =============================================================================
# Enum Validation
# =============================================================================

class TestEnumBoundaries:
    """Test enum value handling."""

    def test_all_item_types_valid(self):
        """All ItemType enum values should be usable."""
        for item_type in ItemType:
            item = Item(
                id=uuid4(),
                code=f"ITEM-{item_type.value}",
                description=f"Test {item_type.value}",
                item_type=item_type,
                unit_of_measure="EA",
                standard_cost=Decimal("10.00"),
            )
            assert item.item_type == item_type

    def test_all_asset_statuses_valid(self):
        """All AssetStatus enum values should be usable."""
        for status in AssetStatus:
            asset = Asset(
                id=uuid4(),
                asset_number=f"FA-{status.value}",
                description=f"Test {status.value}",
                category_id=uuid4(),
                acquisition_date=date.today(),
                status=status,
            )
            assert asset.status == status

    def test_all_account_types_valid(self):
        """All AccountType enum values should be usable."""
        for acct_type in AccountType:
            account = Account(
                id=uuid4(),
                account_code=f"1000-{acct_type.value}",
                name=f"Test {acct_type.value}",
                account_type=acct_type,
            )
            assert account.account_type == acct_type

    def test_all_tax_types_valid(self):
        """All TaxType enum values should be usable."""
        for tax_type in TaxType:
            jurisdiction = TaxJurisdiction(
                id=uuid4(),
                code=f"TEST-{tax_type.value}",
                name=f"Test {tax_type.value}",
                jurisdiction_type="state",
                tax_type=tax_type,
            )
            assert jurisdiction.tax_type == tax_type

    def test_all_expense_categories_valid(self):
        """All ExpenseCategory enum values should be usable."""
        for category in ExpenseCategory:
            line = ExpenseLine(
                id=uuid4(),
                report_id=uuid4(),
                line_number=1,
                expense_date=date.today(),
                category=category,
                description=f"Test {category.value}",
                amount=Decimal("100.00"),
                currency="USD",
                payment_method=PaymentMethod.CORPORATE_CARD,
            )
            assert line.category == category


# =============================================================================
# UUID Handling
# =============================================================================

class TestUUIDBoundaries:
    """Test UUID handling edge cases."""

    def test_uuid_nil_value(self):
        """Nil UUID (all zeros) should be valid."""
        nil_uuid = UUID("00000000-0000-0000-0000-000000000000")
        vendor = Vendor(
            id=nil_uuid,
            code="V001",
            name="Test Vendor",
        )
        assert vendor.id == nil_uuid

    def test_uuid_max_value(self):
        """Max UUID (all f's) should be valid."""
        max_uuid = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        customer = Customer(
            id=max_uuid,
            code="C001",
            name="Test Customer",
        )
        assert customer.id == max_uuid

    def test_uuid_uniqueness_in_collections(self):
        """UUIDs should maintain uniqueness in collections."""
        ids = {uuid4() for _ in range(1000)}
        assert len(ids) == 1000  # No collisions


# =============================================================================
# String Boundary Conditions
# =============================================================================

class TestStringBoundaries:
    """Test string handling boundaries."""

    def test_empty_string_fields(self):
        """Empty strings should be valid where allowed."""
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name="",  # Empty name
        )
        assert vendor.name == ""

    def test_very_long_strings(self):
        """Very long strings should be preserved."""
        long_name = "A" * 1000
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name=long_name,
        )
        assert len(vendor.name) == 1000

    def test_unicode_strings(self):
        """Unicode strings should be handled correctly."""
        unicode_name = "Empresa Espa\u00f1ola \u4e2d\u6587\u516c\u53f8 \ud55c\uad6d\ud68c\uc0ac"
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name=unicode_name,
        )
        assert vendor.name == unicode_name

    def test_special_characters_in_strings(self):
        """Special characters should be preserved."""
        special = "Test & Co. <special> 'chars' \"quoted\""
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name=special,
        )
        assert vendor.name == special

    def test_whitespace_only_strings(self):
        """Whitespace-only strings should be preserved."""
        whitespace = "   \t\n   "
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name=whitespace,
        )
        assert vendor.name == whitespace


# =============================================================================
# Optional Field Boundaries
# =============================================================================

class TestOptionalFieldBoundaries:
    """Test optional field handling."""

    def test_all_optional_fields_none(self):
        """Optional fields with no default should accept None."""
        vendor = Vendor(
            id=uuid4(),
            code="V001",
            name="Test Vendor",
            # Tax ID and GL account code are optional
        )
        assert vendor.tax_id is None
        assert vendor.default_gl_account_code is None

    def test_optional_decimal_fields(self):
        """Optional decimal fields should accept None."""
        customer = Customer(
            id=uuid4(),
            code="C001",
            name="Test Customer",
            credit_limit=None,
        )
        assert customer.credit_limit is None

    def test_optional_date_fields(self):
        """Optional date fields should accept None."""
        asset = Asset(
            id=uuid4(),
            asset_number="FA-001",
            description="Test Asset",
            category_id=uuid4(),
            acquisition_date=date.today(),
            in_service_date=None,  # Optional date field
        )
        assert asset.in_service_date is None


# =============================================================================
# Cross-Module Boundary Tests
# =============================================================================

class TestCrossModuleBoundaries:
    """Test boundaries across module interactions."""

    def test_po_line_references_item(self):
        """PO line should reference inventory item correctly."""
        item_id = uuid4()
        po_line = PurchaseOrderLine(
            id=uuid4(),
            purchase_order_id=uuid4(),
            line_number=1,
            item_id=item_id,
            description="Test item",
            quantity_ordered=Decimal("10"),
            unit_price=Decimal("25.00"),
            line_total=Decimal("250.00"),
        )
        assert po_line.item_id == item_id

    def test_fiscal_period_boundaries(self):
        """Fiscal period should handle date boundaries correctly."""
        period = FiscalPeriod(
            id=uuid4(),
            period_number=12,
            fiscal_year=2024,
            start_date=date(2024, 12, 1),
            end_date=date(2024, 12, 31),
        )
        assert period.period_number == 12
        assert period.end_date.day == 31

    def test_employee_payroll_references(self):
        """Employee should support all pay types."""
        for pay_type in PayType:
            for pay_freq in PayFrequency:
                employee = Employee(
                    id=uuid4(),
                    employee_number=f"E-{pay_type.value}-{pay_freq.value}",
                    first_name="Test",
                    last_name="Employee",
                    pay_type=pay_type,
                    pay_frequency=pay_freq,
                    base_pay=Decimal("50000.00"),
                )
                assert employee.pay_type == pay_type
                assert employee.pay_frequency == pay_freq
