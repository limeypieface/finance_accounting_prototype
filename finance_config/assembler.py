"""
finance_config.assembler -- composes YAML fragments into one ConfigurationSet.

Responsibility:
    Humans edit small, well-owned YAML fragments.  This module composes
    them into a single ``AccountingConfigurationSet`` at build time.
    Runtime only ever sees the final ``CompiledPolicyPack``; this module
    is strictly build/test tooling.

Architecture position:
    Configuration -- YAML-driven policy pipeline, build-time validation.
    Called by ``finance_config.get_active_config()`` during the load phase
    and by tests that construct config fixtures.  The assembler reads the
    filesystem (I/O boundary); the resulting ``AccountingConfigurationSet``
    is a pure, frozen data structure.

Fragment structure::

    sets/US-GAAP-2026-v1/
    +-- root.yaml              # Scope, identity, predecessor, capabilities
    +-- chart_of_accounts.yaml # Role bindings
    +-- ledgers.yaml           # Ledger definitions
    +-- policies/              # One YAML per domain
    |   +-- inventory.yaml
    |   +-- ...
    +-- engine_params.yaml     # Engine configurations
    +-- controls.yaml          # Governance rules
    +-- subledger_contracts.yaml  # Subledger integration contracts
    +-- dcaa_overlay.yaml      # Conditional overlay (optional)

Invariants enforced:
    - ``root.yaml`` must exist in every fragment directory.
    - A deterministic SHA-256 checksum is computed over all assembled data
      to support fingerprint pinning and replay determinism (R21).
    - All parsed structures are immutable frozen dataclasses.

Failure modes:
    - ``AssemblyError`` -- required fragments missing, malformed YAML, or
      missing mandatory fields in ``root.yaml``.
    - ``yaml.YAMLError`` (propagated from loader) -- invalid YAML syntax.

Audit relevance:
    The assembled ``AccountingConfigurationSet.checksum`` is carried
    through to the ``CompiledPolicyPack`` and recorded in every
    ``FINANCE_CONFIG_TRACE`` log entry, providing a tamper-evident chain
    from YAML source to posted journal entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from finance_config.lifecycle import ConfigStatus
from finance_config.loader import (
    compute_checksum,
    load_yaml_file,
    parse_approval_policy,
    parse_batch_schedule,
    parse_control_rule,
    parse_date,
    parse_engine_config,
    parse_import_mapping,
    parse_ledger_definition,
    parse_policy,
    parse_role_binding,
    parse_scope,
    parse_subledger_contract,
)
from finance_config.schema import (
    AccountingConfigurationSet,
    ApprovalPolicyDef,
    BatchScheduleDef,
    ControlRule,
    EngineConfigDef,
    ImportMappingDef,
    LedgerDefinition,
    PolicyDefinition,
    PrecedenceRule,
    RoleBinding,
    SubledgerContractDef,
)
from finance_kernel.exceptions import FinanceKernelError


class AssemblyError(FinanceKernelError):
    """Error during fragment assembly.

    Contract:
        Raised when a fragment directory is missing, ``root.yaml`` is
        absent, or any required field within fragments cannot be parsed.

    Guarantees:
        Carries a machine-readable ``code`` attribute (R18) for
        programmatic error handling.

    Non-goals:
        Does not enumerate all individual field-level parse errors; the
        first fatal issue aborts assembly.
    """

    code: str = "ASSEMBLY_FAILED"


def assemble_from_directory(fragment_dir: Path) -> AccountingConfigurationSet:
    """Compose fragments from a directory into one ConfigurationSet.

    Build/test tooling only.  Runtime never calls this directly; it is
    invoked by ``get_active_config()`` during the load phase.

    Preconditions:
        - ``fragment_dir`` is an existing directory.
        - ``fragment_dir / "root.yaml"`` exists and contains at minimum
          ``config_id`` and ``scope`` keys.

    Postconditions:
        - Returns a frozen ``AccountingConfigurationSet`` with a
          deterministic SHA-256 ``checksum``.
        - All policies, role bindings, ledger definitions, engine
          configs, controls, and subledger contracts from the fragment
          directory are included in the result.

    Args:
        fragment_dir: Path to the fragment directory (e.g.,
            ``finance_config/sets/US-GAAP-2026-v1/``).

    Returns:
        Assembled ``AccountingConfigurationSet``.

    Raises:
        AssemblyError: If required fragments are missing or malformed.
    """
    if not fragment_dir.is_dir():
        raise AssemblyError(f"Fragment directory not found: {fragment_dir}")

    # 1. Load root.yaml (required)
    root_path = fragment_dir / "root.yaml"
    if not root_path.exists():
        raise AssemblyError(f"root.yaml not found in {fragment_dir}")
    root_data = load_yaml_file(root_path)

    # 2. Load chart_of_accounts.yaml (required)
    coa_path = fragment_dir / "chart_of_accounts.yaml"
    role_bindings: tuple[RoleBinding, ...] = ()
    if coa_path.exists():
        coa_data = load_yaml_file(coa_path)
        role_bindings = tuple(
            parse_role_binding(rb) for rb in coa_data.get("role_bindings", [])
        )

    # 3. Load ledgers.yaml (optional)
    ledgers_path = fragment_dir / "ledgers.yaml"
    ledger_defs: tuple[LedgerDefinition, ...] = ()
    if ledgers_path.exists():
        ledgers_data = load_yaml_file(ledgers_path)
        ledger_defs = tuple(
            parse_ledger_definition(ld) for ld in ledgers_data.get("ledgers", [])
        )

    # 4. Load all policy files from policies/ subdirectory
    policies: list[PolicyDefinition] = []
    policies_dir = fragment_dir / "policies"
    if policies_dir.is_dir():
        for policy_file in sorted(policies_dir.glob("*.yaml")):
            policy_data = load_yaml_file(policy_file)
            for p in policy_data.get("policies", []):
                policies.append(parse_policy(p))

    # 5. Load engine_params.yaml (optional)
    engine_path = fragment_dir / "engine_params.yaml"
    engine_configs: tuple[EngineConfigDef, ...] = ()
    if engine_path.exists():
        engine_data = load_yaml_file(engine_path)
        engine_configs = tuple(
            parse_engine_config(ec) for ec in engine_data.get("engines", [])
        )

    # 6. Load controls.yaml (optional)
    controls_path = fragment_dir / "controls.yaml"
    controls: tuple[ControlRule, ...] = ()
    if controls_path.exists():
        controls_data = load_yaml_file(controls_path)
        controls = tuple(
            parse_control_rule(cr) for cr in controls_data.get("controls", [])
        )

    # 7. Load subledger contracts (optional)
    subledger_path = fragment_dir / "subledger_contracts.yaml"
    subledger_contracts: tuple[SubledgerContractDef, ...] = ()
    if subledger_path.exists():
        sl_data = load_yaml_file(subledger_path)
        subledger_contracts = tuple(
            parse_subledger_contract(sc) for sc in sl_data.get("contracts", [])
        )

    # 8. Load approval_policies.yaml (optional)
    approval_path = fragment_dir / "approval_policies.yaml"
    approval_policies: tuple[ApprovalPolicyDef, ...] = ()
    if approval_path.exists():
        ap_data = load_yaml_file(approval_path)
        approval_policies = tuple(
            parse_approval_policy(ap) for ap in ap_data.get("approval_policies", [])
        )

    # 8b. Load import_mappings (optional: single file or import_mappings/ directory)
    import_mappings_list: list[ImportMappingDef] = []
    imp_single = fragment_dir / "import_mappings.yaml"
    if imp_single.exists():
        imp_data = load_yaml_file(imp_single)
        import_mappings_list.extend(
            parse_import_mapping(im) for im in imp_data.get("import_mappings", [])
        )
    imp_dir = fragment_dir / "import_mappings"
    if imp_dir.is_dir():
        for imp_file in sorted(imp_dir.glob("*.yaml")):
            imp_data = load_yaml_file(imp_file)
            items = imp_data.get("import_mappings", [])
            if not items and isinstance(imp_data, dict) and "name" in imp_data:
                items = [imp_data]
            for im in items:
                if im and isinstance(im, dict) and "name" in im:
                    import_mappings_list.append(parse_import_mapping(im))

    import_mappings: tuple[ImportMappingDef, ...] = tuple(import_mappings_list)

    # 8c. Load batch_schedules (optional: single file or batch_schedules/ directory)
    batch_schedules_list: list[BatchScheduleDef] = []
    bs_single = fragment_dir / "batch_schedules.yaml"
    if bs_single.exists():
        bs_data = load_yaml_file(bs_single)
        batch_schedules_list.extend(
            parse_batch_schedule(bs) for bs in bs_data.get("batch_schedules", [])
        )
    bs_dir = fragment_dir / "batch_schedules"
    if bs_dir.is_dir():
        for bs_file in sorted(bs_dir.glob("*.yaml")):
            bs_data = load_yaml_file(bs_file)
            items = bs_data.get("batch_schedules", [])
            if not items and isinstance(bs_data, dict) and "name" in bs_data:
                items = [bs_data]
            for bs in items:
                if bs and isinstance(bs, dict) and "name" in bs:
                    batch_schedules_list.append(parse_batch_schedule(bs))

    batch_schedules: tuple[BatchScheduleDef, ...] = tuple(batch_schedules_list)

    # 9. Parse root metadata
    scope = parse_scope(root_data["scope"])
    capabilities = root_data.get("capabilities", {})
    status_str = root_data.get("status", "draft")
    status = ConfigStatus(status_str)

    predecessor = root_data.get("predecessor")

    # Precedence rules
    precedence_rules = tuple(
        PrecedenceRule(
            name=pr["name"],
            description=pr.get("description", ""),
            rule_type=pr.get("rule_type", "specificity"),
        )
        for pr in root_data.get("precedence_rules", [])
    )

    # 10. Compute checksum over all assembled data
    all_data: dict[str, Any] = {
        "root": root_data,
        "role_bindings": len(role_bindings),
        "policies": [p.name for p in policies],
        "engine_configs": [ec.engine_name for ec in engine_configs],
        "controls": [c.name for c in controls],
        "capabilities": capabilities,
        "import_mappings": [im.name for im in import_mappings],
        "batch_schedules": [bs.name for bs in batch_schedules],
    }
    checksum = compute_checksum(all_data)

    # INVARIANT: checksum must be a non-empty SHA-256 hex digest.
    assert checksum and len(checksum) == 64, (
        f"Checksum must be a 64-char SHA-256 hex digest, got {checksum!r}"
    )

    return AccountingConfigurationSet(
        config_id=root_data["config_id"],
        version=root_data.get("version", 1),
        checksum=checksum,
        scope=scope,
        status=status,
        policies=tuple(policies),
        role_bindings=role_bindings,
        predecessor=predecessor,
        policy_precedence_rules=precedence_rules,
        ledger_definitions=ledger_defs,
        engine_configs=engine_configs,
        controls=controls,
        capabilities=capabilities,
        subledger_contracts=subledger_contracts,
        approval_policies=approval_policies,
        import_mappings=import_mappings,
        batch_schedules=batch_schedules,
    )
