"""
Event schemas for bank/cash management.

Defines schemas for deposits, withdrawals, transfers, and reconciliation.
"""

from decimal import Decimal

from finance_kernel.domain.schemas.base import (
    EventFieldSchema,
    EventFieldType,
    EventSchema,
)
from finance_kernel.domain.schemas.registry import EventSchemaRegistry

# ============================================================================
# bank.deposit - Funds deposited to bank
# ============================================================================
BANK_DEPOSIT_V1 = EventSchema(
    event_type="bank.deposit",
    version=1,
    description="Bank deposit event for recording funds deposited",
    fields=(
        EventFieldSchema(
            name="deposit_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the deposit",
        ),
        EventFieldSchema(
            name="deposit_reference",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Deposit reference number",
        ),
        EventFieldSchema(
            name="bank_account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Bank account receiving the deposit",
        ),
        EventFieldSchema(
            name="deposit_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the deposit",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Deposit amount",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Deposit currency",
        ),
        EventFieldSchema(
            name="source_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"CUSTOMER_PAYMENT", "CASH_SALES", "TRANSFER", "OTHER"}),
            description="Source of the deposit",
        ),
        EventFieldSchema(
            name="source_reference",
            field_type=EventFieldType.STRING,
            required=False,
            nullable=True,
            max_length=100,
            description="Reference to source document",
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
EventSchemaRegistry.register(BANK_DEPOSIT_V1)


# ============================================================================
# bank.withdrawal - Funds withdrawn from bank
# ============================================================================
BANK_WITHDRAWAL_V1 = EventSchema(
    event_type="bank.withdrawal",
    version=1,
    description="Bank withdrawal event for recording funds withdrawn",
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
EventSchemaRegistry.register(BANK_WITHDRAWAL_V1)


# ============================================================================
# bank.transfer - Inter-account transfer
# ============================================================================
BANK_TRANSFER_V1 = EventSchema(
    event_type="bank.transfer",
    version=1,
    description="Bank transfer event for recording inter-account transfers",
    fields=(
        EventFieldSchema(
            name="transfer_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the transfer",
        ),
        EventFieldSchema(
            name="transfer_reference",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Transfer reference number",
        ),
        EventFieldSchema(
            name="from_bank_account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Source bank account",
        ),
        EventFieldSchema(
            name="to_bank_account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Destination bank account",
        ),
        EventFieldSchema(
            name="transfer_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Date of the transfer",
        ),
        EventFieldSchema(
            name="from_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Amount debited from source account",
        ),
        EventFieldSchema(
            name="from_currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of source account",
        ),
        EventFieldSchema(
            name="to_amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            description="Amount credited to destination account",
        ),
        EventFieldSchema(
            name="to_currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Currency of destination account",
        ),
        EventFieldSchema(
            name="exchange_rate",
            field_type=EventFieldType.DECIMAL,
            required=False,
            nullable=True,
            min_value=Decimal("0.000001"),
            description="Exchange rate if currencies differ",
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
EventSchemaRegistry.register(BANK_TRANSFER_V1)


# ============================================================================
# bank.reconciliation - Bank statement line matched
# ============================================================================
BANK_RECONCILIATION_V1 = EventSchema(
    event_type="bank.reconciliation",
    version=1,
    description="Bank reconciliation event for matching statement lines",
    fields=(
        EventFieldSchema(
            name="reconciliation_id",
            field_type=EventFieldType.UUID,
            required=True,
            description="Unique identifier for the reconciliation",
        ),
        EventFieldSchema(
            name="bank_account_code",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=50,
            description="Bank account being reconciled",
        ),
        EventFieldSchema(
            name="statement_date",
            field_type=EventFieldType.DATE,
            required=True,
            description="Bank statement date",
        ),
        EventFieldSchema(
            name="bank_transaction_id",
            field_type=EventFieldType.STRING,
            required=True,
            min_length=1,
            max_length=100,
            description="Bank's transaction identifier",
        ),
        EventFieldSchema(
            name="matched_transaction_ids",
            field_type=EventFieldType.ARRAY,
            required=True,
            item_type=EventFieldType.UUID,
            description="Internal transaction IDs matched to this bank line",
        ),
        EventFieldSchema(
            name="amount",
            field_type=EventFieldType.DECIMAL,
            required=True,
            description="Amount of the bank transaction",
        ),
        EventFieldSchema(
            name="currency",
            field_type=EventFieldType.CURRENCY,
            required=True,
            description="Transaction currency",
        ),
        EventFieldSchema(
            name="match_type",
            field_type=EventFieldType.STRING,
            required=True,
            allowed_values=frozenset({"EXACT", "PARTIAL", "GROUPED", "MANUAL"}),
            description="Type of match",
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
EventSchemaRegistry.register(BANK_RECONCILIATION_V1)
