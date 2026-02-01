#!/usr/bin/env python3
"""
Migration script: Generate YAML configuration fragments from existing
Python profile definitions.

Reads all registered AccountingPolicys from finance_modules/*/profiles.py
and writes finance_config/sets/US-GAAP-2026-v1/ fragment files.

One-time migration tool — run once, then maintain YAML going forward.
"""

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datetime import date
from typing import Any

import yaml

from finance_kernel.domain.accounting_policy import AccountingPolicy
from finance_kernel.domain.policy_bridge import ModuleLineMapping

# ---------------------------------------------------------------------------
# Profile collection — import all module profiles
# ---------------------------------------------------------------------------

def collect_all_profiles() -> dict[str, list[tuple[AccountingPolicy, tuple[ModuleLineMapping, ...]]]]:
    """Import and collect all profiles from all modules."""
    from finance_modules.ap.profiles import _ALL_PROFILES as ap
    from finance_modules.ar.profiles import _ALL_PROFILES as ar
    from finance_modules.assets.profiles import _ALL_PROFILES as assets
    from finance_modules.cash.profiles import _ALL_PROFILES as cash
    from finance_modules.contracts.profiles import _ALL_PROFILES as contracts
    from finance_modules.expense.profiles import _ALL_PROFILES as expense
    from finance_modules.gl.profiles import _ALL_PROFILES as gl
    from finance_modules.inventory.profiles import _ALL_PROFILES as inv
    from finance_modules.payroll.profiles import _ALL_PROFILES as payroll
    from finance_modules.procurement.profiles import _ALL_PROFILES as procurement
    from finance_modules.tax.profiles import _ALL_PROFILES as tax
    from finance_modules.wip.profiles import _ALL_PROFILES as wip

    return {
        "inventory": list(inv),
        "ap": list(ap),
        "ar": list(ar),
        "cash": list(cash),
        "expense": list(expense),
        "gl": list(gl),
        "payroll": list(payroll),
        "procurement": list(procurement),
        "tax": list(tax),
        "wip": list(wip),
        "assets": list(assets),
        "contracts": list(contracts),
    }


def profile_to_dict(
    module_name: str,
    profile: AccountingPolicy,
    mappings: tuple[ModuleLineMapping, ...],
) -> dict[str, Any]:
    """Convert an AccountingPolicy + mappings to a serializable dict."""
    d: dict[str, Any] = {
        "name": profile.name,
        "version": profile.version,
        "module": module_name,
        "description": profile.description or "",
        "effective_from": str(profile.effective_from),
    }

    if profile.effective_to:
        d["effective_to"] = str(profile.effective_to)
    if profile.scope != "*":
        d["scope"] = profile.scope

    # Trigger
    trigger: dict[str, Any] = {
        "event_type": profile.trigger.event_type,
    }
    if profile.trigger.schema_version != 1:
        trigger["schema_version"] = profile.trigger.schema_version
    if profile.trigger.where:
        trigger["where"] = [
            {"field": field, "value": _serialize_value(value)}
            for field, value in profile.trigger.where
        ]
    d["trigger"] = trigger

    # Meaning
    meaning: dict[str, Any] = {
        "economic_type": profile.meaning.economic_type,
    }
    if profile.meaning.quantity_field:
        meaning["quantity_field"] = profile.meaning.quantity_field
    if profile.meaning.dimensions:
        meaning["dimensions"] = list(profile.meaning.dimensions)
    d["meaning"] = meaning

    # Ledger effects
    d["ledger_effects"] = [
        {
            "ledger": e.ledger,
            "debit_role": e.debit_role,
            "credit_role": e.credit_role,
        }
        for e in profile.ledger_effects
    ]

    # Guards
    if profile.guards:
        d["guards"] = [
            {
                "guard_type": g.guard_type.value if hasattr(g.guard_type, 'value') else str(g.guard_type),
                "expression": g.expression,
                "reason_code": g.reason_code,
                "message": g.message,
            }
            for g in profile.guards
        ]

    # Line mappings
    if mappings:
        d["line_mappings"] = [
            _mapping_to_dict(m) for m in mappings
        ]

    # Precedence
    if profile.precedence and (
        profile.precedence.priority != 0
        or profile.precedence.overrides
    ):
        prec: dict[str, Any] = {}
        if hasattr(profile.precedence, 'mode'):
            prec["mode"] = profile.precedence.mode.value if hasattr(profile.precedence.mode, 'value') else str(profile.precedence.mode)
        if profile.precedence.priority:
            prec["priority"] = profile.precedence.priority
        if profile.precedence.overrides:
            prec["overrides"] = list(profile.precedence.overrides)
        if prec:
            d["precedence"] = prec

    return d


def _mapping_to_dict(m: ModuleLineMapping) -> dict[str, Any]:
    """Convert a ModuleLineMapping to dict."""
    d: dict[str, Any] = {
        "role": m.role,
        "side": m.side,
    }
    if m.ledger != "GL":
        d["ledger"] = m.ledger
    if m.from_context:
        d["from_context"] = m.from_context
    if m.foreach:
        d["foreach"] = m.foreach
    return d


def _serialize_value(value: Any) -> Any:
    """Convert Python values to YAML-safe types."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# YAML generation
# ---------------------------------------------------------------------------

def generate_root_yaml() -> dict[str, Any]:
    """Generate root.yaml content."""
    return {
        "config_id": "US-GAAP-2026-v1",
        "version": 1,
        "status": "published",
        "predecessor": None,
        "scope": {
            "legal_entity": "*",
            "jurisdiction": "US",
            "regulatory_regime": "GAAP",
            "currency": "USD",
            "effective_from": "2024-01-01",
            "effective_to": None,
        },
        "capabilities": {
            "inventory": True,
            "ap": True,
            "ar": True,
            "cash": True,
            "expense": True,
            "gl": True,
            "payroll": True,
            "procurement": True,
            "tax": True,
            "wip": True,
            "assets": True,
            "contracts": True,
            "dcaa": False,
            "ifrs": False,
            "multicurrency": False,
        },
        "precedence_rules": [
            {
                "name": "specificity_first",
                "description": "More specific where-clause wins over generic",
                "rule_type": "specificity",
            },
        ],
    }


def generate_chart_of_accounts() -> dict[str, Any]:
    """Generate chart_of_accounts.yaml from known role bindings."""
    # These are the role bindings from conftest.py module_role_resolver
    # plus additional roles discovered in profiles
    bindings = [
        # Core GL roles
        {"role": "INVENTORY", "account_code": "1200", "ledger": "GL"},
        {"role": "GRNI", "account_code": "2100", "ledger": "GL"},
        {"role": "EXPENSE", "account_code": "6000", "ledger": "GL"},
        {"role": "ACCOUNTS_PAYABLE", "account_code": "2000", "ledger": "GL"},
        {"role": "CASH", "account_code": "1000", "ledger": "GL"},
        {"role": "COGS", "account_code": "5000", "ledger": "GL"},
        {"role": "ACCOUNTS_RECEIVABLE", "account_code": "1100", "ledger": "GL"},
        {"role": "REVENUE", "account_code": "4000", "ledger": "GL"},
        {"role": "TAX_PAYABLE", "account_code": "2300", "ledger": "GL"},
        {"role": "ACCRUED_LIABILITY", "account_code": "2200", "ledger": "GL"},
        {"role": "PREPAID_EXPENSE", "account_code": "1300", "ledger": "GL"},
        {"role": "SCRAP_EXPENSE", "account_code": "6100", "ledger": "GL"},
        {"role": "PPV", "account_code": "6200", "ledger": "GL"},
        {"role": "INVENTORY_VARIANCE", "account_code": "6300", "ledger": "GL"},
        {"role": "WIP", "account_code": "1400", "ledger": "GL"},
        {"role": "INVENTORY_REVALUATION", "account_code": "6400", "ledger": "GL"},
        {"role": "RoundingExpense", "account_code": "6999", "ledger": "GL"},
        # Inventory subledger roles
        {"role": "STOCK_ON_HAND", "account_code": "SL-1001", "ledger": "INVENTORY"},
        {"role": "IN_TRANSIT", "account_code": "SL-1002", "ledger": "INVENTORY"},
        {"role": "SOLD", "account_code": "SL-1003", "ledger": "INVENTORY"},
        {"role": "IN_PRODUCTION", "account_code": "SL-1004", "ledger": "INVENTORY"},
        {"role": "SCRAPPED", "account_code": "SL-1005", "ledger": "INVENTORY"},
        # AP subledger roles
        {"role": "INVOICE", "account_code": "SL-2001", "ledger": "AP"},
        {"role": "SUPPLIER_BALANCE", "account_code": "SL-2002", "ledger": "AP"},
        {"role": "PAYMENT", "account_code": "SL-2003", "ledger": "AP"},
        # Additional GL roles from profiles
        {"role": "FIXED_ASSET", "account_code": "1500", "ledger": "GL"},
        {"role": "ACCUMULATED_DEPRECIATION", "account_code": "1510", "ledger": "GL"},
        {"role": "DEPRECIATION_EXPENSE", "account_code": "6500", "ledger": "GL"},
        {"role": "CIP", "account_code": "1520", "ledger": "GL"},
        {"role": "GAIN_ON_DISPOSAL", "account_code": "4100", "ledger": "GL"},
        {"role": "LOSS_ON_DISPOSAL", "account_code": "6600", "ledger": "GL"},
        {"role": "IMPAIRMENT_LOSS", "account_code": "6700", "ledger": "GL"},
        {"role": "SALARY_EXPENSE", "account_code": "6010", "ledger": "GL"},
        {"role": "WAGE_EXPENSE", "account_code": "6020", "ledger": "GL"},
        {"role": "OVERTIME_EXPENSE", "account_code": "6030", "ledger": "GL"},
        {"role": "PTO_EXPENSE", "account_code": "6040", "ledger": "GL"},
        {"role": "PAYROLL_TAX_EXPENSE", "account_code": "6050", "ledger": "GL"},
        {"role": "ACCRUED_PAYROLL", "account_code": "2400", "ledger": "GL"},
        {"role": "FEDERAL_TAX_PAYABLE", "account_code": "2310", "ledger": "GL"},
        {"role": "STATE_TAX_PAYABLE", "account_code": "2320", "ledger": "GL"},
        {"role": "FICA_PAYABLE", "account_code": "2330", "ledger": "GL"},
        {"role": "BENEFITS_PAYABLE", "account_code": "2340", "ledger": "GL"},
        {"role": "LABOR_CLEARING", "account_code": "2500", "ledger": "GL"},
        {"role": "OVERHEAD_POOL", "account_code": "6800", "ledger": "GL"},
        {"role": "OVERHEAD_EXPENSE", "account_code": "6810", "ledger": "GL"},
        {"role": "OVERHEAD_APPLIED", "account_code": "6820", "ledger": "GL"},
        {"role": "OVERHEAD_VARIANCE", "account_code": "6830", "ledger": "GL"},
        {"role": "OVERHEAD_CONTROL", "account_code": "6840", "ledger": "GL"},
        {"role": "RAW_MATERIALS", "account_code": "1210", "ledger": "GL"},
        {"role": "FINISHED_GOODS", "account_code": "1220", "ledger": "GL"},
        {"role": "LABOR_VARIANCE", "account_code": "6350", "ledger": "GL"},
        {"role": "MATERIAL_VARIANCE", "account_code": "6360", "ledger": "GL"},
        {"role": "SALES_TAX_PAYABLE", "account_code": "2350", "ledger": "GL"},
        {"role": "SALES_TAX_RECEIVABLE", "account_code": "1350", "ledger": "GL"},
        {"role": "USE_TAX_EXPENSE", "account_code": "6060", "ledger": "GL"},
        {"role": "USE_TAX_PAYABLE", "account_code": "2360", "ledger": "GL"},
        {"role": "TAX_PROVISION", "account_code": "2370", "ledger": "GL"},
        {"role": "TAX_EXPENSE_FEDERAL", "account_code": "6070", "ledger": "GL"},
        {"role": "TAX_EXPENSE_STATE", "account_code": "6080", "ledger": "GL"},
        {"role": "TAX_REFUND_RECEIVABLE", "account_code": "1360", "ledger": "GL"},
        {"role": "ENCUMBRANCE", "account_code": "1600", "ledger": "GL"},
        {"role": "RESERVE_ENCUMBRANCE", "account_code": "2600", "ledger": "GL"},
        {"role": "UNEARNED_REVENUE", "account_code": "2700", "ledger": "GL"},
        {"role": "DISCOUNT_ALLOWED", "account_code": "4200", "ledger": "GL"},
        {"role": "ALLOWANCE_DOUBTFUL", "account_code": "1110", "ledger": "GL"},
        {"role": "BAD_DEBT_EXPENSE", "account_code": "6900", "ledger": "GL"},
        {"role": "BANK_FEES", "account_code": "6910", "ledger": "GL"},
        {"role": "INTEREST_INCOME", "account_code": "4300", "ledger": "GL"},
        {"role": "INTEREST_EXPENSE", "account_code": "6920", "ledger": "GL"},
        {"role": "FOREIGN_CURRENCY_BALANCE", "account_code": "1700", "ledger": "GL"},
        {"role": "REALIZED_FX_GAIN", "account_code": "4400", "ledger": "GL"},
        {"role": "REALIZED_FX_LOSS", "account_code": "6930", "ledger": "GL"},
        {"role": "UNREALIZED_FX_GAIN", "account_code": "4410", "ledger": "GL"},
        {"role": "UNREALIZED_FX_LOSS", "account_code": "6940", "ledger": "GL"},
        {"role": "RETAINED_EARNINGS", "account_code": "3100", "ledger": "GL"},
        {"role": "INCOME_SUMMARY", "account_code": "3200", "ledger": "GL"},
        # Contract-specific roles
        {"role": "CONTRACT_RECEIVABLE", "account_code": "1120", "ledger": "GL"},
        {"role": "CONTRACT_REVENUE", "account_code": "4500", "ledger": "GL"},
        {"role": "UNBILLED_RECEIVABLE", "account_code": "1130", "ledger": "GL"},
        {"role": "BILLING_IN_EXCESS", "account_code": "2710", "ledger": "GL"},
        {"role": "CONTRACT_WIP", "account_code": "1410", "ledger": "GL"},
        {"role": "DIRECT_COST", "account_code": "5100", "ledger": "GL"},
        {"role": "INDIRECT_COST", "account_code": "5200", "ledger": "GL"},
        {"role": "OVERHEAD_APPLIED_CONTRACT", "account_code": "5300", "ledger": "GL"},
        {"role": "FEE_EARNED", "account_code": "4600", "ledger": "GL"},
        {"role": "FEE_ACCRUED", "account_code": "2720", "ledger": "GL"},
        {"role": "PROVISIONAL_RATE_ADJ", "account_code": "5400", "ledger": "GL"},
        {"role": "RATE_ADJUSTMENT_PAYABLE", "account_code": "2730", "ledger": "GL"},
        {"role": "COST_SHARING", "account_code": "5500", "ledger": "GL"},
        {"role": "COST_SHARING_PAYABLE", "account_code": "2740", "ledger": "GL"},
        {"role": "CONTRACT_LOSS", "account_code": "6950", "ledger": "GL"},
        {"role": "CONTRACT_LOSS_RESERVE", "account_code": "2750", "ledger": "GL"},
        {"role": "PENALTY_EXPENSE", "account_code": "6960", "ledger": "GL"},
        {"role": "PENALTY_PAYABLE", "account_code": "2760", "ledger": "GL"},
        {"role": "INCENTIVE_RECEIVABLE", "account_code": "1140", "ledger": "GL"},
        {"role": "INCENTIVE_FEE_INCOME", "account_code": "4700", "ledger": "GL"},
        {"role": "RETAINAGE_RECEIVABLE", "account_code": "1150", "ledger": "GL"},
    ]

    return {"role_bindings": bindings}


def generate_ledgers() -> dict[str, Any]:
    """Generate ledgers.yaml."""
    return {
        "ledgers": [
            {
                "ledger_id": "GL",
                "name": "General Ledger",
                "required_roles": [],
            },
            {
                "ledger_id": "INVENTORY",
                "name": "Inventory Subledger",
                "required_roles": [
                    "STOCK_ON_HAND", "IN_TRANSIT", "SOLD",
                    "IN_PRODUCTION", "SCRAPPED",
                ],
            },
            {
                "ledger_id": "AP",
                "name": "Accounts Payable Subledger",
                "required_roles": [
                    "INVOICE", "SUPPLIER_BALANCE", "PAYMENT",
                ],
            },
        ],
    }


def generate_engine_params() -> dict[str, Any]:
    """Generate engine_params.yaml."""
    return {
        "engines": [
            {
                "engine_name": "variance",
                "version_constraint": "1.*",
                "parameters": {
                    "tolerance_percent": 5.0,
                    "tolerance_amount": 10.0,
                },
            },
            {
                "engine_name": "allocation",
                "version_constraint": "1.*",
                "parameters": {
                    "method": "proportional",
                    "rounding_method": "largest_remainder",
                },
            },
            {
                "engine_name": "matching",
                "version_constraint": "1.*",
                "parameters": {
                    "tolerance_percent": 5.0,
                    "tolerance_amount": 10.0,
                    "match_strategy": "three_way",
                },
            },
            {
                "engine_name": "aging",
                "version_constraint": "1.*",
                "parameters": {
                    "buckets": [30, 60, 90, 120],
                },
            },
            {
                "engine_name": "tax",
                "version_constraint": "1.*",
                "parameters": {
                    "calculation_method": "destination",
                },
            },
        ],
    }


def generate_controls() -> dict[str, Any]:
    """Generate controls.yaml."""
    return {
        "controls": [
            {
                "name": "positive_amount_required",
                "applies_to": "*",
                "action": "reject",
                "expression": "payload.amount <= 0",
                "reason_code": "INVALID_AMOUNT",
                "message": "Transaction amount must be positive",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    output_dir = ROOT / "finance_config" / "sets" / "US-GAAP-2026-v1"
    policies_dir = output_dir / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)

    # Collect profiles
    print("Collecting profiles from all modules...")
    all_profiles = collect_all_profiles()
    total = sum(len(v) for v in all_profiles.values())
    print(f"  Found {total} profiles across {len(all_profiles)} modules")

    # Write root.yaml
    root_path = output_dir / "root.yaml"
    with open(root_path, "w") as f:
        yaml.dump(generate_root_yaml(), f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {root_path.relative_to(ROOT)}")

    # Write chart_of_accounts.yaml
    coa_path = output_dir / "chart_of_accounts.yaml"
    with open(coa_path, "w") as f:
        yaml.dump(generate_chart_of_accounts(), f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {coa_path.relative_to(ROOT)}")

    # Write ledgers.yaml
    ledgers_path = output_dir / "ledgers.yaml"
    with open(ledgers_path, "w") as f:
        yaml.dump(generate_ledgers(), f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {ledgers_path.relative_to(ROOT)}")

    # Write engine_params.yaml
    engine_path = output_dir / "engine_params.yaml"
    with open(engine_path, "w") as f:
        yaml.dump(generate_engine_params(), f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {engine_path.relative_to(ROOT)}")

    # Write controls.yaml
    controls_path = output_dir / "controls.yaml"
    with open(controls_path, "w") as f:
        yaml.dump(generate_controls(), f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {controls_path.relative_to(ROOT)}")

    # Write policy files (one per module)
    for module_name, profiles in all_profiles.items():
        policy_list = []
        for profile, mappings in profiles:
            policy_list.append(profile_to_dict(module_name, profile, mappings))

        policy_path = policies_dir / f"{module_name}.yaml"
        with open(policy_path, "w") as f:
            yaml.dump(
                {"policies": policy_list},
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        print(f"  Wrote {policy_path.relative_to(ROOT)} ({len(policy_list)} policies)")

    print(f"\nDone! Generated configuration fragments in {output_dir.relative_to(ROOT)}")
    print(f"Total: {total} policies across {len(all_profiles)} modules")


if __name__ == "__main__":
    main()
