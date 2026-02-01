#!/usr/bin/env python3
"""
Generate three tiered configuration sets from the US-GAAP-2026-v1 source.

Each tier is a complete, self-contained config set under finance_config/sets/:
  - US-GAAP-2026-STARTUP    (5 modules:  inventory, payroll, cash, gl, expense)
  - US-GAAP-2026-MIDMARKET  (10 modules: + ap, ar, assets, tax, procurement)
  - US-GAAP-2026-ENTERPRISE (19 modules: all)

Role bindings, engine params, subledger contracts, and ledger definitions are
derived programmatically by tracing every role referenced in the tier's policies.
Policy YAML files are copied verbatim from v1.

Usage:
    python3 scripts/generate_tier_configs.py [--validate]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETS_DIR = PROJECT_ROOT / "finance_config" / "sets"
V1_DIR = SETS_DIR / "US-GAAP-2026-v1"

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIERS = {
    "US-GAAP-2026-STARTUP": {
        "config_id": "US-GAAP-2026-STARTUP",
        "legal_entity": "STARTUP",
        "description": "Startup: basic inventory, payroll, cash, GL, expense",
        "modules": ("inventory", "payroll", "cash", "gl", "expense"),
    },
    "US-GAAP-2026-MIDMARKET": {
        "config_id": "US-GAAP-2026-MIDMARKET",
        "legal_entity": "MIDMARKET",
        "description": "Mid-market: adds AP, AR, fixed assets, tax, procurement",
        "modules": (
            "inventory", "payroll", "cash", "gl", "expense",
            "ap", "ar", "assets", "tax", "procurement",
        ),
    },
    "US-GAAP-2026-ENTERPRISE": {
        "config_id": "US-GAAP-2026-ENTERPRISE",
        "legal_entity": "ENTERPRISE",
        "description": "Enterprise: all 19 modules",
        "modules": (
            "inventory", "payroll", "cash", "gl", "expense",
            "ap", "ar", "assets", "tax", "procurement",
            "wip", "contracts", "reporting", "revenue", "lease",
            "budget", "intercompany", "credit_loss", "project",
        ),
    },
}

# All known module names (for capability gating)
ALL_MODULES = (
    "inventory", "payroll", "cash", "gl", "expense",
    "ap", "ar", "assets", "tax", "procurement",
    "wip", "contracts", "reporting", "revenue", "lease",
    "budget", "intercompany", "credit_loss", "project",
)

# Non-module capabilities that are always false in US-GAAP base
ALWAYS_FALSE_CAPABILITIES = ("dcaa", "ifrs", "multicurrency")


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, data: dict, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        if header:
            f.write(header)
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Role tracing
# ---------------------------------------------------------------------------

def trace_roles_from_policies(
    policies: list[dict],
    subledger_contracts: list[dict],
) -> set[tuple[str, str]]:
    """Trace all (role, ledger) pairs referenced by policies + subledger contracts.

    Returns a set of (role_name, ledger_id) tuples.
    """
    roles: set[tuple[str, str]] = set()

    for policy in policies:
        # From ledger_effects
        for effect in policy.get("ledger_effects", []):
            ledger = effect.get("ledger", "GL")
            roles.add((effect["debit_role"], ledger))
            roles.add((effect["credit_role"], ledger))

        # From line_mappings
        for mapping in policy.get("line_mappings", []):
            ledger = mapping.get("ledger", "GL")
            roles.add((mapping["role"], ledger))

    # ROUNDING is always required (used by Bookkeeper)
    roles.add(("ROUNDING", "GL"))

    # Control account roles from subledger contracts
    for contract in subledger_contracts:
        roles.add((contract["control_account_role"], "GL"))

    return roles


def trace_required_engines(policies: list[dict]) -> set[str]:
    """Collect all engine names referenced by policies."""
    engines: set[str] = set()
    for policy in policies:
        for engine in policy.get("required_engines", []):
            engines.add(engine)
    return engines


def trace_required_ledgers(
    policies: list[dict],
    subledger_contracts: list[dict],
) -> set[str]:
    """Determine which ledger IDs are used by the tier's policies."""
    ledgers: set[str] = {"GL"}  # GL is always present

    for policy in policies:
        for effect in policy.get("ledger_effects", []):
            ledgers.add(effect.get("ledger", "GL"))
        for mapping in policy.get("line_mappings", []):
            if "ledger" in mapping:
                ledgers.add(mapping["ledger"])

    # Subledger contract subledger_ids map to ledger IDs
    for contract in subledger_contracts:
        sid = contract["subledger_id"]
        # BANK subledger uses BANK ledger, WIP uses CONTRACT, etc.
        ledgers.add(sid)

    return ledgers


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_tier(tier_name: str, tier_def: dict) -> Path:
    """Generate a complete config set for one tier. Returns the output directory."""

    config_id = tier_def["config_id"]
    legal_entity = tier_def["legal_entity"]
    modules = set(tier_def["modules"])

    output_dir = SETS_DIR / tier_name
    policies_dir = output_dir / "policies"

    # Clean previous output
    if output_dir.exists():
        shutil.rmtree(output_dir)
    policies_dir.mkdir(parents=True)

    # ---- Load v1 source data ----
    v1_root = load_yaml(V1_DIR / "root.yaml")
    v1_coa = load_yaml(V1_DIR / "chart_of_accounts.yaml")
    v1_ledgers = load_yaml(V1_DIR / "ledgers.yaml")
    v1_engines = load_yaml(V1_DIR / "engine_params.yaml")
    v1_controls = load_yaml(V1_DIR / "controls.yaml")
    v1_subledger = load_yaml(V1_DIR / "subledger_contracts.yaml")

    # ---- 1. Filter policies by module ----
    all_policies: list[dict] = []
    policy_files: list[str] = []

    v1_policies_dir = V1_DIR / "policies"
    for policy_file in sorted(v1_policies_dir.glob("*.yaml")):
        file_data = load_yaml(policy_file)
        file_policies = file_data.get("policies", [])

        # Check if ANY policy in this file belongs to our tier
        file_modules = {p.get("module", "") for p in file_policies}
        if file_modules & modules:
            # Copy the entire file (all policies in a module file share the module)
            all_policies.extend(file_policies)
            policy_files.append(policy_file.name)
            shutil.copy2(policy_file, policies_dir / policy_file.name)

    # ---- 2. Filter subledger contracts by owner_module ----
    v1_contracts = v1_subledger.get("contracts", [])
    tier_contracts = [c for c in v1_contracts if c["owner_module"] in modules]

    # ---- 3. Trace roles from filtered policies + contracts ----
    required_roles = trace_roles_from_policies(all_policies, tier_contracts)

    # ---- 4. Filter role bindings to only traced roles ----
    v1_bindings = v1_coa.get("role_bindings", [])
    tier_bindings = []
    for binding in v1_bindings:
        role = binding["role"]
        ledger = binding.get("ledger", "GL")
        if (role, ledger) in required_roles:
            tier_bindings.append(binding)

    # ---- 5. Determine required ledgers ----
    required_ledger_ids = trace_required_ledgers(all_policies, tier_contracts)

    # Filter ledger definitions
    v1_ledger_defs = v1_ledgers.get("ledgers", [])
    tier_ledger_defs = [ld for ld in v1_ledger_defs if ld["ledger_id"] in required_ledger_ids]

    # Add any ledgers used by policies/contracts but missing from v1 ledgers.yaml
    existing_ledger_ids = {ld["ledger_id"] for ld in tier_ledger_defs}
    for lid in sorted(required_ledger_ids - existing_ledger_ids):
        if lid == "BANK":
            tier_ledger_defs.append({
                "ledger_id": "BANK",
                "name": "Bank Subledger",
                "required_roles": ["AVAILABLE", "DEPOSIT", "WITHDRAWAL", "RECONCILED", "PENDING"],
            })
        elif lid == "AR":
            tier_ledger_defs.append({
                "ledger_id": "AR",
                "name": "Accounts Receivable Subledger",
                "required_roles": ["CUSTOMER_BALANCE", "INVOICE", "PAYMENT", "CREDIT",
                                   "WRITE_OFF", "REFUND", "FINANCE_CHARGE"],
            })
        elif lid == "CONTRACT":
            tier_ledger_defs.append({
                "ledger_id": "CONTRACT",
                "name": "Contract WIP Subledger",
                "required_roles": ["CONTRACT_COST_INCURRED", "COST_CLEARING",
                                   "BILLED", "COST_BILLED"],
            })

    # ---- 6. Filter engine configs ----
    required_engines = trace_required_engines(all_policies)
    v1_engine_defs = v1_engines.get("engines", [])
    tier_engine_defs = [e for e in v1_engine_defs if e["engine_name"] in required_engines]

    # Also include aging engine if we have AP or AR (used for reports, not required_engines)
    if {"ap", "ar"} & modules:
        if not any(e["engine_name"] == "aging" for e in tier_engine_defs):
            for e in v1_engine_defs:
                if e["engine_name"] == "aging":
                    tier_engine_defs.append(e)
                    break

    # ---- 7. Build capabilities dict ----
    capabilities = {}
    for mod in ALL_MODULES:
        capabilities[mod] = mod in modules
    for cap in ALWAYS_FALSE_CAPABILITIES:
        capabilities[cap] = False

    # ---- 8. Write root.yaml ----
    root_data = {
        "config_id": config_id,
        "version": 2,
        "status": "published",
        "predecessor": "US-GAAP-2026-v1",
        "scope": {
            "legal_entity": legal_entity,
            "jurisdiction": v1_root["scope"]["jurisdiction"],
            "regulatory_regime": v1_root["scope"]["regulatory_regime"],
            "currency": v1_root["scope"]["currency"],
            "effective_from": v1_root["scope"]["effective_from"],
            "effective_to": v1_root["scope"]["effective_to"],
        },
        "capabilities": capabilities,
        "precedence_rules": v1_root.get("precedence_rules", []),
    }
    write_yaml(output_dir / "root.yaml", root_data)

    # ---- 9. Write chart_of_accounts.yaml ----
    write_yaml(output_dir / "chart_of_accounts.yaml", {"role_bindings": tier_bindings})

    # ---- 10. Write ledgers.yaml ----
    write_yaml(output_dir / "ledgers.yaml", {"ledgers": tier_ledger_defs})

    # ---- 11. Copy controls.yaml verbatim ----
    shutil.copy2(V1_DIR / "controls.yaml", output_dir / "controls.yaml")

    # ---- 12. Write engine_params.yaml ----
    if tier_engine_defs:
        write_yaml(output_dir / "engine_params.yaml", {"engines": tier_engine_defs})
    else:
        write_yaml(output_dir / "engine_params.yaml", {"engines": []})

    # ---- 13. Write subledger_contracts.yaml ----
    header = (
        "# Subledger control contracts — declarative reconciliation rules.\n"
        "#\n"
        "# SL-G3: All contracts default to per-currency reconciliation.\n\n"
    )
    write_yaml(output_dir / "subledger_contracts.yaml", {"contracts": tier_contracts}, header=header)

    return output_dir


def validate_tier(tier_dir: Path) -> tuple[bool, str]:
    """Validate a generated config set by assembling and compiling it."""
    sys.path.insert(0, str(PROJECT_ROOT))

    from finance_config.assembler import assemble_from_directory
    from finance_config.compiler import compile_policy_pack

    try:
        config_set = assemble_from_directory(tier_dir)
        pack = compile_policy_pack(config_set)
        return True, (
            f"  config_id: {pack.config_id}\n"
            f"  policies:  {len(pack.policies)}\n"
            f"  bindings:  {len(pack.role_bindings)}\n"
            f"  engines:   {len(pack.engine_contracts)}\n"
            f"  subledger: {len(pack.subledger_contracts)}"
        )
    except Exception as e:
        return False, f"  ERROR: {e}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    do_validate = "--validate" in sys.argv

    print("=" * 60)
    print("Generating tiered configuration sets from US-GAAP-2026-v1")
    print("=" * 60)

    if not V1_DIR.exists():
        print(f"ERROR: Source config not found at {V1_DIR}")
        sys.exit(1)

    results = {}
    for tier_name, tier_def in TIERS.items():
        print(f"\n--- {tier_name} ({len(tier_def['modules'])} modules) ---")
        output_dir = generate_tier(tier_name, tier_def)
        print(f"  Output: {output_dir}")
        print(f"  Modules: {', '.join(tier_def['modules'])}")

        # Count files
        policy_count = len(list((output_dir / "policies").glob("*.yaml")))
        print(f"  Policy files: {policy_count}")

        if do_validate:
            ok, detail = validate_tier(output_dir)
            status = "PASS" if ok else "FAIL"
            print(f"  Validation: {status}")
            print(detail)
            results[tier_name] = ok
        else:
            results[tier_name] = True

    print("\n" + "=" * 60)
    print("Summary:")
    for name, ok in results.items():
        print(f"  {name}: {'OK' if ok else 'FAILED'}")

    if not all(results.values()):
        sys.exit(1)

    print("\nAll tier config sets generated successfully.")
    if not do_validate:
        print("Run with --validate to also compile and validate each set.")


if __name__ == "__main__":
    main()
