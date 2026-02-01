"""
Complexity tier definitions and configuration loading for benchmarks.

Provides three tiers of accounting configuration complexity:
  - SIMPLE: 5 modules (startup)     -> loads US-GAAP-2026-STARTUP
  - MEDIUM: 10 modules (mid-market) -> loads US-GAAP-2026-MIDMARKET
  - FULL: 19 modules (enterprise)   -> loads US-GAAP-2026-ENTERPRISE

Each tier has its own on-disk configuration set with complete YAML files.
The ``load_tier_config()`` function loads a tier's config directly via
``get_active_config()``, using legal_entity-based scope matching.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finance_config.compiler import (
    CompiledPolicyPack,
    PolicyMatchIndex,
    PolicyDecisionTrace,
)


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplexityTier:
    """A named complexity tier with a fixed module set."""

    name: str
    modules: tuple[str, ...]
    description: str


SIMPLE_TIER = ComplexityTier(
    name="SIMPLE",
    modules=("inventory", "payroll", "cash", "gl", "expense"),
    description="Startup: basic inventory, payroll, cash, GL, expense",
)

MEDIUM_TIER = ComplexityTier(
    name="MEDIUM",
    modules=(
        "inventory", "payroll", "cash", "gl", "expense",
        "ap", "ar", "assets", "tax", "procurement",
    ),
    description="Mid-market: adds AP, AR, fixed assets, tax, procurement",
)

FULL_TIER = ComplexityTier(
    name="FULL",
    modules=(
        "inventory", "payroll", "cash", "gl", "expense",
        "ap", "ar", "assets", "tax", "procurement",
        "wip", "contracts", "reporting", "revenue", "lease",
        "budget", "intercompany", "credit_loss", "project",
    ),
    description="Enterprise: all 19 modules",
)

TIERS = {"simple": SIMPLE_TIER, "medium": MEDIUM_TIER, "full": FULL_TIER}

# Map tier names to the legal_entity values used by on-disk config sets
_TIER_ENTITY_MAP = {
    "SIMPLE": "STARTUP",
    "MEDIUM": "MIDMARKET",
    "FULL": "ENTERPRISE",
}


def load_tier_config(tier: ComplexityTier) -> CompiledPolicyPack:
    """Load a tier's configuration directly from its on-disk config set.

    Each tier maps to a dedicated config set loaded via get_active_config():
      SIMPLE  -> legal_entity='STARTUP'   -> US-GAAP-2026-STARTUP
      MEDIUM  -> legal_entity='MIDMARKET'  -> US-GAAP-2026-MIDMARKET
      FULL    -> legal_entity='ENTERPRISE' -> US-GAAP-2026-ENTERPRISE
    """
    from finance_config import get_active_config

    legal_entity = _TIER_ENTITY_MAP[tier.name]
    return get_active_config(
        legal_entity=legal_entity,
        as_of_date=date(2026, 6, 15),
    )


# ---------------------------------------------------------------------------
# Config filtering (legacy — kept for backward compatibility)
# ---------------------------------------------------------------------------


def filter_compiled_pack(
    pack: CompiledPolicyPack, tier: ComplexityTier
) -> CompiledPolicyPack:
    """Filter a CompiledPolicyPack to include only the given tier's modules.

    Returns a new frozen pack with:
    - policies filtered by module
    - match_index rebuilt from filtered policies
    - role_bindings filtered to only roles referenced by filtered policies
    - engine_contracts filtered to only engines required by filtered policies
    - subledger_contracts filtered by owner_module
    """
    tier_modules = set(tier.modules)

    # 1. Filter policies
    filtered_policies = tuple(
        p for p in pack.policies if p.module in tier_modules
    )

    # 2. Rebuild match_index from filtered policies
    by_event_type: dict[str, list] = {}
    for p in filtered_policies:
        et = p.trigger.event_type
        by_event_type.setdefault(et, []).append(p)

    match_index = PolicyMatchIndex(
        entries={et: tuple(ps) for et, ps in by_event_type.items()}
    )

    # 3. Rebuild decision trace
    decision_trace = PolicyDecisionTrace(
        event_type_decisions={
            et: [p.name for p in ps] for et, ps in by_event_type.items()
        }
    )

    # 4. Collect referenced roles from filtered policies
    referenced_roles: set[str] = set()
    for p in filtered_policies:
        for effect in p.ledger_effects:
            referenced_roles.add(effect.debit_role)
            referenced_roles.add(effect.credit_role)
        for mapping in p.line_mappings:
            referenced_roles.add(mapping.role)

    # Always include ROUNDING role
    referenced_roles.add("ROUNDING")

    # 4b. Filter subledger contracts by owner_module (need this before
    # role filtering to include subledger control_account_roles)
    filtered_subledger = tuple(
        sc for sc in pack.subledger_contracts
        if sc.owner_module in tier_modules
    )

    # Include roles referenced by subledger contracts
    for sc in filtered_subledger:
        referenced_roles.add(sc.control_account_role)

    # 5. Filter role bindings to only referenced roles
    filtered_bindings = tuple(
        b for b in pack.role_bindings if b.role in referenced_roles
    )

    # 6. Collect required engines from filtered policies
    required_engines: set[str] = set()
    for p in filtered_policies:
        required_engines.update(p.required_engines)

    # 7. Filter engine contracts
    filtered_engine_contracts = {
        k: v for k, v in pack.engine_contracts.items()
        if k in required_engines
    }

    # 8. Filter resolved engine params
    filtered_engine_params = {
        k: v for k, v in pack.resolved_engine_params.items()
        if k in required_engines
    }

    return dataclasses.replace(
        pack,
        policies=filtered_policies,
        match_index=match_index,
        role_bindings=filtered_bindings,
        engine_contracts=filtered_engine_contracts,
        resolved_engine_params=filtered_engine_params,
        subledger_contracts=filtered_subledger,
        decision_trace=decision_trace,
    )


# ---------------------------------------------------------------------------
# Module registration by tier
# ---------------------------------------------------------------------------

# Map of module name -> import path for register function
_MODULE_REGISTER_MAP: dict[str, str] = {
    "inventory": "finance_modules.inventory.profiles",
    "payroll": "finance_modules.payroll.profiles",
    "cash": "finance_modules.cash.profiles",
    "gl": "finance_modules.gl.profiles",
    "expense": "finance_modules.expense.profiles",
    "ap": "finance_modules.ap.profiles",
    "ar": "finance_modules.ar.profiles",
    "assets": "finance_modules.assets.profiles",
    "tax": "finance_modules.tax.profiles",
    "procurement": "finance_modules.procurement.profiles",
    "wip": "finance_modules.wip.profiles",
    "contracts": "finance_modules.contracts.profiles",
    "reporting": "finance_modules.reporting.profiles",
    "revenue": "finance_modules.revenue.profiles",
    "lease": "finance_modules.lease.profiles",
    "budget": "finance_modules.budget.profiles",
    "intercompany": "finance_modules.intercompany.profiles",
    "credit_loss": "finance_modules.credit_loss.profiles",
    "project": "finance_modules.project.profiles",
}


def register_tier_modules(tier: ComplexityTier) -> int:
    """Clear global registries and register only the tier's modules.

    Returns the number of modules successfully registered.
    """
    import importlib

    from finance_kernel.domain.policy_selector import PolicySelector
    from finance_kernel.domain.policy_bridge import ModulePolicyRegistry

    # Clear global registries
    PolicySelector.clear()
    ModulePolicyRegistry.clear()

    registered = 0
    for module_name in tier.modules:
        module_path = _MODULE_REGISTER_MAP.get(module_name)
        if module_path is None:
            continue
        try:
            mod = importlib.import_module(module_path)
            mod.register()
            registered += 1
        except (ImportError, AttributeError):
            pass  # Module not available — skip

    return registered


# ---------------------------------------------------------------------------
# Tier-appropriate scenario factories
# ---------------------------------------------------------------------------


def make_tier_scenarios(tier: ComplexityTier) -> list[dict]:
    """Return scenario factories appropriate for the tier.

    Only includes events for modules that are registered in the tier.
    """
    scenarios = []

    # All tiers have inventory
    if "inventory" in tier.modules:
        scenarios.append({
            "label": "inventory_receipt",
            "event_type": "inventory.receipt",
            "amount": Decimal("25000.00"),
            "currency": "USD",
            "payload": {"quantity": 500, "has_variance": False},
            "producer": "inventory",
        })

    # MEDIUM and FULL add payroll
    if "payroll" in tier.modules and tier.name != "SIMPLE":
        scenarios.append({
            "label": "payroll_accrual",
            "event_type": "payroll.accrual",
            "amount": Decimal("125000.00"),
            "currency": "USD",
            "payload": {
                "gross_pay": "125000.00",
                "federal_tax_amount": "25000.00",
                "state_tax_amount": "8750.00",
                "fica_amount": "9562.50",
                "net_pay_amount": "81687.50",
            },
            "producer": "payroll",
        })

    # FULL adds engine-requiring event
    if tier.name == "FULL" and "inventory" in tier.modules:
        scenarios.append({
            "label": "inventory_ppv_variance",
            "event_type": "inventory.receipt",
            "amount": Decimal("10500.00"),
            "currency": "USD",
            "payload": {
                "quantity": 1000,
                "has_variance": True,
                "standard_price": "10.00",
                "actual_price": "10.50",
                "standard_total": "10000.00",
                "variance_amount": "500.00",
                "variance_type": "price",
                "expected_price": "10.00",
            },
            "producer": "inventory",
        })

    return scenarios
