"""
Project Accounting Economic Profiles (``finance_modules.project.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the project accounting module.

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new project event type requires ONLY a new profile.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise.
* Guard expression match causes REJECTED outcome.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).
* Project cost and billing profiles support EVM compliance.

Profiles:
    ProjectCostRecorded      -- Dr PROJECT_WIP / Cr DIRECT_COST
    ProjectBillingMilestone  -- Dr CONTRACT_RECEIVABLE / Cr CONTRACT_REVENUE
    ProjectBillingTM         -- Dr UNBILLED_AR / Cr CONTRACT_REVENUE
    ProjectRevenueRecognized -- Dr UNBILLED_AR / Cr CONTRACT_REVENUE
    ProjectBudgetRevised     -- Dr PROJECT_WIP / Cr WIP_BILLED
    ProjectPhaseCompleted    -- Dr WIP_BILLED / Cr PROJECT_WIP
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

logger = get_logger("modules.project.profiles")

MODULE_NAME = "project"


class AccountRole(Enum):
    PROJECT_WIP = "project_wip"
    DIRECT_COST = "direct_cost"
    CONTRACT_RECEIVABLE = "contract_receivable"
    CONTRACT_REVENUE = "contract_revenue"
    UNBILLED_AR = "unbilled_ar"
    WIP_BILLED = "wip_billed"


# --- Project Cost Recorded ---------------------------------------------------

PROJECT_COST_RECORDED = AccountingPolicy(
    name="ProjectCostRecorded",
    version=1,
    trigger=PolicyTrigger(event_type="project.cost_recorded"),
    meaning=PolicyMeaning(
        economic_type="PROJECT_COST",
        dimensions=("project_id", "wbs_code"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="PROJECT_WIP", credit_role="DIRECT_COST"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Cost amount must be positive",
        ),
    ),
    description="Records project cost incurrence",
)

PROJECT_COST_RECORDED_MAPPINGS = (
    ModuleLineMapping(role="PROJECT_WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="DIRECT_COST", side="credit", ledger="GL"),
)


# --- Project Billing Milestone ------------------------------------------------

PROJECT_BILLING_MILESTONE = AccountingPolicy(
    name="ProjectBillingMilestone",
    version=1,
    trigger=PolicyTrigger(event_type="project.billing_milestone"),
    meaning=PolicyMeaning(
        economic_type="PROJECT_BILLING",
        dimensions=("project_id",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="CONTRACT_RECEIVABLE", credit_role="CONTRACT_REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_BILLING",
            message="Billing amount must be positive",
        ),
    ),
    description="Records milestone-based billing",
)

PROJECT_BILLING_MILESTONE_MAPPINGS = (
    ModuleLineMapping(role="CONTRACT_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_REVENUE", side="credit", ledger="GL"),
)


# --- Project Billing T&M -----------------------------------------------------

PROJECT_BILLING_TM = AccountingPolicy(
    name="ProjectBillingTM",
    version=1,
    trigger=PolicyTrigger(event_type="project.billing_tm"),
    meaning=PolicyMeaning(
        economic_type="PROJECT_BILLING",
        dimensions=("project_id",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="UNBILLED_AR", credit_role="CONTRACT_REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_BILLING",
            message="Billing amount must be positive",
        ),
    ),
    description="Records time and materials billing",
)

PROJECT_BILLING_TM_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_AR", side="debit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_REVENUE", side="credit", ledger="GL"),
)


# --- Project Revenue Recognized -----------------------------------------------

PROJECT_REVENUE_RECOGNIZED = AccountingPolicy(
    name="ProjectRevenueRecognized",
    version=1,
    trigger=PolicyTrigger(event_type="project.revenue_recognized"),
    meaning=PolicyMeaning(
        economic_type="REVENUE_RECOGNITION",
        dimensions=("project_id",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="UNBILLED_AR", credit_role="CONTRACT_REVENUE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_REVENUE",
            message="Revenue amount must be positive",
        ),
    ),
    description="Records project revenue recognition",
)

PROJECT_REVENUE_RECOGNIZED_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_AR", side="debit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_REVENUE", side="credit", ledger="GL"),
)


# --- Project Budget Revised ---------------------------------------------------

PROJECT_BUDGET_REVISED = AccountingPolicy(
    name="ProjectBudgetRevised",
    version=1,
    trigger=PolicyTrigger(event_type="project.budget_revised"),
    meaning=PolicyMeaning(
        economic_type="BUDGET_CHANGE",
        dimensions=("project_id", "wbs_code"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="PROJECT_WIP", credit_role="WIP_BILLED"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Budget revision amount must be positive",
        ),
    ),
    description="Records project budget revision",
)

PROJECT_BUDGET_REVISED_MAPPINGS = (
    ModuleLineMapping(role="PROJECT_WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP_BILLED", side="credit", ledger="GL"),
)


# --- Project Phase Completed --------------------------------------------------

PROJECT_PHASE_COMPLETED = AccountingPolicy(
    name="ProjectPhaseCompleted",
    version=1,
    trigger=PolicyTrigger(event_type="project.phase_completed"),
    meaning=PolicyMeaning(
        economic_type="PROJECT_COMPLETION",
        dimensions=("project_id",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="WIP_BILLED", credit_role="PROJECT_WIP"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Phase completion amount must be positive",
        ),
    ),
    description="Records project phase completion (WIP relief)",
)

PROJECT_PHASE_COMPLETED_MAPPINGS = (
    ModuleLineMapping(role="WIP_BILLED", side="debit", ledger="GL"),
    ModuleLineMapping(role="PROJECT_WIP", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (PROJECT_COST_RECORDED, PROJECT_COST_RECORDED_MAPPINGS),
    (PROJECT_BILLING_MILESTONE, PROJECT_BILLING_MILESTONE_MAPPINGS),
    (PROJECT_BILLING_TM, PROJECT_BILLING_TM_MAPPINGS),
    (PROJECT_REVENUE_RECOGNIZED, PROJECT_REVENUE_RECOGNIZED_MAPPINGS),
    (PROJECT_BUDGET_REVISED, PROJECT_BUDGET_REVISED_MAPPINGS),
    (PROJECT_PHASE_COMPLETED, PROJECT_PHASE_COMPLETED_MAPPINGS),
)


def register() -> None:
    """Register all project profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "project_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


PROJECT_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
