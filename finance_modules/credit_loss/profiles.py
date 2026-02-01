"""
Credit Loss Economic Profiles (``finance_modules.credit_loss.profiles``).

Responsibility
--------------
Declares all ``AccountingPolicy`` instances and companion
``ModuleLineMapping`` tuples for the credit loss (ASC 326 / CECL) module.

Architecture position
---------------------
**Modules layer** -- thin ERP glue (this layer).
Profiles are registered into kernel registries by ``register()`` and
resolved at posting time by the interpretation pipeline (L1).

Invariants enforced
-------------------
* R14 -- No ``if/switch`` on event_type in the posting engine.
* R15 -- Adding a new credit loss event requires ONLY a new profile.
* L1  -- Account roles are resolved to COA codes at posting time.

Failure modes
-------------
* Duplicate profile names cause ``register_rich_profile`` to raise.
* Guard expression match causes REJECTED outcome.

Audit relevance
---------------
* Profile version numbers support replay compatibility (R23).
* ASC 326 compliance requires traceable loss provisioning profiles.

Profiles:
    CreditLossProvision   — Dr BAD_DEBT_EXPENSE / Cr ALLOWANCE_DOUBTFUL
    CreditLossAdjustment  — Dr BAD_DEBT_EXPENSE / Cr ALLOWANCE_DOUBTFUL
    CreditLossWriteOff    — Dr ALLOWANCE_DOUBTFUL / Cr ACCOUNTS_RECEIVABLE
    CreditLossRecovery    — Dr ACCOUNTS_RECEIVABLE / Cr ALLOWANCE_DOUBTFUL
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

logger = get_logger("modules.credit_loss.profiles")

MODULE_NAME = "credit_loss"


class AccountRole(Enum):
    BAD_DEBT_EXPENSE = "bad_debt_expense"
    ALLOWANCE_DOUBTFUL = "allowance_doubtful"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"


# --- Credit Loss Provision ---------------------------------------------------

CREDIT_LOSS_PROVISION = AccountingPolicy(
    name="CreditLossProvision",
    version=1,
    trigger=PolicyTrigger(event_type="credit_loss.provision"),
    meaning=PolicyMeaning(
        economic_type="CREDIT_LOSS_PROVISION",
        dimensions=("segment",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="BAD_DEBT_EXPENSE", credit_role="ALLOWANCE_DOUBTFUL"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_PROVISION",
            message="Provision amount must be positive",
        ),
    ),
    description="Records credit loss provision (CECL)",
)

CREDIT_LOSS_PROVISION_MAPPINGS = (
    ModuleLineMapping(role="BAD_DEBT_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ALLOWANCE_DOUBTFUL", side="credit", ledger="GL"),
)


# --- Credit Loss Adjustment --------------------------------------------------

CREDIT_LOSS_ADJUSTMENT = AccountingPolicy(
    name="CreditLossAdjustment",
    version=1,
    trigger=PolicyTrigger(event_type="credit_loss.adjustment"),
    meaning=PolicyMeaning(
        economic_type="CREDIT_LOSS_ADJUSTMENT",
        dimensions=("segment",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="BAD_DEBT_EXPENSE", credit_role="ALLOWANCE_DOUBTFUL"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_ADJUSTMENT",
            message="Adjustment amount must be positive",
        ),
    ),
    description="Adjusts credit loss provision",
)

CREDIT_LOSS_ADJUSTMENT_MAPPINGS = (
    ModuleLineMapping(role="BAD_DEBT_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ALLOWANCE_DOUBTFUL", side="credit", ledger="GL"),
)


# --- Credit Loss Write-Off ---------------------------------------------------

CREDIT_LOSS_WRITE_OFF = AccountingPolicy(
    name="CreditLossWriteOff",
    version=1,
    trigger=PolicyTrigger(event_type="credit_loss.write_off"),
    meaning=PolicyMeaning(
        economic_type="CREDIT_LOSS_WRITE_OFF",
        dimensions=("segment",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ALLOWANCE_DOUBTFUL", credit_role="ACCOUNTS_RECEIVABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_WRITE_OFF",
            message="Write-off amount must be positive",
        ),
    ),
    description="Write-off against allowance",
)

CREDIT_LOSS_WRITE_OFF_MAPPINGS = (
    ModuleLineMapping(role="ALLOWANCE_DOUBTFUL", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="credit", ledger="GL"),
)


# --- Credit Loss Recovery -----------------------------------------------------

CREDIT_LOSS_RECOVERY = AccountingPolicy(
    name="CreditLossRecovery",
    version=1,
    trigger=PolicyTrigger(event_type="credit_loss.recovery"),
    meaning=PolicyMeaning(
        economic_type="CREDIT_LOSS_RECOVERY",
        dimensions=("segment",),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="ACCOUNTS_RECEIVABLE", credit_role="ALLOWANCE_DOUBTFUL"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_RECOVERY",
            message="Recovery amount must be positive",
        ),
    ),
    description="Recovery of previously written-off amount",
)

CREDIT_LOSS_RECOVERY_MAPPINGS = (
    ModuleLineMapping(role="ACCOUNTS_RECEIVABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ALLOWANCE_DOUBTFUL", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (CREDIT_LOSS_PROVISION, CREDIT_LOSS_PROVISION_MAPPINGS),
    (CREDIT_LOSS_ADJUSTMENT, CREDIT_LOSS_ADJUSTMENT_MAPPINGS),
    (CREDIT_LOSS_WRITE_OFF, CREDIT_LOSS_WRITE_OFF_MAPPINGS),
    (CREDIT_LOSS_RECOVERY, CREDIT_LOSS_RECOVERY_MAPPINGS),
)


def register() -> None:
    """Register all credit loss profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "credit_loss_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


CREDIT_LOSS_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
