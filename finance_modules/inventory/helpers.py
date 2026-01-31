"""
Inventory Pure Functions.

Stateless calculations for ABC classification, reorder points, and EOQ.
No I/O, no session, no clock — pure input/output.
"""

from __future__ import annotations

from decimal import Decimal
from math import sqrt
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from finance_modules.inventory.models import ItemValue


def classify_abc(
    items: Sequence[ItemValue],
    a_pct: Decimal = Decimal("80"),
    b_pct: Decimal = Decimal("15"),
) -> dict[str, str]:
    """
    Classify items into A/B/C categories by cumulative annual value.

    Args:
        items: Sequence of ItemValue(item_id, annual_value).
        a_pct: Cumulative % threshold for A items (default 80).
        b_pct: Cumulative % threshold for B items (default 15, so A+B=95).

    Returns:
        Mapping of item_id -> "A" | "B" | "C".

    Raises:
        ValueError: If items is empty or percentages are invalid.
    """
    if not items:
        raise ValueError("Items sequence must not be empty")
    if a_pct + b_pct > Decimal("100"):
        raise ValueError(f"a_pct ({a_pct}) + b_pct ({b_pct}) exceeds 100%")

    total_value = sum(item.annual_value for item in items)
    if total_value == 0:
        return {item.item_id: "C" for item in items}

    # Sort descending by annual value
    sorted_items = sorted(items, key=lambda x: x.annual_value, reverse=True)

    result: dict[str, str] = {}
    cumulative = Decimal("0")
    a_threshold = total_value * a_pct / Decimal("100")
    ab_threshold = total_value * (a_pct + b_pct) / Decimal("100")

    for item in sorted_items:
        prev_cumulative = cumulative
        cumulative += item.annual_value
        if prev_cumulative < a_threshold:
            result[item.item_id] = "A"
        elif prev_cumulative < ab_threshold:
            result[item.item_id] = "B"
        else:
            result[item.item_id] = "C"

    return result


def calculate_reorder_point(
    avg_daily_usage: Decimal,
    lead_time_days: int,
    safety_stock: Decimal,
) -> Decimal:
    """
    Calculate reorder point (ROP).

    ROP = (avg_daily_usage * lead_time_days) + safety_stock

    Args:
        avg_daily_usage: Average units consumed per day.
        lead_time_days: Supplier lead time in days.
        safety_stock: Buffer stock quantity.

    Returns:
        Reorder point quantity.

    Raises:
        ValueError: If inputs are negative.
    """
    if avg_daily_usage < 0:
        raise ValueError(f"avg_daily_usage must be non-negative, got {avg_daily_usage}")
    if lead_time_days < 0:
        raise ValueError(f"lead_time_days must be non-negative, got {lead_time_days}")
    if safety_stock < 0:
        raise ValueError(f"safety_stock must be non-negative, got {safety_stock}")

    return (avg_daily_usage * lead_time_days) + safety_stock


def calculate_eoq(
    annual_demand: Decimal,
    order_cost: Decimal,
    holding_cost: Decimal,
) -> Decimal:
    """
    Calculate Economic Order Quantity (EOQ).

    EOQ = sqrt(2 * D * S / H)

    Args:
        annual_demand: Annual demand in units (D).
        order_cost: Cost per order placed (S).
        holding_cost: Annual holding cost per unit (H).

    Returns:
        Optimal order quantity (rounded to 2 decimal places).

    Raises:
        ValueError: If any input is non-positive.
    """
    if annual_demand <= 0:
        raise ValueError(f"annual_demand must be positive, got {annual_demand}")
    if order_cost <= 0:
        raise ValueError(f"order_cost must be positive, got {order_cost}")
    if holding_cost <= 0:
        raise ValueError(f"holding_cost must be positive, got {holding_cost}")

    numerator = Decimal("2") * annual_demand * order_cost
    eoq_float = sqrt(float(numerator / holding_cost))
    return Decimal(str(round(eoq_float, 2)))
