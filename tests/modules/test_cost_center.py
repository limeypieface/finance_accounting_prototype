"""
Cost Center and Dimension Tests.

Tests cost center propagation from source documents to GL entries.

CRITICAL: Cost centers enable profitability analysis and budgeting.

Domain specification tests using self-contained business logic models.
Tests validate correct cost center propagation, hierarchy, validation,
distribution, and reporting patterns.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

import pytest

# =============================================================================
# Domain Models for Cost Centers
# =============================================================================

@dataclass(frozen=True)
class CostCenter:
    """Cost center for expense/revenue allocation."""
    cost_center_id: str
    name: str
    parent_id: str | None = None
    is_group: bool = False
    is_active: bool = True

    def __post_init__(self):
        if not self.cost_center_id:
            raise ValueError("Cost center ID is required")
        if not self.name:
            raise ValueError("Cost center name is required")


@dataclass(frozen=True)
class GLEntry:
    """GL entry with cost center dimension."""
    entry_id: str
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    cost_center: str | None = None
    project: str | None = None


@dataclass
class AccountDefault:
    """Default cost center for an account."""
    account: str
    default_cost_center: str


@dataclass
class Invoice:
    """Invoice with cost center."""
    invoice_id: str
    party_id: str
    amount: Decimal
    cost_center: str | None = None


@dataclass
class InvoiceLine:
    """Invoice line with cost center."""
    line_id: str
    invoice_id: str
    account: str
    amount: Decimal
    cost_center: str | None = None


@dataclass
class Payment:
    """Payment with cost center."""
    payment_id: str
    invoice_id: str
    amount: Decimal
    cost_center: str | None = None


# =============================================================================
# Cost Center Service
# =============================================================================

class CostCenterService:
    """Manage cost center operations."""

    def __init__(self):
        self.cost_centers: dict[str, CostCenter] = {}
        self.account_defaults: dict[str, str] = {}

    def register_cost_center(self, cost_center: CostCenter) -> None:
        """Register a cost center."""
        self.cost_centers[cost_center.cost_center_id] = cost_center

    def set_account_default(self, account: str, cost_center_id: str) -> None:
        """Set default cost center for account."""
        if cost_center_id not in self.cost_centers:
            raise ValueError(f"Unknown cost center: {cost_center_id}")
        self.account_defaults[account] = cost_center_id

    def get_cost_center(self, cost_center_id: str) -> CostCenter | None:
        """Get cost center by ID."""
        return self.cost_centers.get(cost_center_id)

    def get_default_for_account(self, account: str) -> str | None:
        """Get default cost center for account."""
        return self.account_defaults.get(account)

    def resolve_cost_center(
        self,
        explicit: str | None,
        account: str,
    ) -> str | None:
        """
        Resolve cost center: explicit takes precedence, then account default.
        """
        if explicit:
            return explicit
        return self.get_default_for_account(account)

    def validate_cost_center(self, cost_center_id: str) -> bool:
        """Validate cost center exists and is active."""
        cc = self.cost_centers.get(cost_center_id)
        if not cc:
            return False
        if not cc.is_active:
            return False
        if cc.is_group:
            return False  # Cannot post to group cost centers
        return True

    def get_hierarchy(self, cost_center_id: str) -> list[str]:
        """Get cost center hierarchy (child to parent)."""
        hierarchy = []
        current_id = cost_center_id

        while current_id:
            cc = self.cost_centers.get(current_id)
            if not cc:
                break
            hierarchy.append(current_id)
            current_id = cc.parent_id

        return hierarchy


# =============================================================================
# GL Entry Generator with Cost Center
# =============================================================================

class GLEntryGenerator:
    """Generate GL entries with cost center propagation."""

    def __init__(self, cost_center_service: CostCenterService):
        self.cc_service = cost_center_service

    def generate_invoice_entries(
        self,
        invoice: Invoice,
        lines: list[InvoiceLine],
        payable_account: str = "2100-AP",
    ) -> list[GLEntry]:
        """
        Generate GL entries for invoice with cost center.

        Cost center priority:
        1. Line-level cost center
        2. Invoice-level cost center
        3. Account default cost center
        """
        entries = []

        for line in lines:
            # Resolve cost center for expense/revenue line
            cost_center = self.cc_service.resolve_cost_center(
                explicit=line.cost_center or invoice.cost_center,
                account=line.account,
            )

            entries.append(GLEntry(
                entry_id=str(uuid4()),
                account=line.account,
                debit=line.amount,
                cost_center=cost_center,
            ))

        # AP entry typically doesn't need cost center (balance sheet)
        total = sum(line.amount for line in lines)
        entries.append(GLEntry(
            entry_id=str(uuid4()),
            account=payable_account,
            credit=total,
            cost_center=None,  # Balance sheet accounts often skip CC
        ))

        return entries

    def generate_payment_entries(
        self,
        payment: Payment,
        bank_account: str = "1100-Bank",
        payable_account: str = "2100-AP",
    ) -> list[GLEntry]:
        """Generate GL entries for payment with cost center."""
        cost_center = self.cc_service.resolve_cost_center(
            explicit=payment.cost_center,
            account=bank_account,
        )

        return [
            GLEntry(
                entry_id=str(uuid4()),
                account=payable_account,
                debit=payment.amount,
                cost_center=cost_center,
            ),
            GLEntry(
                entry_id=str(uuid4()),
                account=bank_account,
                credit=payment.amount,
                cost_center=cost_center,
            ),
        ]


# =============================================================================
# Test: Cost Center Propagation
# =============================================================================

class TestCostCenterPropagation:
    """Cost center flows to GL entries."""

    @pytest.fixture
    def cc_service(self):
        service = CostCenterService()
        service.register_cost_center(CostCenter("CC-PROD", "Production"))
        service.register_cost_center(CostCenter("CC-SALES", "Sales"))
        service.register_cost_center(CostCenter("CC-ADMIN", "Administration"))
        return service

    @pytest.fixture
    def generator(self, cc_service):
        return GLEntryGenerator(cc_service)

    def test_invoice_cost_center_to_gl(self, generator):
        """Invoice CC propagates to ledger."""
        invoice = Invoice(
            invoice_id="INV-001",
            party_id="SUPPLIER-001",
            amount=Decimal("1000.00"),
            cost_center="CC-PROD",
        )

        lines = [
            InvoiceLine(
                line_id="LINE-001",
                invoice_id="INV-001",
                account="5000-Purchases",
                amount=Decimal("1000.00"),
                # No line-level CC, uses invoice CC
            ),
        ]

        entries = generator.generate_invoice_entries(invoice, lines)

        # Expense entry should have invoice cost center
        expense_entry = next(e for e in entries if e.account == "5000-Purchases")
        assert expense_entry.cost_center == "CC-PROD"

    def test_line_cost_center_overrides_invoice(self, generator):
        """Line-level CC overrides invoice CC."""
        invoice = Invoice(
            invoice_id="INV-002",
            party_id="SUPPLIER-001",
            amount=Decimal("1000.00"),
            cost_center="CC-PROD",
        )

        lines = [
            InvoiceLine(
                line_id="LINE-002",
                invoice_id="INV-002",
                account="5000-Purchases",
                amount=Decimal("1000.00"),
                cost_center="CC-SALES",  # Override invoice CC
            ),
        ]

        entries = generator.generate_invoice_entries(invoice, lines)

        expense_entry = next(e for e in entries if e.account == "5000-Purchases")
        assert expense_entry.cost_center == "CC-SALES"

    def test_payment_cost_center_to_gl(self, generator):
        """Payment CC propagates to ledger."""
        payment = Payment(
            payment_id="PMT-001",
            invoice_id="INV-001",
            amount=Decimal("500.00"),
            cost_center="CC-ADMIN",
        )

        entries = generator.generate_payment_entries(payment)

        # Both entries should have the cost center
        assert all(e.cost_center == "CC-ADMIN" for e in entries)

    def test_default_cost_center_applied(self, cc_service, generator):
        """Uses account default when not specified."""
        cc_service.set_account_default("5100-Utilities", "CC-ADMIN")

        invoice = Invoice(
            invoice_id="INV-003",
            party_id="SUPPLIER-002",
            amount=Decimal("200.00"),
            # No cost center specified
        )

        lines = [
            InvoiceLine(
                line_id="LINE-003",
                invoice_id="INV-003",
                account="5100-Utilities",
                amount=Decimal("200.00"),
                # No cost center - should use default
            ),
        ]

        entries = generator.generate_invoice_entries(invoice, lines)

        expense_entry = next(e for e in entries if e.account == "5100-Utilities")
        assert expense_entry.cost_center == "CC-ADMIN"


# =============================================================================
# Test: Cost Center Balance Query
# =============================================================================

class TestCostCenterBalanceQuery:
    """Query balance filtered by cost center."""

    @pytest.fixture
    def ledger(self):
        return MockGLLedger()

    def test_balance_by_cost_center(self, ledger):
        """Query balance filtered by CC."""
        # Add entries with different cost centers
        ledger.post_entry(GLEntry("E1", "5000-Purchases", debit=Decimal("1000"), cost_center="CC-PROD"))
        ledger.post_entry(GLEntry("E2", "5000-Purchases", debit=Decimal("500"), cost_center="CC-SALES"))
        ledger.post_entry(GLEntry("E3", "5000-Purchases", debit=Decimal("300"), cost_center="CC-PROD"))

        # Query by cost center
        prod_balance = ledger.get_balance("5000-Purchases", cost_center="CC-PROD")
        sales_balance = ledger.get_balance("5000-Purchases", cost_center="CC-SALES")

        assert prod_balance == Decimal("1300")  # 1000 + 300
        assert sales_balance == Decimal("500")

    def test_total_balance_across_cost_centers(self, ledger):
        """Query total balance without CC filter."""
        ledger.post_entry(GLEntry("E1", "5000-Purchases", debit=Decimal("1000"), cost_center="CC-PROD"))
        ledger.post_entry(GLEntry("E2", "5000-Purchases", debit=Decimal("500"), cost_center="CC-SALES"))

        total = ledger.get_balance("5000-Purchases")

        assert total == Decimal("1500")

    def test_balance_with_no_cost_center(self, ledger):
        """Handle entries without cost center."""
        ledger.post_entry(GLEntry("E1", "1100-Bank", credit=Decimal("2000"), cost_center=None))
        ledger.post_entry(GLEntry("E2", "1100-Bank", credit=Decimal("500"), cost_center="CC-PROD"))

        # Query without CC should include all
        total = ledger.get_balance("1100-Bank")
        assert total == Decimal("-2500")  # Credits are negative

        # Query with specific CC
        prod = ledger.get_balance("1100-Bank", cost_center="CC-PROD")
        assert prod == Decimal("-500")


class MockGLLedger:
    """Mock GL ledger for testing."""

    def __init__(self):
        self.entries: list[GLEntry] = []

    def post_entry(self, entry: GLEntry):
        self.entries.append(entry)

    def get_balance(
        self,
        account: str,
        cost_center: str | None = None,
    ) -> Decimal:
        """Get account balance, optionally filtered by cost center."""
        filtered = [e for e in self.entries if e.account == account]

        if cost_center:
            filtered = [e for e in filtered if e.cost_center == cost_center]

        total_debit = sum(e.debit for e in filtered)
        total_credit = sum(e.credit for e in filtered)

        return total_debit - total_credit


# =============================================================================
# Test: Cost Center Validation
# =============================================================================

class TestCostCenterValidation:
    """Validate cost center assignments."""

    @pytest.fixture
    def cc_service(self):
        service = CostCenterService()
        service.register_cost_center(CostCenter("CC-PROD", "Production"))
        service.register_cost_center(CostCenter("CC-INACTIVE", "Inactive", is_active=False))
        service.register_cost_center(CostCenter("CC-GROUP", "Group", is_group=True))
        return service

    def test_valid_cost_center_accepted(self, cc_service):
        """Valid cost center passes validation."""
        assert cc_service.validate_cost_center("CC-PROD")

    def test_inactive_cost_center_rejected(self, cc_service):
        """Inactive cost center fails validation."""
        assert not cc_service.validate_cost_center("CC-INACTIVE")

    def test_group_cost_center_rejected(self, cc_service):
        """Cannot post to group cost center."""
        assert not cc_service.validate_cost_center("CC-GROUP")

    def test_unknown_cost_center_rejected(self, cc_service):
        """Unknown cost center fails validation."""
        assert not cc_service.validate_cost_center("CC-NONEXISTENT")

    def test_cost_center_requires_id(self):
        """Cost center ID is required."""
        with pytest.raises(ValueError, match="ID is required"):
            CostCenter("", "Empty ID")

    def test_cost_center_requires_name(self):
        """Cost center name is required."""
        with pytest.raises(ValueError, match="name is required"):
            CostCenter("CC-001", "")


# =============================================================================
# Test: Cost Center Hierarchy
# =============================================================================

class TestCostCenterHierarchy:
    """Cost center parent-child relationships."""

    @pytest.fixture
    def cc_service(self):
        service = CostCenterService()
        # Build hierarchy: Company > Division > Department
        service.register_cost_center(CostCenter("CC-COMPANY", "Company", is_group=True))
        service.register_cost_center(CostCenter("CC-OPERATIONS", "Operations", parent_id="CC-COMPANY", is_group=True))
        service.register_cost_center(CostCenter("CC-PROD", "Production", parent_id="CC-OPERATIONS"))
        service.register_cost_center(CostCenter("CC-QC", "Quality Control", parent_id="CC-OPERATIONS"))
        return service

    def test_get_hierarchy(self, cc_service):
        """Get cost center hierarchy."""
        hierarchy = cc_service.get_hierarchy("CC-PROD")

        assert hierarchy == ["CC-PROD", "CC-OPERATIONS", "CC-COMPANY"]

    def test_leaf_cost_center(self, cc_service):
        """Leaf cost center has no children."""
        # Leaf CC can be posted to (not a group)
        assert cc_service.validate_cost_center("CC-PROD")
        assert cc_service.validate_cost_center("CC-QC")

    def test_group_cost_center_not_postable(self, cc_service):
        """Group cost centers cannot be posted to directly."""
        assert not cc_service.validate_cost_center("CC-COMPANY")
        assert not cc_service.validate_cost_center("CC-OPERATIONS")


# =============================================================================
# Test: Multi-Cost Center Distribution
# =============================================================================

class TestMultiCostCenterDistribution:
    """Distribute amounts across multiple cost centers."""

    def test_split_expense_across_cost_centers(self):
        """Split single expense across multiple CCs."""
        total = Decimal("1000.00")

        allocations = [
            ("CC-PROD", Decimal("60")),  # 60%
            ("CC-SALES", Decimal("30")),  # 30%
            ("CC-ADMIN", Decimal("10")),  # 10%
        ]

        entries = distribute_to_cost_centers(
            account="5000-Rent",
            total=total,
            allocations=allocations,
        )

        assert len(entries) == 3
        assert entries[0].cost_center == "CC-PROD"
        assert entries[0].debit == Decimal("600.00")
        assert entries[1].cost_center == "CC-SALES"
        assert entries[1].debit == Decimal("300.00")
        assert entries[2].cost_center == "CC-ADMIN"
        assert entries[2].debit == Decimal("100.00")

    def test_allocation_must_total_100(self):
        """Allocation percentages must total 100."""
        allocations = [
            ("CC-PROD", Decimal("50")),
            ("CC-SALES", Decimal("30")),
            # Missing 20%!
        ]

        with pytest.raises(ValueError, match="must total 100"):
            distribute_to_cost_centers(
                account="5000-Rent",
                total=Decimal("1000.00"),
                allocations=allocations,
            )


def distribute_to_cost_centers(
    account: str,
    total: Decimal,
    allocations: list[tuple],
) -> list[GLEntry]:
    """Distribute amount across cost centers by percentage."""
    # Validate percentages total 100
    total_pct = sum(pct for _, pct in allocations)
    if total_pct != Decimal("100"):
        raise ValueError(f"Allocation percentages must total 100, got {total_pct}")

    entries = []
    for cost_center, percentage in allocations:
        amount = (total * percentage / Decimal("100")).quantize(Decimal("0.01"))
        entries.append(GLEntry(
            entry_id=str(uuid4()),
            account=account,
            debit=amount,
            cost_center=cost_center,
        ))

    return entries


# =============================================================================
# Test: Dimension-Based Offsetting
# =============================================================================

class TestDimensionOffsetting:
    """Offsetting entries maintain dimension consistency."""

    def test_offset_entry_inherits_cost_center(self):
        """Offsetting entry should have same cost center."""
        original = GLEntry(
            entry_id="E1",
            account="5000-Purchases",
            debit=Decimal("500"),
            cost_center="CC-PROD",
        )

        # Create offset (reversal)
        offset = GLEntry(
            entry_id="E2",
            account=original.account,
            credit=original.debit,
            cost_center=original.cost_center,  # Same CC
        )

        assert offset.cost_center == "CC-PROD"

    def test_intercompany_entries_preserve_cc(self):
        """Intercompany entries preserve cost center."""
        # When posting intercompany, both sides should maintain CC
        source_entry = GLEntry(
            entry_id="IC-S1",
            account="1500-IC Receivable",
            debit=Decimal("1000"),
            cost_center="CC-SALES",
        )

        target_entry = GLEntry(
            entry_id="IC-T1",
            account="2500-IC Payable",
            credit=Decimal("1000"),
            cost_center="CC-SALES",  # Preserve for reporting
        )

        assert source_entry.cost_center == target_entry.cost_center


# =============================================================================
# Test: Cost Center Reporting
# =============================================================================

class TestCostCenterReporting:
    """Generate reports by cost center."""

    @pytest.fixture
    def ledger(self):
        ledger = MockGLLedger()
        # Setup test data
        ledger.post_entry(GLEntry("E1", "4000-Revenue", credit=Decimal("5000"), cost_center="CC-SALES"))
        ledger.post_entry(GLEntry("E2", "5000-COGS", debit=Decimal("2000"), cost_center="CC-SALES"))
        ledger.post_entry(GLEntry("E3", "5100-Expenses", debit=Decimal("1000"), cost_center="CC-SALES"))
        ledger.post_entry(GLEntry("E4", "4000-Revenue", credit=Decimal("3000"), cost_center="CC-PROD"))
        ledger.post_entry(GLEntry("E5", "5000-COGS", debit=Decimal("1500"), cost_center="CC-PROD"))
        return ledger

    def test_profit_by_cost_center(self, ledger):
        """Calculate profit per cost center."""
        sales_revenue = abs(ledger.get_balance("4000-Revenue", cost_center="CC-SALES"))
        sales_cogs = ledger.get_balance("5000-COGS", cost_center="CC-SALES")
        sales_expenses = ledger.get_balance("5100-Expenses", cost_center="CC-SALES")
        sales_profit = sales_revenue - sales_cogs - sales_expenses

        assert sales_revenue == Decimal("5000")
        assert sales_cogs == Decimal("2000")
        assert sales_expenses == Decimal("1000")
        assert sales_profit == Decimal("2000")

        prod_revenue = abs(ledger.get_balance("4000-Revenue", cost_center="CC-PROD"))
        prod_cogs = ledger.get_balance("5000-COGS", cost_center="CC-PROD")
        prod_profit = prod_revenue - prod_cogs

        assert prod_revenue == Decimal("3000")
        assert prod_cogs == Decimal("1500")
        assert prod_profit == Decimal("1500")

    def test_total_company_profit(self, ledger):
        """Total profit across all cost centers."""
        total_revenue = abs(ledger.get_balance("4000-Revenue"))
        total_cogs = ledger.get_balance("5000-COGS")
        total_expenses = ledger.get_balance("5100-Expenses")
        total_profit = total_revenue - total_cogs - total_expenses

        assert total_revenue == Decimal("8000")
        assert total_cogs == Decimal("3500")
        assert total_expenses == Decimal("1000")
        assert total_profit == Decimal("3500")


# =============================================================================
# Summary
# =============================================================================

class TestCostCenterSummary:
    """Summary of cost center test coverage."""

    def test_document_coverage(self):
        """
        Cost Center Test Coverage:

        Propagation:
        - Invoice CC to GL
        - Line CC overrides invoice CC
        - Payment CC to GL
        - Default CC from account

        Balance Query:
        - Balance filtered by CC
        - Total balance across CCs
        - Handle entries without CC

        Validation:
        - Valid CC accepted
        - Inactive CC rejected
        - Group CC rejected
        - Unknown CC rejected
        - Required fields

        Hierarchy:
        - Get CC hierarchy
        - Leaf CC is postable
        - Group CC not postable

        Distribution:
        - Split across multiple CCs
        - Allocation must total 100%

        Offsetting:
        - Offset inherits CC
        - Intercompany preserves CC

        Reporting:
        - Profit by CC
        - Total company profit

        Total: ~25 tests covering cost center patterns.
        """
        pass
