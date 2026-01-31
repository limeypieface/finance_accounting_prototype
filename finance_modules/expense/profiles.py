"""
Travel & Expense Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Profiles:
    ExpenseReportApproved       — Report approved: Dr Expense / Cr Employee Payable
    ExpenseReportBillable       — Billable report: Dr Project WIP / Cr Employee Payable
    ExpenseReimbursementPaid    — Reimbursement: Dr Employee Payable / Cr Cash
    ExpenseCardStatement        — Card statement: Dr Expense / Cr Corp Card Liability
    ExpenseCardPayment          — Card paid: Dr Corp Card Liability / Cr Cash
    ExpenseAdvanceIssued        — Advance out: Dr Advance Clearing / Cr Cash
    ExpenseAdvanceCleared       — Advance cleared: Dr Employee Payable / Cr Advance Clearing
    ExpenseReceiptMatched       — Receipt match: Dr Expense / Cr Corp Card Liability
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

logger = get_logger("modules.expense.profiles")

MODULE_NAME = "expense"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for T&E."""

    EXPENSE = "expense"  # category-specific
    EMPLOYEE_PAYABLE = "employee_payable"
    CASH = "cash"
    CORPORATE_CARD_LIABILITY = "corporate_card_liability"
    ADVANCE_CLEARING = "advance_clearing"
    PROJECT_WIP = "project_wip"  # for billable expenses


# =============================================================================
# Profile definitions
# =============================================================================


# --- Report Approved ----------------------------------------------------------

REPORT_APPROVED = AccountingPolicy(
    name="ExpenseReportApproved",
    version=1,
    trigger=PolicyTrigger(event_type="expense.report_approved"),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_RECOGNITION",
        quantity_field="payload.total_amount",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="EXPENSE", credit_role="EMPLOYEE_PAYABLE"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.total_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Report total must be positive",
        ),
    ),
    description="Expense report approved for payment",
)

REPORT_APPROVED_MAPPINGS = (
    ModuleLineMapping(
        role="EXPENSE", side="debit", ledger="GL", foreach="expense_lines"
    ),
    ModuleLineMapping(role="EMPLOYEE_PAYABLE", side="credit", ledger="GL"),
)


# --- Report Approved — Billable ----------------------------------------------

REPORT_APPROVED_BILLABLE = AccountingPolicy(
    name="ExpenseReportBillable",
    version=1,
    trigger=PolicyTrigger(
        event_type="expense.report_approved",
        where=(("payload.billable", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_CAPITALIZATION",
        quantity_field="payload.total_amount",
        dimensions=("org_unit", "cost_center", "project"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="PROJECT_WIP", credit_role="EMPLOYEE_PAYABLE"
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.total_amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Report total must be positive",
        ),
    ),
    description="Billable expense report approved",
)

REPORT_APPROVED_BILLABLE_MAPPINGS = (
    ModuleLineMapping(
        role="PROJECT_WIP", side="debit", ledger="GL", foreach="expense_lines"
    ),
    ModuleLineMapping(role="EMPLOYEE_PAYABLE", side="credit", ledger="GL"),
)


# --- Reimbursement Paid ------------------------------------------------------

REIMBURSEMENT_PAID = AccountingPolicy(
    name="ExpenseReimbursementPaid",
    version=1,
    trigger=PolicyTrigger(event_type="expense.reimbursement_paid"),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_SETTLEMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="EMPLOYEE_PAYABLE", credit_role="CASH"
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Reimbursement amount must be positive",
        ),
    ),
    description="Employee reimbursement paid",
)

REIMBURSEMENT_PAID_MAPPINGS = (
    ModuleLineMapping(role="EMPLOYEE_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- Corporate Card Statement ------------------------------------------------

CORPORATE_CARD_STATEMENT = AccountingPolicy(
    name="ExpenseCardStatement",
    version=1,
    trigger=PolicyTrigger(event_type="expense.card_statement"),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_RECOGNITION",
        quantity_field="payload.statement_total",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE",
            credit_role="CORPORATE_CARD_LIABILITY",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.statement_total <= 0",
            reason_code="INVALID_AMOUNT",
            message="Statement total must be positive",
        ),
    ),
    description="Corporate card statement received",
)

CORPORATE_CARD_STATEMENT_MAPPINGS = (
    ModuleLineMapping(
        role="EXPENSE", side="debit", ledger="GL", foreach="card_transactions"
    ),
    ModuleLineMapping(role="CORPORATE_CARD_LIABILITY", side="credit", ledger="GL"),
)


# --- Corporate Card Payment --------------------------------------------------

CORPORATE_CARD_PAYMENT = AccountingPolicy(
    name="ExpenseCardPayment",
    version=1,
    trigger=PolicyTrigger(event_type="expense.card_payment"),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_SETTLEMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="CORPORATE_CARD_LIABILITY",
            credit_role="CASH",
        ),
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
    description="Corporate card statement paid",
)

CORPORATE_CARD_PAYMENT_MAPPINGS = (
    ModuleLineMapping(role="CORPORATE_CARD_LIABILITY", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- Advance Issued ----------------------------------------------------------

ADVANCE_ISSUED = AccountingPolicy(
    name="ExpenseAdvanceIssued",
    version=1,
    trigger=PolicyTrigger(event_type="expense.advance_issued"),
    meaning=PolicyMeaning(
        economic_type="ADVANCE_DISBURSEMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="ADVANCE_CLEARING", credit_role="CASH"
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Advance amount must be positive",
        ),
    ),
    description="Travel advance issued to employee",
)

ADVANCE_ISSUED_MAPPINGS = (
    ModuleLineMapping(role="ADVANCE_CLEARING", side="debit", ledger="GL"),
    ModuleLineMapping(role="CASH", side="credit", ledger="GL"),
)


# --- Advance Cleared ---------------------------------------------------------

ADVANCE_CLEARED = AccountingPolicy(
    name="ExpenseAdvanceCleared",
    version=1,
    trigger=PolicyTrigger(event_type="expense.advance_cleared"),
    meaning=PolicyMeaning(
        economic_type="ADVANCE_SETTLEMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="EMPLOYEE_PAYABLE", credit_role="ADVANCE_CLEARING"
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Clearing amount must be positive",
        ),
    ),
    description="Travel advance cleared against expense report",
)

ADVANCE_CLEARED_MAPPINGS = (
    ModuleLineMapping(role="EMPLOYEE_PAYABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ADVANCE_CLEARING", side="credit", ledger="GL"),
)


# --- Receipt Matched ----------------------------------------------------------

RECEIPT_MATCHED = AccountingPolicy(
    name="ExpenseReceiptMatched",
    version=1,
    trigger=PolicyTrigger(event_type="expense.receipt_matched"),
    meaning=PolicyMeaning(
        economic_type="EXPENSE_RECONCILIATION",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE",
            credit_role="CORPORATE_CARD_LIABILITY",
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
    description="Expense receipt matched to corporate card transaction",
)

RECEIPT_MATCHED_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="CORPORATE_CARD_LIABILITY", side="credit", ledger="GL"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (REPORT_APPROVED, REPORT_APPROVED_MAPPINGS),
    (REPORT_APPROVED_BILLABLE, REPORT_APPROVED_BILLABLE_MAPPINGS),
    (REIMBURSEMENT_PAID, REIMBURSEMENT_PAID_MAPPINGS),
    (CORPORATE_CARD_STATEMENT, CORPORATE_CARD_STATEMENT_MAPPINGS),
    (CORPORATE_CARD_PAYMENT, CORPORATE_CARD_PAYMENT_MAPPINGS),
    (ADVANCE_ISSUED, ADVANCE_ISSUED_MAPPINGS),
    (ADVANCE_CLEARED, ADVANCE_CLEARED_MAPPINGS),
    (RECEIPT_MATCHED, RECEIPT_MATCHED_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all expense profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "expense_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

EXPENSE_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
