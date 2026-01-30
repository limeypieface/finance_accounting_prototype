"""
Event schemas for Accounts Payable.

Defines schemas for AP invoice and payment events.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry


# ============================================================================
# ap.invoice_received - Supplier invoice received
# ============================================================================
AP_INVOICE_RECEIVED_V1 = EventSchema(
    event_type="ap.invoice_received",
    version=1,
    description="AP invoice event for recording supplier invoices",
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
EventSchemaRegistry.register(AP_INVOICE_RECEIVED_V1)


# ============================================================================
# ap.payment - Payment to supplier
# ============================================================================
AP_PAYMENT_V1 = EventSchema(
    event_type="ap.payment",
    version=1,
    description="AP payment event for recording payments to suppliers",
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
            name="supplier_party_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Supplier party code",
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
            description="Bank account used for payment",
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
EventSchemaRegistry.register(AP_PAYMENT_V1)
