"""
Event schemas for fixed asset management.

Defines schemas for asset acquisition, depreciation, and disposal events.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# asset.acquisition - Recording purchase of fixed asset
# ============================================================================
ASSET_ACQUISITION_V1 = EventSchema(
    event_type="asset.acquisition",
    version=1,
    description="Fixed asset acquisition event for recording asset purchases",
    fields=(
        EventFieldSchema(
            name="asset_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the asset",
        ),
        EventFieldSchema(
            name="asset_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Asset code for reference",
        ),
        EventFieldSchema(
            name="description",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Description of the asset",
        ),
        EventFieldSchema(
            name="cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Acquisition cost of the asset",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the acquisition cost",
        ),
        EventFieldSchema(
            name="acquisition_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date the asset was acquired",
        ),
        EventFieldSchema(
            name="useful_life_months",
            field_type=EventFieldType.INTEGER,
            required=True,
            min_value=1,
            max_value=600,
            description="Expected useful life in months",
        ),
        EventFieldSchema(
            name="salvage_value",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Expected salvage value at end of life",
        ),
        EventFieldSchema(
            name="depreciation_method",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"STRAIGHT_LINE", "DECLINING_BALANCE", "UNITS_OF_PRODUCTION"}),
            description="Method used for depreciation calculation",
        ),
        EventFieldSchema(
            name="asset_category",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=100,
            description="Category classification for the asset",
        ),
        EventFieldSchema(
            name="location",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=200,
            description="Physical location of the asset",
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
EventSchemaRegistry.register(ASSET_ACQUISITION_V1)


# ============================================================================
# asset.depreciation - Recording periodic depreciation
# ============================================================================
ASSET_DEPRECIATION_V1 = EventSchema(
    event_type="asset.depreciation",
    version=1,
    description="Fixed asset depreciation event for recording periodic depreciation",
    fields=(
        EventFieldSchema(
            name="asset_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the asset",
        ),
        EventFieldSchema(
            name="period_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="Start date of the depreciation period",
        ),
        EventFieldSchema(
            name="period_end",
            field_type=EventFieldType.DATE,
            required=True,
            description="End date of the depreciation period",
        ),
        EventFieldSchema(
            name="depreciation_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Depreciation amount for this period",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the depreciation amount",
        ),
        EventFieldSchema(
            name="accumulated_depreciation",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total accumulated depreciation after this period",
        ),
        EventFieldSchema(
            name="net_book_value",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Net book value after this depreciation",
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
EventSchemaRegistry.register(ASSET_DEPRECIATION_V1)


# ============================================================================
# asset.disposal - Recording sale/retirement of asset
# ============================================================================
ASSET_DISPOSAL_V1 = EventSchema(
    event_type="asset.disposal",
    version=1,
    description="Fixed asset disposal event for recording asset sale or retirement",
    fields=(
        EventFieldSchema(
            name="asset_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the asset",
        ),
        EventFieldSchema(
            name="disposal_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the disposal",
        ),
        EventFieldSchema(
            name="disposal_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"SALE", "RETIREMENT", "WRITE_OFF", "TRANSFER"}),
            description="Type of disposal",
        ),
        EventFieldSchema(
            name="proceeds",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Sale proceeds (if applicable)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the disposal",
        ),
        EventFieldSchema(
            name="net_book_value_at_disposal",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Net book value at time of disposal",
        ),
        EventFieldSchema(
            name="gain_loss",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Gain or loss on disposal (negative for loss)",
        ),
        EventFieldSchema(
            name="buyer_party_code",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Party code of the buyer (if sale)",
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
EventSchemaRegistry.register(ASSET_DISPOSAL_V1)
