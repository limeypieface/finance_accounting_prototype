"""
Intercompany Economic Profiles (``finance_modules.intercompany.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the intercompany module.

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new IC event type requires ONLY a new profile.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise.
* Guard expression match causes REJECTED outcome.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).
* IC elimination profiles must be traceable for consolidation audit.

Profiles:
    ICTransferPosted        — Dr IC Due From / Cr IC Due To
    ICEliminationPosted     — Dr IC Due To / Cr IC Due From (reversal)
    ICMarkupPosted          — Dr IC Due From / Cr Income Summary
    ICTransferPricing       — Dr IC Due From / Cr IC Due To (with markup)
"""

from datetime import date

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

MODULE_NAME = "intercompany"


# --- IC Transfer Posted ---
ICTransferPosted = AccountingPolicy(
    name="ICTransferPosted",
    version=1,
    trigger=PolicyTrigger(event_type="ic.transfer"),
    meaning=PolicyMeaning(
        economic_type="IC_TRANSFER",
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
    description="Intercompany transfer between entities",
)

ICTransferPosted_MAPPINGS = (
    ModuleLineMapping(role="INTERCOMPANY_DUE_FROM", side="debit", ledger="GL"),
    ModuleLineMapping(role="INTERCOMPANY_DUE_TO", side="credit", ledger="GL"),
)


# --- IC Elimination Posted ---
ICEliminationPosted = AccountingPolicy(
    name="ICEliminationPosted",
    version=1,
    trigger=PolicyTrigger(event_type="ic.elimination"),
    meaning=PolicyMeaning(
        economic_type="IC_ELIMINATION",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INTERCOMPANY_DUE_TO",
            credit_role="INTERCOMPANY_DUE_FROM",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Intercompany elimination entry",
)

ICEliminationPosted_MAPPINGS = (
    ModuleLineMapping(role="INTERCOMPANY_DUE_TO", side="debit", ledger="GL"),
    ModuleLineMapping(role="INTERCOMPANY_DUE_FROM", side="credit", ledger="GL"),
)


# --- IC Markup Posted ---
ICMarkupPosted = AccountingPolicy(
    name="ICMarkupPosted",
    version=1,
    trigger=PolicyTrigger(event_type="ic.markup"),
    meaning=PolicyMeaning(
        economic_type="IC_MARKUP",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INTERCOMPANY_DUE_FROM",
            credit_role="INCOME_SUMMARY",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Intercompany markup / transfer pricing adjustment",
)

ICMarkupPosted_MAPPINGS = (
    ModuleLineMapping(role="INTERCOMPANY_DUE_FROM", side="debit", ledger="GL"),
    ModuleLineMapping(role="INCOME_SUMMARY", side="credit", ledger="GL"),
)


# --- IC Transfer Pricing Adjustment ---
ICTransferPricing = AccountingPolicy(
    name="ICTransferPricing",
    version=1,
    trigger=PolicyTrigger(event_type="ic.transfer_pricing"),
    meaning=PolicyMeaning(
        economic_type="IC_TRANSFER_PRICING",
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
    description="Transfer pricing adjustment between entities",
)

ICTransferPricing_MAPPINGS = (
    ModuleLineMapping(role="INTERCOMPANY_DUE_FROM", side="debit", ledger="GL"),
    ModuleLineMapping(role="INTERCOMPANY_DUE_TO", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (ICTransferPosted, ICTransferPosted_MAPPINGS),
    (ICEliminationPosted, ICEliminationPosted_MAPPINGS),
    (ICMarkupPosted, ICMarkupPosted_MAPPINGS),
    (ICTransferPricing, ICTransferPricing_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all intercompany profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

INTERCOMPANY_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
