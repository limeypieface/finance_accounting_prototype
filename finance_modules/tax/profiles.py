"""
Tax Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Profiles:
    SalesTaxCollected   — Sales tax collected: Dr Clearing / Cr Payable
    UseTaxAccrued       — Use tax self-assessed: Dr Expense / Cr Accrual
    TaxPayment          — Tax remitted: Dr Payable / Cr Cash
    VatInput            — VAT paid on purchase: Dr Receivable / Cr Clearing
    VatOutput           — VAT collected on sale: Dr Clearing / Cr Payable
    VatSettlement       — Net VAT settlement: Dr Payable / Cr Receivable + Cash
    TaxRefundReceived   — Tax refund received: Dr Cash / Cr Receivable
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

logger = get_logger("modules.tax.profiles")

MODULE_NAME = "tax"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Tax."""

    TAX_PAYABLE = "tax_payable"  # collected, owed to jurisdiction
    TAX_RECEIVABLE = "tax_receivable"  # input tax credits (VAT)
    TAX_EXPENSE = "tax_expense"  # non-recoverable tax
    CASH = "cash"
    USE_TAX_ACCRUAL = "use_tax_accrual"
    TAX_CLEARING = "tax_clearing"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Sales Tax Collected -----------------------------------------------------

SALES_TAX_COLLECTED = AccountingPolicy(
    name="SalesTaxCollected",
    version=1,
    trigger=PolicyTrigger(event_type="tax.sales_tax_collected"),
    meaning=PolicyMeaning(
        economic_type="TAX_LIABILITY_INCREASE",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_CLEARING", credit_role="TAX_PAYABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_TAX_AMOUNT",
            message="Tax collected amount must be positive",
        ),
    ),
    description="Sales tax collected from customer",
)

SALES_TAX_COLLECTED_MAPPINGS = (
    ModuleLineMapping(role="TAX_CLEARING", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_PAYABLE", side="credit", ledger="GL"),
)


# --- Use Tax Accrued ---------------------------------------------------------

USE_TAX_ACCRUED = AccountingPolicy(
    name="UseTaxAccrued",
    version=1,
    trigger=PolicyTrigger(event_type="tax.use_tax_accrued"),
    meaning=PolicyMeaning(
        economic_type="TAX_EXPENSE_ACCRUAL",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_EXPENSE", credit_role="USE_TAX_ACCRUAL"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_TAX_AMOUNT",
            message="Use tax amount must be positive",
        ),
    ),
    description="Use tax self-assessed on purchase",
)

USE_TAX_ACCRUED_MAPPINGS = (
    ModuleLineMapping(role="TAX_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="USE_TAX_ACCRUAL", side="credit", ledger="GL"),
)


# --- Tax Payment -------------------------------------------------------------

TAX_PAYMENT = AccountingPolicy(
    name="TaxPayment",
    version=1,
    trigger=PolicyTrigger(event_type="tax.payment"),
    meaning=PolicyMeaning(
        economic_type="TAX_LIABILITY_DECREASE",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_PAYABLE", credit_role="CASH"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_PAYMENT_AMOUNT",
            message="Tax payment amount must be positive",
        ),
    ),
    description="Tax remitted to jurisdiction",
)

TAX_PAYMENT_MAPPINGS = (
    ModuleLineMapping(role="TAX_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- VAT Input ---------------------------------------------------------------

VAT_INPUT = AccountingPolicy(
    name="VatInput",
    version=1,
    trigger=PolicyTrigger(event_type="tax.vat_input"),
    meaning=PolicyMeaning(
        economic_type="TAX_ASSET_INCREASE",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_RECEIVABLE", credit_role="TAX_CLEARING"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_TAX_AMOUNT",
            message="VAT input amount must be positive",
        ),
    ),
    description="VAT paid on purchase (input credit)",
)

VAT_INPUT_MAPPINGS = (
    ModuleLineMapping(role="TAX_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_CLEARING", side="credit", ledger="GL"),
)


# --- VAT Output --------------------------------------------------------------

VAT_OUTPUT = AccountingPolicy(
    name="VatOutput",
    version=1,
    trigger=PolicyTrigger(event_type="tax.vat_output"),
    meaning=PolicyMeaning(
        economic_type="TAX_LIABILITY_INCREASE",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_CLEARING", credit_role="TAX_PAYABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_TAX_AMOUNT",
            message="VAT output amount must be positive",
        ),
    ),
    description="VAT collected on sale (output)",
)

VAT_OUTPUT_MAPPINGS = (
    ModuleLineMapping(role="TAX_CLEARING", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_PAYABLE", side="credit", ledger="GL"),
)


# --- VAT Settlement ----------------------------------------------------------

VAT_SETTLEMENT = AccountingPolicy(
    name="VatSettlement",
    version=1,
    trigger=PolicyTrigger(event_type="tax.vat_settlement"),
    meaning=PolicyMeaning(
        economic_type="TAX_SETTLEMENT",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_PAYABLE", credit_role="TAX_RECEIVABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_SETTLEMENT_AMOUNT",
            message="VAT settlement amount must be positive",
        ),
    ),
    description="Net VAT payment (output minus input)",
)

VAT_SETTLEMENT_MAPPINGS = (
    ModuleLineMapping(role="TAX_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(
        role="TAX_RECEIVABLE", side="credit", ledger="GL", from_context="input_vat_amount"
    ),
    ModuleLineMapping(
        role="CASH", side="credit", ledger="GL", from_context="net_payment"
    ),
)


# --- Tax Refund Received -----------------------------------------------------

TAX_REFUND_RECEIVED = AccountingPolicy(
    name="TaxRefundReceived",
    version=1,
    trigger=PolicyTrigger(event_type="tax.refund_received"),
    meaning=PolicyMeaning(
        economic_type="TAX_ASSET_DECREASE",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="CASH", credit_role="TAX_RECEIVABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_REFUND_AMOUNT",
            message="Tax refund amount must be positive",
        ),
    ),
    description="Tax refund received from jurisdiction",
)

TAX_REFUND_RECEIVED_MAPPINGS = (
    ModuleLineMapping(role="CASH", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_RECEIVABLE", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (SALES_TAX_COLLECTED, SALES_TAX_COLLECTED_MAPPINGS),
    (USE_TAX_ACCRUED, USE_TAX_ACCRUED_MAPPINGS),
    (TAX_PAYMENT, TAX_PAYMENT_MAPPINGS),
    (VAT_INPUT, VAT_INPUT_MAPPINGS),
    (VAT_OUTPUT, VAT_OUTPUT_MAPPINGS),
    (VAT_SETTLEMENT, VAT_SETTLEMENT_MAPPINGS),
    (TAX_REFUND_RECEIVED, TAX_REFUND_RECEIVED_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all tax profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "tax_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

TAX_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
