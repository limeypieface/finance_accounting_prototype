"""
Inventory Economic Profiles — Kernel format.

Merged authoritative profiles from kernel (guards, where-clauses, multi-ledger)
and module (line mappings, additional scenarios). Each profile is a kernel
AccountingPolicy with companion ModuleLineMapping tuples for intent construction.

Profiles:
    InventoryReceipt            — PO receipt: Dr Inventory / Cr GRNI
    InventoryReceiptWithVariance — Standard cost variance on receipt
    InventoryIssueSale          — Sale issue: Dr COGS / Cr Inventory
    InventoryIssueProduction    — WIP issue: Dr WIP / Cr Inventory
    InventoryIssueScrap         — Scrap: Dr Scrap Expense / Cr Inventory
    InventoryIssueTransfer      — Transfer out: Dr In_Transit / Cr Stock
    InventoryTransferIn         — Transfer in: Dr Inventory / Cr In_Transit
    InventoryReceiptFromProduction — Finished goods: Dr Inventory / Cr WIP
    InventoryAdjustmentPositive — Count increase: Dr Inventory / Cr Variance
    InventoryAdjustmentNegative — Count decrease: Dr Variance / Cr Inventory
    InventoryRevaluation        — LCM adjustment: Dr Revaluation / Cr Inventory
    InventoryCycleCountPositive — Positive cycle count: Dr Inv Asset / Cr Adjustment + subledger
    InventoryCycleCountNegative — Negative cycle count: Dr Adjustment / Cr Inv Asset + subledger
    InventoryWarehouseTransferOut — WH transfer out: Dr In Transit / Cr Inv Asset + subledger
    InventoryWarehouseTransferIn  — WH transfer in: Dr Inv Asset / Cr In Transit + subledger
    InventoryExpiredWriteOff    — Expired: Dr Scrap Expense / Cr Inv Asset + subledger
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

logger = get_logger("modules.inventory.profiles")

MODULE_NAME = "inventory"


# =============================================================================
# Account roles (used by config.py for account mapping)
# =============================================================================


class AccountRole(Enum):
    """Logical account roles for Inventory."""

    INVENTORY_ASSET = "inventory_asset"
    INVENTORY_IN_TRANSIT = "inventory_in_transit"
    GOODS_RECEIVED_NOT_INVOICED = "grni"
    COGS = "cost_of_goods_sold"
    WIP = "work_in_process"
    INVENTORY_ADJUSTMENT = "inventory_adjustment"
    SCRAP_EXPENSE = "scrap_expense"
    PPV = "purchase_price_variance"
    INVENTORY_REVALUATION = "inventory_revaluation"


# =============================================================================
# Profile definitions
# =============================================================================


# --- Receipt from Purchase ---------------------------------------------------

INVENTORY_RECEIPT = AccountingPolicy(
    name="InventoryReceipt",
    version=1,
    trigger=PolicyTrigger(event_type="inventory.receipt"),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_INCREASE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="INVENTORY", credit_role="GRNI"),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="STOCK_ON_HAND",
            credit_role="IN_TRANSIT",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Receipt quantity must be positive",
        ),
    ),
    description="Records inventory receipt increasing stock on hand",
)

INVENTORY_RECEIPT_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY", side="debit", ledger="GL"),
    ModuleLineMapping(role="GRNI", side="credit", ledger="GL"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="IN_TRANSIT", side="credit", ledger="INVENTORY"),
)


# --- Receipt with Variance (standard costing) --------------------------------

INVENTORY_RECEIPT_WITH_VARIANCE = AccountingPolicy(
    name="InventoryReceiptWithVariance",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.receipt",
        where=(("payload.has_variance", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_INCREASE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="INVENTORY", credit_role="GRNI"),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Receipt quantity must be positive",
        ),
    ),
    description="Receipt at actual cost differs from standard — records PPV",
)

INVENTORY_RECEIPT_WITH_VARIANCE_MAPPINGS = (
    ModuleLineMapping(
        role="INVENTORY", side="debit", ledger="GL", from_context="standard_total"
    ),
    ModuleLineMapping(
        role="PPV", side="debit", ledger="GL", from_context="variance_amount"
    ),
    ModuleLineMapping(role="GRNI", side="credit", ledger="GL"),
)


# --- Issue — Sale -------------------------------------------------------------

INVENTORY_ISSUE_SALE = AccountingPolicy(
    name="InventoryIssueSale",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.issue",
        where=(("payload.issue_type", "SALE"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_DECREASE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="COGS", credit_role="INVENTORY"),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="SOLD",
            credit_role="STOCK_ON_HAND",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.quantity <= 0",
            reason_code="INVALID_QUANTITY",
            message="Issue quantity must be positive",
        ),
    ),
    description="Records inventory issue for sales, recognizing COGS",
)

INVENTORY_ISSUE_SALE_MAPPINGS = (
    ModuleLineMapping(role="COGS", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
    ModuleLineMapping(role="SOLD", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="credit", ledger="INVENTORY"),
)


# --- Issue — Production -------------------------------------------------------

INVENTORY_ISSUE_PRODUCTION = AccountingPolicy(
    name="InventoryIssueProduction",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.issue",
        where=(("payload.issue_type", "PRODUCTION"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_TO_WIP",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center", "project"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="WIP", credit_role="INVENTORY"),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="IN_PRODUCTION",
            credit_role="STOCK_ON_HAND",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records inventory issue to production WIP",
)

INVENTORY_ISSUE_PRODUCTION_MAPPINGS = (
    ModuleLineMapping(role="WIP", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
    ModuleLineMapping(role="IN_PRODUCTION", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="credit", ledger="INVENTORY"),
)


# --- Issue — Scrap ------------------------------------------------------------

INVENTORY_ISSUE_SCRAP = AccountingPolicy(
    name="InventoryIssueScrap",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.issue",
        where=(("payload.issue_type", "SCRAP"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_DECREASE",
        quantity_field="payload.quantity",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="SCRAP_EXPENSE", credit_role="INVENTORY"
        ),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="SCRAPPED",
            credit_role="STOCK_ON_HAND",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records inventory scrapped and expensed",
)

INVENTORY_ISSUE_SCRAP_MAPPINGS = (
    ModuleLineMapping(role="SCRAP_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
    ModuleLineMapping(role="SCRAPPED", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="credit", ledger="INVENTORY"),
)


# --- Issue — Transfer Out -----------------------------------------------------

INVENTORY_ISSUE_TRANSFER = AccountingPolicy(
    name="InventoryIssueTransfer",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.issue",
        where=(("payload.issue_type", "TRANSFER"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_TRANSFER",
        quantity_field="payload.quantity",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="IN_TRANSIT",
            credit_role="STOCK_ON_HAND",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records inventory transfer between locations (outbound)",
)

INVENTORY_ISSUE_TRANSFER_MAPPINGS = (
    ModuleLineMapping(role="IN_TRANSIT", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="credit", ledger="INVENTORY"),
)


# --- Transfer In --------------------------------------------------------------

INVENTORY_TRANSFER_IN = AccountingPolicy(
    name="InventoryTransferIn",
    version=1,
    trigger=PolicyTrigger(event_type="inventory.transfer_in"),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_TRANSFER",
        quantity_field="payload.quantity",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="STOCK_ON_HAND",
            credit_role="IN_TRANSIT",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records inventory transfer received (inbound)",
)

INVENTORY_TRANSFER_IN_MAPPINGS = (
    ModuleLineMapping(role="STOCK_ON_HAND", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="IN_TRANSIT", side="credit", ledger="INVENTORY"),
)


# --- Receipt from Production --------------------------------------------------

INVENTORY_RECEIPT_FROM_PRODUCTION = AccountingPolicy(
    name="InventoryReceiptFromProduction",
    version=1,
    trigger=PolicyTrigger(event_type="inventory.receipt_from_production"),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_INCREASE",
        quantity_field="payload.quantity",
        dimensions=("org_unit", "cost_center"),
    ),
    ledger_effects=(
        LedgerEffect(ledger="GL", debit_role="INVENTORY", credit_role="WIP"),
    ),
    effective_from=date(2024, 1, 1),
    description="Finished goods received from production",
)

INVENTORY_RECEIPT_FROM_PRODUCTION_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY", side="debit", ledger="GL"),
    ModuleLineMapping(role="WIP", side="credit", ledger="GL"),
)


# --- Adjustment — Positive ----------------------------------------------------

INVENTORY_ADJUSTMENT_POSITIVE = AccountingPolicy(
    name="InventoryAdjustmentPositive",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.adjustment",
        where=(("payload.quantity_change > 0", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_ADJUSTMENT",
        quantity_field="payload.quantity_change",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="INVENTORY", credit_role="INVENTORY_VARIANCE"
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records positive inventory adjustment from count variance",
)

INVENTORY_ADJUSTMENT_POSITIVE_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY_VARIANCE", side="credit", ledger="GL"),
)


# --- Adjustment — Negative ----------------------------------------------------

INVENTORY_ADJUSTMENT_NEGATIVE = AccountingPolicy(
    name="InventoryAdjustmentNegative",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.adjustment",
        where=(("payload.quantity_change < 0", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_ADJUSTMENT",
        quantity_field="payload.quantity_change",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL", debit_role="INVENTORY_VARIANCE", credit_role="INVENTORY"
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Records negative inventory adjustment from count variance",
)

INVENTORY_ADJUSTMENT_NEGATIVE_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY_VARIANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
)


# --- Revaluation (LCM / NRV) -------------------------------------------------

INVENTORY_REVALUATION = AccountingPolicy(
    name="InventoryRevaluation",
    version=1,
    trigger=PolicyTrigger(event_type="inventory.revaluation"),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_REVALUATION",
        quantity_field="payload.quantity",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INVENTORY_REVALUATION",
            credit_role="INVENTORY",
        ),
    ),
    effective_from=date(2024, 1, 1),
    description="Inventory revalued (e.g., lower of cost or market adjustment)",
)

INVENTORY_REVALUATION_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY_REVALUATION", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
)


# --- Cycle Count — Positive ---------------------------------------------------

INVENTORY_CYCLE_COUNT_POSITIVE = AccountingPolicy(
    name="InventoryCycleCountPositive",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.cycle_count",
        where=(("payload.variance_quantity > 0", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_ADJUSTMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INVENTORY",
            credit_role="INVENTORY_VARIANCE",
        ),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="ITEM_BALANCE",
            credit_role="ADJUSTMENT",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Count adjustment amount must be positive",
        ),
    ),
    description="Positive cycle count adjustment (actual > expected)",
)

INVENTORY_CYCLE_COUNT_POSITIVE_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY_VARIANCE", side="credit", ledger="GL"),
    ModuleLineMapping(role="ITEM_BALANCE", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="ADJUSTMENT", side="credit", ledger="INVENTORY"),
)


# --- Cycle Count — Negative ---------------------------------------------------

INVENTORY_CYCLE_COUNT_NEGATIVE = AccountingPolicy(
    name="InventoryCycleCountNegative",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.cycle_count",
        where=(("payload.variance_quantity < 0", True),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_ADJUSTMENT",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INVENTORY_VARIANCE",
            credit_role="INVENTORY",
        ),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="ADJUSTMENT",
            credit_role="ITEM_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Count adjustment amount must be positive",
        ),
    ),
    description="Negative cycle count adjustment (actual < expected)",
)

INVENTORY_CYCLE_COUNT_NEGATIVE_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY_VARIANCE", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
    ModuleLineMapping(role="ADJUSTMENT", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="ITEM_BALANCE", side="credit", ledger="INVENTORY"),
)


# --- Warehouse Transfer Out ---------------------------------------------------

INVENTORY_WAREHOUSE_TRANSFER_OUT = AccountingPolicy(
    name="InventoryWarehouseTransferOut",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.warehouse_transfer",
        where=(("payload.direction", "out"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_TRANSFER",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INVENTORY_IN_TRANSIT",
            credit_role="INVENTORY",
        ),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="IN_TRANSIT",
            credit_role="STOCK_ON_HAND",
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
    ),
    description="Warehouse transfer outbound with subledger",
)

INVENTORY_WAREHOUSE_TRANSFER_OUT_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY_IN_TRANSIT", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
    ModuleLineMapping(role="IN_TRANSIT", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="credit", ledger="INVENTORY"),
)


# --- Warehouse Transfer In ----------------------------------------------------

INVENTORY_WAREHOUSE_TRANSFER_IN = AccountingPolicy(
    name="InventoryWarehouseTransferIn",
    version=1,
    trigger=PolicyTrigger(
        event_type="inventory.warehouse_transfer",
        where=(("payload.direction", "in"),),
    ),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_TRANSFER",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="INVENTORY",
            credit_role="INVENTORY_IN_TRANSIT",
        ),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="STOCK_ON_HAND",
            credit_role="IN_TRANSIT",
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
    ),
    description="Warehouse transfer inbound with subledger",
)

INVENTORY_WAREHOUSE_TRANSFER_IN_MAPPINGS = (
    ModuleLineMapping(role="INVENTORY", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY_IN_TRANSIT", side="credit", ledger="GL"),
    ModuleLineMapping(role="STOCK_ON_HAND", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="IN_TRANSIT", side="credit", ledger="INVENTORY"),
)


# --- Expired Write-Off --------------------------------------------------------

INVENTORY_EXPIRED_WRITE_OFF = AccountingPolicy(
    name="InventoryExpiredWriteOff",
    version=1,
    trigger=PolicyTrigger(event_type="inventory.expired"),
    meaning=PolicyMeaning(
        economic_type="INVENTORY_DECREASE",
        quantity_field="payload.amount",
        dimensions=("org_unit",),
    ),
    ledger_effects=(
        LedgerEffect(
            ledger="GL",
            debit_role="SCRAP_EXPENSE",
            credit_role="INVENTORY",
        ),
        LedgerEffect(
            ledger="INVENTORY",
            debit_role="WRITEOFF",
            credit_role="ITEM_BALANCE",
        ),
    ),
    effective_from=date(2024, 1, 1),
    guards=(
        GuardCondition(
            guard_type=GuardType.REJECT,
            expression="payload.amount <= 0",
            reason_code="INVALID_AMOUNT",
            message="Write-off amount must be positive",
        ),
    ),
    description="Expired inventory written off with subledger",
)

INVENTORY_EXPIRED_WRITE_OFF_MAPPINGS = (
    ModuleLineMapping(role="SCRAP_EXPENSE", side="debit", ledger="GL"),
    ModuleLineMapping(role="INVENTORY", side="credit", ledger="GL"),
    ModuleLineMapping(role="WRITEOFF", side="debit", ledger="INVENTORY"),
    ModuleLineMapping(role="ITEM_BALANCE", side="credit", ledger="INVENTORY"),
)


# =============================================================================
# Profile + Mapping pairs for registration
# =============================================================================

_ALL_PROFILES: tuple[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]], ...] = (
    (INVENTORY_RECEIPT, INVENTORY_RECEIPT_MAPPINGS),
    (INVENTORY_RECEIPT_WITH_VARIANCE, INVENTORY_RECEIPT_WITH_VARIANCE_MAPPINGS),
    (INVENTORY_ISSUE_SALE, INVENTORY_ISSUE_SALE_MAPPINGS),
    (INVENTORY_ISSUE_PRODUCTION, INVENTORY_ISSUE_PRODUCTION_MAPPINGS),
    (INVENTORY_ISSUE_SCRAP, INVENTORY_ISSUE_SCRAP_MAPPINGS),
    (INVENTORY_ISSUE_TRANSFER, INVENTORY_ISSUE_TRANSFER_MAPPINGS),
    (INVENTORY_TRANSFER_IN, INVENTORY_TRANSFER_IN_MAPPINGS),
    (INVENTORY_RECEIPT_FROM_PRODUCTION, INVENTORY_RECEIPT_FROM_PRODUCTION_MAPPINGS),
    (INVENTORY_ADJUSTMENT_POSITIVE, INVENTORY_ADJUSTMENT_POSITIVE_MAPPINGS),
    (INVENTORY_ADJUSTMENT_NEGATIVE, INVENTORY_ADJUSTMENT_NEGATIVE_MAPPINGS),
    (INVENTORY_REVALUATION, INVENTORY_REVALUATION_MAPPINGS),
    (INVENTORY_CYCLE_COUNT_POSITIVE, INVENTORY_CYCLE_COUNT_POSITIVE_MAPPINGS),
    (INVENTORY_CYCLE_COUNT_NEGATIVE, INVENTORY_CYCLE_COUNT_NEGATIVE_MAPPINGS),
    (INVENTORY_WAREHOUSE_TRANSFER_OUT, INVENTORY_WAREHOUSE_TRANSFER_OUT_MAPPINGS),
    (INVENTORY_WAREHOUSE_TRANSFER_IN, INVENTORY_WAREHOUSE_TRANSFER_IN_MAPPINGS),
    (INVENTORY_EXPIRED_WRITE_OFF, INVENTORY_EXPIRED_WRITE_OFF_MAPPINGS),
)


# =============================================================================
# Registration
# =============================================================================


def register() -> None:
    """Register all inventory profiles in kernel registries."""
    for profile, mappings in _ALL_PROFILES:
        register_rich_profile(MODULE_NAME, profile, mappings)

    logger.info(
        "inventory_profiles_registered",
        extra={"profile_count": len(_ALL_PROFILES)},
    )


# =============================================================================
# Backward-compat lookup dict
# =============================================================================

INVENTORY_PROFILES = {p.name: p for p, _ in _ALL_PROFILES}
