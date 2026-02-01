"""
Accounts Receivable Economic Profiles (``finance_modules.ar.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the AR module.  Each profile maps a
single event type to journal-line specifications using account ROLES
(not COA codes).

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new AR event type requires ONLY a new profile +
         mapping + registration.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise at
  startup.
* A guard expression evaluating to ``True`` causes the event to be
  REJECTED with the declared ``reason_code``.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).
* Guard conditions provide machine-readable rejection codes for
  audit inspection.

Profiles:
    ARInvoice                   -- Invoice issued: Dr AR / Cr Revenue + Tax
    ARPaymentReceived           -- Payment received (direct): Dr Cash / Cr AR
    ARReceiptReceived           -- Receipt to unapplied cash: Dr Cash / Cr Unapplied
    ARReceiptApplied            -- Apply receipt to invoice: Dr Unapplied / Cr AR
    ARReceiptAppliedDiscount    -- Apply receipt with discount: Dr Unapplied+Disc / Cr AR
    ARCreditMemoReturn          -- Credit memo (return): Dr Sales Returns / Cr AR
    ARCreditMemoPriceAdj        -- Credit memo (price adj): Dr Sales Allowance / Cr AR
    ARCreditMemoService         -- Credit memo (service): Dr Revenue / Cr AR
    ARCreditMemoError           -- Credit memo (error): Dr Revenue / Cr AR
    ARWriteOff                  -- Write off: Dr Allowance / Cr AR
    ARBadDebtProvision          -- Provision: Dr Bad Debt Expense / Cr Allowance
    ARDeferredRevenueRecorded   -- Advance: Dr Cash / Cr Deferred Revenue
    ARDeferredRevenueRecognized -- Recognize: Dr Deferred Revenue / Cr Revenue
    ARRefundIssued              -- Refund: Dr AR / Cr Cash
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

logger = get_logger("modules.ar.profiles")

MODULE_NAME = "ar"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for AR."""

    AR_ASSET = "ar_asset"
    REVENUE = "revenue"
    CASH = "cash"
    DISCOUNT_EXPENSE = "sales_discount"
    TAX_LIABILITY = "tax_liability"
    ALLOWANCE_DOUBTFUL = "allowance_doubtful"
    BAD_DEBT_EXPENSE = "bad_debt_expense"
    DEFERRED_REVENUE = "deferred_revenue"
    UNAPPLIED_CASH = "unapplied_cash"
    SALES_RETURNS = "sales_returns"
    SALES_ALLOWANCE = "sales_allowance"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Invoice Issued ----------------------------------------------------------

AR_INVOICE = AccountingPolicy(
    name="ARInvoice",
    version=1,
    trigger=PolicyTrigger(event_type="ar.invoice"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        dimensions=("org_unit", "cost_center", "project"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ACCOUNTS_RECEIVABLE",
            credit_role="REVENUE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="CUSTOMER_BALANCE",
            credit_role="INVOICE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="party.is_frozen",
            reason_code="CUSTOMER_FROZEN",
            message="Customer is frozen - cannot issue invoice",
        ),
        GuardCondition(
            guard_type=GuardType.BLOCK,
            expression="not check_credit_limit(party, payload.gross_amount)",
            reason_code="CREDIT_LIMIT_EXCEEDED",
            message="Transaction would exceed customer credit limit",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.gross_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Invoice amount must be positive",
        ),
    ),
    description="Records customer invoice, recognizing revenue",
)

AR_INVOICE_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(
        role="REVENUE", side="credit", ledger="GL", foreach="invoice_lines"
    ),
    ModuleLineMapping(
        role="TAX_LIABILITY", side="credit", ledger="GL", from_context="tax_amount"
    ),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="debit", ledger="AR"),
    ModuleLineMapping(role="INVOICE", side="credit", ledger="AR"),
)


# --- Payment Received (direct application) ----------------------------------

AR_PAYMENT_RECEIVED = AccountingPolicy(
    name="ARPaymentReceived",
    version=1,
    trigger=PolicyTrigger(event_type="ar.payment"),
    meaning=PolicyMeaning(
        economic_type="ASSET_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="PAYMENT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.payment_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Payment amount must be positive",
        ),
    ),
    description="Records customer payment applied directly to AR balance",
)

AR_PAYMENT_RECEIVED_MAPPINGS = (
    ModuleLineMapping(role="CASH", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="PAYMENT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Receipt Received (unapplied cash) --------------------------------------

AR_RECEIPT_RECEIVED = AccountingPolicy(
    name="ARReceiptReceived",
    version=1,
    trigger=PolicyTrigger(event_type="ar.receipt"),
    meaning=PolicyMeaning(
        economic_type="ASSET_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="UNAPPLIED_CASH",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.receipt_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Receipt amount must be positive",
        ),
    ),
    description="Records payment received as unapplied cash",
)

AR_RECEIPT_RECEIVED_MAPPINGS = (
    ModuleLineMapping(role="CASH", side="debit", ledger="GL"),
    ModuleLineMapping(role="UNAPPLIED_CASH", side="credit", ledger="GL"),
)


# --- Receipt Applied ---------------------------------------------------------

AR_RECEIPT_APPLIED = AccountingPolicy(
    name="ARReceiptApplied",
    version=1,
    trigger=PolicyTrigger(event_type="ar.receipt_applied"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_SETTLEMENT",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="UNAPPLIED_CASH",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="PAYMENT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Applies unapplied receipt to an invoice, reducing AR balance",
)

AR_RECEIPT_APPLIED_MAPPINGS = (
    ModuleLineMapping(role="UNAPPLIED_CASH", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="PAYMENT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Receipt Applied with Discount ------------------------------------------

AR_RECEIPT_APPLIED_WITH_DISCOUNT = AccountingPolicy(
    name="ARReceiptAppliedDiscount",
    version=1,
    trigger=PolicyTrigger(
        event_type="ar.receipt_applied",
        where=(("payload.has_discount", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_SETTLEMENT",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="UNAPPLIED_CASH",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="PAYMENT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Applies receipt with early payment discount",
)

AR_RECEIPT_APPLIED_WITH_DISCOUNT_MAPPINGS = (
    ModuleLineMapping(role="UNAPPLIED_CASH", side="debit", ledger="GL"),
    ModuleLineMapping(
        role="DISCOUNT_EXPENSE",
        side="debit",
        ledger="GL",
        from_context="discount_amount",
    ),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="PAYMENT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Credit Memo: Return -----------------------------------------------------

AR_CREDIT_MEMO_RETURN = AccountingPolicy(
    name="ARCreditMemoReturn",
    version=1,
    trigger=PolicyTrigger(
        event_type="ar.credit_memo",
        where=(("payload.reason_code", "RETURN"),),
    ),
    meaning=PolicyMeaning(
        economic_type="REVENUE_REVERSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="SALES_RETURNS",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="CREDIT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records credit memo for customer return",
)

AR_CREDIT_MEMO_RETURN_MAPPINGS = (
    ModuleLineMapping(role="SALES_RETURNS", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="CREDIT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Credit Memo: Price Adjustment -------------------------------------------

AR_CREDIT_MEMO_PRICE_ADJ = AccountingPolicy(
    name="ARCreditMemoPriceAdj",
    version=1,
    trigger=PolicyTrigger(
        event_type="ar.credit_memo",
        where=(("payload.reason_code", "PRICE_ADJUSTMENT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="REVENUE_REVERSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="SALES_ALLOWANCE",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="CREDIT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records credit memo for price adjustment",
)

AR_CREDIT_MEMO_PRICE_ADJ_MAPPINGS = (
    ModuleLineMapping(role="SALES_ALLOWANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="CREDIT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Credit Memo: Service Credit ---------------------------------------------

AR_CREDIT_MEMO_SERVICE = AccountingPolicy(
    name="ARCreditMemoService",
    version=1,
    trigger=PolicyTrigger(
        event_type="ar.credit_memo",
        where=(("payload.reason_code", "SERVICE_CREDIT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="REVENUE_REVERSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="REVENUE",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="CREDIT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records credit memo for service credit",
)

AR_CREDIT_MEMO_SERVICE_MAPPINGS = (
    ModuleLineMapping(role="REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="CREDIT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Credit Memo: Error Correction -------------------------------------------

AR_CREDIT_MEMO_ERROR = AccountingPolicy(
    name="ARCreditMemoError",
    version=1,
    trigger=PolicyTrigger(
        event_type="ar.credit_memo",
        where=(("payload.reason_code", "ERROR_CORRECTION"),),
    ),
    meaning=PolicyMeaning(
        economic_type="REVENUE_REVERSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="REVENUE",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="CREDIT",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records credit memo for billing error correction",
)

AR_CREDIT_MEMO_ERROR_MAPPINGS = (
    ModuleLineMapping(role="REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="CREDIT", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Write Off ---------------------------------------------------------------

AR_WRITE_OFF = AccountingPolicy(
    name="ARWriteOff",
    version=1,
    trigger=PolicyTrigger(event_type="ar.write_off"),
    meaning=PolicyMeaning(
        economic_type="ASSET_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ALLOWANCE_DOUBTFUL",
            credit_role="ACCOUNTS_RECEIVABLE",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="WRITE_OFF",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.write_off_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Write-off amount must be positive",
        ),
    ),
    description="Writes off invoice as uncollectible against allowance",
)

AR_WRITE_OFF_MAPPINGS = (
    ModuleLineMapping(role="ALLOWANCE_DOUBTFUL", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="WRITE_OFF", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Bad Debt Provision ------------------------------------------------------

AR_BAD_DEBT_PROVISION = AccountingPolicy(
    name="ARBadDebtProvision",
    version=1,
    trigger=PolicyTrigger(event_type="ar.bad_debt_provision"),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_RECOGNITION",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="BAD_DEBT_EXPENSE",
            credit_role="ALLOWANCE_DOUBTFUL",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records provision for doubtful accounts",
)

AR_BAD_DEBT_PROVISION_MAPPINGS = (
    ModuleLineMapping(role="BAD_DEBT_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ALLOWANCE_DOUBTFUL", side="credit", ledger="GL"),
)


# --- Deferred Revenue Recorded -----------------------------------------------

AR_DEFERRED_REVENUE_RECORDED = AccountingPolicy(
    name="ARDeferredRevenueRecorded",
    version=1,
    trigger=PolicyTrigger(event_type="ar.deferred_revenue_recorded"),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="DEFERRED_REVENUE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records advance payment as deferred revenue",
)

AR_DEFERRED_REVENUE_RECORDED_MAPPINGS = (
    ModuleLineMapping(role="CASH", side="debit", ledger="GL"),
    ModuleLineMapping(role="DEFERRED_REVENUE", side="credit", ledger="GL"),
)


# --- Deferred Revenue Recognized ---------------------------------------------

AR_DEFERRED_REVENUE_RECOGNIZED = AccountingPolicy(
    name="ARDeferredRevenueRecognized",
    version=1,
    trigger=PolicyTrigger(event_type="ar.deferred_revenue_recognized"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="DEFERRED_REVENUE",
            credit_role="REVENUE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Recognizes deferred revenue as earned",
)

AR_DEFERRED_REVENUE_RECOGNIZED_MAPPINGS = (
    ModuleLineMapping(role="DEFERRED_REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Refund Issued -----------------------------------------------------------

AR_REFUND_ISSUED = AccountingPolicy(
    name="ARRefundIssued",
    version=1,
    trigger=PolicyTrigger(event_type="ar.refund"),
    meaning=PolicyMeaning(
        economic_type="ASSET_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ACCOUNTS_RECEIVABLE",
            credit_role="CASH",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="REFUND",
            credit_role="CUSTOMER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.refund_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Refund amount must be positive",
        ),
    ),
    description="Records refund issued to customer",
)

AR_REFUND_ISSUED_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
    ModuleLineMapping(role="REFUND", side="debit", ledger="AR"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="credit", ledger="AR"),
)


# --- Finance Charge -----------------------------------------------------------

AR_FINANCE_CHARGE = AccountingPolicy(
    name="ARFinanceCharge",
    version=1,
    trigger=PolicyTrigger(event_type="ar.finance_charge"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ACCOUNTS_RECEIVABLE",
            credit_role="INTEREST_INCOME",
        ),
        LedgerEffect(
            ledger="AR",
            debit_role="CUSTOMER_BALANCE",
            credit_role="FINANCE_CHARGE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Finance charge amount must be positive",
        ),
    ),
    description="Records finance charge for late payment on customer account",
)

AR_FINANCE_CHARGE_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="INTEREST_INCOME", side="credit", ledger="GL"),
    ModuleLineMapping(role="CUSTOMER_BALANCE", side="debit", ledger="AR"),
    ModuleLineMapping(role="FINANCE_CHARGE", side="credit", ledger="AR"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (AR_INVOICE, AR_INVOICE_MAPPINGS),
    (AR_PAYMENT_RECEIVED, AR_PAYMENT_RECEIVED_MAPPINGS),
    (AR_RECEIPT_RECEIVED, AR_RECEIPT_RECEIVED_MAPPINGS),
    (AR_RECEIPT_APPLIED, AR_RECEIPT_APPLIED_MAPPINGS),
    (AR_RECEIPT_APPLIED_WITH_DISCOUNT, AR_RECEIPT_APPLIED_WITH_DISCOUNT_MAPPINGS),
    (AR_CREDIT_MEMO_RETURN, AR_CREDIT_MEMO_RETURN_MAPPINGS),
    (AR_CREDIT_MEMO_PRICE_ADJ, AR_CREDIT_MEMO_PRICE_ADJ_MAPPINGS),
    (AR_CREDIT_MEMO_SERVICE, AR_CREDIT_MEMO_SERVICE_MAPPINGS),
    (AR_CREDIT_MEMO_ERROR, AR_CREDIT_MEMO_ERROR_MAPPINGS),
    (AR_WRITE_OFF, AR_WRITE_OFF_MAPPINGS),
    (AR_BAD_DEBT_PROVISION, AR_BAD_DEBT_PROVISION_MAPPINGS),
    (AR_DEFERRED_REVENUE_RECORDED, AR_DEFERRED_REVENUE_RECORDED_MAPPINGS),
    (AR_DEFERRED_REVENUE_RECOGNIZED, AR_DEFERRED_REVENUE_RECOGNIZED_MAPPINGS),
    (AR_REFUND_ISSUED, AR_REFUND_ISSUED_MAPPINGS),
    # AR Deepening
    (AR_FINANCE_CHARGE, AR_FINANCE_CHARGE_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all AR profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "ar_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

AR_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
