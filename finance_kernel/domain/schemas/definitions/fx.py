"""
Event schemas for foreign exchange operations.

Defines schemas for FX revaluation events.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# fx.revaluation - Period-end revaluation of foreign currency balances
# ============================================================================
FX_REVALUATION_V1 = EventSchema(
    event_type="fx.revaluation",
    version=1,
    description="FX revaluation event for recording currency gains/losses",
    fields=(
        EventFieldSchema(
            name="revaluation_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the revaluation",
        ),
        EventFieldSchema(
            name="revaluation_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the revaluation",
        ),
        EventFieldSchema(
            name="account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Account being revalued",
        ),
        EventFieldSchema(
            name="foreign_currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Foreign currency of the balance",
        ),
        EventFieldSchema(
            name="functional_currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Functional (reporting) currency",
        ),
        EventFieldSchema(
            name="foreign_balance",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Balance in foreign currency",
        ),
        EventFieldSchema(
            name="old_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.000001"),
            description="Previous exchange rate",
        ),
        EventFieldSchema(
            name="new_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.000001"),
            description="New exchange rate",
        ),
        EventFieldSchema(
            name="old_functional_value",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Previous value in functional currency",
        ),
        EventFieldSchema(
            name="new_functional_value",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="New value in functional currency",
        ),
        EventFieldSchema(
            name="gain_loss",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Gain (positive) or loss (negative)",
        ),
        EventFieldSchema(
            name="is_realized",
            field_type=EventFieldType.BOOLEAN,
            required=True,
            description="True if realized gain/loss, False if unrealized",
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
EventSchemaRegistry.register(FX_REVALUATION_V1)
