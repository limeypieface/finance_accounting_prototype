"""
DCAA-compliant schema versions.

Adds allowability tracking to cost-related schemas for government contracting.
Creates v2 versions of schemas that can incur costs chargeable to contracts.

DCAA Allowability Categories:
- ALLOWABLE: Can be charged to government contracts
- UNALLOWABLE: Cannot be charged (entertainment, lobbying, alcohol, etc.)
- CONDITIONAL: Allowable with restrictions (travel per diem limits, etc.)
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry

# ============================================================================
# Common DCAA Field Definitions
# ============================================================================

ALLOWABILITY_FIELD = EventFieldSchema(
    name="allowability",
    field_type=EventFieldType.STRING,
    required=True,
    allowed_values=frozenset({"ALLOWABLE", "UNALLOWABLE", "CONDITIONAL"}),
    description="DCAA cost allowability classification",
)

ALLOWABILITY_FIELD_OPTIONAL = EventFieldSchema(
    name="allowability",
    field_type=EventFieldType.STRING,
    required=False,
    nullable=True,
    allowed_values=frozenset({"ALLOWABLE", "UNALLOWABLE", "CONDITIONAL"}),
    description="DCAA cost allowability classification (defaults to ALLOWABLE if not specified)",
)

UNALLOWABLE_REASON_FIELD = EventFieldSchema(
    name="unallowable_reason",
    field_type=EventFieldType.STRING,
    required=False,
    nullable=True,
    allowed_values=frozenset({
        "ENTERTAINMENT",
        "ALCOHOL",
        "LOBBYING",
        "ADVERTISING",
        "BAD_DEBT",
        "CONTRIBUTIONS",
        "FINES_PENALTIES",
        "INTEREST",
        "ORGANIZATION_COSTS",
        "PATENT_COSTS",
        "GOODWILL",
        "TRAVEL_EXCESS",
        "COMPENSATION_EXCESS",
        "OTHER",
    }),
    description="Reason code for unallowable costs per FAR 31.205",
)

CONTRACT_FIELD = EventFieldSchema(
    name="contract_id",
    field_type=EventFieldType.UUID,
    required=False,
    nullable=True,
    description="Government contract ID if cost is contract-chargeable",
)

CONTRACT_LINE_FIELD = EventFieldSchema(
    name="contract_line_number",
    field_type=EventFieldType.STRING,
    required=False,
    nullable=True,
    max_length=50,
    description="Contract line item number (CLIN)",
)


# ============================================================================
# ap.invoice_received v2 - With DCAA allowability
# ============================================================================
AP_INVOICE_RECEIVED_V2 = EventSchema(
    event_type="ap.invoice_received",
    version=2,
    description="AP invoice event with DCAA cost allowability tracking",
    fields=(
        EventFieldSchema(
            name="invoice_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the invoice",
        ),
        EventFieldSchema(
            name="invoice_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Supplier invoice number",
        ),
        EventFieldSchema(
            name="supplier_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Supplier party code",
        ),
        EventFieldSchema(
            name="invoice_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date on the invoice",
        ),
        EventFieldSchema(
            name="due_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Payment due date",
        ),
        EventFieldSchema(
            name="gross_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Gross invoice amount including tax",
        ),
        EventFieldSchema(
            name="tax_amount",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Tax amount",
        ),
        EventFieldSchema(
            name="net_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Net invoice amount before tax",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Invoice currency",
        ),
        EventFieldSchema(
            name="exchange_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0.000001"),
            description="Exchange rate if foreign currency",
        ),
        EventFieldSchema(
            name="po_number",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Related purchase order number",
        ),
        EventFieldSchema(
            name="receipt_number",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Related goods receipt number",
        ),
        EventFieldSchema(
            name="expense_account_role",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Expense account role for direct expense invoices",
        ),
        EventFieldSchema(
            name="lines",
            field_type=EventFieldType.ARRAY,
            required=False,
            nullable=True,
            item_type=EventFieldType.OBJECT,
            item_schema=(
                EventFieldSchema(
                    name="line_number",
                    field_type=EventFieldType.INTEGER,
                    required=True,
                    description="Line sequence number",
                ),
                EventFieldSchema(
                    name="description",
                    field_type=EventFieldType.STRING,
                    required=True,
                    max_length=500,
                    description="Line description",
                ),
                EventFieldSchema(
                    name="amount",
                    field_type=EventFieldType.DECIMAL,
                    required=True,
                    description="Line amount",
                ),
                EventFieldSchema(
                    name="account_role",
                    field_type=EventFieldType.STRING,
                    required=False,
                    nullable=True,
                    description="Account role for this line",
                ),
                EventFieldSchema(
                    name="cost_center",
                    field_type=EventFieldType.STRING,
                    required=False,
                    nullable=True,
                    description="Cost center for this line",
                ),
                # DCAA fields at line level
                EventFieldSchema(
                    name="allowability",
                    field_type=EventFieldType.STRING,
                    required=False,
                    nullable=True,
                    allowed_values=frozenset({"ALLOWABLE", "UNALLOWABLE", "CONDITIONAL"}),
                    description="Line-level allowability override",
                ),
                EventFieldSchema(
                    name="unallowable_reason",
                    field_type=EventFieldType.STRING,
                    required=False,
                    nullable=True,
                    description="Reason if line is unallowable",
                ),
            ),
            description="Invoice line items",
        ),
        # DCAA Fields
        ALLOWABILITY_FIELD,
        UNALLOWABLE_REASON_FIELD,
        CONTRACT_FIELD,
        CONTRACT_LINE_FIELD,
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
EventSchemaRegistry.register(AP_INVOICE_RECEIVED_V2)


# ============================================================================
# bank.withdrawal v2 - With DCAA allowability
# ============================================================================
BANK_WITHDRAWAL_V2 = EventSchema(
    event_type="bank.withdrawal",
    version=2,
    description="Bank withdrawal event with DCAA cost allowability tracking",
    fields=(
        EventFieldSchema(
            name="withdrawal_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the withdrawal",
        ),
        EventFieldSchema(
            name="withdrawal_reference",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Withdrawal reference number",
        ),
        EventFieldSchema(
            name="bank_account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Bank account for the withdrawal",
        ),
        EventFieldSchema(
            name="withdrawal_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the withdrawal",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Withdrawal amount",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Withdrawal currency",
        ),
        EventFieldSchema(
            name="destination_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "SUPPLIER_PAYMENT",
                "EXPENSE",
                "TRANSFER",
                "PAYROLL",
                "OTHER",
            }),
            description="Destination/purpose of withdrawal",
        ),
        EventFieldSchema(
            name="destination_reference",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=100,
            description="Reference to destination document",
        ),
        EventFieldSchema(
            name="expense_account_role",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Expense account role (if direct expense)",
        ),
        # DCAA Fields
        ALLOWABILITY_FIELD_OPTIONAL,
        UNALLOWABLE_REASON_FIELD,
        CONTRACT_FIELD,
        CONTRACT_LINE_FIELD,
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
EventSchemaRegistry.register(BANK_WITHDRAWAL_V2)


# ============================================================================
# payroll.timesheet v2 - With DCAA allowability
# ============================================================================
PAYROLL_TIMESHEET_V2 = EventSchema(
    event_type="payroll.timesheet",
    version=2,
    description="Payroll timesheet event with DCAA cost allowability tracking",
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
        # DCAA Fields
        ALLOWABILITY_FIELD,
        UNALLOWABLE_REASON_FIELD,
        CONTRACT_FIELD,
        CONTRACT_LINE_FIELD,
        EventFieldSchema(
            name="labor_category",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Labor category code for contract billing rates",
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
EventSchemaRegistry.register(PAYROLL_TIMESHEET_V2)


# ============================================================================
# payroll.labor_distribution v2 - With DCAA allowability
# ============================================================================
PAYROLL_LABOR_DISTRIBUTION_V2 = EventSchema(
    event_type="payroll.labor_distribution",
    version=2,
    description="Payroll labor distribution event with DCAA cost allowability tracking",
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
        # DCAA Fields
        ALLOWABILITY_FIELD,
        UNALLOWABLE_REASON_FIELD,
        CONTRACT_FIELD,
        CONTRACT_LINE_FIELD,
        EventFieldSchema(
            name="labor_category",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Labor category code for contract billing rates",
        ),
        EventFieldSchema(
            name="indirect_rate_type",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            allowed_values=frozenset({"FRINGE", "OVERHEAD", "G_AND_A", "MATERIAL_HANDLING"}),
            description="Type of indirect rate pool",
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
EventSchemaRegistry.register(PAYROLL_LABOR_DISTRIBUTION_V2)
