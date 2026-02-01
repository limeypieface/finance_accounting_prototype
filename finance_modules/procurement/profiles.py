"""
Procurement Economic Profiles (``finance_modules.procurement.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the procurement module.  Each profile
maps a single event type to journal-line specifications using account
ROLES.

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new procurement event type requires ONLY a new profile.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise.
* Guard expression match causes REJECTED outcome.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).
* Encumbrance profiles support budget control compliance.

Profiles:
    POEncumbrance          — PO encumbrance: Dr Encumbrance / Cr Reserve
    POEncumbranceRelief    — Relief on receipt/invoice: Dr Reserve / Cr Encumbrance
    POCommitment           — Commitment recorded (memo): Dr Commitment / Cr Offset
    POCommitmentRelief     — Commitment relieved (memo): Dr Offset / Cr Commitment
    RequisitionCreated     — Requisition commitment: Dr Commitment / Cr Offset
    RequisitionConverted   — Req->PO: relieve commitment + create encumbrance
    POAmended              — PO amendment: adjust encumbrance delta
    ReceiptMatched         — 3-way match: relieve encumbrance + AP subledger
    QuantityVariance       — Qty variance: Dr Qty Variance / Cr Reserve
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
    QUANTITY_VARIANCE = "quantity_variance"  # qty received vs ordered


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


# --- Requisition Created -----------------------------------------------------

REQUISITION_CREATED = AccountingPolicy(
    name="RequisitionCreated",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.requisition_created"),
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
            message="Requisition amount must be positive",
        ),
    ),
    description="Purchase requisition commitment recorded",
)

REQUISITION_CREATED_MAPPINGS = (
    ModuleLineMapping(role="PURCHASE_COMMITMENT", side="debit", ledger="GL"),
    ModuleLineMapping(role="COMMITMENT_OFFSET", side="credit", ledger="GL"),
)


# --- Requisition Converted to PO ---------------------------------------------

REQUISITION_CONVERTED = AccountingPolicy(
    name="RequisitionConverted",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.requisition_converted"),
    meaning=PolicyMeaning(
        economic_type="BUDGETARY_ENCUMBRANCE",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        # Relieve requisition commitment
        LedgerEffect(
            ledger="GL",
            debit_role="COMMITMENT_OFFSET",
            credit_role="PURCHASE_COMMITMENT",
        ),
        # Create PO encumbrance
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
            message="Conversion amount must be positive",
        ),
    ),
    description="Requisition converted to PO: relieve commitment, create encumbrance",
)

REQUISITION_CONVERTED_MAPPINGS = (
    ModuleLineMapping(role="COMMITMENT_OFFSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="PURCHASE_COMMITMENT", side="credit", ledger="GL"),
    ModuleLineMapping(role="ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- PO Amended ---------------------------------------------------------------

PO_AMENDED = AccountingPolicy(
    name="POAmended",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.po_amended"),
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
            message="Amendment amount must be positive",
        ),
    ),
    description="PO amended: adjust encumbrance by delta",
)

PO_AMENDED_MAPPINGS = (
    ModuleLineMapping(role="ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="credit", ledger="GL"),
)


# --- Receipt Matched (3-way match) -------------------------------------------

RECEIPT_MATCHED = AccountingPolicy(
    name="ReceiptMatched",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.receipt_matched"),
    meaning=PolicyMeaning(
        economic_type="BUDGETARY_RELIEF",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        # Relieve encumbrance
        LedgerEffect(
            ledger="GL",
            debit_role="RESERVE_FOR_ENCUMBRANCE",
            credit_role="ENCUMBRANCE",
        ),
        # AP subledger: receipt creates liability
        LedgerEffect(
            ledger="AP",
            debit_role="INVOICE",
            credit_role="SUPPLIER_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Match amount must be positive",
        ),
    ),
    description="Receipt matched to PO: relieve encumbrance, create AP subledger entry",
)

RECEIPT_MATCHED_MAPPINGS = (
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ENCUMBRANCE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# --- Quantity Variance --------------------------------------------------------

QUANTITY_VARIANCE = AccountingPolicy(
    name="QuantityVariance",
    version=1,
    trigger=PolicyTrigger(event_type="procurement.quantity_variance"),
    meaning=PolicyMeaning(
        economic_type="QUANTITY_VARIANCE",
        quantity_field="payload.amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="QUANTITY_VARIANCE",
            credit_role="RESERVE_FOR_ENCUMBRANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Variance amount must be positive",
        ),
    ),
    description="Quantity variance between PO and receipt",
)

QUANTITY_VARIANCE_MAPPINGS = (
    ModuleLineMapping(role="QUANTITY_VARIANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (PO_ENCUMBRANCE, PO_ENCUMBRANCE_MAPPINGS),
    (PO_ENCUMBRANCE_RELIEF, PO_ENCUMBRANCE_RELIEF_MAPPINGS),
    (PO_COMMITMENT, PO_COMMITMENT_MAPPINGS),
    (PO_COMMITMENT_RELIEF, PO_COMMITMENT_RELIEF_MAPPINGS),
    (REQUISITION_CREATED, REQUISITION_CREATED_MAPPINGS),
    # RequisitionConverted not registered — service uses two-step posting
    # (commitment_relieved + po_encumbered) to avoid dual-GL-effect issue.
    (PO_AMENDED, PO_AMENDED_MAPPINGS),
    (RECEIPT_MATCHED, RECEIPT_MATCHED_MAPPINGS),
    (QUANTITY_VARIANCE, QUANTITY_VARIANCE_MAPPINGS),
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
