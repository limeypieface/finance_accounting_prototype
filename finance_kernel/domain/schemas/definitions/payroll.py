"""
Event schemas for payroll and labor distribution.

Defines schemas for timesheet and labor distribution events.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# payroll.timesheet - Time recorded against projects/cost centers
# ============================================================================
PAYROLL_TIMESHEET_V1 = EventSchema(
    event_type="payroll.timesheet",
    version=1,
    description="Payroll timesheet event for recording time worked",
    fields=(
        EventFieldSchema(
            name="timesheet_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the timesheet entry",
        ),
        EventFieldSchema(
            name="employee_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Employee party code",
        ),
        EventFieldSchema(
            name="work_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date the work was performed",
        ),
        EventFieldSchema(
            name="hours",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            max_value=Decimal("24"),
            description="Hours worked",
        ),
        EventFieldSchema(
            name="pay_code",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "REGULAR",
                "OVERTIME",
                "DOUBLE_TIME",
                "SICK",
                "VACATION",
                "HOLIDAY",
            }),
            description="Type of time/pay",
        ),
        EventFieldSchema(
            name="hourly_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Hourly rate for the work",
        ),
        EventFieldSchema(
            name="total_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total amount (hours * rate)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the amounts",
        ),
        EventFieldSchema(
            name="is_billable",
            field_type=EventFieldType.BOOLEAN,
            required=True,
            description="Whether time is billable to customer",
        ),
        EventFieldSchema(
            name="work_order_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Related work order ID",
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
            required=True,
            min_length=1,
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
        EventFieldSchema(
            name="department",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Department dimension",
        ),
    ),
)
EventSchemaRegistry.register(PAYROLL_TIMESHEET_V1)


# ============================================================================
# payroll.labor_distribution - Labor cost distributed to WIP/projects
# ============================================================================
PAYROLL_LABOR_DISTRIBUTION_V1 = EventSchema(
    event_type="payroll.labor_distribution",
    version=1,
    description="Payroll labor distribution event for cost allocation",
    fields=(
        EventFieldSchema(
            name="distribution_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the distribution",
        ),
        EventFieldSchema(
            name="pay_period_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Pay period ID",
        ),
        EventFieldSchema(
            name="employee_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Employee party code",
        ),
        EventFieldSchema(
            name="distribution_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the distribution",
        ),
        EventFieldSchema(
            name="labor_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"DIRECT", "INDIRECT", "OVERHEAD"}),
            description="Type of labor cost",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Amount being distributed",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the amount",
        ),
        EventFieldSchema(
            name="work_order_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Related work order ID",
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
            required=True,
            min_length=1,
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
EventSchemaRegistry.register(PAYROLL_LABOR_DISTRIBUTION_V1)
