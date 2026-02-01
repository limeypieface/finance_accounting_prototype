"""
Event schemas for deferred revenue and expense recognition.

Defines schemas for recognition of deferred amounts over time.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry

# ============================================================================
# deferred.recognition - Recognize portion of deferred amount
# ============================================================================
DEFERRED_RECOGNITION_V1 = EventSchema(
    event_type="deferred.recognition",
    version=1,
    description="Deferred recognition event for revenue/expense amortization",
    fields=(
        EventFieldSchema(
            name="recognition_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the recognition",
        ),
        EventFieldSchema(
            name="source_document_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="ID of the source document being recognized",
        ),
        EventFieldSchema(
            name="recognition_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"REVENUE", "EXPENSE"}),
            description="Type of deferred amount being recognized",
        ),
        EventFieldSchema(
            name="recognition_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of recognition",
        ),
        EventFieldSchema(
            name="period_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="Start of the recognition period",
        ),
        EventFieldSchema(
            name="period_end",
            field_type=EventFieldType.DATE,
            required=True,
            description="End of the recognition period",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Amount being recognized this period",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the amount",
        ),
        EventFieldSchema(
            name="total_deferred",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total original deferred amount",
        ),
        EventFieldSchema(
            name="remaining_deferred",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Remaining deferred amount after recognition",
        ),
        EventFieldSchema(
            name="schedule_reference",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Reference to amortization schedule",
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
EventSchemaRegistry.register(DEFERRED_RECOGNITION_V1)
