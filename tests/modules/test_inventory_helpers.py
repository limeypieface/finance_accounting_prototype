"""
Tests for Inventory Helper Pure Functions.

Validates classify_abc, calculate_reorder_point, calculate_eoq.
All tests are pure — no database, no session.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from finance_modules.inventory.helpers import (
    calculate_eoq,
    calculate_reorder_point,
    classify_abc,
)
from finance_modules.inventory.models import ItemValue

# =============================================================================
# ABC Classification Tests
# =============================================================================


class TestClassifyABC:

    def test_basic_classification(self):
        items = [
            ItemValue(item_id="A1", annual_value=Decimal("50000")),
            ItemValue(item_id="B1", annual_value=Decimal("10000")),
            ItemValue(item_id="B2", annual_value=Decimal("8000")),
            ItemValue(item_id="C1", annual_value=Decimal("1000")),
            ItemValue(item_id="C2", annual_value=Decimal("500")),
            ItemValue(item_id="C3", annual_value=Decimal("200")),
        ]
        result = classify_abc(items)
        assert result["A1"] == "A"
        assert result["C3"] == "C"

    def test_single_item(self):
        items = [ItemValue(item_id="ONLY", annual_value=Decimal("100"))]
        result = classify_abc(items)
        assert result["ONLY"] == "A"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            classify_abc([])

    def test_all_zero_value(self):
        items = [
            ItemValue(item_id="Z1", annual_value=Decimal("0")),
            ItemValue(item_id="Z2", annual_value=Decimal("0")),
        ]
        result = classify_abc(items)
        assert all(v == "C" for v in result.values())

    def test_custom_thresholds(self):
        items = [
            ItemValue(item_id="H1", annual_value=Decimal("70")),
            ItemValue(item_id="H2", annual_value=Decimal("20")),
            ItemValue(item_id="H3", annual_value=Decimal("10")),
        ]
        result = classify_abc(items, a_pct=Decimal("70"), b_pct=Decimal("20"))
        assert result["H1"] == "A"
        assert result["H2"] == "B"
        assert result["H3"] == "C"

    def test_invalid_percentages(self):
        items = [ItemValue(item_id="X", annual_value=Decimal("100"))]
        with pytest.raises(ValueError, match="exceeds"):
            classify_abc(items, a_pct=Decimal("90"), b_pct=Decimal("20"))


# =============================================================================
# Reorder Point Tests
# =============================================================================


class TestCalculateReorderPoint:

    def test_basic_rop(self):
        result = calculate_reorder_point(
            avg_daily_usage=Decimal("10"),
            lead_time_days=5,
            safety_stock=Decimal("20"),
        )
        # ROP = (10 * 5) + 20 = 70
        assert result == Decimal("70")

    def test_zero_safety_stock(self):
        result = calculate_reorder_point(
            avg_daily_usage=Decimal("25"),
            lead_time_days=3,
            safety_stock=Decimal("0"),
        )
        assert result == Decimal("75")

    def test_zero_usage(self):
        result = calculate_reorder_point(
            avg_daily_usage=Decimal("0"),
            lead_time_days=10,
            safety_stock=Decimal("50"),
        )
        assert result == Decimal("50")

    def test_negative_usage_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            calculate_reorder_point(
                avg_daily_usage=Decimal("-5"),
                lead_time_days=3,
                safety_stock=Decimal("10"),
            )

    def test_negative_lead_time_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            calculate_reorder_point(
                avg_daily_usage=Decimal("10"),
                lead_time_days=-1,
                safety_stock=Decimal("10"),
            )


# =============================================================================
# EOQ Tests
# =============================================================================


class TestCalculateEOQ:

    def test_basic_eoq(self):
        # EOQ = sqrt(2 * 1000 * 50 / 2) = sqrt(50000) ≈ 223.61
        result = calculate_eoq(
            annual_demand=Decimal("1000"),
            order_cost=Decimal("50"),
            holding_cost=Decimal("2"),
        )
        assert Decimal("223") <= result <= Decimal("224")

    def test_high_demand(self):
        result = calculate_eoq(
            annual_demand=Decimal("10000"),
            order_cost=Decimal("100"),
            holding_cost=Decimal("5"),
        )
        # EOQ = sqrt(2 * 10000 * 100 / 5) = sqrt(400000) ≈ 632.46
        assert Decimal("632") <= result <= Decimal("633")

    def test_zero_demand_raises(self):
        with pytest.raises(ValueError, match="positive"):
            calculate_eoq(
                annual_demand=Decimal("0"),
                order_cost=Decimal("50"),
                holding_cost=Decimal("2"),
            )

    def test_zero_order_cost_raises(self):
        with pytest.raises(ValueError, match="positive"):
            calculate_eoq(
                annual_demand=Decimal("1000"),
                order_cost=Decimal("0"),
                holding_cost=Decimal("2"),
            )

    def test_zero_holding_cost_raises(self):
        with pytest.raises(ValueError, match="positive"):
            calculate_eoq(
                annual_demand=Decimal("1000"),
                order_cost=Decimal("50"),
                holding_cost=Decimal("0"),
            )
