"""
Payroll Economic Profiles -- Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses) and module
(line mappings, additional scenarios). Each profile is a kernel AccountingPolicy
with companion ModuleLineMapping tuples for intent construction.

Profiles:
    PayrollAccrual              -- Expense accrual: Dr Salary+Wage+Tax / Cr liabilities
    PayrollPayment              -- Net pay: Dr Accrued Payroll / Cr Cash
    PayrollTaxDeposit           -- Tax remittance: Dr Tax Payables / Cr Cash
    PayrollBenefitsPayment      -- Benefits paid: Dr Benefits Payable / Cr Cash
    TimesheetRegular            -- Regular hours: Dr Wage Expense / Cr Accrued Payroll
    TimesheetOvertime           -- Overtime hours: Dr Overtime Expense / Cr Accrued Payroll
    TimesheetPTO                -- Paid time off: Dr PTO Expense / Cr Accrued Payroll
    LaborDistributionDirect     -- Direct labor: Dr WIP / Cr Labor Clearing
    LaborDistributionIndirect   -- Indirect labor: Dr Overhead Pool / Cr Labor Clearing
    LaborDistributionOverhead   -- Overhead labor: Dr Overhead Expense / Cr Labor Clearing
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

logger = get_logger("modules.payroll.profiles")

MODULE_NAME = "payroll"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Payroll."""

    SALARY_EXPENSE = "salary_expense"
    WAGE_EXPENSE = "wage_expense"
    OVERTIME_EXPENSE = "overtime_expense"
    PTO_EXPENSE = "pto_expense"
    PAYROLL_TAX_EXPENSE = "payroll_tax_expense"
    FEDERAL_TAX_PAYABLE = "federal_tax_payable"
    STATE_TAX_PAYABLE = "state_tax_payable"
    FICA_PAYABLE = "fica_payable"
    BENEFITS_PAYABLE = "benefits_payable"
    ACCRUED_PAYROLL = "accrued_payroll"
    CASH = "cash"
    LABOR_CLEARING = "labor_clearing"
    WIP = "wip"
    OVERHEAD_POOL = "overhead_pool"
    OVERHEAD_EXPENSE = "overhead_expense"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Payroll Accrual ---------------------------------------------------------

PAYROLL_ACCRUAL = AccountingPolicy(
    name="PayrollAccrual",
    version=1,
    trigger=PolicyTrigger(event_type="payroll.accrual"),
    meaning=PolicyMeaning(
        economic_type="PAYROLL_ACCRUAL",
        dimensions=("org_unit", "department"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="SALARY_EXPENSE",
            credit_role="ACCRUED_PAYROLL",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.gross_amount <= 0",
            reason_code="INVALID_GROSS",
            message="Gross payroll amount must be positive",
        ),
    ),
    description="Payroll expense accrued with withholding liabilities",
)

PAYROLL_ACCRUAL_MAPPINGS = (
    ModuleLineMapping(role="SALARY_EXPENSE", side="debit"),
    ModuleLineMapping(role="WAGE_EXPENSE", side="debit", from_context="wage_amount"),
    ModuleLineMapping(
        role="PAYROLL_TAX_EXPENSE", side="debit", from_context="tax_expense_amount"
    ),
    ModuleLineMapping(
        role="FEDERAL_TAX_PAYABLE", side="credit", from_context="federal_tax_amount"
    ),
    ModuleLineMapping(
        role="STATE_TAX_PAYABLE", side="credit", from_context="state_tax_amount"
    ),
    ModuleLineMapping(
        role="FICA_PAYABLE", side="credit", from_context="fica_amount"
    ),
    ModuleLineMapping(
        role="BENEFITS_PAYABLE", side="credit", from_context="benefits_amount"
    ),
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="credit", from_context="net_pay_amount"),
)


# --- Payroll Payment ---------------------------------------------------------

PAYROLL_PAYMENT = AccountingPolicy(
    name="PayrollPayment",
    version=1,
    trigger=PolicyTrigger(event_type="payroll.payment"),
    meaning=PolicyMeaning(
        economic_type="PAYROLL_DISBURSEMENT",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="ACCRUED_PAYROLL",
            credit_role="CASH",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.net_amount <= 0",
            reason_code="INVALID_NET",
            message="Net pay amount must be positive",
        ),
    ),
    description="Net payroll paid to employees",
)

PAYROLL_PAYMENT_MAPPINGS = (
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="debit"),
    ModuleLineMapping(role="CASH", side="credit"),
)


# --- Tax Deposit -------------------------------------------------------------

PAYROLL_TAX_DEPOSIT = AccountingPolicy(
    name="PayrollTaxDeposit",
    version=1,
    trigger=PolicyTrigger(event_type="payroll.tax_deposit"),
    meaning=PolicyMeaning(
        economic_type="TAX_REMITTANCE",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="FEDERAL_TAX_PAYABLE",
            credit_role="CASH",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.deposit_amount <= 0",
            reason_code="INVALID_DEPOSIT",
            message="Tax deposit amount must be positive",
        ),
    ),
    description="Payroll taxes deposited with taxing authorities",
)

PAYROLL_TAX_DEPOSIT_MAPPINGS = (
    ModuleLineMapping(
        role="FEDERAL_TAX_PAYABLE", side="debit", from_context="federal_tax_amount"
    ),
    ModuleLineMapping(
        role="STATE_TAX_PAYABLE", side="debit", from_context="state_tax_amount"
    ),
    ModuleLineMapping(
        role="FICA_PAYABLE", side="debit", from_context="fica_amount"
    ),
    ModuleLineMapping(role="CASH", side="credit"),
)


# --- Benefits Payment --------------------------------------------------------

PAYROLL_BENEFITS_PAYMENT = AccountingPolicy(
    name="PayrollBenefitsPayment",
    version=1,
    trigger=PolicyTrigger(event_type="payroll.benefits_payment"),
    meaning=PolicyMeaning(
        economic_type="BENEFITS_DISBURSEMENT",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="BENEFITS_PAYABLE",
            credit_role="CASH",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Benefits payment amount must be positive",
        ),
    ),
    description="Employee benefits paid to providers",
)

PAYROLL_BENEFITS_PAYMENT_MAPPINGS = (
    ModuleLineMapping(role="BENEFITS_PAYABLE", side="debit"),
    ModuleLineMapping(role="CASH", side="credit"),
)


# --- Timesheet: Regular Hours ------------------------------------------------

TIMESHEET_REGULAR = AccountingPolicy(
    name="TimesheetRegular",
    version=1,
    trigger=PolicyTrigger(
        event_type="timesheet.regular",
        schema_version=1,
        where=(("payload.pay_code", "REGULAR"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ACCRUAL",
        quantity_field="payload.hours",
        dimensions=("org_unit", "cost_center", "project", "department"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WAGE_EXPENSE",
            credit_role="ACCRUED_PAYROLL",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.hours <= 0",
            reason_code="INVALID_HOURS",
            message="Hours must be positive",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.hours > 24",
            reason_code="EXCESSIVE_HOURS",
            message="Hours cannot exceed 24 per day",
        ),
    ),
    description="Records regular hourly wages accrual",
)

TIMESHEET_REGULAR_MAPPINGS = (
    ModuleLineMapping(role="WAGE_EXPENSE", side="debit"),
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="credit"),
)


# --- Timesheet: Overtime -----------------------------------------------------

TIMESHEET_OVERTIME = AccountingPolicy(
    name="TimesheetOvertime",
    version=1,
    trigger=PolicyTrigger(
        event_type="timesheet.overtime",
        schema_version=1,
        where=(("payload.pay_code", "OVERTIME"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ACCRUAL",
        quantity_field="payload.hours",
        dimensions=("org_unit", "cost_center", "project", "department"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OVERTIME_EXPENSE",
            credit_role="ACCRUED_PAYROLL",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.hours <= 0",
            reason_code="INVALID_HOURS",
            message="Hours must be positive",
        ),
    ),
    description="Records overtime wages accrual",
)

TIMESHEET_OVERTIME_MAPPINGS = (
    ModuleLineMapping(role="OVERTIME_EXPENSE", side="debit"),
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="credit"),
)


# --- Timesheet: Paid Time Off ------------------------------------------------

TIMESHEET_PTO = AccountingPolicy(
    name="TimesheetPTO",
    version=1,
    trigger=PolicyTrigger(
        event_type="timesheet.pto",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ACCRUAL",
        quantity_field="payload.hours",
        dimensions=("org_unit", "department"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="PTO_EXPENSE",
            credit_role="ACCRUED_PAYROLL",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records paid time off accrual",
)

TIMESHEET_PTO_MAPPINGS = (
    ModuleLineMapping(role="PTO_EXPENSE", side="debit"),
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="credit"),
)


# --- Labor Distribution: Direct ----------------------------------------------

LABOR_DISTRIBUTION_DIRECT = AccountingPolicy(
    name="LaborDistributionDirect",
    version=1,
    trigger=PolicyTrigger(
        event_type="labor.distribution_direct",
        schema_version=1,
        where=(("payload.labor_type", "DIRECT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ALLOCATION",
        dimensions=("org_unit", "cost_center", "project"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP",
            credit_role="LABOR_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Distribution amount must be positive",
        ),
    ),
    description="Records direct labor cost distribution to WIP",
)

LABOR_DISTRIBUTION_DIRECT_MAPPINGS = (
    ModuleLineMapping(role="WIP", side="debit"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit"),
)


# --- Labor Distribution: Indirect --------------------------------------------

LABOR_DISTRIBUTION_INDIRECT = AccountingPolicy(
    name="LaborDistributionIndirect",
    version=1,
    trigger=PolicyTrigger(
        event_type="labor.distribution_indirect",
        schema_version=1,
        where=(("payload.labor_type", "INDIRECT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="OVERHEAD_ALLOCATION",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OVERHEAD_POOL",
            credit_role="LABOR_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records indirect labor cost distribution to overhead pool",
)

LABOR_DISTRIBUTION_INDIRECT_MAPPINGS = (
    ModuleLineMapping(role="OVERHEAD_POOL", side="debit"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit"),
)


# --- Labor Distribution: Overhead --------------------------------------------

LABOR_DISTRIBUTION_OVERHEAD = AccountingPolicy(
    name="LaborDistributionOverhead",
    version=1,
    trigger=PolicyTrigger(
        event_type="labor.distribution_overhead",
        schema_version=1,
        where=(("payload.labor_type", "OVERHEAD"),),
    ),
    meaning=PolicyMeaning(
        economic_type="OVERHEAD_ALLOCATION",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OVERHEAD_EXPENSE",
            credit_role="LABOR_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records overhead labor cost allocation",
)

LABOR_DISTRIBUTION_OVERHEAD_MAPPINGS = (
    ModuleLineMapping(role="OVERHEAD_EXPENSE", side="debit"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (PAYROLL_ACCRUAL, PAYROLL_ACCRUAL_MAPPINGS),
    (PAYROLL_PAYMENT, PAYROLL_PAYMENT_MAPPINGS),
    (PAYROLL_TAX_DEPOSIT, PAYROLL_TAX_DEPOSIT_MAPPINGS),
    (PAYROLL_BENEFITS_PAYMENT, PAYROLL_BENEFITS_PAYMENT_MAPPINGS),
    (TIMESHEET_REGULAR, TIMESHEET_REGULAR_MAPPINGS),
    (TIMESHEET_OVERTIME, TIMESHEET_OVERTIME_MAPPINGS),
    (TIMESHEET_PTO, TIMESHEET_PTO_MAPPINGS),
    (LABOR_DISTRIBUTION_DIRECT, LABOR_DISTRIBUTION_DIRECT_MAPPINGS),
    (LABOR_DISTRIBUTION_INDIRECT, LABOR_DISTRIBUTION_INDIRECT_MAPPINGS),
    (LABOR_DISTRIBUTION_OVERHEAD, LABOR_DISTRIBUTION_OVERHEAD_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all payroll profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "payroll_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

PAYROLL_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
