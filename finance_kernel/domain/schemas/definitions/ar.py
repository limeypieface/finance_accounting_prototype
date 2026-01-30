"""
Event schemas for Accounts Receivable.

Defines schemas for AR invoice, payment, and credit memo events.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# ar.invoice_issued - Customer invoice issued
# ============================================================================
AR_INVOICE_ISSUED_V1 = EventSchema(
    event_type="ar.invoice_issued",
    version=1,
    description="AR invoice event for recording customer invoices",
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
            description="Invoice number",
        ),
        EventFieldSchema(
            name="customer_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Customer party code",
        ),
        EventFieldSchema(
            name="invoice_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the invoice",
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
            name="sales_order_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Related sales order ID",
        ),
        EventFieldSchema(
            name="revenue_account_role",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=50,
            description="Revenue account role",
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
                    name="quantity",
                    field_type=EventFieldType.DECIMAL,
                    required=False,
                    nullable=True,
                    description="Quantity",
                ),
                EventFieldSchema(
                    name="unit_price",
                    field_type=EventFieldType.DECIMAL,
                    required=False,
                    nullable=True,
                    description="Unit price",
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
            ),
            description="Invoice line items",
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
EventSchemaRegistry.register(AR_INVOICE_ISSUED_V1)


# ============================================================================
# ar.payment_received - Payment from customer
# ============================================================================
AR_PAYMENT_RECEIVED_V1 = EventSchema(
    event_type="ar.payment_received",
    version=1,
    description="AR payment event for recording customer payments",
    fields=(
        EventFieldSchema(
            name="payment_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the payment",
        ),
        EventFieldSchema(
            name="payment_reference",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Payment reference number",
        ),
        EventFieldSchema(
            name="customer_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Customer party code",
        ),
        EventFieldSchema(
            name="payment_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of payment",
        ),
        EventFieldSchema(
            name="payment_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Total payment amount",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Payment currency",
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
            name="payment_method",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"WIRE", "ACH", "CHECK", "CARD", "CASH"}),
            description="Method of payment",
        ),
        EventFieldSchema(
            name="bank_account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Bank account receiving payment",
        ),
        EventFieldSchema(
            name="invoice_allocations",
            field_type=EventFieldType.ARRAY,
            required=True,
            item_type=EventFieldType.OBJECT,
            item_schema=(
                EventFieldSchema(
                    name="invoice_id",
                    field_type=EventFieldType.UUID,
                    required=True,
                    description="ID of invoice being paid",
                ),
                EventFieldSchema(
                    name="amount_applied",
                    field_type=EventFieldType.DECIMAL,
                    required=True,
                    min_value=Decimal("0.01"),
                    description="Amount applied to this invoice",
                ),
                EventFieldSchema(
                    name="discount_taken",
                    field_type=EventFieldType.DECIMAL,
                    required=False,
                    nullable=True,
                    min_value=Decimal("0"),
                    description="Early payment discount taken",
                ),
            ),
            description="Invoices being paid",
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
EventSchemaRegistry.register(AR_PAYMENT_RECEIVED_V1)


# ============================================================================
# ar.credit_memo - Credit issued to customer
# ============================================================================
AR_CREDIT_MEMO_V1 = EventSchema(
    event_type="ar.credit_memo",
    version=1,
    description="AR credit memo event for recording customer credits",
    fields=(
        EventFieldSchema(
            name="credit_memo_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the credit memo",
        ),
        EventFieldSchema(
            name="credit_memo_number",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Credit memo number",
        ),
        EventFieldSchema(
            name="customer_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Customer party code",
        ),
        EventFieldSchema(
            name="credit_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the credit memo",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Credit memo amount",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Credit memo currency",
        ),
        EventFieldSchema(
            name="reason_code",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({
                "RETURN",
                "PRICE_ADJUSTMENT",
                "SERVICE_CREDIT",
                "ERROR_CORRECTION",
            }),
            description="Reason for the credit",
        ),
        EventFieldSchema(
            name="original_invoice_id",
            field_type=EventFieldType.UUID,
            required=False,
            nullable=True,
            description="Original invoice being credited",
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
EventSchemaRegistry.register(AR_CREDIT_MEMO_V1)
