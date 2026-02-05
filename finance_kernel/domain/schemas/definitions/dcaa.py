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


# ============================================================================
# DCAA Operational Control Schemas (v3)
# ============================================================================
# These schemas support the DCAA compliance gap closure:
# D1 daily recording, D2 supervisor approval, D3 total time accounting,
# D4 concurrent overlap, D5 correction by reversal, D6 pre-travel auth,
# D7 GSA rate cap, D8 rate ceiling, D9 floor check audit.
# ============================================================================


# --- timesheet.submitted v3 (D1, D3, D4) -----------------------------------
TIMESHEET_SUBMITTED_V3 = EventSchema(
    event_type="timesheet.submitted",
    version=3,
    description="Timesheet submission event with DCAA compliance fields",
    fields=(
        EventFieldSchema(
            name="submission_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the timesheet submission",
        ),
        EventFieldSchema(
            name="employee_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Employee submitting the timesheet",
        ),
        EventFieldSchema(
            name="pay_period_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Pay period for this submission",
        ),
        EventFieldSchema(
            name="work_week_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="Start date of the work week",
        ),
        EventFieldSchema(
            name="total_hours",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total hours in submission",
        ),
        EventFieldSchema(
            name="entry_count",
            field_type=EventFieldType.INTEGER,
            required=True,
            description="Number of time entries in submission",
        ),
        EventFieldSchema(
            name="submitted_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date the timesheet was submitted",
        ),
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
EventSchemaRegistry.register(TIMESHEET_SUBMITTED_V3)


# --- timesheet.approved v3 (D2) -------------------------------------------
TIMESHEET_APPROVED_V3 = EventSchema(
    event_type="timesheet.approved",
    version=3,
    description="Timesheet approval event (D2: supervisor approval gate)",
    fields=(
        EventFieldSchema(
            name="submission_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The submission being approved",
        ),
        EventFieldSchema(
            name="approver_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Supervisor/manager who approved",
        ),
        EventFieldSchema(
            name="approved_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of approval",
        ),
    ),
)
EventSchemaRegistry.register(TIMESHEET_APPROVED_V3)


# --- timesheet.rejected v3 (D2) -------------------------------------------
TIMESHEET_REJECTED_V3 = EventSchema(
    event_type="timesheet.rejected",
    version=3,
    description="Timesheet rejection event",
    fields=(
        EventFieldSchema(
            name="submission_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The submission being rejected",
        ),
        EventFieldSchema(
            name="rejector_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Supervisor/manager who rejected",
        ),
        EventFieldSchema(
            name="reason",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Reason for rejection",
        ),
        EventFieldSchema(
            name="rejected_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of rejection",
        ),
    ),
)
EventSchemaRegistry.register(TIMESHEET_REJECTED_V3)


# --- timesheet.corrected v3 (D5, R10) -------------------------------------
TIMESHEET_CORRECTED_V3 = EventSchema(
    event_type="timesheet.corrected",
    version=3,
    description="Timesheet correction event (D5: reversal + replacement)",
    fields=(
        EventFieldSchema(
            name="correction_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the correction",
        ),
        EventFieldSchema(
            name="original_entry_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The entry being corrected",
        ),
        EventFieldSchema(
            name="reversal_event_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The reversal event ID",
        ),
        EventFieldSchema(
            name="new_entry_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The replacement entry ID",
        ),
        EventFieldSchema(
            name="reason",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Reason for the correction",
        ),
        EventFieldSchema(
            name="corrected_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the correction",
        ),
    ),
)
EventSchemaRegistry.register(TIMESHEET_CORRECTED_V3)


# --- floor_check.completed v3 (D9) ----------------------------------------
FLOOR_CHECK_COMPLETED_V3 = EventSchema(
    event_type="floor_check.completed",
    version=3,
    description="Floor check audit event (D9: append-only audit artifact)",
    fields=(
        EventFieldSchema(
            name="check_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the floor check",
        ),
        EventFieldSchema(
            name="employee_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Employee being checked",
        ),
        EventFieldSchema(
            name="check_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the floor check",
        ),
        EventFieldSchema(
            name="observed_activity",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=500,
            description="Activity observed during the check",
        ),
        EventFieldSchema(
            name="charged_contract_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Contract the employee was charging at time of check",
        ),
        EventFieldSchema(
            name="result",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"CONFIRMED", "DISCREPANCY", "EMPLOYEE_ABSENT"}),
            description="Floor check result",
        ),
        EventFieldSchema(
            name="checker_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Person who performed the floor check",
        ),
    ),
)
EventSchemaRegistry.register(FLOOR_CHECK_COMPLETED_V3)


# --- floor_check.discrepancy_resolved v3 (D9) -----------------------------
FLOOR_CHECK_DISCREPANCY_RESOLVED_V3 = EventSchema(
    event_type="floor_check.discrepancy_resolved",
    version=3,
    description="Floor check discrepancy resolution",
    fields=(
        EventFieldSchema(
            name="check_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The floor check being resolved",
        ),
        EventFieldSchema(
            name="resolution",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Resolution description",
        ),
        EventFieldSchema(
            name="resolved_by",
            field_type=EventFieldType.UUID,
            required=True,
            description="Person who resolved the discrepancy",
        ),
        EventFieldSchema(
            name="resolved_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of resolution",
        ),
    ),
)
EventSchemaRegistry.register(FLOOR_CHECK_DISCREPANCY_RESOLVED_V3)


# --- expense.travel_auth_submitted v3 (D6) --------------------------------
EXPENSE_TRAVEL_AUTH_SUBMITTED_V3 = EventSchema(
    event_type="expense.travel_auth_submitted",
    version=3,
    description="Travel authorization submitted (D6: pre-travel auth)",
    fields=(
        EventFieldSchema(
            name="authorization_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the travel authorization",
        ),
        EventFieldSchema(
            name="employee_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Employee requesting travel authorization",
        ),
        EventFieldSchema(
            name="destination",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=200,
            description="Travel destination",
        ),
        EventFieldSchema(
            name="purpose",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Purpose of travel",
        ),
        EventFieldSchema(
            name="travel_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="First day of travel",
        ),
        EventFieldSchema(
            name="travel_end",
            field_type=EventFieldType.DATE,
            required=True,
            description="Last day of travel",
        ),
        EventFieldSchema(
            name="total_estimated",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Total estimated travel cost",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the estimates",
        ),
        CONTRACT_FIELD,
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
EventSchemaRegistry.register(EXPENSE_TRAVEL_AUTH_SUBMITTED_V3)


# --- expense.travel_auth_approved v3 (D6) ---------------------------------
EXPENSE_TRAVEL_AUTH_APPROVED_V3 = EventSchema(
    event_type="expense.travel_auth_approved",
    version=3,
    description="Travel authorization approved",
    fields=(
        EventFieldSchema(
            name="authorization_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The authorization being approved",
        ),
        EventFieldSchema(
            name="approver_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Person who approved the authorization",
        ),
        EventFieldSchema(
            name="approved_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of approval",
        ),
    ),
)
EventSchemaRegistry.register(EXPENSE_TRAVEL_AUTH_APPROVED_V3)


# --- expense.travel_auth_rejected v3 (D6) ---------------------------------
EXPENSE_TRAVEL_AUTH_REJECTED_V3 = EventSchema(
    event_type="expense.travel_auth_rejected",
    version=3,
    description="Travel authorization rejected",
    fields=(
        EventFieldSchema(
            name="authorization_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The authorization being rejected",
        ),
        EventFieldSchema(
            name="rejector_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Person who rejected the authorization",
        ),
        EventFieldSchema(
            name="reason",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=500,
            description="Reason for rejection",
        ),
        EventFieldSchema(
            name="rejected_at",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of rejection",
        ),
    ),
)
EventSchemaRegistry.register(EXPENSE_TRAVEL_AUTH_REJECTED_V3)


# --- expense.report_gsa_validated v3 (D7) ---------------------------------
EXPENSE_REPORT_GSA_VALIDATED_V3 = EventSchema(
    event_type="expense.report_gsa_validated",
    version=3,
    description="Expense report validated against GSA per diem limits (D7)",
    fields=(
        EventFieldSchema(
            name="report_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="The expense report that was validated",
        ),
        EventFieldSchema(
            name="is_compliant",
            field_type=EventFieldType.BOOLEAN,
            required=True,
            description="Whether the report is GSA-compliant",
        ),
        EventFieldSchema(
            name="total_claimed",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total amount claimed",
        ),
        EventFieldSchema(
            name="total_allowed",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total amount allowed by GSA rates",
        ),
        EventFieldSchema(
            name="excess_amount",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Amount exceeding GSA limits",
        ),
        EventFieldSchema(
            name="violation_count",
            field_type=EventFieldType.INTEGER,
            required=True,
            description="Number of GSA violations found",
        ),
        EventFieldSchema(
            name="travel_location",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=200,
            description="Travel location used for GSA rate lookup",
        ),
    ),
)
EventSchemaRegistry.register(EXPENSE_REPORT_GSA_VALIDATED_V3)


# --- contract.rate_verified v3 (D8) ---------------------------------------
CONTRACT_RATE_VERIFIED_V3 = EventSchema(
    event_type="contract.rate_verified",
    version=3,
    description="Labor rate verification result (D8)",
    fields=(
        EventFieldSchema(
            name="employee_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Employee whose rate was verified",
        ),
        EventFieldSchema(
            name="contract_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Contract being charged",
        ),
        EventFieldSchema(
            name="labor_category",
            field_type=EventFieldType.STRING,
            required=True,
            max_length=100,
            description="Labor category code",
        ),
        EventFieldSchema(
            name="charged_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Rate being charged",
        ),
        EventFieldSchema(
            name="approved_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Approved rate from schedule",
        ),
        EventFieldSchema(
            name="ceiling_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Contract ceiling rate (if applicable)",
        ),
        EventFieldSchema(
            name="is_valid",
            field_type=EventFieldType.BOOLEAN,
            required=True,
            description="Whether the rate passes verification",
        ),
        EventFieldSchema(
            name="violation_type",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            allowed_values=frozenset({
                "EXCEEDS_CLASSIFICATION",
                "EXCEEDS_CONTRACT_CEILING",
                "PROVISIONAL_NOT_APPROVED",
                "RATE_EXPIRED",
            }),
            description="Type of rate violation (if any)",
        ),
        EventFieldSchema(
            name="charge_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the labor charge",
        ),
    ),
)
EventSchemaRegistry.register(CONTRACT_RATE_VERIFIED_V3)


# --- contract.rate_ceiling_exceeded v3 (D8) --------------------------------
CONTRACT_RATE_CEILING_EXCEEDED_V3 = EventSchema(
    event_type="contract.rate_ceiling_exceeded",
    version=3,
    description="Rate ceiling exceeded alert event (D8)",
    fields=(
        EventFieldSchema(
            name="employee_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Employee whose rate exceeded ceiling",
        ),
        EventFieldSchema(
            name="contract_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Contract with the ceiling",
        ),
        EventFieldSchema(
            name="labor_category",
            field_type=EventFieldType.STRING,
            required=True,
            max_length=100,
            description="Labor category code",
        ),
        EventFieldSchema(
            name="charged_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Rate that was charged",
        ),
        EventFieldSchema(
            name="ceiling_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Contract ceiling rate",
        ),
        EventFieldSchema(
            name="excess_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Amount by which rate exceeds ceiling",
        ),
        EventFieldSchema(
            name="charge_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the labor charge",
        ),
    ),
)
EventSchemaRegistry.register(CONTRACT_RATE_CEILING_EXCEEDED_V3)


# --- contract.rate_reconciliation v3 --------------------------------------
CONTRACT_RATE_RECONCILIATION_V3 = EventSchema(
    event_type="contract.rate_reconciliation",
    version=3,
    description="Provisional-to-final indirect rate reconciliation",
    fields=(
        EventFieldSchema(
            name="reconciliation_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for this reconciliation",
        ),
        EventFieldSchema(
            name="fiscal_year",
            field_type=EventFieldType.INTEGER,
            required=True,
            description="Fiscal year being reconciled",
        ),
        EventFieldSchema(
            name="rate_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"FRINGE", "OVERHEAD", "G_AND_A", "MATERIAL_HANDLING"}),
            description="Indirect rate type",
        ),
        EventFieldSchema(
            name="provisional_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Provisional rate used during the year",
        ),
        EventFieldSchema(
            name="final_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="DCAA-audited final rate",
        ),
        EventFieldSchema(
            name="base_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total base dollars for the year",
        ),
        EventFieldSchema(
            name="adjustment_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Computed adjustment amount",
        ),
        EventFieldSchema(
            name="direction",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"UNDERAPPLIED", "OVERAPPLIED"}),
            description="Whether cost was underapplied or overapplied",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the amounts",
        ),
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
EventSchemaRegistry.register(CONTRACT_RATE_RECONCILIATION_V3)
