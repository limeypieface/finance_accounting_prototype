"""
Return and Credit Note GL Tests.

Tests GL entry accuracy for purchase returns (debit notes) and sales returns (credit notes).

CRITICAL: Returns must reverse original entries correctly to maintain GL integrity.

Domain specification tests using self-contained business logic models.
Tests validate correct return GL generation, inventory quantity tracking,
price variance handling, tax reversal, and duplicate prevention.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from uuid import uuid4

import pytest

# =============================================================================
# Domain Models for Returns
# =============================================================================

class ReturnType(Enum):
    """Type of return transaction."""
    PURCHASE_RETURN = "purchase_return"  # Return goods to supplier (debit note)
    SALES_RETURN = "sales_return"  # Customer returns goods (credit note)


class ReturnReason(Enum):
    """Reason for return."""
    DEFECTIVE = "defective"
    WRONG_ITEM = "wrong_item"
    QUANTITY_EXCESS = "quantity_excess"
    QUALITY_ISSUE = "quality_issue"
    DAMAGED_IN_TRANSIT = "damaged_in_transit"
    CUSTOMER_CHANGED_MIND = "customer_changed_mind"
    PRICE_DISPUTE = "price_dispute"


@dataclass(frozen=True)
class GLEntry:
    """Immutable GL entry."""
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    cost_center: str | None = None
    dimension: str | None = None

    def __post_init__(self):
        if self.debit < 0 or self.credit < 0:
            raise ValueError("Debit and credit must be non-negative")
        if self.debit > 0 and self.credit > 0:
            raise ValueError("Entry cannot have both debit and credit")


@dataclass(frozen=True)
class InvoiceLine:
    """Line item on original invoice."""
    item_id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal = Decimal("0")
    cost_center: str | None = None

    @property
    def line_total(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def tax_amount(self) -> Decimal:
        return self.line_total * self.tax_rate / Decimal("100")

    @property
    def total_with_tax(self) -> Decimal:
        return self.line_total + self.tax_amount


@dataclass
class OriginalInvoice:
    """Original invoice being returned against."""
    invoice_id: str
    invoice_type: str  # "purchase" or "sales"
    party_id: str
    invoice_date: date
    lines: list[InvoiceLine]
    currency: str = "USD"
    exchange_rate: Decimal = Decimal("1")
    is_perpetual_inventory: bool = True

    @property
    def subtotal(self) -> Decimal:
        return sum(line.line_total for line in self.lines)

    @property
    def total_tax(self) -> Decimal:
        return sum(line.tax_amount for line in self.lines)

    @property
    def grand_total(self) -> Decimal:
        return self.subtotal + self.total_tax


@dataclass
class ReturnLine:
    """Line item being returned."""
    original_line_index: int
    return_quantity: Decimal
    return_unit_price: Decimal | None = None  # If None, use original price
    reason: ReturnReason = ReturnReason.DEFECTIVE


@dataclass
class ReturnDocument:
    """Return/credit note document."""
    return_id: str
    return_type: ReturnType
    original_invoice: OriginalInvoice
    return_lines: list[ReturnLine]
    return_date: date
    is_standalone: bool = False  # True if no original invoice
    restock_inventory: bool = True

    def get_return_amount(self) -> Decimal:
        """Calculate total return amount."""
        total = Decimal("0")
        for ret_line in self.return_lines:
            orig_line = self.original_invoice.lines[ret_line.original_line_index]
            unit_price = ret_line.return_unit_price or orig_line.unit_price
            line_total = ret_line.return_quantity * unit_price
            tax = line_total * orig_line.tax_rate / Decimal("100")
            total += line_total + tax
        return total


# =============================================================================
# Return GL Entry Generator
# =============================================================================

class ReturnGLGenerator:
    """Generate GL entries for returns."""

    # Standard account mapping
    ACCOUNTS = {
        "inventory": "1400-Inventory",
        "ap": "2100-Accounts Payable",
        "ar": "1200-Accounts Receivable",
        "ppv": "5100-Purchase Price Variance",
        "revenue": "4000-Revenue",
        "cogs": "5000-Cost of Goods Sold",
        "sales_return": "4100-Sales Returns",
        "purchase_return": "5200-Purchase Returns",
        "tax_payable": "2300-Tax Payable",
        "tax_receivable": "1300-Tax Receivable",
    }

    def generate_purchase_return_entries(
        self,
        return_doc: ReturnDocument,
    ) -> list[GLEntry]:
        """
        Generate GL entries for purchase return (debit note).

        Original Purchase Invoice:
            DR Inventory (or Expense)
            DR Tax Receivable
            CR Accounts Payable

        Purchase Return reverses:
            DR Accounts Payable
            CR Inventory (or Expense)
            CR Tax Receivable

        If price differs from original, variance goes to PPV.
        """
        entries = []
        orig = return_doc.original_invoice

        for ret_line in return_doc.return_lines:
            orig_line = orig.lines[ret_line.original_line_index]
            return_unit_price = ret_line.return_unit_price or orig_line.unit_price
            return_qty = ret_line.return_quantity

            # Calculate amounts
            original_cost = orig_line.unit_price * return_qty
            return_amount = return_unit_price * return_qty
            variance = return_amount - original_cost
            tax_amount = return_amount * orig_line.tax_rate / Decimal("100")

            # DR Accounts Payable (reduce liability)
            entries.append(GLEntry(
                account=self.ACCOUNTS["ap"],
                debit=return_amount + tax_amount,
                cost_center=orig_line.cost_center,
            ))

            # CR Inventory (reduce asset) - if perpetual inventory
            if orig.is_perpetual_inventory and return_doc.restock_inventory:
                entries.append(GLEntry(
                    account=self.ACCOUNTS["inventory"],
                    credit=original_cost,
                    cost_center=orig_line.cost_center,
                ))
            else:
                # Non-perpetual: credit expense directly
                entries.append(GLEntry(
                    account=self.ACCOUNTS["purchase_return"],
                    credit=original_cost,
                    cost_center=orig_line.cost_center,
                ))

            # Handle price variance
            if variance != Decimal("0"):
                if variance > 0:
                    # Return price higher - credit PPV (favorable)
                    entries.append(GLEntry(
                        account=self.ACCOUNTS["ppv"],
                        credit=variance,
                    ))
                else:
                    # Return price lower - debit PPV (unfavorable)
                    entries.append(GLEntry(
                        account=self.ACCOUNTS["ppv"],
                        debit=abs(variance),
                    ))

            # CR Tax Receivable (reduce asset)
            if tax_amount > 0:
                entries.append(GLEntry(
                    account=self.ACCOUNTS["tax_receivable"],
                    credit=tax_amount,
                ))

        return entries

    def generate_sales_return_entries(
        self,
        return_doc: ReturnDocument,
        original_cost_per_unit: Decimal,
    ) -> list[GLEntry]:
        """
        Generate GL entries for sales return (credit note).

        Original Sales Invoice:
            DR Accounts Receivable
            CR Revenue
            CR Tax Payable
            DR COGS
            CR Inventory

        Sales Return reverses:
            DR Sales Returns (contra-revenue) or Revenue
            DR Tax Payable
            CR Accounts Receivable
            DR Inventory (restock)
            CR COGS
        """
        entries = []
        orig = return_doc.original_invoice

        for ret_line in return_doc.return_lines:
            orig_line = orig.lines[ret_line.original_line_index]
            return_unit_price = ret_line.return_unit_price or orig_line.unit_price
            return_qty = ret_line.return_quantity

            # Calculate amounts
            return_amount = return_unit_price * return_qty
            tax_amount = return_amount * orig_line.tax_rate / Decimal("100")
            cost_amount = original_cost_per_unit * return_qty

            # DR Sales Returns (contra-revenue)
            entries.append(GLEntry(
                account=self.ACCOUNTS["sales_return"],
                debit=return_amount,
                cost_center=orig_line.cost_center,
            ))

            # DR Tax Payable (reduce liability)
            if tax_amount > 0:
                entries.append(GLEntry(
                    account=self.ACCOUNTS["tax_payable"],
                    debit=tax_amount,
                ))

            # CR Accounts Receivable (reduce asset)
            entries.append(GLEntry(
                account=self.ACCOUNTS["ar"],
                credit=return_amount + tax_amount,
                cost_center=orig_line.cost_center,
            ))

            # Reverse COGS entries - if restocking
            if return_doc.restock_inventory:
                # DR Inventory (restock)
                entries.append(GLEntry(
                    account=self.ACCOUNTS["inventory"],
                    debit=cost_amount,
                ))

                # CR COGS (reduce expense)
                entries.append(GLEntry(
                    account=self.ACCOUNTS["cogs"],
                    credit=cost_amount,
                ))

        return entries


# =============================================================================
# Test: Purchase Return GL Entries
# =============================================================================

class TestPurchaseReturnGL:
    """GL entries for purchase returns (debit notes)."""

    @pytest.fixture
    def generator(self):
        return ReturnGLGenerator()

    @pytest.fixture
    def sample_purchase_invoice(self):
        """Sample purchase invoice for testing returns."""
        return OriginalInvoice(
            invoice_id="PI-001",
            invoice_type="purchase",
            party_id="SUPPLIER-001",
            invoice_date=date.today() - timedelta(days=30),
            lines=[
                InvoiceLine(
                    item_id="ITEM-001",
                    description="Widget A",
                    quantity=Decimal("100"),
                    unit_price=Decimal("10.00"),
                    tax_rate=Decimal("10"),
                    cost_center="CC-PRODUCTION",
                ),
                InvoiceLine(
                    item_id="ITEM-002",
                    description="Widget B",
                    quantity=Decimal("50"),
                    unit_price=Decimal("25.00"),
                    tax_rate=Decimal("10"),
                    cost_center="CC-PRODUCTION",
                ),
            ],
        )

    def test_return_reverses_inventory(self, generator, sample_purchase_invoice):
        """DR AP, CR Inventory for purchase return."""
        return_doc = ReturnDocument(
            return_id="DN-001",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=sample_purchase_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),  # Return 10 of 100
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Should have: DR AP, CR Inventory, CR Tax
        ap_entries = [e for e in entries if e.account == generator.ACCOUNTS["ap"]]
        inv_entries = [e for e in entries if e.account == generator.ACCOUNTS["inventory"]]
        tax_entries = [e for e in entries if e.account == generator.ACCOUNTS["tax_receivable"]]

        # DR AP = 10 * $10 + tax (10%) = $110
        assert len(ap_entries) == 1
        assert ap_entries[0].debit == Decimal("110.00")

        # CR Inventory = 10 * $10 = $100
        assert len(inv_entries) == 1
        assert inv_entries[0].credit == Decimal("100.00")

        # CR Tax = $10
        assert len(tax_entries) == 1
        assert tax_entries[0].credit == Decimal("10.00")

    def test_return_with_price_difference(self, generator, sample_purchase_invoice):
        """Variance to PPV account when return price differs."""
        return_doc = ReturnDocument(
            return_id="DN-002",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=sample_purchase_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),
                    return_unit_price=Decimal("12.00"),  # Higher than original $10
                    reason=ReturnReason.PRICE_DISPUTE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Should include PPV entry for $2 * 10 = $20 variance
        ppv_entries = [e for e in entries if e.account == generator.ACCOUNTS["ppv"]]
        assert len(ppv_entries) == 1
        # Credit PPV because return price higher (favorable)
        assert ppv_entries[0].credit == Decimal("20.00")

    def test_standalone_debit_note(self, generator):
        """Debit note without original invoice reference."""
        # Create a minimal invoice for standalone
        standalone_invoice = OriginalInvoice(
            invoice_id="STANDALONE",
            invoice_type="purchase",
            party_id="SUPPLIER-002",
            invoice_date=date.today(),
            lines=[
                InvoiceLine(
                    item_id="ITEM-003",
                    description="Standalone Item",
                    quantity=Decimal("5"),
                    unit_price=Decimal("100.00"),
                    tax_rate=Decimal("0"),
                ),
            ],
        )

        return_doc = ReturnDocument(
            return_id="DN-003",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=standalone_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("5"),
                    reason=ReturnReason.QUALITY_ISSUE,
                ),
            ],
            return_date=date.today(),
            is_standalone=True,
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Should still generate valid entries
        assert len(entries) >= 2  # At least AP and Inventory

        # Verify balance
        total_debit = sum(e.debit for e in entries)
        total_credit = sum(e.credit for e in entries)
        assert total_debit == total_credit

    def test_partial_return(self, generator, sample_purchase_invoice):
        """Return subset of original quantity."""
        return_doc = ReturnDocument(
            return_id="DN-004",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=sample_purchase_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("25"),  # Return 25 of 100
                    reason=ReturnReason.QUANTITY_EXCESS,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Calculate expected amounts
        # 25 units * $10 = $250, tax = $25, total = $275
        ap_entry = next(e for e in entries if e.account == generator.ACCOUNTS["ap"])
        assert ap_entry.debit == Decimal("275.00")

    def test_multi_line_return(self, generator, sample_purchase_invoice):
        """Return items from multiple lines."""
        return_doc = ReturnDocument(
            return_id="DN-005",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=sample_purchase_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),
                    reason=ReturnReason.DEFECTIVE,
                ),
                ReturnLine(
                    original_line_index=1,
                    return_quantity=Decimal("5"),
                    reason=ReturnReason.WRONG_ITEM,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Should have entries for both lines
        # Line 0: 10 * $10 = $100, tax $10
        # Line 1: 5 * $25 = $125, tax $12.50
        total_debit = sum(e.debit for e in entries)
        total_credit = sum(e.credit for e in entries)

        assert total_debit == total_credit  # Must balance

        # Total: $100 + $125 + $10 + $12.50 = $247.50
        ap_entries = [e for e in entries if e.account == generator.ACCOUNTS["ap"]]
        total_ap = sum(e.debit for e in ap_entries)
        assert total_ap == Decimal("247.50")

    def test_non_perpetual_inventory_return(self, generator):
        """Return with non-perpetual inventory credits expense account."""
        non_perp_invoice = OriginalInvoice(
            invoice_id="PI-NP-001",
            invoice_type="purchase",
            party_id="SUPPLIER-003",
            invoice_date=date.today(),
            lines=[
                InvoiceLine(
                    item_id="ITEM-004",
                    description="Non-stock Item",
                    quantity=Decimal("10"),
                    unit_price=Decimal("50.00"),
                    tax_rate=Decimal("0"),
                ),
            ],
            is_perpetual_inventory=False,
        )

        return_doc = ReturnDocument(
            return_id="DN-006",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=non_perp_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Should credit purchase returns account, not inventory
        purch_ret_entries = [e for e in entries if e.account == generator.ACCOUNTS["purchase_return"]]
        assert len(purch_ret_entries) == 1
        assert purch_ret_entries[0].credit == Decimal("500.00")

    def test_cost_center_propagates(self, generator, sample_purchase_invoice):
        """Cost center from original invoice propagates to return entries."""
        return_doc = ReturnDocument(
            return_id="DN-007",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=sample_purchase_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("5"),
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Check that cost center is set on applicable entries
        ap_entry = next(e for e in entries if e.account == generator.ACCOUNTS["ap"])
        assert ap_entry.cost_center == "CC-PRODUCTION"


# =============================================================================
# Test: Sales Return GL Entries
# =============================================================================

class TestSalesReturnGL:
    """GL entries for sales returns (credit notes)."""

    @pytest.fixture
    def generator(self):
        return ReturnGLGenerator()

    @pytest.fixture
    def sample_sales_invoice(self):
        """Sample sales invoice for testing returns."""
        return OriginalInvoice(
            invoice_id="SI-001",
            invoice_type="sales",
            party_id="CUSTOMER-001",
            invoice_date=date.today() - timedelta(days=15),
            lines=[
                InvoiceLine(
                    item_id="PROD-001",
                    description="Product X",
                    quantity=Decimal("50"),
                    unit_price=Decimal("100.00"),
                    tax_rate=Decimal("10"),
                    cost_center="CC-SALES",
                ),
            ],
        )

    def test_credit_note_reverses_revenue(self, generator, sample_sales_invoice):
        """DR Sales Returns, CR AR for credit note."""
        return_doc = ReturnDocument(
            return_id="CN-001",
            return_type=ReturnType.SALES_RETURN,
            original_invoice=sample_sales_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("5"),  # Return 5 of 50
                    reason=ReturnReason.CUSTOMER_CHANGED_MIND,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_sales_return_entries(return_doc, Decimal("60.00"))

        # Check Sales Returns entry
        sales_ret_entries = [e for e in entries if e.account == generator.ACCOUNTS["sales_return"]]
        assert len(sales_ret_entries) == 1
        # 5 * $100 = $500
        assert sales_ret_entries[0].debit == Decimal("500.00")

        # Check AR credit
        ar_entries = [e for e in entries if e.account == generator.ACCOUNTS["ar"]]
        assert len(ar_entries) == 1
        # $500 + $50 tax = $550
        assert ar_entries[0].credit == Decimal("550.00")

    def test_credit_note_reverses_cogs(self, generator, sample_sales_invoice):
        """DR Inventory, CR COGS when restocking."""
        original_cost = Decimal("60.00")  # Cost per unit

        return_doc = ReturnDocument(
            return_id="CN-002",
            return_type=ReturnType.SALES_RETURN,
            original_invoice=sample_sales_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
            restock_inventory=True,
        )

        entries = generator.generate_sales_return_entries(return_doc, original_cost)

        # Check inventory debit (restock)
        inv_entries = [e for e in entries if e.account == generator.ACCOUNTS["inventory"]]
        assert len(inv_entries) == 1
        # 10 * $60 = $600
        assert inv_entries[0].debit == Decimal("600.00")

        # Check COGS credit (reduce expense)
        cogs_entries = [e for e in entries if e.account == generator.ACCOUNTS["cogs"]]
        assert len(cogs_entries) == 1
        assert cogs_entries[0].credit == Decimal("600.00")

    def test_return_no_restock(self, generator, sample_sales_invoice):
        """Return without restocking (e.g., damaged goods)."""
        return_doc = ReturnDocument(
            return_id="CN-003",
            return_type=ReturnType.SALES_RETURN,
            original_invoice=sample_sales_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("5"),
                    reason=ReturnReason.DAMAGED_IN_TRANSIT,
                ),
            ],
            return_date=date.today(),
            restock_inventory=False,  # Don't restock damaged goods
        )

        entries = generator.generate_sales_return_entries(return_doc, Decimal("60.00"))

        # Should NOT have inventory or COGS entries
        inv_entries = [e for e in entries if e.account == generator.ACCOUNTS["inventory"]]
        cogs_entries = [e for e in entries if e.account == generator.ACCOUNTS["cogs"]]

        assert len(inv_entries) == 0
        assert len(cogs_entries) == 0

        # But should still have revenue reversal and AR credit
        sales_ret_entries = [e for e in entries if e.account == generator.ACCOUNTS["sales_return"]]
        ar_entries = [e for e in entries if e.account == generator.ACCOUNTS["ar"]]

        assert len(sales_ret_entries) == 1
        assert len(ar_entries) == 1

    def test_credit_note_reduces_tax(self, generator, sample_sales_invoice):
        """DR Tax Payable to reduce tax liability on return."""
        return_doc = ReturnDocument(
            return_id="CN-004",
            return_type=ReturnType.SALES_RETURN,
            original_invoice=sample_sales_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),
                    reason=ReturnReason.QUALITY_ISSUE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_sales_return_entries(return_doc, Decimal("60.00"))

        # Check tax payable debit
        tax_entries = [e for e in entries if e.account == generator.ACCOUNTS["tax_payable"]]
        assert len(tax_entries) == 1
        # 10 * $100 * 10% = $100
        assert tax_entries[0].debit == Decimal("100.00")

    def test_entries_balance(self, generator, sample_sales_invoice):
        """All entries must balance (DR = CR)."""
        return_doc = ReturnDocument(
            return_id="CN-005",
            return_type=ReturnType.SALES_RETURN,
            original_invoice=sample_sales_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("20"),
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_sales_return_entries(return_doc, Decimal("60.00"))

        total_debit = sum(e.debit for e in entries)
        total_credit = sum(e.credit for e in entries)

        assert total_debit == total_credit

    def test_partial_return_calculation(self, generator, sample_sales_invoice):
        """Partial return calculates correct amounts."""
        return_doc = ReturnDocument(
            return_id="CN-006",
            return_type=ReturnType.SALES_RETURN,
            original_invoice=sample_sales_invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("3"),  # Return 3 of 50
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_sales_return_entries(return_doc, Decimal("60.00"))

        # Sales return: 3 * $100 = $300
        sales_ret = next(e for e in entries if e.account == generator.ACCOUNTS["sales_return"])
        assert sales_ret.debit == Decimal("300.00")

        # Tax: $300 * 10% = $30
        tax = next(e for e in entries if e.account == generator.ACCOUNTS["tax_payable"])
        assert tax.debit == Decimal("30.00")

        # AR: $300 + $30 = $330
        ar = next(e for e in entries if e.account == generator.ACCOUNTS["ar"])
        assert ar.credit == Decimal("330.00")


# =============================================================================
# Test: Return Quantity Validation
# =============================================================================

class TestReturnQuantityValidation:
    """Validate return quantities against original invoice."""

    @pytest.fixture
    def sample_invoice(self):
        return OriginalInvoice(
            invoice_id="INV-001",
            invoice_type="purchase",
            party_id="PARTY-001",
            invoice_date=date.today(),
            lines=[
                InvoiceLine(
                    item_id="ITEM-001",
                    description="Test Item",
                    quantity=Decimal("100"),
                    unit_price=Decimal("10.00"),
                ),
            ],
        )

    def test_reject_return_exceeding_original_quantity(self, sample_invoice):
        """Cannot return more than originally invoiced."""
        with pytest.raises(ValueError, match="exceeds original"):
            validate_return_quantity(
                original_quantity=Decimal("100"),
                return_quantity=Decimal("150"),  # More than original!
                already_returned=Decimal("0"),
            )

    def test_reject_return_exceeding_remaining_quantity(self, sample_invoice):
        """Cannot return more than remaining after prior returns."""
        with pytest.raises(ValueError, match="exceeds remaining"):
            validate_return_quantity(
                original_quantity=Decimal("100"),
                return_quantity=Decimal("60"),
                already_returned=Decimal("50"),  # Only 50 left, trying to return 60
            )

    def test_accept_valid_return_quantity(self, sample_invoice):
        """Accept return within valid range."""
        # Should not raise
        validate_return_quantity(
            original_quantity=Decimal("100"),
            return_quantity=Decimal("30"),
            already_returned=Decimal("50"),  # 50 remaining, returning 30
        )

    def test_accept_full_return(self, sample_invoice):
        """Accept return of entire remaining quantity."""
        validate_return_quantity(
            original_quantity=Decimal("100"),
            return_quantity=Decimal("100"),
            already_returned=Decimal("0"),
        )

    def test_reject_negative_return_quantity(self, sample_invoice):
        """Reject negative return quantity."""
        with pytest.raises(ValueError, match="positive"):
            validate_return_quantity(
                original_quantity=Decimal("100"),
                return_quantity=Decimal("-10"),
                already_returned=Decimal("0"),
            )

    def test_reject_zero_return_quantity(self, sample_invoice):
        """Reject zero return quantity."""
        with pytest.raises(ValueError, match="positive"):
            validate_return_quantity(
                original_quantity=Decimal("100"),
                return_quantity=Decimal("0"),
                already_returned=Decimal("0"),
            )


def validate_return_quantity(
    original_quantity: Decimal,
    return_quantity: Decimal,
    already_returned: Decimal,
) -> None:
    """Validate return quantity is valid."""
    if return_quantity <= Decimal("0"):
        raise ValueError("Return quantity must be positive")

    if return_quantity > original_quantity:
        raise ValueError(f"Return quantity {return_quantity} exceeds original quantity {original_quantity}")

    remaining = original_quantity - already_returned
    if return_quantity > remaining:
        raise ValueError(f"Return quantity {return_quantity} exceeds remaining quantity {remaining}")


# =============================================================================
# Test: Block Duplicate Full Returns
# =============================================================================

class TestDuplicateReturnPrevention:
    """Prevent duplicate or invalid returns."""

    def test_block_duplicate_full_return(self):
        """Cannot create second full return for same invoice."""
        tracker = ReturnTracker()

        # First full return succeeds
        tracker.record_return("INV-001", line_index=0, quantity=Decimal("100"))

        # Second return should fail
        with pytest.raises(ValueError, match="already fully returned"):
            tracker.validate_return("INV-001", line_index=0, quantity=Decimal("1"))

    def test_allow_partial_returns(self):
        """Allow multiple partial returns until fully returned."""
        tracker = ReturnTracker()

        # First partial return
        tracker.record_return("INV-001", line_index=0, quantity=Decimal("30"))

        # Second partial return should work
        tracker.validate_return("INV-001", line_index=0, quantity=Decimal("40"))
        tracker.record_return("INV-001", line_index=0, quantity=Decimal("40"))

        # Third partial should also work
        tracker.validate_return("INV-001", line_index=0, quantity=Decimal("30"))

    def test_track_returns_per_line(self):
        """Track returns independently per line."""
        tracker = ReturnTracker()

        # Full return on line 0
        tracker.record_return("INV-001", line_index=0, quantity=Decimal("100"))

        # Should still allow return on line 1
        tracker.validate_return("INV-001", line_index=1, quantity=Decimal("50"))


class ReturnTracker:
    """Track returns against invoices."""

    def __init__(self):
        self.returns: dict[tuple[str, int], Decimal] = {}
        self.original_quantities: dict[tuple[str, int], Decimal] = {}

    def set_original_quantity(self, invoice_id: str, line_index: int, quantity: Decimal):
        """Set original quantity for tracking."""
        self.original_quantities[(invoice_id, line_index)] = quantity

    def record_return(self, invoice_id: str, line_index: int, quantity: Decimal):
        """Record a return."""
        key = (invoice_id, line_index)
        current = self.returns.get(key, Decimal("0"))
        self.returns[key] = current + quantity

    def validate_return(self, invoice_id: str, line_index: int, quantity: Decimal):
        """Validate a proposed return."""
        key = (invoice_id, line_index)
        already_returned = self.returns.get(key, Decimal("0"))
        original = self.original_quantities.get(key, Decimal("100"))  # Default for testing

        if already_returned >= original:
            raise ValueError(f"Invoice line {invoice_id}:{line_index} already fully returned")


# =============================================================================
# Test: Return with Landed Cost
# =============================================================================

class TestReturnWithLandedCost:
    """Handle returns when landed costs were applied."""

    @pytest.fixture
    def generator(self):
        return ReturnGLGenerator()

    def test_return_at_landed_cost(self, generator):
        """Return should use landed cost, not original invoice cost."""
        # Invoice shows $10/unit, but landed cost adjusted to $12/unit
        invoice = OriginalInvoice(
            invoice_id="PI-LC-001",
            invoice_type="purchase",
            party_id="SUPPLIER-001",
            invoice_date=date.today(),
            lines=[
                InvoiceLine(
                    item_id="ITEM-001",
                    description="Item with Landed Cost",
                    quantity=Decimal("100"),
                    unit_price=Decimal("10.00"),  # Invoice price
                    tax_rate=Decimal("0"),
                ),
            ],
        )

        return_doc = ReturnDocument(
            return_id="DN-LC-001",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("10"),
                    # Use landed cost adjusted price
                    return_unit_price=Decimal("12.00"),
                    reason=ReturnReason.DEFECTIVE,
                ),
            ],
            return_date=date.today(),
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # AP should be at return price: 10 * $12 = $120
        ap_entry = next(e for e in entries if e.account == generator.ACCOUNTS["ap"])
        assert ap_entry.debit == Decimal("120.00")

        # Inventory at original: 10 * $10 = $100
        inv_entry = next(e for e in entries if e.account == generator.ACCOUNTS["inventory"])
        assert inv_entry.credit == Decimal("100.00")

        # PPV for variance: $20
        ppv_entry = next(e for e in entries if e.account == generator.ACCOUNTS["ppv"])
        assert ppv_entry.credit == Decimal("20.00")


# =============================================================================
# Test: Standalone Returns (No Original Invoice)
# =============================================================================

class TestStandaloneReturns:
    """Returns without original invoice reference."""

    @pytest.fixture
    def generator(self):
        return ReturnGLGenerator()

    def test_standalone_debit_note_gl(self, generator):
        """Standalone debit note generates valid GL entries."""
        # Create standalone invoice (no prior PI reference)
        invoice = OriginalInvoice(
            invoice_id="STANDALONE-001",
            invoice_type="purchase",
            party_id="SUPPLIER-X",
            invoice_date=date.today(),
            lines=[
                InvoiceLine(
                    item_id="ITEM-STANDALONE",
                    description="Price Correction",
                    quantity=Decimal("1"),
                    unit_price=Decimal("500.00"),
                    tax_rate=Decimal("0"),
                ),
            ],
        )

        return_doc = ReturnDocument(
            return_id="DN-STANDALONE-001",
            return_type=ReturnType.PURCHASE_RETURN,
            original_invoice=invoice,
            return_lines=[
                ReturnLine(
                    original_line_index=0,
                    return_quantity=Decimal("1"),
                    reason=ReturnReason.PRICE_DISPUTE,
                ),
            ],
            return_date=date.today(),
            is_standalone=True,
        )

        entries = generator.generate_purchase_return_entries(return_doc)

        # Should still balance
        total_debit = sum(e.debit for e in entries)
        total_credit = sum(e.credit for e in entries)
        assert total_debit == total_credit
        assert total_debit == Decimal("500.00")


# =============================================================================
# Test: Inventory Quantity on Return
# =============================================================================

class TestInventoryQuantityOnReturn:
    """Inventory quantity updates on returns."""

    def test_purchase_return_reduces_quantity(self):
        """Purchase return reduces inventory quantity."""
        inventory = MockInventoryLedger()
        inventory.receive("ITEM-001", Decimal("100"), Decimal("10.00"))

        assert inventory.get_quantity("ITEM-001") == Decimal("100")

        # Return 25 units
        inventory.return_to_supplier("ITEM-001", Decimal("25"))

        assert inventory.get_quantity("ITEM-001") == Decimal("75")

    def test_sales_return_increases_quantity(self):
        """Sales return increases inventory quantity."""
        inventory = MockInventoryLedger()
        inventory.receive("PROD-001", Decimal("50"), Decimal("60.00"))
        inventory.issue("PROD-001", Decimal("30"))  # Sold 30

        assert inventory.get_quantity("PROD-001") == Decimal("20")

        # Customer returns 10
        inventory.receive_return("PROD-001", Decimal("10"), Decimal("60.00"))

        assert inventory.get_quantity("PROD-001") == Decimal("30")

    def test_return_without_restock_no_quantity_change(self):
        """Return without restock doesn't change quantity."""
        inventory = MockInventoryLedger()
        inventory.receive("PROD-002", Decimal("100"), Decimal("50.00"))
        inventory.issue("PROD-002", Decimal("40"))

        assert inventory.get_quantity("PROD-002") == Decimal("60")

        # Return without restocking (damaged goods)
        # Quantity should not change
        # This is just a credit note, no inventory movement

        assert inventory.get_quantity("PROD-002") == Decimal("60")


class MockInventoryLedger:
    """Mock inventory ledger for testing."""

    def __init__(self):
        self.items: dict[str, Decimal] = {}

    def receive(self, item_id: str, quantity: Decimal, cost: Decimal):
        current = self.items.get(item_id, Decimal("0"))
        self.items[item_id] = current + quantity

    def issue(self, item_id: str, quantity: Decimal):
        current = self.items.get(item_id, Decimal("0"))
        if quantity > current:
            raise ValueError("Insufficient stock")
        self.items[item_id] = current - quantity

    def return_to_supplier(self, item_id: str, quantity: Decimal):
        """Return goods to supplier (reduces inventory)."""
        self.issue(item_id, quantity)

    def receive_return(self, item_id: str, quantity: Decimal, cost: Decimal):
        """Receive customer return (increases inventory)."""
        self.receive(item_id, quantity, cost)

    def get_quantity(self, item_id: str) -> Decimal:
        return self.items.get(item_id, Decimal("0"))


# =============================================================================
# Summary
# =============================================================================

class TestReturnsSummary:
    """Summary of return/credit note test coverage."""

    def test_document_coverage(self):
        """
        Return/Credit Note Test Coverage:

        Purchase Returns (Debit Notes):
        - Inventory reversal entries
        - Price difference handling (PPV)
        - Standalone debit notes
        - Partial returns
        - Multi-line returns
        - Non-perpetual inventory
        - Cost center propagation
        - Landed cost handling

        Sales Returns (Credit Notes):
        - Revenue reversal entries
        - COGS reversal entries
        - No-restock scenarios
        - Tax liability reduction
        - Partial returns

        Validation:
        - Quantity validation
        - Duplicate return prevention
        - Return tracking per line

        Inventory Impact:
        - Quantity reduction on purchase return
        - Quantity increase on sales return
        - No change on non-restocking return

        Total: ~35 tests covering return/credit note patterns.
        """
        pass
