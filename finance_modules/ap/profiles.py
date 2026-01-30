"""
Accounts Payable Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios).

Profiles:
    APInvoiceExpense           — Direct expense invoice (no PO)
    APInvoicePOMatched         — PO-matched invoice clearing GRNI
    APInvoiceInventory         — Inventory invoice
    APPayment                  — Standard supplier payment
    APPaymentWithDiscount      — Payment with early payment discount
    APInvoiceCancelled         — Invoice reversal
    APAccrualRecorded          — Period-end uninvoiced receipt accrual
    APAccrualReversed          — Accrual reversal
    APPrepaymentRecorded       — Advance payment to vendor
    APPrepaymentApplied        — Prepayment applied to invoice
"""

from datetime import date
from enum import Enum

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PolicyMeaning,
    PolicyTrigger,
)
from finance_kernel.domain.policy_bridge import (
    ModuleLineMapping,
    register_rich_profile,
)
from finance_kernel.logging_config import get_logger

logger = get_logger("modules.ap.profiles")

MODULE_NAME = "ap"


# =============================================================================
# Account roles (used by config.py)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for AP."""

    AP_LIABILITY = "ap_liability"
    EXPENSE = "expense"
    INVENTORY = "inventory"
    CASH = "cash"
    DISCOUNT_INCOME = "purchase_discount"
    TAX_PAYABLE = "tax_payable"
    PREPAID_EXPENSE = "prepaid_expense"
    ACCRUED_LIABILITY = "accrued_liability"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Invoice — Direct Expense (no PO) ----------------------------------------

AP_INVOICE_EXPENSE = AccountingPolicy(
    name="APInvoiceExpense",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.invoice_received",
        where=(("payload.po_number", None),),
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center", "project"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="EXPENSE", credit_role="ACCOUNTS_PAYABLE"),
        LedgerEffect(ledger="AP", debit_role="INVOICE", credit_role="SUPPLIER_BALANCE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot record invoice",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.gross_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Invoice amount must be positive",
        ),
    ),
    description="Records AP invoice for direct expense (not PO-matched)",
)

AP_INVOICE_EXPENSE_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE", side="debit", ledger="GL", foreach="invoice_lines"),
    ModuleLineMapping(role="TAX_PAYABLE", side="debit", ledger="GL", from_context="tax_amount"),
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# --- Invoice — PO Matched (clears GRNI) --------------------------------------

AP_INVOICE_PO_MATCHED = AccountingPolicy(
    name="APInvoicePOMatched",
    version=1,
    trigger=PolicyTrigger(event_type="ap.invoice_received"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="GRNI", credit_role="ACCOUNTS_PAYABLE"),
        LedgerEffect(ledger="AP", debit_role="INVOICE", credit_role="SUPPLIER_BALANCE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot record invoice",
        ),
    ),
    description="Records AP invoice matched to purchase order, clearing GRNI",
)

AP_INVOICE_PO_MATCHED_MAPPINGS = (
    ModuleLineMapping(role="GRNI", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_PAYABLE", side="debit", ledger="GL", from_context="tax_amount"),
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# --- Invoice — Inventory Items ------------------------------------------------

AP_INVOICE_INVENTORY = AccountingPolicy(
    name="APInvoiceInventory",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.invoice_received_inventory",
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="INVENTORY", credit_role="ACCOUNTS_PAYABLE"),
        LedgerEffect(ledger="AP", debit_role="INVOICE", credit_role="SUPPLIER_BALANCE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot record invoice",
        ),
    ),
    description="Records AP invoice for inventory items",
)

AP_INVOICE_INVENTORY_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY", side="debit", ledger="GL", foreach="invoice_lines"),
    ModuleLineMapping(role="TAX_PAYABLE", side="debit", ledger="GL", from_context="tax_amount"),
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# --- Payment — Standard -------------------------------------------------------

AP_PAYMENT = AccountingPolicy(
    name="APPayment",
    version=1,
    trigger=PolicyTrigger(event_type="ap.payment"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_PAYABLE", credit_role="CASH"),
        LedgerEffect(ledger="AP", debit_role="SUPPLIER_BALANCE", credit_role="PAYMENT"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot process payment",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.payment_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Payment amount must be positive",
        ),
    ),
    description="Records payment to supplier, reducing AP liability",
)

AP_PAYMENT_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="debit", ledger="AP"),
    ModuleLineMapping(role="PAYMENT", side="credit", ledger="AP"),
)


# --- Payment with Discount ---------------------------------------------------

AP_PAYMENT_WITH_DISCOUNT = AccountingPolicy(
    name="APPaymentWithDiscount",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.payment_with_discount",
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_PAYABLE", credit_role="CASH"),
        LedgerEffect(ledger="AP", debit_role="SUPPLIER_BALANCE", credit_role="PAYMENT"),
    ),
    effective_from=date(2024, 1, 1),
    description="Records payment with early payment discount taken",
)

AP_PAYMENT_WITH_DISCOUNT_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL", from_context="payment_amount"),
    ModuleLineMapping(role="PURCHASE_DISCOUNT", side="credit", ledger="GL", from_context="discount_amount"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="debit", ledger="AP"),
    ModuleLineMapping(role="PAYMENT", side="credit", ledger="AP"),
)


# --- Invoice Cancelled --------------------------------------------------------

AP_INVOICE_CANCELLED = AccountingPolicy(
    name="APInvoiceCancelled",
    version=1,
    trigger=PolicyTrigger(event_type="ap.invoice_cancelled"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_DECREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_PAYABLE", credit_role="EXPENSE"),
        LedgerEffect(ledger="AP", debit_role="SUPPLIER_BALANCE", credit_role="REVERSAL"),
    ),
    effective_from=date(2024, 1, 1),
    description="Records vendor invoice cancellation/reversal",
)

AP_INVOICE_CANCELLED_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="EXPENSE", side="credit", ledger="GL", foreach="invoice_lines"),
    ModuleLineMapping(role="TAX_PAYABLE", side="credit", ledger="GL", from_context="tax_amount"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="debit", ledger="AP"),
    ModuleLineMapping(role="REVERSAL", side="credit", ledger="AP"),
)


# --- Accrual Recorded ---------------------------------------------------------

AP_ACCRUAL_RECORDED = AccountingPolicy(
    name="APAccrualRecorded",
    version=1,
    trigger=PolicyTrigger(event_type="ap.accrual_recorded"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="EXPENSE", credit_role="ACCRUED_LIABILITY"),
    ),
    effective_from=date(2024, 1, 1),
    description="Period-end accrual for uninvoiced receipts",
)

AP_ACCRUAL_RECORDED_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCRUED_LIABILITY", side="credit", ledger="GL"),
)


# --- Accrual Reversed ---------------------------------------------------------

AP_ACCRUAL_REVERSED = AccountingPolicy(
    name="APAccrualReversed",
    version=1,
    trigger=PolicyTrigger(event_type="ap.accrual_reversed"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_DECREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCRUED_LIABILITY", credit_role="EXPENSE"),
    ),
    effective_from=date(2024, 1, 1),
    description="Reversal of period-end accrual",
)

AP_ACCRUAL_REVERSED_MAPPINGS = (
    ModuleLineMapping(role="ACCRUED_LIABILITY", side="debit", ledger="GL"),
    ModuleLineMapping(role="EXPENSE", side="credit", ledger="GL"),
)


# --- Prepayment Recorded ------------------------------------------------------

AP_PREPAYMENT_RECORDED = AccountingPolicy(
    name="APPrepaymentRecorded",
    version=1,
    trigger=PolicyTrigger(event_type="ap.prepayment_recorded"),
    meaning=PolicyMeaning(
        economic_type="ASSET_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="PREPAID_EXPENSE", credit_role="CASH"),
    ),
    effective_from=date(2024, 1, 1),
    description="Records advance payment to vendor",
)

AP_PREPAYMENT_RECORDED_MAPPINGS = (
    ModuleLineMapping(role="PREPAID_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- Prepayment Applied -------------------------------------------------------

AP_PREPAYMENT_APPLIED = AccountingPolicy(
    name="APPrepaymentApplied",
    version=1,
    trigger=PolicyTrigger(event_type="ap.prepayment_applied"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_PAYABLE", credit_role="PREPAID_EXPENSE"),
    ),
    effective_from=date(2024, 1, 1),
    description="Records prepayment applied against invoice",
)

AP_PREPAYMENT_APPLIED_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="PREPAID_EXPENSE", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (AP_INVOICE_EXPENSE, AP_INVOICE_EXPENSE_MAPPINGS),
    (AP_INVOICE_PO_MATCHED, AP_INVOICE_PO_MATCHED_MAPPINGS),
    (AP_INVOICE_INVENTORY, AP_INVOICE_INVENTORY_MAPPINGS),
    (AP_PAYMENT, AP_PAYMENT_MAPPINGS),
    (AP_PAYMENT_WITH_DISCOUNT, AP_PAYMENT_WITH_DISCOUNT_MAPPINGS),
    (AP_INVOICE_CANCELLED, AP_INVOICE_CANCELLED_MAPPINGS),
    (AP_ACCRUAL_RECORDED, AP_ACCRUAL_RECORDED_MAPPINGS),
    (AP_ACCRUAL_REVERSED, AP_ACCRUAL_REVERSED_MAPPINGS),
    (AP_PREPAYMENT_RECORDED, AP_PREPAYMENT_RECORDED_MAPPINGS),
    (AP_PREPAYMENT_APPLIED, AP_PREPAYMENT_APPLIED_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all AP profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "ap_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

AP_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
