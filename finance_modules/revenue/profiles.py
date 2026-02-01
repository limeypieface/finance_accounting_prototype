"""
Module: finance_modules.revenue.profiles
Responsibility:
    Declarative AccountingPolicy definitions for every revenue event type
    under ASC 606.  Each profile maps one event_type to one set of ledger
    effects (debit/credit ROLES), guards, and semantic meaning.

    Profiles registered here:
        RevenuePointInTime             -- Point-in-time recognition (Step 5)
        RevenueOverTimeInput           -- Over-time input method (Step 5)
        RevenueOverTimeOutput          -- Over-time output method (Step 5)
        RevenuePriceAllocation         -- SSP allocation posting (Step 4)
        RevenueModificationCumulative  -- Modification cumulative catch-up
        RevenueModificationProspective -- Modification prospective treatment
        RevenueVariableUpdate          -- Variable consideration re-estimate
        RevenueLicenseRenewal          -- License renewal deferred recognition

Architecture:
    finance_modules layer -- purely declarative.  No logic beyond
    AccountingPolicy construction and registration via
    ``register_rich_profile``.  Each profile is paired with a
    ModuleLineMapping tuple for the posting pipeline.

    Dependency direction (strict):
        profiles.py  -->  finance_kernel.domain.accounting_policy
        profiles.py  -->  finance_kernel.domain.policy_bridge
        profiles.py  -X-> finance_services (FORBIDDEN)

Invariants:
    - R14: One AccountingPolicy per event_type -- no central dispatch.
    - R15: Adding a new revenue event requires ONLY a new profile + mapping
      pair appended to _ALL_PROFILES, plus registration.
    - P1:  Exactly one profile matches any given revenue event.
    - L1:  Profiles reference account ROLES, never COA codes.

Failure modes:
    - Duplicate event_type registration raises at startup.
    - Guard expression evaluation failure produces REJECTED outcome.

Audit relevance:
    - Profiles are the authoritative source for how each revenue event
      maps to ledger effects.  Auditors trace from event_type to profile
      to journal entry.
    - Guard conditions (amount > 0) prevent posting of invalid entries.
    - Version field enables replay compatibility (R23).
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

logger = get_logger("modules.revenue.profiles")

MODULE_NAME = "revenue"


# =============================================================================
# Profile definitions
# =============================================================================

# --- Point in Time Recognition -----------------------------------------------

REVENUE_POINT_IN_TIME = AccountingPolicy(
    name="RevenuePointInTime",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.recognize_point_in_time"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_RECEIVABLE", credit_role="REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Recognition amount must be positive",
        ),
    ),
    description="Revenue recognized at a point in time (ASC 606 Step 5)",
)

REVENUE_POINT_IN_TIME_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Over Time Input ---------------------------------------------------------

REVENUE_OVER_TIME_INPUT = AccountingPolicy(
    name="RevenueOverTimeInput",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.recognize_over_time_input"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="UNBILLED_RECEIVABLE", credit_role="REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Recognition amount must be positive",
        ),
    ),
    description="Revenue recognized over time using input method (costs incurred)",
)

REVENUE_OVER_TIME_INPUT_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Over Time Output --------------------------------------------------------

REVENUE_OVER_TIME_OUTPUT = AccountingPolicy(
    name="RevenueOverTimeOutput",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.recognize_over_time_output"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="UNBILLED_RECEIVABLE", credit_role="REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Recognition amount must be positive",
        ),
    ),
    description="Revenue recognized over time using output method (units delivered)",
)

REVENUE_OVER_TIME_OUTPUT_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Price Allocation --------------------------------------------------------

REVENUE_PRICE_ALLOCATION = AccountingPolicy(
    name="RevenuePriceAllocation",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.price_allocation"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_ALLOCATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="CONTRACT_RECEIVABLE", credit_role="DEFERRED_REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Allocation amount must be positive",
        ),
    ),
    description="Transaction price allocated to performance obligations (Step 4)",
)

REVENUE_PRICE_ALLOCATION_MAPPINGS = (
    ModuleLineMapping(role="CONTRACT_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="DEFERRED_REVENUE", side="credit", ledger="GL"),
)


# --- Modification Cumulative Catch-Up ----------------------------------------

REVENUE_MODIFICATION_CUMULATIVE = AccountingPolicy(
    name="RevenueModificationCumulative",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.modification_cumulative"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_ADJUSTMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_RECEIVABLE", credit_role="REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Modification amount must be positive",
        ),
    ),
    description="Contract modification with cumulative catch-up adjustment",
)

REVENUE_MODIFICATION_CUMULATIVE_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Modification Prospective ------------------------------------------------

REVENUE_MODIFICATION_PROSPECTIVE = AccountingPolicy(
    name="RevenueModificationProspective",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.modification_prospective"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_ADJUSTMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="DEFERRED_REVENUE", credit_role="REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Modification amount must be positive",
        ),
    ),
    description="Contract modification with prospective treatment",
)

REVENUE_MODIFICATION_PROSPECTIVE_MAPPINGS = (
    ModuleLineMapping(role="DEFERRED_REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- Variable Consideration Update -------------------------------------------

REVENUE_VARIABLE_UPDATE = AccountingPolicy(
    name="RevenueVariableUpdate",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.variable_consideration_update"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_ADJUSTMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_RECEIVABLE", credit_role="REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Variable consideration amount must be positive",
        ),
    ),
    description="Variable consideration estimate updated",
)

REVENUE_VARIABLE_UPDATE_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="REVENUE", side="credit", ledger="GL"),
)


# --- License Renewal ----------------------------------------------------------

REVENUE_LICENSE_RENEWAL = AccountingPolicy(
    name="RevenueLicenseRenewal",
    version=1,
    trigger=PolicyTrigger(event_type="revenue.license_renewal"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_RECEIVABLE", credit_role="DEFERRED_REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Renewal amount must be positive",
        ),
    ),
    description="License renewal — deferred until recognition criteria met",
)

REVENUE_LICENSE_RENEWAL_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="DEFERRED_REVENUE", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (REVENUE_POINT_IN_TIME, REVENUE_POINT_IN_TIME_MAPPINGS),
    (REVENUE_OVER_TIME_INPUT, REVENUE_OVER_TIME_INPUT_MAPPINGS),
    (REVENUE_OVER_TIME_OUTPUT, REVENUE_OVER_TIME_OUTPUT_MAPPINGS),
    (REVENUE_PRICE_ALLOCATION, REVENUE_PRICE_ALLOCATION_MAPPINGS),
    (REVENUE_MODIFICATION_CUMULATIVE, REVENUE_MODIFICATION_CUMULATIVE_MAPPINGS),
    (REVENUE_MODIFICATION_PROSPECTIVE, REVENUE_MODIFICATION_PROSPECTIVE_MAPPINGS),
    (REVENUE_VARIABLE_UPDATE, REVENUE_VARIABLE_UPDATE_MAPPINGS),
    (REVENUE_LICENSE_RENEWAL, REVENUE_LICENSE_RENEWAL_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """
    Register all revenue profiles in kernel registries.

    Preconditions:
        - Kernel policy_bridge registry is initialized.

    Postconditions:
        - All 8 revenue profiles are registered with their mappings.
        - Each event_type has exactly one profile (P1).

    Raises:
        DuplicateRegistrationError: If any event_type is already registered.
    """
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "revenue_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

REVENUE_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
