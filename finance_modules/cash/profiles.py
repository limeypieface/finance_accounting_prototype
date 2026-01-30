"""
Cash Economic Profiles — Kernel format.

Merged authoritative profiles from kernel bank_profiles (guards, where-clauses,
multi-ledger) and module cash profiles (line mappings, wire/recon scenarios).
Each profile is a kernel AccountingPolicy with companion ModuleLineMapping tuples
for intent construction.

Profiles:
    CashDeposit                 — Bank deposit: Dr Bank / Cr Undeposited Funds
    CashWithdrawalExpense       — Expense withdrawal: Dr Expense / Cr Bank
    CashWithdrawalSupplier      — Supplier payment: Cr Bank (AP side linked)
    CashWithdrawalPayroll       — Payroll disbursement: Dr Payroll Clrg / Cr Bank
    CashBankFee                 — Bank service charge: Dr Fee Expense / Cr Cash
    CashInterestEarned          — Interest income: Dr Cash / Cr Interest Income
    CashTransfer                — Inter-account transfer: Dr Dest Bank / Cr Src Bank
    CashWireTransferOut         — Outbound wire: Dr Transit / Cr Cash
    CashWireTransferCleared     — Wire confirmed: Dr Cash / Cr Transit
    CashReconciliation          — Recon adjustment: Dr/Cr Cash vs Variance
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

logger = get_logger("modules.cash.profiles")

MODULE_NAME = "cash"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Cash Management."""

    CASH = "cash"
    BANK_FEE_EXPENSE = "bank_fee_expense"
    INTEREST_INCOME = "interest_income"
    RECON_VARIANCE = "recon_variance"
    CASH_IN_TRANSIT = "cash_in_transit"
    UNDEPOSITED_FUNDS = "undeposited_funds"
    EXPENSE = "expense"
    PAYROLL_CLEARING = "payroll_clearing"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Deposit -----------------------------------------------------------------

CASH_DEPOSIT = AccountingPolicy(
    name="CashDeposit",
    version=1,
    trigger=PolicyTrigger(event_type="cash.deposit"),
    meaning=PolicyMeaning(
        economic_type="BANK_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="BANK",
            credit_role="UNDEPOSITED_FUNDS",
        ),
        LedgerEffect(
            ledger="BANK",
            debit_role="AVAILABLE",
            credit_role="DEPOSIT",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Deposit amount must be positive",
        ),
    ),
    description="Records bank deposit, moving funds from undeposited to bank",
)

CASH_DEPOSIT_MAPPINGS = (
    ModuleLineMapping(role="BANK", side="debit", ledger="GL"),
    ModuleLineMapping(role="UNDEPOSITED_FUNDS", side="credit", ledger="GL"),
    ModuleLineMapping(role="AVAILABLE", side="debit", ledger="BANK"),
    ModuleLineMapping(role="DEPOSIT", side="credit", ledger="BANK"),
)


# --- Withdrawal — Expense ----------------------------------------------------

CASH_WITHDRAWAL_EXPENSE = AccountingPolicy(
    name="CashWithdrawalExpense",
    version=1,
    trigger=PolicyTrigger(
        event_type="cash.withdrawal",
        where=(("payload.destination_type", "EXPENSE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE",
            credit_role="BANK",
        ),
        LedgerEffect(
            ledger="BANK",
            debit_role="WITHDRAWAL",
            credit_role="AVAILABLE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Withdrawal amount must be positive",
        ),
    ),
    description="Records bank withdrawal for direct expense",
)

CASH_WITHDRAWAL_EXPENSE_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="BANK", side="credit", ledger="GL"),
    ModuleLineMapping(role="WITHDRAWAL", side="debit", ledger="BANK"),
    ModuleLineMapping(role="AVAILABLE", side="credit", ledger="BANK"),
)


# --- Withdrawal — Supplier Payment -------------------------------------------

CASH_WITHDRAWAL_SUPPLIER = AccountingPolicy(
    name="CashWithdrawalSupplier",
    version=1,
    trigger=PolicyTrigger(
        event_type="cash.withdrawal",
        where=(("payload.destination_type", "SUPPLIER_PAYMENT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        # AP clearing is handled by linked AP payment event
        LedgerEffect(
            ledger="BANK",
            debit_role="WITHDRAWAL",
            credit_role="AVAILABLE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records bank withdrawal for supplier payment",
)

CASH_WITHDRAWAL_SUPPLIER_MAPPINGS = (
    ModuleLineMapping(role="WITHDRAWAL", side="debit", ledger="BANK"),
    ModuleLineMapping(role="AVAILABLE", side="credit", ledger="BANK"),
)


# --- Withdrawal — Payroll ----------------------------------------------------

CASH_WITHDRAWAL_PAYROLL = AccountingPolicy(
    name="CashWithdrawalPayroll",
    version=1,
    trigger=PolicyTrigger(
        event_type="cash.withdrawal",
        where=(("payload.destination_type", "PAYROLL"),),
    ),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="PAYROLL_CLEARING",
            credit_role="BANK",
        ),
        LedgerEffect(
            ledger="BANK",
            debit_role="WITHDRAWAL",
            credit_role="AVAILABLE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records bank withdrawal for payroll disbursement",
)

CASH_WITHDRAWAL_PAYROLL_MAPPINGS = (
    ModuleLineMapping(role="PAYROLL_CLEARING", side="debit", ledger="GL"),
    ModuleLineMapping(role="BANK", side="credit", ledger="GL"),
    ModuleLineMapping(role="WITHDRAWAL", side="debit", ledger="BANK"),
    ModuleLineMapping(role="AVAILABLE", side="credit", ledger="BANK"),
)


# --- Bank Fee ----------------------------------------------------------------

CASH_BANK_FEE = AccountingPolicy(
    name="CashBankFee",
    version=1,
    trigger=PolicyTrigger(event_type="cash.bank_fee"),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="BANK_FEE_EXPENSE",
            credit_role="CASH",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Bank fee amount must be positive",
        ),
    ),
    description="Bank service charge or fee",
)

CASH_BANK_FEE_MAPPINGS = (
    ModuleLineMapping(
        role="BANK_FEE_EXPENSE", side="debit", ledger="GL",
    ),
    ModuleLineMapping(
        role="CASH", side="credit", ledger="GL",
    ),
)


# --- Interest Earned ---------------------------------------------------------

CASH_INTEREST_EARNED = AccountingPolicy(
    name="CashInterestEarned",
    version=1,
    trigger=PolicyTrigger(event_type="cash.interest_earned"),
    meaning=PolicyMeaning(
        economic_type="BANK_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="INTEREST_INCOME",
        ),
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
    description="Interest income on bank balance",
)

CASH_INTEREST_EARNED_MAPPINGS = (
    ModuleLineMapping(
        role="CASH", side="debit", ledger="GL",
    ),
    ModuleLineMapping(
        role="INTEREST_INCOME", side="credit", ledger="GL",
    ),
)


# --- Transfer (inter-account) -----------------------------------------------

CASH_TRANSFER = AccountingPolicy(
    name="CashTransfer",
    version=1,
    trigger=PolicyTrigger(event_type="cash.transfer"),
    meaning=PolicyMeaning(
        economic_type="BANK_TRANSFER",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="BANK_DESTINATION",
            credit_role="BANK_SOURCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Transfer amount must be positive",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.from_bank_account_code == payload.to_bank_account_code",
            reason_code="SAME_ACCOUNT",
            message="Cannot transfer to the same account",
        ),
    ),
    description="Records inter-account bank transfer",
)

CASH_TRANSFER_MAPPINGS = (
    ModuleLineMapping(
        role="BANK_DESTINATION", side="debit", ledger="GL",
    ),
    ModuleLineMapping(
        role="BANK_SOURCE", side="credit", ledger="GL",
    ),
)


# --- Wire Transfer Out -------------------------------------------------------

CASH_WIRE_TRANSFER_OUT = AccountingPolicy(
    name="CashWireTransferOut",
    version=1,
    trigger=PolicyTrigger(event_type="cash.wire_transfer_out"),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH_IN_TRANSIT",
            credit_role="CASH",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Wire transfer amount must be positive",
        ),
    ),
    description="Outbound wire transfer (uses transit account)",
)

CASH_WIRE_TRANSFER_OUT_MAPPINGS = (
    ModuleLineMapping(role="CASH_IN_TRANSIT", side="debit", ledger="GL"),
    ModuleLineMapping(
        role="CASH", side="credit", ledger="GL",
    ),
)


# --- Wire Transfer Cleared ---------------------------------------------------

CASH_WIRE_TRANSFER_CLEARED = AccountingPolicy(
    name="CashWireTransferCleared",
    version=1,
    trigger=PolicyTrigger(event_type="cash.wire_transfer_cleared"),
    meaning=PolicyMeaning(
        economic_type="BANK_INCREASE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="CASH_IN_TRANSIT",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Wire transfer confirmed by receiving bank",
)

CASH_WIRE_TRANSFER_CLEARED_MAPPINGS = (
    ModuleLineMapping(
        role="CASH", side="debit", ledger="GL",
    ),
    ModuleLineMapping(role="CASH_IN_TRANSIT", side="credit", ledger="GL"),
)


# --- Reconciliation Adjustment -----------------------------------------------

CASH_RECONCILIATION = AccountingPolicy(
    name="CashReconciliation",
    version=1,
    trigger=PolicyTrigger(event_type="cash.reconciliation"),
    meaning=PolicyMeaning(
        economic_type="RECONCILIATION",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CASH",
            credit_role="RECON_VARIANCE",
        ),
        LedgerEffect(
            ledger="BANK",
            debit_role="RECONCILED",
            credit_role="PENDING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Adjustment discovered during bank reconciliation",
)

CASH_RECONCILIATION_MAPPINGS = (
    ModuleLineMapping(
        role="CASH", side="debit", ledger="GL",
    ),
    ModuleLineMapping(
        role="RECON_VARIANCE", side="credit", ledger="GL",
    ),
    ModuleLineMapping(role="RECONCILED", side="debit", ledger="BANK"),
    ModuleLineMapping(role="PENDING", side="credit", ledger="BANK"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (CASH_DEPOSIT, CASH_DEPOSIT_MAPPINGS),
    (CASH_WITHDRAWAL_EXPENSE, CASH_WITHDRAWAL_EXPENSE_MAPPINGS),
    (CASH_WITHDRAWAL_SUPPLIER, CASH_WITHDRAWAL_SUPPLIER_MAPPINGS),
    (CASH_WITHDRAWAL_PAYROLL, CASH_WITHDRAWAL_PAYROLL_MAPPINGS),
    (CASH_BANK_FEE, CASH_BANK_FEE_MAPPINGS),
    (CASH_INTEREST_EARNED, CASH_INTEREST_EARNED_MAPPINGS),
    (CASH_TRANSFER, CASH_TRANSFER_MAPPINGS),
    (CASH_WIRE_TRANSFER_OUT, CASH_WIRE_TRANSFER_OUT_MAPPINGS),
    (CASH_WIRE_TRANSFER_CLEARED, CASH_WIRE_TRANSFER_CLEARED_MAPPINGS),
    (CASH_RECONCILIATION, CASH_RECONCILIATION_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all cash profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "cash_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

CASH_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
