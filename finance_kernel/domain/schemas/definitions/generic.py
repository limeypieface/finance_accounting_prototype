"""
Generic posting event schema.

Defines the schema for generic.posting events used for direct journal entries.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry

# Generic posting event schema v1
GENERIC_POSTING_V1 = EventSchema(
    event_type="generic.posting",
    version=1,
    description="Generic journal posting event for direct ledger entries",
    fields=(
        EventFieldSchema(
            name="description",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Description of the journal entry",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency for all line amounts",
        ),
        EventFieldSchema(
            name="lines",
            field_type=EventFieldType.ARRAY,
            required=True,
            item_type=EventFieldType.OBJECT,
            item_schema=(
                EventFieldSchema(
                    name="account_code",
                    field_type=EventFieldType.STRING,
                    required=True,
                    min_length=1,
                    description="Account code for this line",
                ),
                EventFieldSchema(
                    name="debit",
                    field_type=EventFieldType.DECIMAL,
                    required=False,
                    nullable=True,
                    min_value=Decimal("0"),
                    description="Debit amount (mutually exclusive with credit)",
                ),
                EventFieldSchema(
                    name="credit",
                    field_type=EventFieldType.DECIMAL,
                    required=False,
                    nullable=True,
                    min_value=Decimal("0"),
                    description="Credit amount (mutually exclusive with debit)",
                ),
                EventFieldSchema(
                    name="memo",
                    field_type=EventFieldType.STRING,
                    required=False,
                    nullable=True,
                    max_length=200,
                    description="Optional line-level memo",
                ),
            ),
            description="Journal entry lines (must balance)",
        ),
        EventFieldSchema(
            name="reference",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=100,
            description="External reference number",
        ),
        EventFieldSchema(
            name="memo",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=1000,
            description="Additional notes",
        ),
    ),
)

# Register the schema
EventSchemaRegistry.register(GENERIC_POSTING_V1)
