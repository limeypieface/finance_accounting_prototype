"""
Contract and DCAA Compliance Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Contract Profiles (18):
    ContractCostDirectLabor         — Dr WIP Direct Labor / Cr Labor Clearing
    ContractCostDirectMaterial      — Dr WIP Direct Material / Cr Material Clearing
    ContractCostSubcontract         — Dr WIP Subcontract / Cr AP Clearing
    ContractCostTravel              — Dr WIP Travel / Cr Expense Clearing
    ContractCostODC                 — Dr WIP ODC / Cr Expense Clearing
    ContractCostIndirectFringe      — Dr WIP Fringe / Cr Fringe Pool Applied
    ContractCostIndirectOverhead    — Dr WIP Overhead / Cr Overhead Pool Applied
    ContractCostIndirectGA          — Dr WIP G&A / Cr G&A Pool Applied
    ContractBillingCostReimbursement — Provisional billing for cost-reimb
    ContractBillingTimeAndMaterials — Provisional billing for T&M
    ContractBillingLaborHour        — Provisional billing for labor-hour
    ContractFeeFixedAccrual         — Fixed fee accrual
    ContractFeeIncentiveAccrual     — Incentive fee accrual
    ContractFeeAwardAccrual         — Award fee accrual
    ContractAllocationFringe        — Fringe allocation to contract
    ContractAllocationOverhead      — Overhead allocation to contract
    ContractAllocationGA            — G&A allocation to contract
    ContractRateAdjustment          — Final vs provisional rate adjustment

DCAA Compliance Profiles (11):
    APInvoiceAllowable              — Allowable AP invoice
    APInvoiceUnallowable            — Unallowable AP invoice (segregated)
    APInvoiceConditional            — Conditional AP invoice (audit review)
    TimesheetAllowable              — Allowable labor
    TimesheetUnallowable            — Unallowable labor (segregated)
    LaborDistDirectAllowable        — Allowable direct labor to WIP
    LaborDistDirectUnallowable      — Unallowable direct labor (segregated)
    LaborDistIndirectAllowable      — Allowable indirect labor to overhead pool
    LaborDistIndirectUnallowable    — Unallowable indirect labor (excluded)
    BankWithdrawalExpenseAllowable  — Allowable expense withdrawal
    BankWithdrawalExpenseUnallowable — Unallowable expense withdrawal (segregated)
"""

from datetime import date
from enum import Enum

from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    GuardType,
    LedgerEffect,
    PrecedenceMode,
    PolicyMeaning,
    PolicyPrecedence,
    PolicyTrigger,
)
from finance_kernel.domain.policy_bridge import (
    ModuleLineMapping,
    register_rich_profile,
)
from finance_kernel.logging_config import get_logger

logger = get_logger("modules.contracts.profiles")

MODULE_NAME = "contracts"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Contract and DCAA compliance profiles."""

    # --- Contract Cost WIP roles ---
    WIP_DIRECT_LABOR = "wip_direct_labor"
    WIP_DIRECT_MATERIAL = "wip_direct_material"
    WIP_SUBCONTRACT = "wip_subcontract"
    WIP_TRAVEL = "wip_travel"
    WIP_ODC = "wip_odc"
    WIP_FRINGE = "wip_fringe"
    WIP_OVERHEAD = "wip_overhead"
    WIP_GA = "wip_ga"
    WIP_RATE_ADJUSTMENT = "wip_rate_adjustment"

    # --- Clearing accounts ---
    LABOR_CLEARING = "labor_clearing"
    MATERIAL_CLEARING = "material_clearing"
    AP_CLEARING = "ap_clearing"
    EXPENSE_CLEARING = "expense_clearing"
    COST_CLEARING = "cost_clearing"

    # --- Contract subledger ---
    CONTRACT_COST_INCURRED = "contract_cost_incurred"

    # --- Indirect pool applied ---
    FRINGE_POOL_APPLIED = "fringe_pool_applied"
    OVERHEAD_POOL_APPLIED = "overhead_pool_applied"
    GA_POOL_APPLIED = "ga_pool_applied"

    # --- Billing & receivable ---
    UNBILLED_AR = "unbilled_ar"
    WIP_BILLED = "wip_billed"
    DEFERRED_FEE_REVENUE = "deferred_fee_revenue"
    BILLED = "billed"
    COST_BILLED = "cost_billed"

    # --- Fee revenue ---
    FEE_REVENUE_EARNED = "fee_revenue_earned"

    # --- Rate variance ---
    INDIRECT_RATE_VARIANCE = "indirect_rate_variance"

    # --- DCAA allowability expense ---
    EXPENSE_ALLOWABLE = "expense_allowable"
    EXPENSE_UNALLOWABLE = "expense_unallowable"
    EXPENSE_CONDITIONAL = "expense_conditional"
    ACCOUNTS_PAYABLE = "accounts_payable"

    # --- DCAA AP subledger ---
    INVOICE = "invoice"
    SUPPLIER_BALANCE = "supplier_balance"

    # --- DCAA labor ---
    LABOR_ALLOWABLE = "labor_allowable"
    LABOR_UNALLOWABLE = "labor_unallowable"
    ACCRUED_PAYROLL = "accrued_payroll"

    # --- DCAA overhead pools ---
    OVERHEAD_POOL_ALLOWABLE = "overhead_pool_allowable"
    OVERHEAD_UNALLOWABLE = "overhead_unallowable"

    # --- Funding / obligation ---
    OBLIGATION_CONTROL = "obligation_control"
    RESERVE_FOR_ENCUMBRANCE = "reserve_for_encumbrance"

    # --- DCAA bank ---
    BANK = "bank"
    WITHDRAWAL = "withdrawal"
    AVAILABLE = "available"


# =============================================================================
# Contract Cost Incurrence Profiles
# =============================================================================


# --- Direct Labor Cost Incurrence -------------------------------------------

CONTRACT_COST_DIRECT_LABOR = AccountingPolicy(
    name="ContractCostDirectLabor",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "DIRECT_LABOR"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_COST_INCURRENCE",
        dimensions=("org_unit", "cost_center", "contract_number", "clin_number", "labor_category"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_DIRECT_LABOR",
            credit_role="LABOR_CLEARING",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
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
    description="Records direct labor cost incurred against contract",
)

CONTRACT_COST_DIRECT_LABOR_MAPPINGS = (
    ModuleLineMapping(role="WIP_DIRECT_LABOR", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Direct Material Cost Incurrence ----------------------------------------

CONTRACT_COST_DIRECT_MATERIAL = AccountingPolicy(
    name="ContractCostDirectMaterial",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "DIRECT_MATERIAL"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_COST_INCURRENCE",
        dimensions=("org_unit", "cost_center", "contract_number", "clin_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_DIRECT_MATERIAL",
            credit_role="MATERIAL_CLEARING",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records direct material cost incurred against contract",
)

CONTRACT_COST_DIRECT_MATERIAL_MAPPINGS = (
    ModuleLineMapping(role="WIP_DIRECT_MATERIAL", side="debit", ledger="GL"),
    ModuleLineMapping(role="MATERIAL_CLEARING", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Subcontract Cost Incurrence --------------------------------------------

CONTRACT_COST_SUBCONTRACT = AccountingPolicy(
    name="ContractCostSubcontract",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "SUBCONTRACT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_COST_INCURRENCE",
        dimensions=("org_unit", "contract_number", "clin_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_SUBCONTRACT",
            credit_role="AP_CLEARING",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records subcontract cost incurred against contract",
)

CONTRACT_COST_SUBCONTRACT_MAPPINGS = (
    ModuleLineMapping(role="WIP_SUBCONTRACT", side="debit", ledger="GL"),
    ModuleLineMapping(role="AP_CLEARING", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Travel Cost Incurrence -------------------------------------------------

CONTRACT_COST_TRAVEL = AccountingPolicy(
    name="ContractCostTravel",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "TRAVEL"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_COST_INCURRENCE",
        dimensions=("org_unit", "contract_number", "clin_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_TRAVEL",
            credit_role="EXPENSE_CLEARING",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records travel cost incurred against contract",
)

CONTRACT_COST_TRAVEL_MAPPINGS = (
    ModuleLineMapping(role="WIP_TRAVEL", side="debit", ledger="GL"),
    ModuleLineMapping(role="EXPENSE_CLEARING", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Other Direct Cost (ODC) Incurrence -------------------------------------

CONTRACT_COST_ODC = AccountingPolicy(
    name="ContractCostODC",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "ODC"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_COST_INCURRENCE",
        dimensions=("org_unit", "contract_number", "clin_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_ODC",
            credit_role="EXPENSE_CLEARING",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records other direct cost incurred against contract",
)

CONTRACT_COST_ODC_MAPPINGS = (
    ModuleLineMapping(role="WIP_ODC", side="debit", ledger="GL"),
    ModuleLineMapping(role="EXPENSE_CLEARING", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Indirect Fringe Cost ---------------------------------------------------

CONTRACT_COST_INDIRECT_FRINGE = AccountingPolicy(
    name="ContractCostIndirectFringe",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "INDIRECT_FRINGE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_INDIRECT_ALLOCATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_FRINGE",
            credit_role="FRINGE_POOL_APPLIED",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records fringe cost allocated to contract",
)

CONTRACT_COST_INDIRECT_FRINGE_MAPPINGS = (
    ModuleLineMapping(role="WIP_FRINGE", side="debit", ledger="GL"),
    ModuleLineMapping(role="FRINGE_POOL_APPLIED", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Indirect Overhead Cost -------------------------------------------------

CONTRACT_COST_INDIRECT_OVERHEAD = AccountingPolicy(
    name="ContractCostIndirectOverhead",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "INDIRECT_OVERHEAD"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_INDIRECT_ALLOCATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_OVERHEAD",
            credit_role="OVERHEAD_POOL_APPLIED",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records overhead cost allocated to contract",
)

CONTRACT_COST_INDIRECT_OVERHEAD_MAPPINGS = (
    ModuleLineMapping(role="WIP_OVERHEAD", side="debit", ledger="GL"),
    ModuleLineMapping(role="OVERHEAD_POOL_APPLIED", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# --- Indirect G&A Cost ------------------------------------------------------

CONTRACT_COST_INDIRECT_GA = AccountingPolicy(
    name="ContractCostIndirectGA",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.cost_incurred",
        schema_version=1,
        where=(("payload.cost_type", "INDIRECT_GA"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_INDIRECT_ALLOCATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_GA",
            credit_role="GA_POOL_APPLIED",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="CONTRACT_COST_INCURRED",
            credit_role="COST_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records G&A cost allocated to contract",
)

CONTRACT_COST_INDIRECT_GA_MAPPINGS = (
    ModuleLineMapping(role="WIP_GA", side="debit", ledger="GL"),
    ModuleLineMapping(role="GA_POOL_APPLIED", side="credit", ledger="GL"),
    ModuleLineMapping(role="CONTRACT_COST_INCURRED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_CLEARING", side="credit", ledger="CONTRACT"),
)


# =============================================================================
# Contract Billing Profiles
# =============================================================================


# --- Provisional Billing - Cost Reimbursement --------------------------------

CONTRACT_BILLING_COST_REIMB = AccountingPolicy(
    name="ContractBillingCostReimbursement",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.billing_provisional",
        schema_version=1,
        where=(("payload.billing_type", "COST_REIMBURSEMENT"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_BILLING",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="UNBILLED_AR",
            credit_role="WIP_BILLED",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="BILLED",
            credit_role="COST_BILLED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.total_billing <= 0",
            reason_code="INVALID_BILLING",
            message="Billing amount must be positive",
        ),
    ),
    description="Records provisional billing for cost-reimbursement contract",
)

CONTRACT_BILLING_COST_REIMB_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_AR", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP_BILLED", side="credit", ledger="GL", from_context="cost_billing"),
    ModuleLineMapping(role="DEFERRED_FEE_REVENUE", side="credit", ledger="GL", from_context="fee_amount"),
    ModuleLineMapping(role="BILLED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_BILLED", side="credit", ledger="CONTRACT"),
)


# --- Provisional Billing - Time & Materials ----------------------------------

CONTRACT_BILLING_TM = AccountingPolicy(
    name="ContractBillingTimeAndMaterials",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.billing_provisional",
        schema_version=1,
        where=(("payload.billing_type", "TIME_AND_MATERIALS"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_BILLING",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="UNBILLED_AR",
            credit_role="WIP_BILLED",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="BILLED",
            credit_role="COST_BILLED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records provisional billing for T&M contract",
)

CONTRACT_BILLING_TM_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_AR", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP_BILLED", side="credit", ledger="GL"),
    ModuleLineMapping(role="BILLED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_BILLED", side="credit", ledger="CONTRACT"),
)


# --- Provisional Billing - Labor Hour ----------------------------------------

CONTRACT_BILLING_LH = AccountingPolicy(
    name="ContractBillingLaborHour",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.billing_provisional",
        schema_version=1,
        where=(("payload.billing_type", "LABOR_HOUR"),),
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_BILLING",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="UNBILLED_AR",
            credit_role="WIP_BILLED",
        ),
        LedgerEffect(
            ledger="CONTRACT",
            debit_role="BILLED",
            credit_role="COST_BILLED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records provisional billing for labor-hour contract",
)

CONTRACT_BILLING_LH_MAPPINGS = (
    ModuleLineMapping(role="UNBILLED_AR", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP_BILLED", side="credit", ledger="GL"),
    ModuleLineMapping(role="BILLED", side="debit", ledger="CONTRACT"),
    ModuleLineMapping(role="COST_BILLED", side="credit", ledger="CONTRACT"),
)


# =============================================================================
# Fee Accrual Profiles
# =============================================================================


# --- Fixed Fee Accrual -------------------------------------------------------

CONTRACT_FEE_FIXED = AccountingPolicy(
    name="ContractFeeFixedAccrual",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.fee_accrual",
        schema_version=1,
        where=(("payload.fee_type", "FIXED_FEE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FEE_ACCRUAL",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="DEFERRED_FEE_REVENUE",
            credit_role="FEE_REVENUE_EARNED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.BLOCK,
            expression="payload.cumulative_fee > payload.ceiling_fee if payload.ceiling_fee else False",
            reason_code="FEE_CEILING_EXCEEDED",
            message="Fee accrual would exceed contract ceiling fee",
        ),
    ),
    description="Records fixed fee accrual on cost-plus contracts",
)

CONTRACT_FEE_FIXED_MAPPINGS = (
    ModuleLineMapping(role="DEFERRED_FEE_REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="FEE_REVENUE_EARNED", side="credit", ledger="GL"),
)


# --- Incentive Fee Accrual ---------------------------------------------------

CONTRACT_FEE_INCENTIVE = AccountingPolicy(
    name="ContractFeeIncentiveAccrual",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.fee_accrual",
        schema_version=1,
        where=(("payload.fee_type", "INCENTIVE_FEE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FEE_ACCRUAL",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="DEFERRED_FEE_REVENUE",
            credit_role="FEE_REVENUE_EARNED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records incentive fee accrual on CPIF contracts",
)

CONTRACT_FEE_INCENTIVE_MAPPINGS = (
    ModuleLineMapping(role="DEFERRED_FEE_REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="FEE_REVENUE_EARNED", side="credit", ledger="GL"),
)


# --- Award Fee Accrual -------------------------------------------------------

CONTRACT_FEE_AWARD = AccountingPolicy(
    name="ContractFeeAwardAccrual",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.fee_accrual",
        schema_version=1,
        where=(("payload.fee_type", "AWARD_FEE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="FEE_ACCRUAL",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="DEFERRED_FEE_REVENUE",
            credit_role="FEE_REVENUE_EARNED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records award fee accrual on CPAF contracts",
)

CONTRACT_FEE_AWARD_MAPPINGS = (
    ModuleLineMapping(role="DEFERRED_FEE_REVENUE", side="debit", ledger="GL"),
    ModuleLineMapping(role="FEE_REVENUE_EARNED", side="credit", ledger="GL"),
)


# =============================================================================
# Indirect Cost Allocation Profiles
# =============================================================================


# --- Fringe Allocation -------------------------------------------------------

CONTRACT_ALLOCATION_FRINGE = AccountingPolicy(
    name="ContractAllocationFringe",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.indirect_allocation",
        schema_version=1,
        where=(("payload.indirect_type", "FRINGE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INDIRECT_ALLOCATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_FRINGE",
            credit_role="FRINGE_POOL_APPLIED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Allocates fringe costs to contract",
)

CONTRACT_ALLOCATION_FRINGE_MAPPINGS = (
    ModuleLineMapping(role="WIP_FRINGE", side="debit", ledger="GL"),
    ModuleLineMapping(role="FRINGE_POOL_APPLIED", side="credit", ledger="GL"),
)


# --- Overhead Allocation -----------------------------------------------------

CONTRACT_ALLOCATION_OVERHEAD = AccountingPolicy(
    name="ContractAllocationOverhead",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.indirect_allocation",
        schema_version=1,
        where=(("payload.indirect_type", "OVERHEAD"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INDIRECT_ALLOCATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_OVERHEAD",
            credit_role="OVERHEAD_POOL_APPLIED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Allocates overhead costs to contract",
)

CONTRACT_ALLOCATION_OVERHEAD_MAPPINGS = (
    ModuleLineMapping(role="WIP_OVERHEAD", side="debit", ledger="GL"),
    ModuleLineMapping(role="OVERHEAD_POOL_APPLIED", side="credit", ledger="GL"),
)


# --- G&A Allocation ----------------------------------------------------------

CONTRACT_ALLOCATION_GA = AccountingPolicy(
    name="ContractAllocationGA",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.indirect_allocation",
        schema_version=1,
        where=(("payload.indirect_type", "G_AND_A"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INDIRECT_ALLOCATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_GA",
            credit_role="GA_POOL_APPLIED",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Allocates G&A costs to contract",
)

CONTRACT_ALLOCATION_GA_MAPPINGS = (
    ModuleLineMapping(role="WIP_GA", side="debit", ledger="GL"),
    ModuleLineMapping(role="GA_POOL_APPLIED", side="credit", ledger="GL"),
)


# =============================================================================
# Rate Adjustment Profile
# =============================================================================


CONTRACT_RATE_ADJUSTMENT = AccountingPolicy(
    name="ContractRateAdjustment",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.rate_adjustment",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="RATE_ADJUSTMENT",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_RATE_ADJUSTMENT",
            credit_role="INDIRECT_RATE_VARIANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    description="Records adjustment for final vs provisional indirect rates",
)

CONTRACT_RATE_ADJUSTMENT_MAPPINGS = (
    ModuleLineMapping(role="WIP_RATE_ADJUSTMENT", side="debit", ledger="GL"),
    ModuleLineMapping(role="INDIRECT_RATE_VARIANCE", side="credit", ledger="GL"),
)


# =============================================================================
# Contract Funding Action
# =============================================================================


CONTRACT_FUNDING_OBLIGATION = AccountingPolicy(
    name="ContractFundingObligation",
    version=1,
    trigger=PolicyTrigger(
        event_type="contract.funding_action",
        schema_version=1,
    ),
    meaning=PolicyMeaning(
        economic_type="CONTRACT_OBLIGATION",
        dimensions=("org_unit", "contract_number"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OBLIGATION_CONTROL",
            credit_role="RESERVE_FOR_ENCUMBRANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Funding amount must be positive",
        ),
    ),
    description="Records contract funding obligation/deobligation",
)

CONTRACT_FUNDING_OBLIGATION_MAPPINGS = (
    ModuleLineMapping(role="OBLIGATION_CONTROL", side="debit", ledger="GL"),
    ModuleLineMapping(role="RESERVE_FOR_ENCUMBRANCE", side="credit", ledger="GL"),
)


# =============================================================================
# DCAA Compliance Profiles — AP Invoice
# =============================================================================


# --- AP Invoice Allowable ----------------------------------------------------

AP_INVOICE_ALLOWABLE = AccountingPolicy(
    name="APInvoiceAllowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.invoice_received",
        schema_version=2,
        where=(("payload.allowability", "ALLOWABLE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center", "project", "contract_id"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE_ALLOWABLE",
            credit_role="ACCOUNTS_PAYABLE",
        ),
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
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot record invoice",
        ),
    ),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("APInvoiceExpense",),
    ),
    description="Records allowable AP invoice - can be charged to government contracts",
)

AP_INVOICE_ALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE_ALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# --- AP Invoice Unallowable -------------------------------------------------

AP_INVOICE_UNALLOWABLE = AccountingPolicy(
    name="APInvoiceUnallowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.invoice_received",
        schema_version=2,
        where=(("payload.allowability", "UNALLOWABLE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE_UNALLOWABLE",
            credit_role="ACCOUNTS_PAYABLE",
        ),
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
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot record invoice",
        ),
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.contract_id is not None",
            reason_code="UNALLOWABLE_TO_CONTRACT",
            message="Unallowable costs cannot be charged to a contract",
        ),
    ),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("APInvoiceExpense",),
    ),
    description="Records unallowable AP invoice - segregated from contract cost pools",
)

AP_INVOICE_UNALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE_UNALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# --- AP Invoice Conditional --------------------------------------------------

AP_INVOICE_CONDITIONAL = AccountingPolicy(
    name="APInvoiceConditional",
    version=1,
    trigger=PolicyTrigger(
        event_type="ap.invoice_received",
        schema_version=2,
        where=(("payload.allowability", "CONDITIONAL"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LIABILITY_INCREASE",
        dimensions=("org_unit", "cost_center", "project", "contract_id"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE_CONDITIONAL",
            credit_role="ACCOUNTS_PAYABLE",
        ),
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
            expression="party.is_frozen",
            reason_code="SUPPLIER_FROZEN",
            message="Supplier is frozen - cannot record invoice",
        ),
        GuardCondition(
            guard_type=GuardType.BLOCK,
            expression="payload.unallowable_reason is None",
            reason_code="CONDITIONAL_REQUIRES_REASON",
            message="Conditional costs require a reason code for audit",
        ),
    ),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("APInvoiceExpense",),
    ),
    description="Records conditional AP invoice - requires audit review for contract charging",
)

AP_INVOICE_CONDITIONAL_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE_CONDITIONAL", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCOUNTS_PAYABLE", side="credit", ledger="GL"),
    ModuleLineMapping(role="INVOICE", side="debit", ledger="AP"),
    ModuleLineMapping(role="SUPPLIER_BALANCE", side="credit", ledger="AP"),
)


# =============================================================================
# DCAA Compliance Profiles — Timesheet
# =============================================================================


# --- Timesheet Allowable -----------------------------------------------------

TIMESHEET_ALLOWABLE = AccountingPolicy(
    name="TimesheetAllowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="payroll.timesheet",
        schema_version=2,
        where=(("payload.allowability", "ALLOWABLE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ACCRUAL",
        quantity_field="payload.hours",
        dimensions=("org_unit", "cost_center", "project", "department", "contract_id", "labor_category"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="LABOR_ALLOWABLE",
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
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("TimesheetRegular", "TimesheetOvertime"),
    ),
    description="Records allowable labor - can be charged to government contracts",
)

TIMESHEET_ALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="LABOR_ALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="credit", ledger="GL"),
)


# --- Timesheet Unallowable ---------------------------------------------------

TIMESHEET_UNALLOWABLE = AccountingPolicy(
    name="TimesheetUnallowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="payroll.timesheet",
        schema_version=2,
        where=(("payload.allowability", "UNALLOWABLE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ACCRUAL",
        quantity_field="payload.hours",
        dimensions=("org_unit", "cost_center", "department"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="LABOR_UNALLOWABLE",
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
            expression="payload.contract_id is not None",
            reason_code="UNALLOWABLE_TO_CONTRACT",
            message="Unallowable labor cannot be charged to a contract",
        ),
    ),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("TimesheetRegular", "TimesheetOvertime"),
    ),
    description="Records unallowable labor - segregated from contract cost pools",
)

TIMESHEET_UNALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="LABOR_UNALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="ACCRUED_PAYROLL", side="credit", ledger="GL"),
)


# =============================================================================
# DCAA Compliance Profiles — Labor Distribution
# =============================================================================


# --- Direct Labor Allowable --------------------------------------------------

LABOR_DIST_DIRECT_ALLOWABLE = AccountingPolicy(
    name="LaborDistDirectAllowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="payroll.labor_distribution",
        schema_version=2,
        where=(
            ("payload.labor_type", "DIRECT"),
            ("payload.allowability", "ALLOWABLE"),
        ),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ALLOCATION",
        dimensions=("org_unit", "cost_center", "project", "contract_id", "labor_category"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="WIP_DIRECT_LABOR",
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
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("LaborDistributionDirect",),
    ),
    description="Records allowable direct labor to WIP - chargeable to contracts",
)

LABOR_DIST_DIRECT_ALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="WIP_DIRECT_LABOR", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
)


# --- Direct Labor Unallowable ------------------------------------------------

LABOR_DIST_DIRECT_UNALLOWABLE = AccountingPolicy(
    name="LaborDistDirectUnallowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="payroll.labor_distribution",
        schema_version=2,
        where=(
            ("payload.labor_type", "DIRECT"),
            ("payload.allowability", "UNALLOWABLE"),
        ),
    ),
    meaning=PolicyMeaning(
        economic_type="LABOR_ALLOCATION",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="LABOR_UNALLOWABLE",
            credit_role="LABOR_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.contract_id is not None",
            reason_code="UNALLOWABLE_TO_CONTRACT",
            message="Unallowable labor cannot be charged to a contract",
        ),
    ),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("LaborDistributionDirect",),
    ),
    description="Records unallowable direct labor - segregated from contract cost pools",
)

LABOR_DIST_DIRECT_UNALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="LABOR_UNALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
)


# --- Indirect Labor Allowable ------------------------------------------------

LABOR_DIST_INDIRECT_ALLOWABLE = AccountingPolicy(
    name="LaborDistIndirectAllowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="payroll.labor_distribution",
        schema_version=2,
        where=(
            ("payload.labor_type", "INDIRECT"),
            ("payload.allowability", "ALLOWABLE"),
        ),
    ),
    meaning=PolicyMeaning(
        economic_type="OVERHEAD_ALLOCATION",
        dimensions=("org_unit", "cost_center", "indirect_rate_type"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OVERHEAD_POOL_ALLOWABLE",
            credit_role="LABOR_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("LaborDistributionIndirect",),
    ),
    description="Records allowable indirect labor to overhead pool - included in indirect rates",
)

LABOR_DIST_INDIRECT_ALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="OVERHEAD_POOL_ALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
)


# --- Indirect Labor Unallowable ----------------------------------------------

LABOR_DIST_INDIRECT_UNALLOWABLE = AccountingPolicy(
    name="LaborDistIndirectUnallowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="payroll.labor_distribution",
        schema_version=2,
        where=(
            ("payload.labor_type", "INDIRECT"),
            ("payload.allowability", "UNALLOWABLE"),
        ),
    ),
    meaning=PolicyMeaning(
        economic_type="OVERHEAD_ALLOCATION",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="OVERHEAD_UNALLOWABLE",
            credit_role="LABOR_CLEARING",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("LaborDistributionIndirect",),
    ),
    description="Records unallowable indirect labor - excluded from indirect rate pools",
)

LABOR_DIST_INDIRECT_UNALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="OVERHEAD_UNALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="LABOR_CLEARING", side="credit", ledger="GL"),
)


# =============================================================================
# DCAA Compliance Profiles — Bank Withdrawal
# =============================================================================


# --- Bank Withdrawal Expense Allowable ----------------------------------------

BANK_WITHDRAWAL_EXPENSE_ALLOWABLE = AccountingPolicy(
    name="BankWithdrawalExpenseAllowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="bank.withdrawal",
        schema_version=2,
        where=(
            ("payload.destination_type", "EXPENSE"),
            ("payload.allowability", "ALLOWABLE"),
        ),
    ),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit", "cost_center", "contract_id"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE_ALLOWABLE",
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
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("BankWithdrawalExpense",),
    ),
    description="Records allowable expense withdrawal - chargeable to contracts",
)

BANK_WITHDRAWAL_EXPENSE_ALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE_ALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="BANK", side="credit", ledger="GL"),
    ModuleLineMapping(role="WITHDRAWAL", side="debit", ledger="BANK"),
    ModuleLineMapping(role="AVAILABLE", side="credit", ledger="BANK"),
)


# --- Bank Withdrawal Expense Unallowable -------------------------------------

BANK_WITHDRAWAL_EXPENSE_UNALLOWABLE = AccountingPolicy(
    name="BankWithdrawalExpenseUnallowable",
    version=1,
    trigger=PolicyTrigger(
        event_type="bank.withdrawal",
        schema_version=2,
        where=(
            ("payload.destination_type", "EXPENSE"),
            ("payload.allowability", "UNALLOWABLE"),
        ),
    ),
    meaning=PolicyMeaning(
        economic_type="BANK_DECREASE",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="EXPENSE_UNALLOWABLE",
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
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.contract_id is not None",
            reason_code="UNALLOWABLE_TO_CONTRACT",
            message="Unallowable expenses cannot be charged to a contract",
        ),
    ),
    precedence=PolicyPrecedence(
        mode=PrecedenceMode.OVERRIDE,
        priority=100,
        overrides=("BankWithdrawalExpense",),
    ),
    description="Records unallowable expense withdrawal - segregated from contract cost pools",
)

BANK_WITHDRAWAL_EXPENSE_UNALLOWABLE_MAPPINGS = (
    ModuleLineMapping(role="EXPENSE_UNALLOWABLE", side="debit", ledger="GL"),
    ModuleLineMapping(role="BANK", side="credit", ledger="GL"),
    ModuleLineMapping(role="WITHDRAWAL", side="debit", ledger="BANK"),
    ModuleLineMapping(role="AVAILABLE", side="credit", ledger="BANK"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    # Contract cost incurrence (8)
    (CONTRACT_COST_DIRECT_LABOR, CONTRACT_COST_DIRECT_LABOR_MAPPINGS),
    (CONTRACT_COST_DIRECT_MATERIAL, CONTRACT_COST_DIRECT_MATERIAL_MAPPINGS),
    (CONTRACT_COST_SUBCONTRACT, CONTRACT_COST_SUBCONTRACT_MAPPINGS),
    (CONTRACT_COST_TRAVEL, CONTRACT_COST_TRAVEL_MAPPINGS),
    (CONTRACT_COST_ODC, CONTRACT_COST_ODC_MAPPINGS),
    (CONTRACT_COST_INDIRECT_FRINGE, CONTRACT_COST_INDIRECT_FRINGE_MAPPINGS),
    (CONTRACT_COST_INDIRECT_OVERHEAD, CONTRACT_COST_INDIRECT_OVERHEAD_MAPPINGS),
    (CONTRACT_COST_INDIRECT_GA, CONTRACT_COST_INDIRECT_GA_MAPPINGS),
    # Contract billing (3)
    (CONTRACT_BILLING_COST_REIMB, CONTRACT_BILLING_COST_REIMB_MAPPINGS),
    (CONTRACT_BILLING_TM, CONTRACT_BILLING_TM_MAPPINGS),
    (CONTRACT_BILLING_LH, CONTRACT_BILLING_LH_MAPPINGS),
    # Fee accruals (3)
    (CONTRACT_FEE_FIXED, CONTRACT_FEE_FIXED_MAPPINGS),
    (CONTRACT_FEE_INCENTIVE, CONTRACT_FEE_INCENTIVE_MAPPINGS),
    (CONTRACT_FEE_AWARD, CONTRACT_FEE_AWARD_MAPPINGS),
    # Indirect allocation (3)
    (CONTRACT_ALLOCATION_FRINGE, CONTRACT_ALLOCATION_FRINGE_MAPPINGS),
    (CONTRACT_ALLOCATION_OVERHEAD, CONTRACT_ALLOCATION_OVERHEAD_MAPPINGS),
    (CONTRACT_ALLOCATION_GA, CONTRACT_ALLOCATION_GA_MAPPINGS),
    # Rate adjustment (1)
    (CONTRACT_RATE_ADJUSTMENT, CONTRACT_RATE_ADJUSTMENT_MAPPINGS),
    # Funding action (1)
    (CONTRACT_FUNDING_OBLIGATION, CONTRACT_FUNDING_OBLIGATION_MAPPINGS),
    # DCAA AP invoice (3)
    (AP_INVOICE_ALLOWABLE, AP_INVOICE_ALLOWABLE_MAPPINGS),
    (AP_INVOICE_UNALLOWABLE, AP_INVOICE_UNALLOWABLE_MAPPINGS),
    (AP_INVOICE_CONDITIONAL, AP_INVOICE_CONDITIONAL_MAPPINGS),
    # DCAA timesheet (2)
    (TIMESHEET_ALLOWABLE, TIMESHEET_ALLOWABLE_MAPPINGS),
    (TIMESHEET_UNALLOWABLE, TIMESHEET_UNALLOWABLE_MAPPINGS),
    # DCAA labor distribution (4)
    (LABOR_DIST_DIRECT_ALLOWABLE, LABOR_DIST_DIRECT_ALLOWABLE_MAPPINGS),
    (LABOR_DIST_DIRECT_UNALLOWABLE, LABOR_DIST_DIRECT_UNALLOWABLE_MAPPINGS),
    (LABOR_DIST_INDIRECT_ALLOWABLE, LABOR_DIST_INDIRECT_ALLOWABLE_MAPPINGS),
    (LABOR_DIST_INDIRECT_UNALLOWABLE, LABOR_DIST_INDIRECT_UNALLOWABLE_MAPPINGS),
    # DCAA bank withdrawal (2)
    (BANK_WITHDRAWAL_EXPENSE_ALLOWABLE, BANK_WITHDRAWAL_EXPENSE_ALLOWABLE_MAPPINGS),
    (BANK_WITHDRAWAL_EXPENSE_UNALLOWABLE, BANK_WITHDRAWAL_EXPENSE_UNALLOWABLE_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all contract and DCAA compliance profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "contracts_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

CONTRACT_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
