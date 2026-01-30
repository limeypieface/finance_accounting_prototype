"""
Fixed Assets Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Profiles:
    AssetAcquisitionCash       — Cash purchase: Dr Fixed Asset / Cr Cash
    AssetAcquisitionOnAccount  — AP purchase: Dr Fixed Asset / Cr AP
    AssetCIPCapitalized        — CIP to asset: Dr Fixed Asset / Cr CIP
    AssetDepreciation          — Periodic: Dr Depr Expense / Cr Accum Depr
    AssetDisposalGain          — Sale at gain: Dr Cash+AccumDepr / Cr Asset+Gain
    AssetDisposalLoss          — Sale at loss: Dr Cash+AccumDepr+Loss / Cr Asset
    AssetImpairment            — Write-off: Dr AccumDepr+Impairment / Cr Asset
    AssetScrap                 — Scrap (no proceeds): Dr AccumDepr+Loss / Cr Asset
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

logger = get_logger("modules.assets.profiles")

MODULE_NAME = "assets"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Fixed Assets."""

    FIXED_ASSET = "fixed_asset"
    ACCUMULATED_DEPRECIATION = "accumulated_depreciation"
    DEPRECIATION_EXPENSE = "depreciation_expense"
    CASH = "cash"
    AP = "accounts_payable"
    GAIN_ON_DISPOSAL = "gain_on_disposal"
    LOSS_ON_DISPOSAL = "loss_on_disposal"
    IMPAIRMENT_LOSS = "impairment_loss"
    CIP = "construction_in_progress"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Acquisition — Cash ------------------------------------------------------

ASSET_ACQUISITION_CASH = AccountingPolicy(
    name="AssetAcquisitionCash",
    version=1,
    trigger=PolicyTrigger(
        event_type="asset.acquisition",
        where=(("payload.payment_method", "CASH"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="FIXED_ASSET", credit_role="CASH"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.cost <= 0",
            reason_code="INVALID_COST",
            message="Asset cost must be positive",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.useful_life_months < 1",
            reason_code="INVALID_USEFUL_LIFE",
            message="Useful life must be at least 1 month",
        ),
    ),
    description="Asset acquired for cash",
)

ASSET_ACQUISITION_CASH_MAPPINGS = (
    ModuleLineMapping(role="FIXED_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- Acquisition — On Account (AP) ------------------------------------------

ASSET_ACQUISITION_ON_ACCOUNT = AccountingPolicy(
    name="AssetAcquisitionOnAccount",
    version=1,
    trigger=PolicyTrigger(
        event_type="asset.acquisition",
        where=(("payload.payment_method", "ON_ACCOUNT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="FIXED_ASSET", credit_role="AP"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.cost <= 0",
            reason_code="INVALID_COST",
            message="Asset cost must be positive",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.useful_life_months < 1",
            reason_code="INVALID_USEFUL_LIFE",
            message="Useful life must be at least 1 month",
        ),
    ),
    description="Asset acquired on account (AP)",
)

ASSET_ACQUISITION_ON_ACCOUNT_MAPPINGS = (
    ModuleLineMapping(role="FIXED_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="AP", side="credit", ledger="GL"),
)


# --- CIP Capitalized --------------------------------------------------------

ASSET_CIP_CAPITALIZED = AccountingPolicy(
    name="AssetCIPCapitalized",
    version=1,
    trigger=PolicyTrigger(event_type="asset.cip_capitalized"),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="FIXED_ASSET", credit_role="CIP"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.cost <= 0",
            reason_code="INVALID_COST",
            message="Capitalized cost must be positive",
        ),
    ),
    description="Construction in progress capitalized to fixed asset",
)

ASSET_CIP_CAPITALIZED_MAPPINGS = (
    ModuleLineMapping(role="FIXED_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="CIP", side="credit", ledger="GL"),
)


# --- Depreciation ------------------------------------------------------------

ASSET_DEPRECIATION = AccountingPolicy(
    name="AssetDepreciation",
    version=1,
    trigger=PolicyTrigger(event_type="asset.depreciation"),
    meaning=PolicyMeaning(
        economic_type="DEPRECIATION_RECOGNITION",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="DEPRECIATION_EXPENSE",
            credit_role="ACCUMULATED_DEPRECIATION",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.depreciation_amount <= 0",
            reason_code="INVALID_DEPRECIATION",
            message="Depreciation amount must be positive",
        ),
    ),
    description="Records periodic depreciation expense",
)

ASSET_DEPRECIATION_MAPPINGS = (
    ModuleLineMapping(role="DEPRECIATION_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="credit", ledger="GL"),
)


# --- Disposal — Gain (Sale with proceeds > book value) ----------------------

ASSET_DISPOSAL_GAIN = AccountingPolicy(
    name="AssetDisposalGain",
    version=1,
    trigger=PolicyTrigger(
        event_type="asset.disposal",
        where=(("payload.disposal_type", "SALE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_DISPOSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="FIXED_ASSET",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Asset disposed via sale (gain or loss computed from proceeds vs book value)",
)

ASSET_DISPOSAL_GAIN_MAPPINGS = (
    ModuleLineMapping(role="CASH", side="debit", ledger="GL", from_context="proceeds"),
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="debit", ledger="GL", from_context="accumulated_depreciation"),
    ModuleLineMapping(role="LOSS_ON_DISPOSAL", side="debit", ledger="GL", from_context="loss_amount"),
    ModuleLineMapping(role="FIXED_ASSET", side="credit", ledger="GL", from_context="original_cost"),
    ModuleLineMapping(role="GAIN_ON_DISPOSAL", side="credit", ledger="GL", from_context="gain_amount"),
)


# --- Disposal — Loss (Retirement with remaining book value) -----------------

ASSET_DISPOSAL_LOSS = AccountingPolicy(
    name="AssetDisposalLoss",
    version=1,
    trigger=PolicyTrigger(
        event_type="asset.disposal",
        where=(("payload.disposal_type", "RETIREMENT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_DISPOSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ACCUMULATED_DEPRECIATION",
            credit_role="FIXED_ASSET",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Asset disposed at a loss (retirement of remaining book value)",
)

ASSET_DISPOSAL_LOSS_MAPPINGS = (
    ModuleLineMapping(role="CASH", side="debit", ledger="GL", from_context="proceeds"),
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="debit", ledger="GL", from_context="accumulated_depreciation"),
    ModuleLineMapping(role="LOSS_ON_DISPOSAL", side="debit", ledger="GL", from_context="loss_amount"),
    ModuleLineMapping(role="FIXED_ASSET", side="credit", ledger="GL", from_context="original_cost"),
)


# --- Impairment (Write-off) -------------------------------------------------

ASSET_IMPAIRMENT = AccountingPolicy(
    name="AssetImpairment",
    version=1,
    trigger=PolicyTrigger(
        event_type="asset.disposal",
        where=(("payload.disposal_type", "WRITE_OFF"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_DISPOSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="IMPAIRMENT_LOSS",
            credit_role="FIXED_ASSET",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Asset impairment recognized (write-off of impaired value)",
)

ASSET_IMPAIRMENT_MAPPINGS = (
    ModuleLineMapping(role="IMPAIRMENT_LOSS", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="credit", ledger="GL"),
)


# --- Scrap (no proceeds) ----------------------------------------------------

ASSET_SCRAP = AccountingPolicy(
    name="AssetScrap",
    version=1,
    trigger=PolicyTrigger(event_type="asset.scrap"),
    meaning=PolicyMeaning(
        economic_type="FIXED_ASSET_DISPOSAL",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ACCUMULATED_DEPRECIATION",
            credit_role="FIXED_ASSET",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Asset scrapped with no proceeds (full write-off of book value)",
)

ASSET_SCRAP_MAPPINGS = (
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="debit", ledger="GL", from_context="accumulated_depreciation"),
    ModuleLineMapping(role="LOSS_ON_DISPOSAL", side="debit", ledger="GL", from_context="book_value"),
    ModuleLineMapping(role="FIXED_ASSET", side="credit", ledger="GL", from_context="original_cost"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (ASSET_ACQUISITION_CASH, ASSET_ACQUISITION_CASH_MAPPINGS),
    (ASSET_ACQUISITION_ON_ACCOUNT, ASSET_ACQUISITION_ON_ACCOUNT_MAPPINGS),
    (ASSET_CIP_CAPITALIZED, ASSET_CIP_CAPITALIZED_MAPPINGS),
    (ASSET_DEPRECIATION, ASSET_DEPRECIATION_MAPPINGS),
    (ASSET_DISPOSAL_GAIN, ASSET_DISPOSAL_GAIN_MAPPINGS),
    (ASSET_DISPOSAL_LOSS, ASSET_DISPOSAL_LOSS_MAPPINGS),
    (ASSET_IMPAIRMENT, ASSET_IMPAIRMENT_MAPPINGS),
    (ASSET_SCRAP, ASSET_SCRAP_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all asset profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "asset_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dicts
# =============================================================================

ASSET_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
