"""ORM round-trip tests for Inventory module."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.inventory.orm import (
    ABCClassificationModel,
    CycleCountModel,
    InventoryAdjustmentModel,
    InventoryIssueModel,
    InventoryReceiptModel,
    ReorderPointModel,
    StockTransferModel,
)

# ===================================================================
# InventoryReceiptModel
# ===================================================================

class TestInventoryReceiptModelORM:

    def test_create_and_query(self, session, test_actor_id):
        item_id = uuid4()
        location_id = uuid4()
        source_id = uuid4()
        receipt = InventoryReceiptModel(
            item_id=item_id,
            location_id=location_id,
            receipt_date=date(2024, 5, 1),
            quantity=Decimal("100"),
            unit_cost=Decimal("25.50"),
            total_cost=Decimal("2550.00"),
            status="received",
            source_type="purchase_order",
            source_id=source_id,
            lot_number="LOT-2024-001",
            created_by_id=test_actor_id,
        )
        session.add(receipt)
        session.flush()

        queried = session.get(InventoryReceiptModel, receipt.id)
        assert queried is not None
        assert queried.item_id == item_id
        assert queried.location_id == location_id
        assert queried.receipt_date == date(2024, 5, 1)
        assert queried.quantity == Decimal("100")
        assert queried.unit_cost == Decimal("25.50")
        assert queried.total_cost == Decimal("2550.00")
        assert queried.status == "received"
        assert queried.source_type == "purchase_order"
        assert queried.source_id == source_id
        assert queried.lot_number == "LOT-2024-001"

    def test_nullable_optional_fields(self, session, test_actor_id):
        receipt = InventoryReceiptModel(
            item_id=uuid4(),
            location_id=uuid4(),
            receipt_date=date(2024, 6, 1),
            quantity=Decimal("50"),
            unit_cost=Decimal("10.00"),
            total_cost=Decimal("500.00"),
            source_type=None,
            source_id=None,
            lot_number=None,
            created_by_id=test_actor_id,
        )
        session.add(receipt)
        session.flush()

        queried = session.get(InventoryReceiptModel, receipt.id)
        assert queried.source_type is None
        assert queried.source_id is None
        assert queried.lot_number is None

    def test_default_status(self, session, test_actor_id):
        receipt = InventoryReceiptModel(
            item_id=uuid4(),
            location_id=uuid4(),
            receipt_date=date(2024, 7, 1),
            quantity=Decimal("10"),
            unit_cost=Decimal("5.00"),
            total_cost=Decimal("50.00"),
            created_by_id=test_actor_id,
        )
        session.add(receipt)
        session.flush()

        queried = session.get(InventoryReceiptModel, receipt.id)
        assert queried.status == "pending"


# ===================================================================
# InventoryIssueModel
# ===================================================================

class TestInventoryIssueModelORM:

    def test_create_and_query(self, session, test_actor_id):
        item_id = uuid4()
        location_id = uuid4()
        dest_id = uuid4()
        issue = InventoryIssueModel(
            item_id=item_id,
            location_id=location_id,
            issue_date=date(2024, 5, 15),
            quantity=Decimal("25"),
            unit_cost=Decimal("25.50"),
            total_cost=Decimal("637.50"),
            status="issued",
            destination_type="work_order",
            destination_id=dest_id,
            lot_number="LOT-2024-001",
            created_by_id=test_actor_id,
        )
        session.add(issue)
        session.flush()

        queried = session.get(InventoryIssueModel, issue.id)
        assert queried is not None
        assert queried.item_id == item_id
        assert queried.location_id == location_id
        assert queried.issue_date == date(2024, 5, 15)
        assert queried.quantity == Decimal("25")
        assert queried.unit_cost == Decimal("25.50")
        assert queried.total_cost == Decimal("637.50")
        assert queried.status == "issued"
        assert queried.destination_type == "work_order"
        assert queried.destination_id == dest_id
        assert queried.lot_number == "LOT-2024-001"

    def test_nullable_optional_fields(self, session, test_actor_id):
        issue = InventoryIssueModel(
            item_id=uuid4(),
            location_id=uuid4(),
            issue_date=date(2024, 6, 15),
            quantity=Decimal("10"),
            unit_cost=Decimal("12.00"),
            total_cost=Decimal("120.00"),
            destination_type=None,
            destination_id=None,
            lot_number=None,
            created_by_id=test_actor_id,
        )
        session.add(issue)
        session.flush()

        queried = session.get(InventoryIssueModel, issue.id)
        assert queried.destination_type is None
        assert queried.destination_id is None
        assert queried.lot_number is None

    def test_default_status(self, session, test_actor_id):
        issue = InventoryIssueModel(
            item_id=uuid4(),
            location_id=uuid4(),
            issue_date=date(2024, 7, 15),
            quantity=Decimal("5"),
            unit_cost=Decimal("8.00"),
            total_cost=Decimal("40.00"),
            created_by_id=test_actor_id,
        )
        session.add(issue)
        session.flush()

        queried = session.get(InventoryIssueModel, issue.id)
        assert queried.status == "requested"


# ===================================================================
# InventoryAdjustmentModel
# ===================================================================

class TestInventoryAdjustmentModelORM:

    def test_create_and_query(self, session, test_actor_id):
        item_id = uuid4()
        location_id = uuid4()
        adj = InventoryAdjustmentModel(
            item_id=item_id,
            location_id=location_id,
            adjustment_date=date(2024, 8, 1),
            quantity_change=Decimal("-5"),
            value_change=Decimal("-127.50"),
            reason_code="damage",
            reference="INV-ADJ-2024-001",
            created_by_id=test_actor_id,
        )
        session.add(adj)
        session.flush()

        queried = session.get(InventoryAdjustmentModel, adj.id)
        assert queried is not None
        assert queried.item_id == item_id
        assert queried.location_id == location_id
        assert queried.adjustment_date == date(2024, 8, 1)
        assert queried.quantity_change == Decimal("-5")
        assert queried.value_change == Decimal("-127.50")
        assert queried.reason_code == "damage"
        assert queried.reference == "INV-ADJ-2024-001"

    def test_nullable_reference(self, session, test_actor_id):
        adj = InventoryAdjustmentModel(
            item_id=uuid4(),
            location_id=uuid4(),
            adjustment_date=date(2024, 9, 1),
            quantity_change=Decimal("10"),
            value_change=Decimal("200.00"),
            reason_code="cycle_count",
            reference=None,
            created_by_id=test_actor_id,
        )
        session.add(adj)
        session.flush()

        queried = session.get(InventoryAdjustmentModel, adj.id)
        assert queried.reference is None

    def test_positive_and_negative_adjustments(self, session, test_actor_id):
        pos = InventoryAdjustmentModel(
            item_id=uuid4(),
            location_id=uuid4(),
            adjustment_date=date(2024, 10, 1),
            quantity_change=Decimal("15"),
            value_change=Decimal("375.00"),
            reason_code="found_stock",
            created_by_id=test_actor_id,
        )
        neg = InventoryAdjustmentModel(
            item_id=uuid4(),
            location_id=uuid4(),
            adjustment_date=date(2024, 10, 1),
            quantity_change=Decimal("-3"),
            value_change=Decimal("-75.00"),
            reason_code="write_off",
            created_by_id=test_actor_id,
        )
        session.add_all([pos, neg])
        session.flush()
        assert session.get(InventoryAdjustmentModel, pos.id).quantity_change == Decimal("15")
        assert session.get(InventoryAdjustmentModel, neg.id).quantity_change == Decimal("-3")


# ===================================================================
# StockTransferModel
# ===================================================================

class TestStockTransferModelORM:

    def test_create_and_query(self, session, test_actor_id):
        item_id = uuid4()
        from_loc = uuid4()
        to_loc = uuid4()
        xfer = StockTransferModel(
            item_id=item_id,
            from_location_id=from_loc,
            to_location_id=to_loc,
            transfer_date=date(2024, 5, 20),
            quantity=Decimal("30"),
            status="in_transit",
            lot_number="LOT-XFER-001",
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()

        queried = session.get(StockTransferModel, xfer.id)
        assert queried is not None
        assert queried.item_id == item_id
        assert queried.from_location_id == from_loc
        assert queried.to_location_id == to_loc
        assert queried.transfer_date == date(2024, 5, 20)
        assert queried.quantity == Decimal("30")
        assert queried.status == "in_transit"
        assert queried.lot_number == "LOT-XFER-001"

    def test_default_status(self, session, test_actor_id):
        xfer = StockTransferModel(
            item_id=uuid4(),
            from_location_id=uuid4(),
            to_location_id=uuid4(),
            transfer_date=date(2024, 6, 20),
            quantity=Decimal("10"),
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()

        queried = session.get(StockTransferModel, xfer.id)
        assert queried.status == "requested"

    def test_nullable_lot_number(self, session, test_actor_id):
        xfer = StockTransferModel(
            item_id=uuid4(),
            from_location_id=uuid4(),
            to_location_id=uuid4(),
            transfer_date=date(2024, 7, 20),
            quantity=Decimal("5"),
            lot_number=None,
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()

        queried = session.get(StockTransferModel, xfer.id)
        assert queried.lot_number is None


# ===================================================================
# CycleCountModel
# ===================================================================

class TestCycleCountModelORM:

    def test_create_and_query(self, session, test_actor_id):
        counter_id = uuid4()
        cc = CycleCountModel(
            count_date=date(2024, 8, 15),
            item_id="ITEM-SKU-001",
            location_id="WH-A-01",
            expected_quantity=Decimal("100"),
            actual_quantity=Decimal("97"),
            variance_quantity=Decimal("-3"),
            variance_amount=Decimal("-75.00"),
            currency="USD",
            counter_id=counter_id,
            notes="Found 3 units missing from shelf A-3",
            created_by_id=test_actor_id,
        )
        session.add(cc)
        session.flush()

        queried = session.get(CycleCountModel, cc.id)
        assert queried is not None
        assert queried.count_date == date(2024, 8, 15)
        assert queried.item_id == "ITEM-SKU-001"
        assert queried.location_id == "WH-A-01"
        assert queried.expected_quantity == Decimal("100")
        assert queried.actual_quantity == Decimal("97")
        assert queried.variance_quantity == Decimal("-3")
        assert queried.variance_amount == Decimal("-75.00")
        assert queried.currency == "USD"
        assert queried.counter_id == counter_id
        assert "3 units missing" in queried.notes

    def test_nullable_optional_fields(self, session, test_actor_id):
        cc = CycleCountModel(
            count_date=date(2024, 9, 15),
            item_id="ITEM-SKU-002",
            location_id=None,
            expected_quantity=Decimal("50"),
            actual_quantity=Decimal("50"),
            variance_quantity=Decimal("0"),
            variance_amount=Decimal("0"),
            counter_id=None,
            created_by_id=test_actor_id,
        )
        session.add(cc)
        session.flush()

        queried = session.get(CycleCountModel, cc.id)
        assert queried.location_id is None
        assert queried.counter_id is None

    def test_default_currency_and_notes(self, session, test_actor_id):
        cc = CycleCountModel(
            count_date=date(2024, 10, 15),
            item_id="ITEM-SKU-003",
            expected_quantity=Decimal("200"),
            actual_quantity=Decimal("200"),
            variance_quantity=Decimal("0"),
            variance_amount=Decimal("0"),
            created_by_id=test_actor_id,
        )
        session.add(cc)
        session.flush()

        queried = session.get(CycleCountModel, cc.id)
        assert queried.currency == "USD"
        assert queried.notes == ""


# ===================================================================
# ABCClassificationModel
# ===================================================================

class TestABCClassificationModelORM:

    def test_create_and_query(self, session, test_actor_id):
        abc = ABCClassificationModel(
            item_id="SKU-HIGH-001",
            classification="A",
            annual_value=Decimal("500000.00"),
            cumulative_percent=Decimal("35.50"),
            as_of_date=date(2024, 12, 31),
            created_by_id=test_actor_id,
        )
        session.add(abc)
        session.flush()

        queried = session.get(ABCClassificationModel, abc.id)
        assert queried is not None
        assert queried.item_id == "SKU-HIGH-001"
        assert queried.classification == "A"
        assert queried.annual_value == Decimal("500000.00")
        assert queried.cumulative_percent == Decimal("35.50")
        assert queried.as_of_date == date(2024, 12, 31)

    def test_all_classifications(self, session, test_actor_id):
        for cls_val, val, pct in [
            ("A", Decimal("100000"), Decimal("40")),
            ("B", Decimal("30000"), Decimal("70")),
            ("C", Decimal("5000"), Decimal("95")),
        ]:
            abc = ABCClassificationModel(
                item_id=f"SKU-{cls_val}-{uuid4().hex[:4]}",
                classification=cls_val,
                annual_value=val,
                cumulative_percent=pct,
                as_of_date=date(2024, 12, 31),
                created_by_id=test_actor_id,
            )
            session.add(abc)
        session.flush()
        # Verify all three were persisted successfully (no errors)


# ===================================================================
# ReorderPointModel
# ===================================================================

class TestReorderPointModelORM:

    def test_create_and_query(self, session, test_actor_id):
        rop = ReorderPointModel(
            item_id="SKU-REORDER-001",
            location_id="WH-MAIN",
            reorder_point=Decimal("50"),
            safety_stock=Decimal("20"),
            eoq=Decimal("200"),
            avg_daily_usage=Decimal("5.5"),
            lead_time_days=7,
            created_by_id=test_actor_id,
        )
        session.add(rop)
        session.flush()

        queried = session.get(ReorderPointModel, rop.id)
        assert queried is not None
        assert queried.item_id == "SKU-REORDER-001"
        assert queried.location_id == "WH-MAIN"
        assert queried.reorder_point == Decimal("50")
        assert queried.safety_stock == Decimal("20")
        assert queried.eoq == Decimal("200")
        assert queried.avg_daily_usage == Decimal("5.5")
        assert queried.lead_time_days == 7

    def test_nullable_location(self, session, test_actor_id):
        rop = ReorderPointModel(
            item_id="SKU-REORDER-002",
            location_id=None,
            reorder_point=Decimal("30"),
            safety_stock=Decimal("10"),
            eoq=Decimal("100"),
            avg_daily_usage=Decimal("3.0"),
            lead_time_days=14,
            created_by_id=test_actor_id,
        )
        session.add(rop)
        session.flush()

        queried = session.get(ReorderPointModel, rop.id)
        assert queried.location_id is None

    def test_multiple_items(self, session, test_actor_id):
        for i in range(3):
            rop = ReorderPointModel(
                item_id=f"SKU-MULTI-{i:03d}",
                location_id="WH-CENTRAL",
                reorder_point=Decimal(str(10 * (i + 1))),
                safety_stock=Decimal(str(5 * (i + 1))),
                eoq=Decimal(str(50 * (i + 1))),
                avg_daily_usage=Decimal(str(2 * (i + 1))),
                lead_time_days=7 + i,
                created_by_id=test_actor_id,
            )
            session.add(rop)
        session.flush()
        # All three persisted without error
