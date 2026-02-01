"""
Lease Accounting Economic Profiles (``finance_modules.lease.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the ASC 842 lease accounting module.

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new lease event type requires ONLY a new profile.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise.
* Guard expression match causes REJECTED outcome.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).
* ASC 842 compliance requires traceable lease classification profiles.

Profiles:
    LeaseFinanceInitial          — Finance lease initial recognition
    LeaseOperatingInitial        — Operating lease initial recognition
    LeasePaymentMade             — Lease payment recorded
    LeaseInterestAccrued         — Interest accrual on finance lease
    LeaseAmortizationFinance     — ROU amortization (finance)
    LeaseAmortizationOperating   — ROU amortization (operating)
    LeaseModified                — Lease modification remeasurement
    LeaseTerminatedEarly         — Early termination
    LeaseImpairment              — ROU asset impairment
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

logger = get_logger("modules.lease.profiles")

MODULE_NAME = "lease"


# --- Finance Lease Initial Recognition ----------------------------------------

LEASE_FINANCE_INITIAL = AccountingPolicy(
    name="LeaseFinanceInitial",
    version=1,
    trigger=PolicyTrigger(event_type="lease.finance_initial"),
    meaning=PolicyMeaning(
        economic_type="LEASE_INITIAL_RECOGNITION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ROU_ASSET", credit_role="LEASE_LIABILITY"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Initial recognition amount must be positive",
        ),
    ),
    description="Finance lease initial recognition: ROU asset and lease liability",
)

LEASE_FINANCE_INITIAL_MAPPINGS = (
    ModuleLineMapping(role="ROU_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="LEASE_LIABILITY", side="credit", ledger="GL"),
)


# --- Operating Lease Initial Recognition ------------------------------------

LEASE_OPERATING_INITIAL = AccountingPolicy(
    name="LeaseOperatingInitial",
    version=1,
    trigger=PolicyTrigger(event_type="lease.operating_initial"),
    meaning=PolicyMeaning(
        economic_type="LEASE_INITIAL_RECOGNITION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ROU_ASSET", credit_role="LEASE_LIABILITY"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Initial recognition amount must be positive",
        ),
    ),
    description="Operating lease initial recognition: ROU asset and lease liability",
)

LEASE_OPERATING_INITIAL_MAPPINGS = (
    ModuleLineMapping(role="ROU_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="LEASE_LIABILITY", side="credit", ledger="GL"),
)


# --- Lease Payment Made ------------------------------------------------------

LEASE_PAYMENT_MADE = AccountingPolicy(
    name="LeasePaymentMade",
    version=1,
    trigger=PolicyTrigger(event_type="lease.payment_made"),
    meaning=PolicyMeaning(
        economic_type="LEASE_PAYMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="LEASE_LIABILITY", credit_role="CASH"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Payment amount must be positive",
        ),
    ),
    description="Lease payment reducing liability",
)

LEASE_PAYMENT_MADE_MAPPINGS = (
    ModuleLineMapping(role="LEASE_LIABILITY", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- Interest Accrual --------------------------------------------------------

LEASE_INTEREST_ACCRUED = AccountingPolicy(
    name="LeaseInterestAccrued",
    version=1,
    trigger=PolicyTrigger(event_type="lease.interest_accrued"),
    meaning=PolicyMeaning(
        economic_type="LEASE_INTEREST",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="LEASE_INTEREST", credit_role="LEASE_LIABILITY"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Interest amount must be positive",
        ),
    ),
    description="Interest accrual on lease liability",
)

LEASE_INTEREST_ACCRUED_MAPPINGS = (
    ModuleLineMapping(role="LEASE_INTEREST", side="debit", ledger="GL"),
    ModuleLineMapping(role="LEASE_LIABILITY", side="credit", ledger="GL"),
)


# --- Finance Lease Amortization ----------------------------------------------

LEASE_AMORTIZATION_FINANCE = AccountingPolicy(
    name="LeaseAmortizationFinance",
    version=1,
    trigger=PolicyTrigger(event_type="lease.amortization_finance"),
    meaning=PolicyMeaning(
        economic_type="LEASE_AMORTIZATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ROU_AMORTIZATION", credit_role="ROU_ASSET"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Amortization amount must be positive",
        ),
    ),
    description="Finance lease ROU asset amortization",
)

LEASE_AMORTIZATION_FINANCE_MAPPINGS = (
    ModuleLineMapping(role="ROU_AMORTIZATION", side="debit", ledger="GL"),
    ModuleLineMapping(role="ROU_ASSET", side="credit", ledger="GL"),
)


# --- Operating Lease Amortization -------------------------------------------

LEASE_AMORTIZATION_OPERATING = AccountingPolicy(
    name="LeaseAmortizationOperating",
    version=1,
    trigger=PolicyTrigger(event_type="lease.amortization_operating"),
    meaning=PolicyMeaning(
        economic_type="LEASE_AMORTIZATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ROU_AMORTIZATION", credit_role="ROU_ASSET"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Amortization amount must be positive",
        ),
    ),
    description="Operating lease ROU asset amortization (straight-line total expense)",
)

LEASE_AMORTIZATION_OPERATING_MAPPINGS = (
    ModuleLineMapping(role="ROU_AMORTIZATION", side="debit", ledger="GL"),
    ModuleLineMapping(role="ROU_ASSET", side="credit", ledger="GL"),
)


# --- Lease Modified ----------------------------------------------------------

LEASE_MODIFIED = AccountingPolicy(
    name="LeaseModified",
    version=1,
    trigger=PolicyTrigger(event_type="lease.modified"),
    meaning=PolicyMeaning(
        economic_type="LEASE_MODIFICATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ROU_ASSET", credit_role="LEASE_LIABILITY"),
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
    description="Lease modification remeasurement",
)

LEASE_MODIFIED_MAPPINGS = (
    ModuleLineMapping(role="ROU_ASSET", side="debit", ledger="GL"),
    ModuleLineMapping(role="LEASE_LIABILITY", side="credit", ledger="GL"),
)


# --- Early Termination -------------------------------------------------------

LEASE_TERMINATED_EARLY = AccountingPolicy(
    name="LeaseTerminatedEarly",
    version=1,
    trigger=PolicyTrigger(event_type="lease.terminated_early"),
    meaning=PolicyMeaning(
        economic_type="LEASE_TERMINATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="LEASE_LIABILITY", credit_role="ROU_ASSET"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Termination amount must be positive",
        ),
    ),
    description="Early lease termination — derecognize ROU and liability",
)

LEASE_TERMINATED_EARLY_MAPPINGS = (
    ModuleLineMapping(role="LEASE_LIABILITY", side="debit", ledger="GL"),
    ModuleLineMapping(role="ROU_ASSET", side="credit", ledger="GL"),
)


# --- Lease Impairment --------------------------------------------------------

LEASE_IMPAIRMENT = AccountingPolicy(
    name="LeaseImpairment",
    version=1,
    trigger=PolicyTrigger(event_type="lease.impairment"),
    meaning=PolicyMeaning(
        economic_type="LEASE_IMPAIRMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="IMPAIRMENT_LOSS", credit_role="ROU_ASSET"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Impairment amount must be positive",
        ),
    ),
    description="ROU asset impairment",
)

LEASE_IMPAIRMENT_MAPPINGS = (
    ModuleLineMapping(role="IMPAIRMENT_LOSS", side="debit", ledger="GL"),
    ModuleLineMapping(role="ROU_ASSET", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (LEASE_FINANCE_INITIAL, LEASE_FINANCE_INITIAL_MAPPINGS),
    (LEASE_OPERATING_INITIAL, LEASE_OPERATING_INITIAL_MAPPINGS),
    (LEASE_PAYMENT_MADE, LEASE_PAYMENT_MADE_MAPPINGS),
    (LEASE_INTEREST_ACCRUED, LEASE_INTEREST_ACCRUED_MAPPINGS),
    (LEASE_AMORTIZATION_FINANCE, LEASE_AMORTIZATION_FINANCE_MAPPINGS),
    (LEASE_AMORTIZATION_OPERATING, LEASE_AMORTIZATION_OPERATING_MAPPINGS),
    (LEASE_MODIFIED, LEASE_MODIFIED_MAPPINGS),
    (LEASE_TERMINATED_EARLY, LEASE_TERMINATED_EARLY_MAPPINGS),
    (LEASE_IMPAIRMENT, LEASE_IMPAIRMENT_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all lease profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "lease_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

LEASE_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
