"""
Contract and billing event schemas.

Schemas for government contract billing, cost incurrence, and fee accrual.
These support DCAA-compliant billing for cost-reimbursement contracts.

Schema Categories:
1. Cost Incurrence - Recording allowable costs against contracts
2. Contract Billing - Invoicing customers (provisional and final)
3. Fee Accrual - Recognizing fee revenue on cost-plus contracts
4. Indirect Cost Allocation - Allocating overhead to contracts
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# contract.cost_incurred - Recording allowable costs to contract
# ============================================================================
CONTRACT_COST_INCURRED_V1 = EventSchema(
    event_type="contract.cost_incurred",
    version=1,
    description="Record allowable cost incurred against government contract",
    fields=(
        EventFieldSchema(
            name="incurrence_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the cost incurrence",
        ),
        EventFieldSchema(
            name="contract_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Contract number",
        ),
        EventFieldSchema(
            name="clin_number",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=20,
            description="Contract Line Item Number (CLIN)",
        ),
        EventFieldSchema(
            name="incurrence_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date the cost was incurred",
        ),
        EventFieldSchema(
            name="cost_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "DIRECT_LABOR",
                "DIRECT_MATERIAL",
                "SUBCONTRACT",
                "TRAVEL",
                "ODC",  # Other Direct Costs
                "INDIRECT_FRINGE",
                "INDIRECT_OVERHEAD",
                "INDIRECT_GA",
            }),
            description="Type of cost being incurred",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Cost amount",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the cost",
        ),
        EventFieldSchema(
            name="quantity",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Quantity (e.g., hours for labor)",
        ),
        EventFieldSchema(
            name="unit_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Unit rate (e.g., hourly rate for labor)",
        ),
        # Source reference
        EventFieldSchema(
            name="source_document_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "TIMESHEET",
                "AP_INVOICE",
                "MATERIAL_ISSUE",
                "EXPENSE_REPORT",
                "INDIRECT_ALLOCATION",
            }),
            description="Type of source document",
        ),
        EventFieldSchema(
            name="source_document_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Reference to source document",
        ),
        # Labor-specific fields
        EventFieldSchema(
            name="labor_category",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Labor category for contract billing rates",
        ),
        EventFieldSchema(
            name="employee_party_code",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Employee who incurred the cost",
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
EventSchemaRegistry.register(CONTRACT_COST_INCURRED_V1)


# ============================================================================
# contract.billing_provisional - Provisional billing to government
# ============================================================================
CONTRACT_BILLING_PROVISIONAL_V1 = EventSchema(
    event_type="contract.billing_provisional",
    version=1,
    description="Provisional billing invoice for cost-reimbursement contract",
    fields=(
        EventFieldSchema(
            name="billing_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the billing",
        ),
        EventFieldSchema(
            name="invoice_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Invoice number for this billing",
        ),
        EventFieldSchema(
            name="contract_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Contract number",
        ),
        EventFieldSchema(
            name="billing_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the billing",
        ),
        EventFieldSchema(
            name="period_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="Start of billing period",
        ),
        EventFieldSchema(
            name="period_end",
            field_type=EventFieldType.DATE,
            required=True,
            description="End of billing period",
        ),
        EventFieldSchema(
            name="billing_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "COST_REIMBURSEMENT",
                "TIME_AND_MATERIALS",
                "LABOR_HOUR",
                "FIXED_PRICE_MILESTONE",
            }),
            description="Type of billing",
        ),
        # Cost amounts
        EventFieldSchema(
            name="direct_labor_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Direct labor costs for the period",
        ),
        EventFieldSchema(
            name="fringe_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Fringe benefits costs",
        ),
        EventFieldSchema(
            name="overhead_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Overhead costs",
        ),
        EventFieldSchema(
            name="ga_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="G&A costs",
        ),
        EventFieldSchema(
            name="material_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Direct material costs",
        ),
        EventFieldSchema(
            name="subcontract_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Subcontract costs",
        ),
        EventFieldSchema(
            name="travel_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Travel costs",
        ),
        EventFieldSchema(
            name="odc_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Other direct costs",
        ),
        EventFieldSchema(
            name="total_cost",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total costs for the period",
        ),
        EventFieldSchema(
            name="fee_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Fee amount (for cost-plus contracts)",
        ),
        EventFieldSchema(
            name="total_billing",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Total billing amount (costs + fee)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the billing",
        ),
        # Provisional rate info
        EventFieldSchema(
            name="fringe_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            description="Provisional fringe rate used",
        ),
        EventFieldSchema(
            name="overhead_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            description="Provisional overhead rate used",
        ),
        EventFieldSchema(
            name="ga_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            description="Provisional G&A rate used",
        ),
        EventFieldSchema(
            name="fee_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            description="Fee rate (for cost-plus contracts)",
        ),
        # Customer info
        EventFieldSchema(
            name="customer_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Customer party code",
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
EventSchemaRegistry.register(CONTRACT_BILLING_PROVISIONAL_V1)


# ============================================================================
# contract.fee_accrual - Fee accrual for cost-plus contracts
# ============================================================================
CONTRACT_FEE_ACCRUAL_V1 = EventSchema(
    event_type="contract.fee_accrual",
    version=1,
    description="Fee accrual on cost-reimbursement contracts",
    fields=(
        EventFieldSchema(
            name="accrual_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the accrual",
        ),
        EventFieldSchema(
            name="contract_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Contract number",
        ),
        EventFieldSchema(
            name="accrual_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the fee accrual",
        ),
        EventFieldSchema(
            name="period_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="Start of accrual period",
        ),
        EventFieldSchema(
            name="period_end",
            field_type=EventFieldType.DATE,
            required=True,
            description="End of accrual period",
        ),
        EventFieldSchema(
            name="fee_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "FIXED_FEE",
                "INCENTIVE_FEE",
                "AWARD_FEE",
            }),
            description="Type of fee",
        ),
        EventFieldSchema(
            name="cost_base",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Cost base for fee calculation",
        ),
        EventFieldSchema(
            name="fee_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Fee rate applied",
        ),
        EventFieldSchema(
            name="fee_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Fee amount accrued",
        ),
        EventFieldSchema(
            name="cumulative_fee",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Cumulative fee earned to date",
        ),
        EventFieldSchema(
            name="ceiling_fee",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0"),
            description="Contract ceiling fee (if applicable)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the fee",
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
EventSchemaRegistry.register(CONTRACT_FEE_ACCRUAL_V1)


# ============================================================================
# contract.indirect_allocation - Allocate indirect costs to contracts
# ============================================================================
CONTRACT_INDIRECT_ALLOCATION_V1 = EventSchema(
    event_type="contract.indirect_allocation",
    version=1,
    description="Allocate indirect costs (fringe, overhead, G&A) to contracts",
    fields=(
        EventFieldSchema(
            name="allocation_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the allocation",
        ),
        EventFieldSchema(
            name="contract_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Contract number",
        ),
        EventFieldSchema(
            name="allocation_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the allocation",
        ),
        EventFieldSchema(
            name="period_start",
            field_type=EventFieldType.DATE,
            required=True,
            description="Start of allocation period",
        ),
        EventFieldSchema(
            name="period_end",
            field_type=EventFieldType.DATE,
            required=True,
            description="End of allocation period",
        ),
        EventFieldSchema(
            name="indirect_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "FRINGE",
                "OVERHEAD",
                "G_AND_A",
                "MATERIAL_HANDLING",
            }),
            description="Type of indirect cost",
        ),
        EventFieldSchema(
            name="base_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Base amount for allocation (e.g., direct labor)",
        ),
        EventFieldSchema(
            name="rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Indirect rate applied",
        ),
        EventFieldSchema(
            name="allocated_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Amount allocated",
        ),
        EventFieldSchema(
            name="rate_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"PROVISIONAL", "ACTUAL", "FINAL"}),
            description="Whether rate is provisional, actual, or final",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the allocation",
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
EventSchemaRegistry.register(CONTRACT_INDIRECT_ALLOCATION_V1)


# ============================================================================
# contract.rate_adjustment - Adjust for final vs provisional rates
# ============================================================================
CONTRACT_RATE_ADJUSTMENT_V1 = EventSchema(
    event_type="contract.rate_adjustment",
    version=1,
    description="Adjustment for difference between provisional and final rates",
    fields=(
        EventFieldSchema(
            name="adjustment_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the adjustment",
        ),
        EventFieldSchema(
            name="contract_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Contract number",
        ),
        EventFieldSchema(
            name="adjustment_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the adjustment",
        ),
        EventFieldSchema(
            name="fiscal_year",
            field_type=EventFieldType.INTEGER,
            required=True,
            min_value=2000,
            max_value=2100,
            description="Fiscal year being adjusted",
        ),
        EventFieldSchema(
            name="indirect_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "FRINGE",
                "OVERHEAD",
                "G_AND_A",
                "MATERIAL_HANDLING",
            }),
            description="Type of indirect cost being adjusted",
        ),
        EventFieldSchema(
            name="provisional_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Provisional rate that was used",
        ),
        EventFieldSchema(
            name="final_rate",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Final negotiated rate",
        ),
        EventFieldSchema(
            name="base_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0"),
            description="Base amount for rate calculation",
        ),
        EventFieldSchema(
            name="adjustment_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Adjustment amount (can be positive or negative)",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of the adjustment",
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
EventSchemaRegistry.register(CONTRACT_RATE_ADJUSTMENT_V1)
