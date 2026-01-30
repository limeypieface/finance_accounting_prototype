"""
Procurement Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Profiles:
    POEncumbrance          — PO encumbrance: Dr Encumbrance / Cr Reserve
    POEncumbranceRelief    — Relief on receipt/invoice: Dr Reserve / Cr Encumbrance
    POCommitment           — Commitment recorded (memo): Dr Commitment / Cr Offset
    POCommitmentRelief     — Commitment relieved (memo): Dr Offset / Cr Commitment
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

logger = get_logger("modules.procurement.profiles")

MODULE_NAME = "procurement"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Procurement."""

    ENCUMBRANCE = "encumbrance"  # budgetary control
    RESERVE_FOR_ENCUMBRANCE = "reserve_for_encumbrance"
    PURCHASE_COMMITMENT = "purchase_commitment"  # memo account
    COMMITMENT_OFFSET = "commitment_offset"


# =============================================================================
# Profile definitions
# =============================================================================


# --- PO Encumbrance ----------------------------------------------------------

PO_ENCUMBRANCE = AccountingPolicy(
    name="POEncumbrance",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.po_encumbered"),
    meaning=PolicyMeaning(
        economic_type="BUDGETARY_ENCUMBRANCE",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ENCUMBRANCE",
            credit_role="RESERVE_FOR_ENCUMBRANCE",
        ),
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
    description="Purchase order encumbrance recorded",
)

PO_ENCUMBRANCE_MAPPINGS = (
    ModuleLineMapping(role="ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- PO Encumbrance Relief ---------------------------------------------------

PO_ENCUMBRANCE_RELIEF = AccountingPolicy(
    name="POEncumbranceRelief",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.po_relief"),
    meaning=PolicyMeaning(
        economic_type="BUDGETARY_RELIEF",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="RESERVE_FOR_ENCUMBRANCE",
            credit_role="ENCUMBRANCE",
        ),
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
    description="Encumbrance relieved upon receipt/invoice",
)

PO_ENCUMBRANCE_RELIEF_MAPPINGS = (
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- Purchase Commitment (memo) ----------------------------------------------

PO_COMMITMENT = AccountingPolicy(
    name="POCommitment",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.commitment_recorded"),
    meaning=PolicyMeaning(
        economic_type="MEMO_COMMITMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="PURCHASE_COMMITMENT",
            credit_role="COMMITMENT_OFFSET",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Commitment amount must be positive",
        ),
    ),
    description="Purchase commitment recorded (memo)",
)

PO_COMMITMENT_MAPPINGS = (
    ModuleLineMapping(role="PURCHASE_COMMITMENT", side="debit", ledger="GL"),
    ModuleLineMapping(role="COMMITMENT_OFFSET", side="credit", ledger="GL"),
)


# --- Purchase Commitment Relief (memo) ---------------------------------------

PO_COMMITMENT_RELIEF = AccountingPolicy(
    name="POCommitmentRelief",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.commitment_relieved"),
    meaning=PolicyMeaning(
        economic_type="MEMO_RELIEF",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="COMMITMENT_OFFSET",
            credit_role="PURCHASE_COMMITMENT",
        ),
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
    description="Commitment relieved (memo)",
)

PO_COMMITMENT_RELIEF_MAPPINGS = (
    ModuleLineMapping(role="COMMITMENT_OFFSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="PURCHASE_COMMITMENT", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (PO_ENCUMBRANCE, PO_ENCUMBRANCE_MAPPINGS),
    (PO_ENCUMBRANCE_RELIEF, PO_ENCUMBRANCE_RELIEF_MAPPINGS),
    (PO_COMMITMENT, PO_COMMITMENT_MAPPINGS),
    (PO_COMMITMENT_RELIEF, PO_COMMITMENT_RELIEF_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all procurement profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "procurement_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

PROCUREMENT_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
