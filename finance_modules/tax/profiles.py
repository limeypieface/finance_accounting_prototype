"""
Tax Economic Profiles -- Kernel format.

Responsibility:
    Declares all ``AccountingPolicy`` instances and companion
    ``ModuleLineMapping`` tuples for the tax module.  Each profile maps a
    single event type to journal-line specifications using account ROLES
    (not COA codes).

Architecture:
    finance_modules -- Thin ERP glue (this layer).
    Profiles are registered into kernel registries by ``register()`` and
    resolved at posting time by the interpretation pipeline (L1).

Invariants:
    - R14 -- No ``if/switch`` on event_type in the posting engine.
             Each event type has exactly one profile (or one per
             where-clause variant).
    - R15 -- Adding a new tax event type requires ONLY a new profile +
             mapping + registration.
    - L1  -- Account roles (e.g., ``TAX_PAYABLE``) are resolved to COA
             codes at posting time by the kernel's ``RoleResolver``.

Failure modes:
    - Duplicate profile names cause ``register_rich_profile`` to raise at
      startup.
    - A guard expression evaluating to ``True`` causes the event to be
      REJECTED with the declared ``reason_code``.

Audit relevance:
    - Profile version numbers support replay compatibility (R23).
    - Guard conditions provide machine-readable rejection codes for
      audit inspection.

Profiles:
    SalesTaxCollected   -- Sales tax collected: Dr Clearing / Cr Payable
    UseTaxAccrued       -- Use tax self-assessed: Dr Expense / Cr Accrual
    TaxPayment          -- Tax remitted: Dr Payable / Cr Cash
    VatInput            -- VAT paid on purchase: Dr Receivable / Cr Clearing
    VatOutput           -- VAT collected on sale: Dr Clearing / Cr Payable
    VatSettlement       -- Net VAT settlement: Dr Payable / Cr Receivable + Cash
    TaxRefundReceived   -- Tax refund received: Dr Cash / Cr Receivable
    TaxDTARecorded      -- Deferred tax asset: Dr Receivable / Cr Expense
    TaxDTLRecorded      -- Deferred tax liability: Dr Expense / Cr Payable
    TaxMultiJurisdiction -- Multi-jurisdiction obligation: Dr Expense / Cr Payable
    TaxAdjustment       -- Prior period adjustment: Dr Expense / Cr Payable
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


# --- Deferred Tax Asset Recorded -----------------------------------------------

TAX_DTA_RECORDED = AccountingPolicy(
    name="TaxDTARecorded",
    version=1,
    trigger=PolicyTrigger(event_type="tax.dta_recorded"),
    meaning=PolicyMeaning(
        economic_type="DEFERRED_TAX_ASSET",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_RECEIVABLE", credit_role="TAX_EXPENSE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_DTA_AMOUNT",
            message="DTA amount must be positive",
        ),
    ),
    description="Deferred tax asset recognized",
)

TAX_DTA_RECORDED_MAPPINGS = (
    ModuleLineMapping(role="TAX_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_EXPENSE", side="credit", ledger="GL"),
)


# --- Deferred Tax Liability Recorded ------------------------------------------

TAX_DTL_RECORDED = AccountingPolicy(
    name="TaxDTLRecorded",
    version=1,
    trigger=PolicyTrigger(event_type="tax.dtl_recorded"),
    meaning=PolicyMeaning(
        economic_type="DEFERRED_TAX_LIABILITY",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_EXPENSE", credit_role="TAX_PAYABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_DTL_AMOUNT",
            message="DTL amount must be positive",
        ),
    ),
    description="Deferred tax liability recognized",
)

TAX_DTL_RECORDED_MAPPINGS = (
    ModuleLineMapping(role="TAX_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_PAYABLE", side="credit", ledger="GL"),
)


# --- Multi-Jurisdiction Tax Posted --------------------------------------------

TAX_MULTI_JURISDICTION = AccountingPolicy(
    name="TaxMultiJurisdiction",
    version=1,
    trigger=PolicyTrigger(event_type="tax.multi_jurisdiction"),
    meaning=PolicyMeaning(
        economic_type="TAX_LIABILITY_INCREASE",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_EXPENSE", credit_role="TAX_PAYABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_TAX_AMOUNT",
            message="Tax amount must be positive",
        ),
    ),
    description="Multi-jurisdiction tax obligation posted",
)

TAX_MULTI_JURISDICTION_MAPPINGS = (
    ModuleLineMapping(role="TAX_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_PAYABLE", side="credit", ledger="GL"),
)


# --- Tax Adjustment -----------------------------------------------------------

TAX_ADJUSTMENT = AccountingPolicy(
    name="TaxAdjustment",
    version=1,
    trigger=PolicyTrigger(event_type="tax.adjustment"),
    meaning=PolicyMeaning(
        economic_type="TAX_ADJUSTMENT",
        dimensions=("jurisdiction",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="TAX_EXPENSE", credit_role="TAX_PAYABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_ADJUSTMENT_AMOUNT",
            message="Tax adjustment amount must be positive",
        ),
    ),
    description="Prior period tax adjustment",
)

TAX_ADJUSTMENT_MAPPINGS = (
    ModuleLineMapping(role="TAX_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="TAX_PAYABLE", side="credit", ledger="GL"),
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
    # New ASC 740 + deepening profiles
    (TAX_DTA_RECORDED, TAX_DTA_RECORDED_MAPPINGS),
    (TAX_DTL_RECORDED, TAX_DTL_RECORDED_MAPPINGS),
    (TAX_MULTI_JURISDICTION, TAX_MULTI_JURISDICTION_MAPPINGS),
    (TAX_ADJUSTMENT, TAX_ADJUSTMENT_MAPPINGS),
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
