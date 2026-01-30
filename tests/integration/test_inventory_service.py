"""
Integration tests for InventoryService — engine + kernel end-to-end.

Tests verify that:
1. Engine calls (ValuationLayer, VarianceCalculator) produce correct values
2. Engine results feed into ModulePostingService payloads
3. Journal entries are created atomically with engine artifacts (cost lots, links)
4. Transaction boundary: commit on success, rollback on failure
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def inventory_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide InventoryService for integration testing."""
    from finance_modules.inventory.service import InventoryService

    return InventoryService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Test 1: Receive Inventory — Happy Path
# =============================================================================


class TestReceiveInventory:
    """Receive inventory: engine creates cost lot, kernel posts journal."""

    def test_receive_creates_lot_and_journal(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Receive should create a cost lot via ValuationLayer and post."""
        result = inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="WIDGET-001",
            quantity=Decimal("100"),
            unit_cost=Decimal("25.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert result.profile_name == "InventoryReceipt"
        assert len(result.journal_entry_ids) > 0

    def test_receive_lot_is_available_for_consumption(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """After receipt, lot should be available for FIFO consumption."""
        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="BOLT-M8",
            quantity=Decimal("50"),
            unit_cost=Decimal("10.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        # Verify lot exists in valuation layer
        available = inventory_service._valuation.get_total_available_quantity("BOLT-M8")
        assert available.value == Decimal("50")


# =============================================================================
# Test 2: Issue Inventory — FIFO Consumption
# =============================================================================


class TestIssueInventory:
    """Issue inventory: engine consumes cost lots, kernel posts COGS."""

    def test_issue_sale_consumes_fifo_and_posts(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Issue for sale should consume FIFO lots and post COGS entry."""
        eff_date = deterministic_clock.now().date()

        # Receive inventory first
        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="WIDGET-001",
            quantity=Decimal("100"),
            unit_cost=Decimal("25.00"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        # Issue for sale
        consumption, result = inventory_service.issue_sale(
            issue_id=uuid4(),
            item_id="WIDGET-001",
            quantity=Decimal("30"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert consumption.total_cost.amount == Decimal("750.00")
        assert consumption.total_quantity.value == Decimal("30")
        assert consumption.cost_method.value == "fifo"

    def test_issue_fifo_order_across_lots(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """FIFO should consume oldest lot first."""
        eff_date = deterministic_clock.now().date()

        # Receive lot 1: 10 @ $20
        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="FIFO-TEST",
            quantity=Decimal("10"),
            unit_cost=Decimal("20.00"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        # Receive lot 2: 10 @ $30
        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="FIFO-TEST",
            quantity=Decimal("10"),
            unit_cost=Decimal("30.00"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        # Issue 15 — should consume all 10 @ $20 + 5 @ $30
        consumption, result = inventory_service.issue_sale(
            issue_id=uuid4(),
            item_id="FIFO-TEST",
            quantity=Decimal("15"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        assert result.is_success
        # Total: (10 × $20) + (5 × $30) = $200 + $150 = $350
        assert consumption.total_cost.amount == Decimal("350.00")
        assert consumption.layer_count == 2

    def test_issue_production_consumes_and_posts(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Issue for production should consume lots and post WIP debit."""
        eff_date = deterministic_clock.now().date()

        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="RAW-STEEL",
            quantity=Decimal("200"),
            unit_cost=Decimal("12.50"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        consumption, result = inventory_service.issue_production(
            issue_id=uuid4(),
            item_id="RAW-STEEL",
            quantity=Decimal("50"),
            work_order_id=uuid4(),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        assert result.is_success
        assert consumption.total_cost.amount == Decimal("625.00")

    def test_issue_scrap_consumes_and_posts(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Issue for scrap should consume lots and post scrap expense."""
        eff_date = deterministic_clock.now().date()

        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="SCRAP-ITEM",
            quantity=Decimal("100"),
            unit_cost=Decimal("5.00"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        consumption, result = inventory_service.issue_scrap(
            issue_id=uuid4(),
            item_id="SCRAP-ITEM",
            quantity=Decimal("10"),
            reason_code="DAMAGED",
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        assert result.is_success
        assert consumption.total_cost.amount == Decimal("50.00")


# =============================================================================
# Test 3: Receive with Variance
# =============================================================================


class TestReceiveWithVariance:
    """Receive with price variance: engine computes PPV, kernel posts."""

    def test_unfavorable_variance(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Actual > standard should produce unfavorable variance."""
        variance_result, post_result = inventory_service.receive_with_variance(
            receipt_id=uuid4(),
            item_id="WIDGET-002",
            quantity=Decimal("100"),
            actual_unit_cost=Decimal("26.00"),
            standard_unit_cost=Decimal("25.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert post_result.is_success
        # Variance = (26 - 25) × 100 = $100 unfavorable
        assert variance_result.variance.amount == Decimal("100.00")
        assert not variance_result.is_favorable

    def test_favorable_variance(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Actual < standard should produce favorable variance."""
        variance_result, post_result = inventory_service.receive_with_variance(
            receipt_id=uuid4(),
            item_id="WIDGET-003",
            quantity=Decimal("50"),
            actual_unit_cost=Decimal("9.50"),
            standard_unit_cost=Decimal("10.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert post_result.is_success
        # Variance = (9.50 - 10.00) × 50 = -$25.00 favorable
        assert variance_result.variance.amount == Decimal("-25.00")
        assert variance_result.is_favorable


# =============================================================================
# Test 4: Adjustment
# =============================================================================


class TestAdjustInventory:
    """Adjust inventory: direct posting, no engine call."""

    def test_positive_adjustment(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Positive adjustment should post successfully."""
        result = inventory_service.adjust_inventory(
            adjustment_id=uuid4(),
            item_id="ADJ-ITEM",
            quantity_change=Decimal("5"),
            value_change=Decimal("50.00"),
            reason_code="CYCLE_COUNT",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.is_success


# =============================================================================
# Test 5: Revalue Inventory
# =============================================================================


class TestRevalueInventory:
    """Revalue inventory: engine computes variance, kernel posts."""

    def test_revalue_posts_with_variance(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Revaluation should compute variance and post adjustment."""
        variance_result, post_result = inventory_service.revalue_inventory(
            item_id="REVAL-ITEM",
            old_value=Decimal("100.00"),
            new_value=Decimal("90.00"),
            quantity=Decimal("10"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert post_result.is_success
        # Variance = (90 - 100) × 10 = -$100 (standard_cost_variance: actual - standard)
        assert variance_result.variance.amount != Decimal("0")


# =============================================================================
# Test 6: Receive from Production
# =============================================================================


class TestReceiveFromProduction:
    """Receive finished goods from production."""

    def test_receive_from_production_creates_lot(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """FG receipt should create cost lot and post."""
        result = inventory_service.receive_from_production(
            receipt_id=uuid4(),
            item_id="FG-WIDGET",
            quantity=Decimal("50"),
            unit_cost=Decimal("45.00"),
            work_order_id=uuid4(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.is_success

        # Verify lot was created
        available = inventory_service._valuation.get_total_available_quantity("FG-WIDGET")
        assert available.value == Decimal("50")


# =============================================================================
# Test 7: Transfer
# =============================================================================


class TestTransferInventory:
    """Inter-location transfer: consume from source, receive at destination."""

    def test_transfer_issue_consumes_lots(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Transfer issue should consume from source location."""
        eff_date = deterministic_clock.now().date()

        # Receive at source location
        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="TRANSFER-ITEM",
            quantity=Decimal("100"),
            unit_cost=Decimal("15.00"),
            effective_date=eff_date,
            actor_id=test_actor_id,
            warehouse="WH-01",
        )

        # Transfer out
        consumption, result = inventory_service.issue_transfer(
            issue_id=uuid4(),
            item_id="TRANSFER-ITEM",
            quantity=Decimal("40"),
            from_location="WH-01",
            to_location="WH-02",
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        assert result.is_success
        assert consumption.total_cost.amount == Decimal("600.00")

    def test_transfer_receive_posts(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Transfer receive should post at destination."""
        result = inventory_service.receive_transfer(
            transfer_id=uuid4(),
            item_id="TRANSFER-ITEM-2",
            quantity=Decimal("40"),
            unit_cost=Decimal("15.00"),
            to_location="WH-02",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.is_success


# =============================================================================
# Test 8: Insufficient Inventory
# =============================================================================


class TestInsufficientInventory:
    """Issue with insufficient stock should raise, not post."""

    def test_issue_more_than_available_raises(
        self, inventory_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Issuing more than available should raise InsufficientInventoryError."""
        from finance_kernel.exceptions import InsufficientInventoryError

        eff_date = deterministic_clock.now().date()

        inventory_service.receive_inventory(
            receipt_id=uuid4(),
            item_id="LOW-STOCK",
            quantity=Decimal("5"),
            unit_cost=Decimal("100.00"),
            effective_date=eff_date,
            actor_id=test_actor_id,
        )

        with pytest.raises(InsufficientInventoryError):
            inventory_service.issue_sale(
                issue_id=uuid4(),
                item_id="LOW-STOCK",
                quantity=Decimal("10"),
                effective_date=eff_date,
                actor_id=test_actor_id,
            )
