"""
Event schemas for inventory management.

Defines schemas for inventory receipt, issue, and adjustment events.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry

# ============================================================================
# inventory.receipt - Goods received (from PO or transfer)
# ============================================================================
INVENTORY_RECEIPT_V1 = EventSchema(
    event_type="inventory.receipt",
    version=1,
    description="Inventory receipt event for recording goods received",
    fields=(
        EventFieldSchema(
            name="receipt_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the receipt",
        ),
        EventFieldSchema(
            name="item_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Item/SKU code",
        ),
        EventFieldSchema(
            name="quantity",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.0001"),
            description="Quantity received",
        ),
        EventFieldSchema(
            name="unit_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Unit cost of the item",
        ),
        EventFieldSchema(
            name="total_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total cost (quantity * unit_cost)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the costs",
        ),
        EventFieldSchema(
            name="warehouse_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Warehouse where goods are received",
        ),
        EventFieldSchema(
            name="lot_number",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Lot/batch number for tracking",
        ),
        EventFieldSchema(
            name="serial_numbers",
            field_type=EventFieldType.ARRAY,
            required=False,
            nullable=True,
            item_type=EventFieldType.STRING,
            description="Serial numbers for serialized items",
        ),
        EventFieldSchema(
            name="po_number",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Purchase order number",
        ),
        EventFieldSchema(
            name="supplier_party_code",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Supplier party code",
        ),
        # Dimensions
        EventFieldSchema(
            name="org_unit",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Organization unit dimension",
        ),
        EventFieldSchema(
            name="cost_center",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Cost center dimension",
        ),
    ),
)
EventSchemaRegistry.register(INVENTORY_RECEIPT_V1)


# ============================================================================
# inventory.issue - Goods issued (to production, sale, or transfer)
# ============================================================================
INVENTORY_ISSUE_V1 = EventSchema(
    event_type="inventory.issue",
    version=1,
    description="Inventory issue event for recording goods consumed or shipped",
    fields=(
        EventFieldSchema(
            name="issue_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the issue",
        ),
        EventFieldSchema(
            name="item_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Item/SKU code",
        ),
        EventFieldSchema(
            name="quantity",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.0001"),
            description="Quantity issued",
        ),
        EventFieldSchema(
            name="unit_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Unit cost of the item",
        ),
        EventFieldSchema(
            name="total_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total cost (quantity * unit_cost)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the costs",
        ),
        EventFieldSchema(
            name="warehouse_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Warehouse from which goods are issued",
        ),
        EventFieldSchema(
            name="issue_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"SALE", "PRODUCTION", "TRANSFER", "SCRAP", "SAMPLE"}),
            description="Type of issue",
        ),
        EventFieldSchema(
            name="lot_number",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Lot/batch number for tracking",
        ),
        EventFieldSchema(
            name="serial_numbers",
            field_type=EventFieldType.ARRAY,
            required=False,
            nullable=True,
            item_type=EventFieldType.STRING,
            description="Serial numbers for serialized items",
        ),
        EventFieldSchema(
            name="work_order_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Work order ID (if production issue)",
        ),
        EventFieldSchema(
            name="sales_order_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Sales order ID (if sale issue)",
        ),
        # Dimensions
        EventFieldSchema(
            name="org_unit",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Organization unit dimension",
        ),
        EventFieldSchema(
            name="cost_center",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Cost center dimension",
        ),
        EventFieldSchema(
            name="project",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Project dimension",
        ),
    ),
)
EventSchemaRegistry.register(INVENTORY_ISSUE_V1)


# ============================================================================
# inventory.adjustment - Physical count adjustments
# ============================================================================
INVENTORY_ADJUSTMENT_V1 = EventSchema(
    event_type="inventory.adjustment",
    version=1,
    description="Inventory adjustment event for recording count variances",
    fields=(
        EventFieldSchema(
            name="adjustment_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the adjustment",
        ),
        EventFieldSchema(
            name="item_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Item/SKU code",
        ),
        EventFieldSchema(
            name="quantity_change",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Quantity change (positive for increase, negative for decrease)",
        ),
        EventFieldSchema(
            name="unit_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Unit cost for valuation",
        ),
        EventFieldSchema(
            name="total_value",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Total value of adjustment",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the values",
        ),
        EventFieldSchema(
            name="warehouse_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Warehouse where adjustment occurs",
        ),
        EventFieldSchema(
            name="adjustment_reason",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "PHYSICAL_COUNT",
                "DAMAGE",
                "THEFT",
                "OBSOLESCENCE",
                "ERROR_CORRECTION",
            }),
            description="Reason for the adjustment",
        ),
        EventFieldSchema(
            name="count_reference",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Reference to physical count document",
        ),
        # Dimensions
        EventFieldSchema(
            name="org_unit",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Organization unit dimension",
        ),
    ),
)
EventSchemaRegistry.register(INVENTORY_ADJUSTMENT_V1)
