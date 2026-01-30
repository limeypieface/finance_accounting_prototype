"""
Work-in-Process Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Profiles:
    WipMaterialIssued       — Raw materials issued to work order
    WipLaborCharged         — Direct labor charged to work order
    WipOverheadApplied      — Overhead applied to work order
    WipCompletion           — Finished goods completed from work order
    WipScrap                — Scrap recorded on work order
    WipRework               — Rework costs charged to work order
    WipLaborVariance        — Labor efficiency variance at close
    WipMaterialVariance     — Material usage variance at close
    WipOverheadVariance     — Overhead over/under applied variance
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

logger = get_logger("modules.wip.profiles")

MODULE_NAME = "wip"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for WIP."""

    WIP = "work_in_process"
    RAW_MATERIALS = "raw_materials"
    FINISHED_GOODS = "finished_goods"
    LABOR_CLEARING = "labor_clearing"
    OVERHEAD_APPLIED = "overhead_applied"
    OVERHEAD_CONTROL = "overhead_control"
    LABOR_VARIANCE = "labor_variance"
    MATERIAL_VARIANCE = "material_variance"
    OVERHEAD_VARIANCE = "overhead_variance"
    SCRAP_EXPENSE = "scrap_expense"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Material Issued ----------------------------------------------------------

WIP_MATERIAL_ISSUED = AccountingPolicy(
    name="WipMaterialIssued",
    version=1,
    trigger=PolicyTrigger(event_type="wip.material_issued"),
    meaning=PolicyMeaning(
        economic_type="WIP_MATERIAL_ISSUE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="WIP", credit_role="RAW_MATERIALS"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Material issue quantity must be positive",
        ),
    ),
    description="Raw materials issued to work order",
)

WIP_MATERIAL_ISSUED_MAPPINGS = (
    ModuleLineMapping(role="WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="RAW_MATERIALS", side="credit", ledger="GL"),
)


# --- Labor Charged ------------------------------------------------------------

WIP_LABOR_CHARGED = AccountingPolicy(
    name="WipLaborCharged",
    version=1,
    trigger=PolicyTrigger(event_type="wip.labor_charged"),
    meaning=PolicyMeaning(
        economic_type="WIP_LABOR_CHARGE",
        quantity_field="payload.hours",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="WIP", credit_role="LABOR_CLEARING"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.hours <= 0",
            reason_code="INVALID_HOURS",
            message="Labor hours must be positive",
        ),
    ),
    description="Direct labor charged to work order",
)

WIP_LABOR_CHARGED_MAPPINGS = (
    ModuleLineMapping(role="WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
)


# --- Overhead Applied ---------------------------------------------------------

WIP_OVERHEAD_APPLIED = AccountingPolicy(
    name="WipOverheadApplied",
    version=1,
    trigger=PolicyTrigger(event_type="wip.overhead_applied"),
    meaning=PolicyMeaning(
        economic_type="WIP_OVERHEAD_APPLICATION",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="WIP", credit_role="OVERHEAD_APPLIED"),
    ),
    effective_from=date(2024, 1, 1),
    description="Overhead applied to work order",
)

WIP_OVERHEAD_APPLIED_MAPPINGS = (
    ModuleLineMapping(role="WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="OVERHEAD_APPLIED", side="credit", ledger="GL"),
)


# --- Completion ---------------------------------------------------------------

WIP_COMPLETION = AccountingPolicy(
    name="WipCompletion",
    version=1,
    trigger=PolicyTrigger(event_type="wip.completion"),
    meaning=PolicyMeaning(
        economic_type="WIP_COMPLETION",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="FINISHED_GOODS", credit_role="WIP"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Completion quantity must be positive",
        ),
    ),
    description="Finished goods completed from work order",
)

WIP_COMPLETION_MAPPINGS = (
    ModuleLineMapping(role="FINISHED_GOODS", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP", side="credit", ledger="GL"),
)


# --- Scrap --------------------------------------------------------------------

WIP_SCRAP = AccountingPolicy(
    name="WipScrap",
    version=1,
    trigger=PolicyTrigger(event_type="wip.scrap"),
    meaning=PolicyMeaning(
        economic_type="WIP_SCRAP",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="SCRAP_EXPENSE", credit_role="WIP"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Scrap quantity must be positive",
        ),
    ),
    description="Scrap recorded on work order",
)

WIP_SCRAP_MAPPINGS = (
    ModuleLineMapping(role="SCRAP_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP", side="credit", ledger="GL"),
)


# --- Rework -------------------------------------------------------------------

WIP_REWORK = AccountingPolicy(
    name="WipRework",
    version=1,
    trigger=PolicyTrigger(event_type="wip.rework"),
    meaning=PolicyMeaning(
        economic_type="WIP_REWORK",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="WIP", credit_role="LABOR_CLEARING"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Rework quantity must be positive",
        ),
    ),
    description="Rework costs charged to work order",
)

WIP_REWORK_MAPPINGS = (
    ModuleLineMapping(role="WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
)


# --- Labor Variance -----------------------------------------------------------

WIP_LABOR_VARIANCE = AccountingPolicy(
    name="WipLaborVariance",
    version=1,
    trigger=PolicyTrigger(event_type="wip.labor_variance"),
    meaning=PolicyMeaning(
        economic_type="WIP_LABOR_VARIANCE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="LABOR_VARIANCE", credit_role="WIP"),
    ),
    effective_from=date(2024, 1, 1),
    description="Labor efficiency variance at close",
)

WIP_LABOR_VARIANCE_MAPPINGS = (
    ModuleLineMapping(role="LABOR_VARIANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP", side="credit", ledger="GL"),
)


# --- Material Variance --------------------------------------------------------

WIP_MATERIAL_VARIANCE = AccountingPolicy(
    name="WipMaterialVariance",
    version=1,
    trigger=PolicyTrigger(event_type="wip.material_variance"),
    meaning=PolicyMeaning(
        economic_type="WIP_MATERIAL_VARIANCE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "work_order"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="MATERIAL_VARIANCE", credit_role="WIP"
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Material usage variance at close",
)

WIP_MATERIAL_VARIANCE_MAPPINGS = (
    ModuleLineMapping(role="MATERIAL_VARIANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP", side="credit", ledger="GL"),
)


# --- Overhead Variance --------------------------------------------------------

WIP_OVERHEAD_VARIANCE = AccountingPolicy(
    name="WipOverheadVariance",
    version=1,
    trigger=PolicyTrigger(event_type="wip.overhead_variance"),
    meaning=PolicyMeaning(
        economic_type="WIP_OVERHEAD_VARIANCE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OVERHEAD_APPLIED",
            credit_role="OVERHEAD_VARIANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Overhead over/under applied variance",
)

WIP_OVERHEAD_VARIANCE_MAPPINGS = (
    ModuleLineMapping(role="OVERHEAD_APPLIED", side="debit", ledger="GL"),
    ModuleLineMapping(role="OVERHEAD_VARIANCE", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (WIP_MATERIAL_ISSUED, WIP_MATERIAL_ISSUED_MAPPINGS),
    (WIP_LABOR_CHARGED, WIP_LABOR_CHARGED_MAPPINGS),
    (WIP_OVERHEAD_APPLIED, WIP_OVERHEAD_APPLIED_MAPPINGS),
    (WIP_COMPLETION, WIP_COMPLETION_MAPPINGS),
    (WIP_SCRAP, WIP_SCRAP_MAPPINGS),
    (WIP_REWORK, WIP_REWORK_MAPPINGS),
    (WIP_LABOR_VARIANCE, WIP_LABOR_VARIANCE_MAPPINGS),
    (WIP_MATERIAL_VARIANCE, WIP_MATERIAL_VARIANCE_MAPPINGS),
    (WIP_OVERHEAD_VARIANCE, WIP_OVERHEAD_VARIANCE_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all WIP profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "wip_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

WIP_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
