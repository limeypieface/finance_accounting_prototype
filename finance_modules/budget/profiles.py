"""
Budgeting Economic Profiles (``finance_modules.budget.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the budget module.  Each profile maps a
single event type to journal-line specifications using account ROLES.

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new budget event type requires ONLY a new profile.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise.
* Guard expression match causes REJECTED outcome.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).

Profiles:
    BudgetEntry               — Budget memo posting
    BudgetTransfer            — Budget transfer between accounts
    EncumbranceCommit         — Encumbrance commitment
    EncumbranceRelieve        — Encumbrance relief
    EncumbranceCancel         — Encumbrance cancellation
    ForecastUpdate            — Forecast update posting
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
from finance_kernel.logging_config import get_logger

logger = get_logger("modules.budget.profiles")

MODULE_NAME = "budget"


# --- Budget Entry (Memo) -----------------------------------------------------

BUDGET_ENTRY = AccountingPolicy(
    name="BudgetEntry",
    version=1,
    trigger=PolicyTrigger(event_type="budget.entry"),
    meaning=PolicyMeaning(
        economic_type="BUDGET_POSTING",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="BUDGET_CONTROL", credit_role="BUDGET_OFFSET"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Budget entry amount must be positive",
        ),
    ),
    description="Budget memo posting entry",
)

BUDGET_ENTRY_MAPPINGS = (
    ModuleLineMapping(role="BUDGET_CONTROL", side="debit", ledger="GL"),
    ModuleLineMapping(role="BUDGET_OFFSET", side="credit", ledger="GL"),
)


# --- Budget Transfer ---------------------------------------------------------

BUDGET_TRANSFER = AccountingPolicy(
    name="BudgetTransfer",
    version=1,
    trigger=PolicyTrigger(event_type="budget.transfer"),
    meaning=PolicyMeaning(
        economic_type="BUDGET_TRANSFER",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="BUDGET_CONTROL", credit_role="BUDGET_CONTROL"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Transfer amount must be positive",
        ),
    ),
    description="Budget transfer between accounts",
)

BUDGET_TRANSFER_MAPPINGS = (
    ModuleLineMapping(role="BUDGET_CONTROL", side="debit", ledger="GL"),
    ModuleLineMapping(role="BUDGET_CONTROL", side="credit", ledger="GL"),
)


# --- Encumbrance Commit ------------------------------------------------------

ENCUMBRANCE_COMMIT = AccountingPolicy(
    name="EncumbranceCommit",
    version=1,
    trigger=PolicyTrigger(event_type="budget.encumbrance_commit"),
    meaning=PolicyMeaning(
        economic_type="ENCUMBRANCE_COMMITMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ENCUMBRANCE", credit_role="RESERVE_FOR_ENCUMBRANCE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Encumbrance amount must be positive",
        ),
    ),
    description="Encumbrance commitment against budget",
)

ENCUMBRANCE_COMMIT_MAPPINGS = (
    ModuleLineMapping(role="ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- Encumbrance Relieve -----------------------------------------------------

ENCUMBRANCE_RELIEVE = AccountingPolicy(
    name="EncumbranceRelieve",
    version=1,
    trigger=PolicyTrigger(event_type="budget.encumbrance_relieve"),
    meaning=PolicyMeaning(
        economic_type="ENCUMBRANCE_RELIEF",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="RESERVE_FOR_ENCUMBRANCE", credit_role="ENCUMBRANCE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Relief amount must be positive",
        ),
    ),
    description="Encumbrance relief when invoice received",
)

ENCUMBRANCE_RELIEVE_MAPPINGS = (
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- Encumbrance Cancel ------------------------------------------------------

ENCUMBRANCE_CANCEL = AccountingPolicy(
    name="EncumbranceCancel",
    version=1,
    trigger=PolicyTrigger(event_type="budget.encumbrance_cancel"),
    meaning=PolicyMeaning(
        economic_type="ENCUMBRANCE_CANCELLATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="RESERVE_FOR_ENCUMBRANCE", credit_role="ENCUMBRANCE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Cancel amount must be positive",
        ),
    ),
    description="Encumbrance cancellation",
)

ENCUMBRANCE_CANCEL_MAPPINGS = (
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- Forecast Update ----------------------------------------------------------

FORECAST_UPDATE = AccountingPolicy(
    name="ForecastUpdate",
    version=1,
    trigger=PolicyTrigger(event_type="budget.forecast_update"),
    meaning=PolicyMeaning(
        economic_type="BUDGET_FORECAST",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="BUDGET_CONTROL", credit_role="BUDGET_OFFSET"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Forecast amount must be positive",
        ),
    ),
    description="Forecast update memo posting",
)

FORECAST_UPDATE_MAPPINGS = (
    ModuleLineMapping(role="BUDGET_CONTROL", side="debit", ledger="GL"),
    ModuleLineMapping(role="BUDGET_OFFSET", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (BUDGET_ENTRY, BUDGET_ENTRY_MAPPINGS),
    (BUDGET_TRANSFER, BUDGET_TRANSFER_MAPPINGS),
    (ENCUMBRANCE_COMMIT, ENCUMBRANCE_COMMIT_MAPPINGS),
    (ENCUMBRANCE_RELIEVE, ENCUMBRANCE_RELIEVE_MAPPINGS),
    (ENCUMBRANCE_CANCEL, ENCUMBRANCE_CANCEL_MAPPINGS),
    (FORECAST_UPDATE, FORECAST_UPDATE_MAPPINGS),
)


def register() -> None:
    """Register all budget profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)
    logger.info("budget_profiles_registered", extra={"profile_count": len(_ALL_PROFILES)})


BUDGET_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
