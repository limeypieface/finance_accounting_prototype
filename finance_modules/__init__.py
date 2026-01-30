"""
Finance Modules.

Thin orchestration layers over the Finance Kernel and Engines.
Each module contains:
- Domain models (the nouns)
- Economic profiles (event -> journal entry mappings)
- Workflows (state machines)
- Configuration schemas (policy and settings)

Modules:
- Cash: Bank accounts, reconciliation, internal transfers
- AP: Vendor invoices, payments, accruals
- AR: Customer invoices, receipts, collections
- Inventory: Stock items, receipts, issues, valuation
- WIP: Work orders, labor, overhead, variances
- Assets: Fixed assets, depreciation, disposal
- Expense: Travel & expense reports, corporate cards
- Tax: Sales/use tax, VAT, reporting
- Procurement: Requisitions, purchase orders, receiving
- Payroll: Timecards, paychecks, labor distribution
- GL: Chart of accounts, period close, consolidation
- Contracts: Government contracts, DCAA compliance

Total: ~2,000 lines of declarative, module-specific code.
Actual processing logic lives in the kernel and engines.
"""

from finance_modules import (
    cash,
    ap,
    ar,
    inventory,
    wip,
    assets,
    expense,
    tax,
    procurement,
    payroll,
    gl,
    reporting,
)

__all__ = [
    "cash",
    "ap",
    "ar",
    "inventory",
    "wip",
    "assets",
    "expense",
    "tax",
    "procurement",
    "payroll",
    "gl",
    "contracts",
    "reporting",
    "register_all_modules",
]


def register_all_modules() -> None:
    """
    Register all module profiles in kernel registries.

    Call this explicitly at startup or in test fixtures.
    Profiles are NOT auto-registered at import time to avoid
    test isolation issues with PolicySelector.clear().
    """
    from finance_kernel.logging_config import get_logger

    logger = get_logger("modules")

    from finance_modules.inventory.profiles import register as register_inventory
    from finance_modules.ap.profiles import register as register_ap
    from finance_modules.ar.profiles import register as register_ar
    from finance_modules.assets.profiles import register as register_assets
    from finance_modules.cash.profiles import register as register_cash
    from finance_modules.expense.profiles import register as register_expense
    from finance_modules.gl.profiles import register as register_gl
    from finance_modules.payroll.profiles import register as register_payroll
    from finance_modules.procurement.profiles import register as register_procurement
    from finance_modules.tax.profiles import register as register_tax
    from finance_modules.wip.profiles import register as register_wip

    register_inventory()
    register_ap()
    register_ar()
    register_assets()
    register_cash()
    register_expense()
    register_gl()
    register_payroll()
    register_procurement()
    register_tax()
    register_wip()

    # Reporting module (read-only — no-op registration)
    from finance_modules.reporting.profiles import register as register_reporting

    register_reporting()

    # Contracts module (may not exist yet during transition)
    try:
        from finance_modules.contracts.profiles import register as register_contracts

        register_contracts()
    except ImportError:
        logger.debug("contracts_module_not_available")

    logger.info("all_module_profiles_registered")
