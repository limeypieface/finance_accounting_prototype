"""
General Ledger Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses) and module
(line mappings, scenarios). Absorbs deferred revenue/expense and FX profiles
that were previously in kernel/domain/profiles/.

Profiles:
    YearEndClose                — Close rev/exp to retained earnings
    DividendDeclared            — Dividend from retained earnings
    FXRevaluation               — Foreign currency revaluation gain/loss
    IntercompanyTransfer        — Intercompany due-to / due-from
    DeferredRevenueRecognition  — Dr Deferred Revenue / Cr Revenue
    DeferredExpenseRecognition  — Dr Expense / Cr Prepaid Expense
    FXUnrealizedGain            — Dr FC Balance / Cr Unrealized FX Gain
    FXUnrealizedLoss            — Dr Unrealized FX Loss / Cr FC Balance
    FXRealizedGain              — Dr FC Balance / Cr Realized FX Gain
    FXRealizedLoss              — Dr Realized FX Loss / Cr FC Balance
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

logger = get_logger("modules.gl.profiles")

MODULE_NAME = "gl"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for GL, deferred, and FX operations."""

    # --- Core GL ---
    RETAINED_EARNINGS = "retained_earnings"
    INCOME_SUMMARY = "income_summary"
    DIVIDENDS = "dividends"
    FOREIGN_EXCHANGE_GAIN_LOSS = "fx_gain_loss"
    INTERCOMPANY_DUE_TO = "intercompany_due_to"
    INTERCOMPANY_DUE_FROM = "intercompany_due_from"
    ROUNDING = "rounding"

    # --- Deferred ---
    DEFERRED_REVENUE = "deferred_revenue"
    REVENUE = "revenue"
    EXPENSE = "expense"
    PREPAID_EXPENSE = "prepaid_expense"

    # --- FX ---
    FOREIGN_CURRENCY_BALANCE = "foreign_currency_balance"
    UNREALIZED_FX_GAIN = "unrealized_fx_gain"
    UNREALIZED_FX_LOSS = "unrealized_fx_loss"
    REALIZED_FX_GAIN = "realized_fx_gain"
    REALIZED_FX_LOSS = "realized_fx_loss"


# =============================================================================
# Profile definitions — Core GL
# =============================================================================


# --- Year-End Close ----------------------------------------------------------

YEAR_END_CLOSE = AccountingPolicy(
    name="YearEndClose",
    version=1,
    trigger=PolicyTrigger(event_type="gl.year_end_close"),
    meaning=PolicyMeaning(
        economic_type="YEAR_END_CLOSE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INCOME_SUMMARY",
            credit_role="RETAINED_EARNINGS",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Close revenue/expense to retained earnings",
)

YEAR_END_CLOSE_MAPPINGS = (
    ModuleLineMapping(role="INCOME_SUMMARY", side="debit", ledger="GL"),
    ModuleLineMapping(role="RETAINED_EARNINGS", side="credit", ledger="GL"),
)


# --- Dividend Declared -------------------------------------------------------

DIVIDEND_DECLARED = AccountingPolicy(
    name="DividendDeclared",
    version=1,
    trigger=PolicyTrigger(event_type="gl.dividend_declared"),
    meaning=PolicyMeaning(
        economic_type="DIVIDEND_DECLARED",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="RETAINED_EARNINGS",
            credit_role="DIVIDENDS",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Dividend declared from retained earnings",
)

DIVIDEND_DECLARED_MAPPINGS = (
    ModuleLineMapping(role="RETAINED_EARNINGS", side="debit", ledger="GL"),
    ModuleLineMapping(role="DIVIDENDS", side="credit", ledger="GL"),
)


# --- FX Revaluation ----------------------------------------------------------

FX_REVALUATION = AccountingPolicy(
    name="FXRevaluation",
    version=1,
    trigger=PolicyTrigger(event_type="gl.fx_revaluation"),
    meaning=PolicyMeaning(
        economic_type="FX_REVALUATION",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="FOREIGN_EXCHANGE_GAIN_LOSS",
            credit_role="FOREIGN_EXCHANGE_GAIN_LOSS",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Foreign currency revaluation gain/loss",
)

FX_REVALUATION_MAPPINGS = (
    ModuleLineMapping(
        role="FOREIGN_EXCHANGE_GAIN_LOSS", side="debit", ledger="GL"
    ),
)


# --- Intercompany Transfer ---------------------------------------------------

INTERCOMPANY_TRANSFER = AccountingPolicy(
    name="IntercompanyTransfer",
    version=1,
    trigger=PolicyTrigger(event_type="gl.intercompany_transfer"),
    meaning=PolicyMeaning(
        economic_type="INTERCOMPANY_TRANSFER",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INTERCOMPANY_DUE_FROM",
            credit_role="INTERCOMPANY_DUE_TO",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Intercompany transaction",
)

INTERCOMPANY_TRANSFER_MAPPINGS = (
    ModuleLineMapping(role="INTERCOMPANY_DUE_FROM", side="debit", ledger="GL"),
    ModuleLineMapping(role="INTERCOMPANY_DUE_TO", side="credit", ledger="GL"),
)


# =============================================================================
# Profile definitions — Deferred (absorbed from kernel)
# =============================================================================


# --- Deferred Revenue Recognition --------------------------------------------

DEFERRED_REVENUE_RECOGNITION = AccountingPolicy(
    name="DeferredRevenueRecognition",
    version=1,
    trigger=PolicyTrigger(
        event_type="deferred.revenue_recognition",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        dimensions=("org_unit", "cost_center", "project"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="DEFERRED_REVENUE",
            credit_role="REVENUE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Recognition amount must be positive",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.remaining_deferred < 0",
            reason_code="OVER_RECOGNITION",
            message="Cannot recognize more than deferred balance",
        ),
    ),
    description="Records recognition of deferred revenue over service period",
)

DEFERRED_REVENUE_RECOGNITION_MAPPINGS = (
    ModuleLineMapping(role="DEFERRED_REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Deferred Expense Recognition (Prepaid) ----------------------------------

DEFERRED_EXPENSE_RECOGNITION = AccountingPolicy(
    name="DeferredExpenseRecognition",
    version=1,
    trigger=PolicyTrigger(
        event_type="deferred.expense_recognition",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_RECOGNITION",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE",
            credit_role="PREPAID_EXPENSE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Recognition amount must be positive",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.remaining_deferred < 0",
            reason_code="OVER_RECOGNITION",
            message="Cannot recognize more than prepaid balance",
        ),
    ),
    description="Records recognition of prepaid expense over benefit period",
)

DEFERRED_EXPENSE_RECOGNITION_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="PREPAID_EXPENSE", side="credit", ledger="GL"),
)


# =============================================================================
# Profile definitions — FX (absorbed from kernel)
# =============================================================================


# --- Unrealized Gain ---------------------------------------------------------

FX_UNREALIZED_GAIN = AccountingPolicy(
    name="FXUnrealizedGain",
    version=1,
    trigger=PolicyTrigger(
        event_type="fx.unrealized_gain",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="FX_GAIN",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="FOREIGN_CURRENCY_BALANCE",
            credit_role="UNREALIZED_FX_GAIN",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records unrealized FX gain from period-end revaluation",
)

FX_UNREALIZED_GAIN_MAPPINGS = (
    ModuleLineMapping(
        role="FOREIGN_CURRENCY_BALANCE", side="debit", ledger="GL"
    ),
    ModuleLineMapping(role="UNREALIZED_FX_GAIN", side="credit", ledger="GL"),
)


# --- Unrealized Loss ---------------------------------------------------------

FX_UNREALIZED_LOSS = AccountingPolicy(
    name="FXUnrealizedLoss",
    version=1,
    trigger=PolicyTrigger(
        event_type="fx.unrealized_loss",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="FX_LOSS",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="UNREALIZED_FX_LOSS",
            credit_role="FOREIGN_CURRENCY_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records unrealized FX loss from period-end revaluation",
)

FX_UNREALIZED_LOSS_MAPPINGS = (
    ModuleLineMapping(role="UNREALIZED_FX_LOSS", side="debit", ledger="GL"),
    ModuleLineMapping(
        role="FOREIGN_CURRENCY_BALANCE", side="credit", ledger="GL"
    ),
)


# --- Realized Gain -----------------------------------------------------------

FX_REALIZED_GAIN = AccountingPolicy(
    name="FXRealizedGain",
    version=1,
    trigger=PolicyTrigger(
        event_type="fx.realized_gain",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="FX_GAIN",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="FOREIGN_CURRENCY_BALANCE",
            credit_role="REALIZED_FX_GAIN",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records realized FX gain from settled transaction",
)

FX_REALIZED_GAIN_MAPPINGS = (
    ModuleLineMapping(
        role="FOREIGN_CURRENCY_BALANCE", side="debit", ledger="GL"
    ),
    ModuleLineMapping(role="REALIZED_FX_GAIN", side="credit", ledger="GL"),
)


# --- Realized Loss -----------------------------------------------------------

FX_REALIZED_LOSS = AccountingPolicy(
    name="FXRealizedLoss",
    version=1,
    trigger=PolicyTrigger(
        event_type="fx.realized_loss",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="FX_LOSS",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="REALIZED_FX_LOSS",
            credit_role="FOREIGN_CURRENCY_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records realized FX loss from settled transaction",
)

FX_REALIZED_LOSS_MAPPINGS = (
    ModuleLineMapping(role="REALIZED_FX_LOSS", side="debit", ledger="GL"),
    ModuleLineMapping(
        role="FOREIGN_CURRENCY_BALANCE", side="credit", ledger="GL"
    ),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    # Core GL
    (YEAR_END_CLOSE, YEAR_END_CLOSE_MAPPINGS),
    (DIVIDEND_DECLARED, DIVIDEND_DECLARED_MAPPINGS),
    (FX_REVALUATION, FX_REVALUATION_MAPPINGS),
    (INTERCOMPANY_TRANSFER, INTERCOMPANY_TRANSFER_MAPPINGS),
    # Deferred
    (DEFERRED_REVENUE_RECOGNITION, DEFERRED_REVENUE_RECOGNITION_MAPPINGS),
    (DEFERRED_EXPENSE_RECOGNITION, DEFERRED_EXPENSE_RECOGNITION_MAPPINGS),
    # FX
    (FX_UNREALIZED_GAIN, FX_UNREALIZED_GAIN_MAPPINGS),
    (FX_UNREALIZED_LOSS, FX_UNREALIZED_LOSS_MAPPINGS),
    (FX_REALIZED_GAIN, FX_REALIZED_GAIN_MAPPINGS),
    (FX_REALIZED_LOSS, FX_REALIZED_LOSS_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all GL profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "gl_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

GL_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
