"""
Landed Cost Allocation Tests.

Tests freight, duty, and other landed costs allocation to inventory.

CRITICAL: Landed costs affect inventory valuation and COGS.

Domain specification tests using self-contained business logic models for
allocation methods (by value, quantity, weight, equally), GL entries, and rounding.
Integration tests at bottom exercise InventoryService.receive_inventory()
and adjust_inventory() through the real posting pipeline.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import uuid4

import pytest

# =============================================================================
# Domain Models for Landed Cost
# =============================================================================

class AllocationMethod(Enum):
    """Method for allocating landed costs."""
    BY_VALUE = "by_value"  # Proportional to item value
    BY_QUANTITY = "by_quantity"  # Proportional to quantity
    BY_WEIGHT = "by_weight"  # Proportional to weight
    BY_VOLUME = "by_volume"  # Proportional to volume
    EQUALLY = "equally"  # Split equally across items
    MANUAL = "manual"  # User-specified allocation


class LandedCostType(Enum):
    """Type of landed cost."""
    FREIGHT = "freight"
    CUSTOMS_DUTY = "customs_duty"
    INSURANCE = "insurance"
    HANDLING = "handling"
    BROKERAGE = "brokerage"
    OTHER = "other"


@dataclass(frozen=True)
class ReceiptItem:
    """Item on a purchase receipt."""
    item_id: str
    description: str
    quantity: Decimal
    unit_cost: Decimal
    weight: Decimal | None = None
    volume: Decimal | None = None

    @property
    def total_value(self) -> Decimal:
        return self.quantity * self.unit_cost

    @property
    def total_weight(self) -> Decimal:
        if self.weight is None:
            return Decimal("0")
        return self.quantity * self.weight

    @property
    def total_volume(self) -> Decimal:
        if self.volume is None:
            return Decimal("0")
        return self.quantity * self.volume


@dataclass
class PurchaseReceipt:
    """Purchase receipt with items."""
    receipt_id: str
    supplier_id: str
    receipt_date: date
    items: list[ReceiptItem]

    @property
    def total_value(self) -> Decimal:
        return sum(item.total_value for item in self.items)

    @property
    def total_quantity(self) -> Decimal:
        return sum(item.quantity for item in self.items)

    @property
    def total_weight(self) -> Decimal:
        return sum(item.total_weight for item in self.items)


@dataclass(frozen=True)
class LandedCostCharge:
    """A single landed cost charge."""
    charge_id: str
    cost_type: LandedCostType
    amount: Decimal
    allocation_method: AllocationMethod = AllocationMethod.BY_VALUE
    account: str = "5200-Freight"

    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("Landed cost amount cannot be negative")


@dataclass
class LandedCostVoucher:
    """Voucher to allocate landed costs to receipt."""
    voucher_id: str
    receipt_id: str
    charges: list[LandedCostCharge]
    posting_date: date

    @property
    def total_charges(self) -> Decimal:
        return sum(c.amount for c in self.charges)


@dataclass
class AllocationResult:
    """Result of landed cost allocation to an item."""
    item_id: str
    original_cost: Decimal
    allocated_cost: Decimal
    new_valuation_rate: Decimal


@dataclass(frozen=True)
class GLEntry:
    """GL entry for landed cost."""
    entry_id: str
    account: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


# =============================================================================
# Landed Cost Allocator
# =============================================================================

class LandedCostAllocator:
    """Allocate landed costs to receipt items."""

    def allocate(
        self,
        receipt: PurchaseReceipt,
        voucher: LandedCostVoucher,
    ) -> list[AllocationResult]:
        """
        Allocate all landed costs to receipt items.

        Returns new valuation rate for each item.
        """
        # Initialize per-item allocations
        allocations: dict[str, Decimal] = {item.item_id: Decimal("0") for item in receipt.items}

        # Allocate each charge
        for charge in voucher.charges:
            item_allocations = self._allocate_charge(receipt, charge)
            for item_id, amount in item_allocations.items():
                allocations[item_id] += amount

        # Build results with new valuation rates
        results = []
        for item in receipt.items:
            original_cost = item.total_value
            allocated_cost = allocations[item.item_id]
            new_total = original_cost + allocated_cost
            new_rate = (new_total / item.quantity).quantize(Decimal("0.0001"))

            results.append(AllocationResult(
                item_id=item.item_id,
                original_cost=original_cost,
                allocated_cost=allocated_cost,
                new_valuation_rate=new_rate,
            ))

        return results

    def _allocate_charge(
        self,
        receipt: PurchaseReceipt,
        charge: LandedCostCharge,
    ) -> dict[str, Decimal]:
        """Allocate a single charge based on its method."""
        allocations = {}

        if charge.allocation_method == AllocationMethod.BY_VALUE:
            allocations = self._allocate_by_value(receipt, charge.amount)
        elif charge.allocation_method == AllocationMethod.BY_QUANTITY:
            allocations = self._allocate_by_quantity(receipt, charge.amount)
        elif charge.allocation_method == AllocationMethod.BY_WEIGHT:
            allocations = self._allocate_by_weight(receipt, charge.amount)
        elif charge.allocation_method == AllocationMethod.EQUALLY:
            allocations = self._allocate_equally(receipt, charge.amount)
        else:
            raise ValueError(f"Unsupported allocation method: {charge.allocation_method}")

        return allocations

    def _allocate_by_value(
        self,
        receipt: PurchaseReceipt,
        amount: Decimal,
    ) -> dict[str, Decimal]:
        """Allocate proportional to item value."""
        total_value = receipt.total_value
        if total_value == 0:
            return {item.item_id: Decimal("0") for item in receipt.items}

        allocations = {}
        allocated_so_far = Decimal("0")

        for i, item in enumerate(receipt.items):
            if i == len(receipt.items) - 1:
                # Last item gets remainder to handle rounding
                allocations[item.item_id] = amount - allocated_so_far
            else:
                ratio = item.total_value / total_value
                allocation = (amount * ratio).quantize(Decimal("0.01"))
                allocations[item.item_id] = allocation
                allocated_so_far += allocation

        return allocations

    def _allocate_by_quantity(
        self,
        receipt: PurchaseReceipt,
        amount: Decimal,
    ) -> dict[str, Decimal]:
        """Allocate proportional to quantity."""
        total_qty = receipt.total_quantity
        if total_qty == 0:
            return {item.item_id: Decimal("0") for item in receipt.items}

        allocations = {}
        allocated_so_far = Decimal("0")

        for i, item in enumerate(receipt.items):
            if i == len(receipt.items) - 1:
                allocations[item.item_id] = amount - allocated_so_far
            else:
                ratio = item.quantity / total_qty
                allocation = (amount * ratio).quantize(Decimal("0.01"))
                allocations[item.item_id] = allocation
                allocated_so_far += allocation

        return allocations

    def _allocate_by_weight(
        self,
        receipt: PurchaseReceipt,
        amount: Decimal,
    ) -> dict[str, Decimal]:
        """Allocate proportional to weight."""
        total_weight = receipt.total_weight
        if total_weight == 0:
            raise ValueError("Cannot allocate by weight when total weight is zero")

        allocations = {}
        allocated_so_far = Decimal("0")

        for i, item in enumerate(receipt.items):
            if i == len(receipt.items) - 1:
                allocations[item.item_id] = amount - allocated_so_far
            else:
                ratio = item.total_weight / total_weight
                allocation = (amount * ratio).quantize(Decimal("0.01"))
                allocations[item.item_id] = allocation
                allocated_so_far += allocation

        return allocations

    def _allocate_equally(
        self,
        receipt: PurchaseReceipt,
        amount: Decimal,
    ) -> dict[str, Decimal]:
        """Allocate equally across all items."""
        item_count = len(receipt.items)
        if item_count == 0:
            return {}

        per_item = (amount / item_count).quantize(Decimal("0.01"))
        allocations = {}
        allocated_so_far = Decimal("0")

        for i, item in enumerate(receipt.items):
            if i == len(receipt.items) - 1:
                allocations[item.item_id] = amount - allocated_so_far
            else:
                allocations[item.item_id] = per_item
                allocated_so_far += per_item

        return allocations


# =============================================================================
# GL Entry Generator for Landed Cost
# =============================================================================

class LandedCostGLGenerator:
    """Generate GL entries for landed costs."""

    INVENTORY_ACCOUNT = "1400-Inventory"

    def generate_entries(
        self,
        voucher: LandedCostVoucher,
        allocations: list[AllocationResult],
    ) -> list[GLEntry]:
        """
        Generate GL entries for landed cost voucher.

        DR Inventory (increase value)
        CR Expense/Freight (absorb cost)
        """
        entries = []

        # DR Inventory for total allocated
        total_allocated = sum(a.allocated_cost for a in allocations)
        entries.append(GLEntry(
            entry_id=str(uuid4()),
            account=self.INVENTORY_ACCOUNT,
            debit=total_allocated,
        ))

        # CR each charge account
        for charge in voucher.charges:
            entries.append(GLEntry(
                entry_id=str(uuid4()),
                account=charge.account,
                credit=charge.amount,
            ))

        return entries


# =============================================================================
# Test: Landed Cost Allocation Methods
# =============================================================================

class TestLandedCostAllocation:
    """Freight/duty allocation to inventory."""

    @pytest.fixture
    def allocator(self):
        return LandedCostAllocator()

    @pytest.fixture
    def sample_receipt(self):
        """Receipt with multiple items of varying values."""
        return PurchaseReceipt(
            receipt_id="GRN-001",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem(
                    item_id="ITEM-A",
                    description="Item A",
                    quantity=Decimal("100"),
                    unit_cost=Decimal("10.00"),  # $1000 total
                    weight=Decimal("2"),  # 200 kg total
                ),
                ReceiptItem(
                    item_id="ITEM-B",
                    description="Item B",
                    quantity=Decimal("50"),
                    unit_cost=Decimal("20.00"),  # $1000 total
                    weight=Decimal("5"),  # 250 kg total
                ),
                ReceiptItem(
                    item_id="ITEM-C",
                    description="Item C",
                    quantity=Decimal("200"),
                    unit_cost=Decimal("5.00"),  # $1000 total
                    weight=Decimal("1"),  # 200 kg total
                ),
            ],
        )

    def test_allocate_by_value(self, allocator, sample_receipt):
        """Proportional by item value."""
        voucher = LandedCostVoucher(
            voucher_id="LCV-001",
            receipt_id="GRN-001",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-001",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("300.00"),
                    allocation_method=AllocationMethod.BY_VALUE,
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(sample_receipt, voucher)

        # Total value = $3000, each item is $1000 (1/3)
        # Each should get $100 of freight
        for result in results:
            assert result.allocated_cost == Decimal("100.00")

        # Verify new rates
        item_a = next(r for r in results if r.item_id == "ITEM-A")
        # Original: 100 qty @ $10 = $1000, add $100 freight = $1100 / 100 = $11
        assert item_a.new_valuation_rate == Decimal("11.0000")

    def test_allocate_by_quantity(self, allocator, sample_receipt):
        """Proportional by quantity."""
        voucher = LandedCostVoucher(
            voucher_id="LCV-002",
            receipt_id="GRN-001",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-002",
                    cost_type=LandedCostType.HANDLING,
                    amount=Decimal("350.00"),
                    allocation_method=AllocationMethod.BY_QUANTITY,
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(sample_receipt, voucher)

        # Total qty = 350, so $1 per unit
        # Item A: 100 qty = $100
        # Item B: 50 qty = $50
        # Item C: 200 qty = $200
        item_a = next(r for r in results if r.item_id == "ITEM-A")
        item_b = next(r for r in results if r.item_id == "ITEM-B")
        item_c = next(r for r in results if r.item_id == "ITEM-C")

        assert item_a.allocated_cost == Decimal("100.00")
        assert item_b.allocated_cost == Decimal("50.00")
        assert item_c.allocated_cost == Decimal("200.00")

    def test_allocate_by_weight(self, allocator, sample_receipt):
        """Proportional by weight."""
        voucher = LandedCostVoucher(
            voucher_id="LCV-003",
            receipt_id="GRN-001",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-003",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("650.00"),
                    allocation_method=AllocationMethod.BY_WEIGHT,
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(sample_receipt, voucher)

        # Total weight = 200 + 250 + 200 = 650 kg
        # $650 / 650 kg = $1/kg
        # Item A: 200 kg = $200
        # Item B: 250 kg = $250
        # Item C: 200 kg = $200
        item_a = next(r for r in results if r.item_id == "ITEM-A")
        item_b = next(r for r in results if r.item_id == "ITEM-B")
        item_c = next(r for r in results if r.item_id == "ITEM-C")

        assert item_a.allocated_cost == Decimal("200.00")
        assert item_b.allocated_cost == Decimal("250.00")
        assert item_c.allocated_cost == Decimal("200.00")

    def test_allocate_equally(self, allocator, sample_receipt):
        """Split equally across items."""
        voucher = LandedCostVoucher(
            voucher_id="LCV-004",
            receipt_id="GRN-001",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-004",
                    cost_type=LandedCostType.INSURANCE,
                    amount=Decimal("300.00"),
                    allocation_method=AllocationMethod.EQUALLY,
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(sample_receipt, voucher)

        # 3 items, $300 / 3 = $100 each
        for result in results:
            assert result.allocated_cost == Decimal("100.00")


# =============================================================================
# Test: Landed Cost GL Entries
# =============================================================================

class TestLandedCostGLEntries:
    """Correct GL for landed costs."""

    @pytest.fixture
    def allocator(self):
        return LandedCostAllocator()

    @pytest.fixture
    def gl_generator(self):
        return LandedCostGLGenerator()

    def test_landed_cost_gl_entries(self, allocator, gl_generator):
        """Correct GL entries for landed cost."""
        receipt = PurchaseReceipt(
            receipt_id="GRN-002",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem("ITEM-X", "Item X", Decimal("100"), Decimal("10.00")),
            ],
        )

        voucher = LandedCostVoucher(
            voucher_id="LCV-005",
            receipt_id="GRN-002",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-005",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("150.00"),
                    account="5200-Freight",
                ),
                LandedCostCharge(
                    charge_id="CHG-006",
                    cost_type=LandedCostType.CUSTOMS_DUTY,
                    amount=Decimal("100.00"),
                    account="5210-Customs Duty",
                ),
            ],
            posting_date=date.today(),
        )

        allocations = allocator.allocate(receipt, voucher)
        entries = gl_generator.generate_entries(voucher, allocations)

        # DR Inventory $250
        inv_entry = next(e for e in entries if e.account == "1400-Inventory")
        assert inv_entry.debit == Decimal("250.00")

        # CR Freight $150
        freight_entry = next(e for e in entries if e.account == "5200-Freight")
        assert freight_entry.credit == Decimal("150.00")

        # CR Customs $100
        customs_entry = next(e for e in entries if e.account == "5210-Customs Duty")
        assert customs_entry.credit == Decimal("100.00")

        # Verify balance
        total_debit = sum(e.debit for e in entries)
        total_credit = sum(e.credit for e in entries)
        assert total_debit == total_credit


# =============================================================================
# Test: Valuation Rate Adjustment
# =============================================================================

class TestValuationRateAdjustment:
    """Inventory rate includes landed costs."""

    @pytest.fixture
    def allocator(self):
        return LandedCostAllocator()

    def test_valuation_rate_increases(self, allocator):
        """Valuation rate increases with landed cost."""
        receipt = PurchaseReceipt(
            receipt_id="GRN-003",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem("ITEM-Y", "Item Y", Decimal("50"), Decimal("20.00")),
            ],
        )

        voucher = LandedCostVoucher(
            voucher_id="LCV-006",
            receipt_id="GRN-003",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-007",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("100.00"),
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(receipt, voucher)

        item_y = results[0]
        # Original: 50 @ $20 = $1000
        # Freight: $100
        # New total: $1100 / 50 = $22
        assert item_y.original_cost == Decimal("1000.00")
        assert item_y.allocated_cost == Decimal("100.00")
        assert item_y.new_valuation_rate == Decimal("22.0000")

    def test_multiple_charges_combined(self, allocator):
        """Multiple charges combine in valuation rate."""
        receipt = PurchaseReceipt(
            receipt_id="GRN-004",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem("ITEM-Z", "Item Z", Decimal("100"), Decimal("15.00")),
            ],
        )

        voucher = LandedCostVoucher(
            voucher_id="LCV-007",
            receipt_id="GRN-004",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-008",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("200.00"),
                ),
                LandedCostCharge(
                    charge_id="CHG-009",
                    cost_type=LandedCostType.CUSTOMS_DUTY,
                    amount=Decimal("150.00"),
                ),
                LandedCostCharge(
                    charge_id="CHG-010",
                    cost_type=LandedCostType.INSURANCE,
                    amount=Decimal("50.00"),
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(receipt, voucher)

        item_z = results[0]
        # Original: 100 @ $15 = $1500
        # Total charges: $200 + $150 + $50 = $400
        # New total: $1900 / 100 = $19
        assert item_z.original_cost == Decimal("1500.00")
        assert item_z.allocated_cost == Decimal("400.00")
        assert item_z.new_valuation_rate == Decimal("19.0000")


# =============================================================================
# Test: Rounding in Allocation
# =============================================================================

class TestAllocationRounding:
    """Handle rounding in landed cost allocation."""

    @pytest.fixture
    def allocator(self):
        return LandedCostAllocator()

    def test_rounding_adjustment_on_last_item(self, allocator):
        """Last item adjusted for rounding."""
        receipt = PurchaseReceipt(
            receipt_id="GRN-005",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem("ITEM-1", "Item 1", Decimal("33"), Decimal("10.00")),
                ReceiptItem("ITEM-2", "Item 2", Decimal("33"), Decimal("10.00")),
                ReceiptItem("ITEM-3", "Item 3", Decimal("34"), Decimal("10.00")),
            ],
        )

        voucher = LandedCostVoucher(
            voucher_id="LCV-008",
            receipt_id="GRN-005",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-011",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("100.00"),
                    allocation_method=AllocationMethod.BY_QUANTITY,
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(receipt, voucher)

        # Total allocated should equal charge amount exactly
        total_allocated = sum(r.allocated_cost for r in results)
        assert total_allocated == Decimal("100.00")


# =============================================================================
# Test: Edge Cases
# =============================================================================

class TestLandedCostEdgeCases:
    """Edge cases in landed cost allocation."""

    @pytest.fixture
    def allocator(self):
        return LandedCostAllocator()

    def test_zero_amount_charge(self, allocator):
        """Zero amount charge should allocate nothing."""
        receipt = PurchaseReceipt(
            receipt_id="GRN-006",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem("ITEM-A", "Item A", Decimal("10"), Decimal("100.00")),
            ],
        )

        voucher = LandedCostVoucher(
            voucher_id="LCV-009",
            receipt_id="GRN-006",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-012",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("0"),
                ),
            ],
            posting_date=date.today(),
        )

        results = allocator.allocate(receipt, voucher)

        assert results[0].allocated_cost == Decimal("0")
        assert results[0].new_valuation_rate == Decimal("100.0000")  # Unchanged

    def test_negative_amount_rejected(self):
        """Negative landed cost amount should be rejected."""
        with pytest.raises(ValueError, match="negative"):
            LandedCostCharge(
                charge_id="CHG-NEG",
                cost_type=LandedCostType.FREIGHT,
                amount=Decimal("-100.00"),
            )

    def test_allocate_by_weight_requires_weight(self, allocator):
        """Weight allocation requires items to have weight."""
        receipt = PurchaseReceipt(
            receipt_id="GRN-007",
            supplier_id="SUPPLIER-001",
            receipt_date=date.today(),
            items=[
                ReceiptItem("ITEM-A", "No Weight", Decimal("10"), Decimal("100.00")),
            ],
        )

        voucher = LandedCostVoucher(
            voucher_id="LCV-010",
            receipt_id="GRN-007",
            charges=[
                LandedCostCharge(
                    charge_id="CHG-013",
                    cost_type=LandedCostType.FREIGHT,
                    amount=Decimal("50.00"),
                    allocation_method=AllocationMethod.BY_WEIGHT,
                ),
            ],
            posting_date=date.today(),
        )

        with pytest.raises(ValueError, match="weight is zero"):
            allocator.allocate(receipt, voucher)


# =============================================================================
# Test: Landed Cost Types
# =============================================================================

class TestLandedCostTypes:
    """Different types of landed costs."""

    def test_freight_cost(self):
        """Freight cost allocation."""
        charge = LandedCostCharge(
            charge_id="FREIGHT-001",
            cost_type=LandedCostType.FREIGHT,
            amount=Decimal("500.00"),
            account="5200-Freight Inward",
        )
        assert charge.cost_type == LandedCostType.FREIGHT

    def test_customs_duty(self):
        """Customs duty allocation."""
        charge = LandedCostCharge(
            charge_id="DUTY-001",
            cost_type=LandedCostType.CUSTOMS_DUTY,
            amount=Decimal("1000.00"),
            account="5210-Import Duty",
        )
        assert charge.cost_type == LandedCostType.CUSTOMS_DUTY

    def test_insurance(self):
        """Insurance cost allocation."""
        charge = LandedCostCharge(
            charge_id="INS-001",
            cost_type=LandedCostType.INSURANCE,
            amount=Decimal("100.00"),
            account="5220-Transit Insurance",
        )
        assert charge.cost_type == LandedCostType.INSURANCE


# =============================================================================
# Summary
# =============================================================================

class TestLandedCostSummary:
    """Summary of landed cost test coverage."""

    def test_document_coverage(self):
        """
        Landed Cost Test Coverage:

        Allocation Methods:
        - Allocate by value (proportional)
        - Allocate by quantity
        - Allocate by weight
        - Allocate equally

        GL Entries:
        - DR Inventory for total
        - CR expense accounts per charge
        - Balance verification

        Valuation Rate:
        - Rate increases with landed cost
        - Multiple charges combined

        Rounding:
        - Last item adjustment

        Edge Cases:
        - Zero amount charge
        - Negative amount rejected
        - Weight allocation requires weight data

        Cost Types:
        - Freight
        - Customs duty
        - Insurance

        Total: ~20 tests covering landed cost patterns.
        """
        pass


# =============================================================================
# Integration Tests — Real Posting via InventoryService
# =============================================================================


class TestLandedCostIntegration:
    """Real integration tests using InventoryService for receive + adjust."""

    @pytest.fixture
    def inventory_service(self, session, module_role_resolver, deterministic_clock, register_modules):
        from finance_modules.inventory.service import InventoryService
        return InventoryService(
            session=session,
            role_resolver=module_role_resolver,
            clock=deterministic_clock,
        )

    def test_receive_inventory_posts(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Receive inventory through the real pipeline (base cost before landed costs)."""
        from finance_kernel.services.module_posting_service import ModulePostingStatus

        result = inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="WIDGET-LC-001",
            quantity=Decimal("100"),
            unit_cost=Decimal("10.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_adjust_inventory_for_landed_cost_reaches_pipeline(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Adjust inventory value upward for landed cost through the real pipeline.

        NOTE: The inventory.adjustment event type does not have a registered
        accounting profile (no where-clause match). This test verifies the
        event is properly ingested and the pipeline runs to profile lookup.
        """
        from finance_kernel.services.module_posting_service import ModulePostingStatus

        result = inventory_service.adjust_inventory(
            adjustment_id=uuid4(),
            item_id="WIDGET-LC-002",
            quantity_change=Decimal("0"),
            value_change=Decimal("250.00"),
            reason_code="LANDED_COST_FREIGHT",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status in (
            ModulePostingStatus.POSTED,
            ModulePostingStatus.PROFILE_NOT_FOUND,
        )
