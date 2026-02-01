"""
Fixed Assets Economic Profiles (``finance_modules.assets.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the assets module.  Each profile maps a
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
* R15 -- Adding a new asset event type requires ONLY a new profile +
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


# --- Mass Depreciation ---
ASSET_MASS_DEPRECIATION = AccountingPolicy(
    name="AssetMassDepreciation",
    version=1,
    trigger=PolicyTrigger(event_type="asset.mass_depreciation"),
    meaning=PolicyMeaning(
        economic_type="ASSET_DEPRECIATION_BATCH",
        dimensions=("cost_center",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="DEPRECIATION_EXPENSE", credit_role="ACCUMULATED_DEPRECIATION"),
    ),
    effective_from=date(2024, 1, 1),
    description="Batch depreciation for multiple assets",
)

ASSET_MASS_DEPRECIATION_MAPPINGS = (
    ModuleLineMapping(role="DEPRECIATION_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="credit", ledger="GL"),
)


# --- Asset Transfer ---
ASSET_TRANSFERRED = AccountingPolicy(
    name="AssetTransferred",
    version=1,
    trigger=PolicyTrigger(event_type="asset.transfer"),
    meaning=PolicyMeaning(
        economic_type="ASSET_TRANSFER",
        dimensions=("cost_center",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="FIXED_ASSET", credit_role="FIXED_ASSET"),
    ),
    effective_from=date(2024, 1, 1),
    description="Asset transfer between cost centers",
)

ASSET_TRANSFERRED_MAPPINGS = (
    ModuleLineMapping(role="FIXED_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="FIXED_ASSET", side="credit", ledger="GL"),
)


# --- Asset Revaluation ---
ASSET_REVALUED = AccountingPolicy(
    name="AssetRevalued",
    version=1,
    trigger=PolicyTrigger(event_type="asset.revaluation"),
    meaning=PolicyMeaning(
        economic_type="ASSET_REVALUATION",
        dimensions=("cost_center",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="FIXED_ASSET", credit_role="GAIN_ON_DISPOSAL"),
    ),
    effective_from=date(2024, 1, 1),
    description="IFRS asset revaluation to fair value",
)

ASSET_REVALUED_MAPPINGS = (
    ModuleLineMapping(role="FIXED_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="GAIN_ON_DISPOSAL", side="credit", ledger="GL"),
)


# --- Component Depreciation ---
ASSET_COMPONENT_DEPRECIATION = AccountingPolicy(
    name="AssetComponentDepreciation",
    version=1,
    trigger=PolicyTrigger(event_type="asset.component_depreciation"),
    meaning=PolicyMeaning(
        economic_type="ASSET_COMPONENT_DEPRECIATION",
        dimensions=("cost_center",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="DEPRECIATION_EXPENSE", credit_role="ACCUMULATED_DEPRECIATION"),
    ),
    effective_from=date(2024, 1, 1),
    description="Component-level depreciation",
)

ASSET_COMPONENT_DEPRECIATION_MAPPINGS = (
    ModuleLineMapping(role="DEPRECIATION_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCUMULATED_DEPRECIATION", side="credit", ledger="GL"),
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
    (ASSET_MASS_DEPRECIATION, ASSET_MASS_DEPRECIATION_MAPPINGS),
    (ASSET_TRANSFERRED, ASSET_TRANSFERRED_MAPPINGS),
    (ASSET_REVALUED, ASSET_REVALUED_MAPPINGS),
    (ASSET_COMPONENT_DEPRECIATION, ASSET_COMPONENT_DEPRECIATION_MAPPINGS),
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
