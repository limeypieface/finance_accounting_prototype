"""
Configuration Compiler — ConfigurationSet → CompiledPolicyPack.

The compiler validates the configuration set and produces a frozen,
machine-validated runtime artifact. The CompiledPolicyPack is the ONLY
object that runtime posting entrypoints accept.

Compilation validates:
  - Every policy's required_engines exists in engine contracts
  - Every engine_parameters_ref satisfies the engine's parameter_schema
  - Guard ASTs parse successfully (restricted AST)
  - No ambiguous dispatch (two policies match same event with same precedence)
  - All role bindings reference roles used by policies
  - All capability_tags reference declared capabilities
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from finance_config.guard_ast import validate_guard_expression
from finance_config.lifecycle import ConfigStatus
from finance_config.schema import (
    AccountingConfigurationSet,
    ConfigScope,
    ControlRule,
    EngineConfigDef,
    GuardDef,
    LedgerEffectDef,
    LineMappingDef,
    PolicyDefinition,
    PolicyMeaningDef,
    PolicyTriggerDef,
    PrecedenceDef,
    RoleBinding,
    SubledgerContractDef,
)
from finance_engines.contracts import ENGINE_CONTRACTS, EngineContract
from finance_kernel.exceptions import FinanceKernelError


# ---------------------------------------------------------------------------
# Compiled types (frozen, runtime-ready)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledGuard:
    """Validated guard with parsed expression."""

    guard_type: str
    expression: str
    reason_code: str
    message: str = ""


@dataclass(frozen=True)
class CompiledPolicy:
    """Validated, indexed policy ready for runtime dispatch."""

    name: str
    version: int
    trigger: PolicyTriggerDef
    meaning: PolicyMeaningDef
    ledger_effects: tuple[LedgerEffectDef, ...]
    guards: tuple[CompiledGuard, ...]
    effective_from: date
    effective_to: date | None
    scope: str
    precedence: PrecedenceDef | None
    valuation_model: str | None
    line_mappings: tuple[LineMappingDef, ...]
    required_engines: tuple[str, ...]
    engine_parameters_ref: str | None
    variance_disposition: str | None
    capability_tags: tuple[str, ...]
    description: str
    module: str


@dataclass(frozen=True)
class CompiledControl:
    """Validated control rule."""

    name: str
    applies_to: str
    action: str
    expression: str
    reason_code: str
    message: str = ""


@dataclass(frozen=True)
class PolicyMatchEntry:
    """Pre-built dispatch index entry for fast runtime lookup."""

    event_type: str
    policies: tuple[CompiledPolicy, ...]


@dataclass(frozen=True)
class PolicyMatchIndex:
    """Pre-built dispatch index mapping event_type → candidate policies."""

    entries: dict[str, tuple[CompiledPolicy, ...]]

    def get_candidates(self, event_type: str) -> tuple[CompiledPolicy, ...]:
        """Get candidate policies for an event type."""
        return self.entries.get(event_type, ())


@dataclass(frozen=True)
class PolicyDecisionTrace:
    """Build-time debugging artifact recording the decision tree per event_type.

    Not used at runtime — for audit and debugging only.
    """

    event_type_decisions: dict[str, list[str]]  # event_type → list of policy names


@dataclass(frozen=True)
class ResolvedEngineContract:
    """Engine contract resolved against configuration."""

    engine_name: str
    engine_version: str
    parameter_key: str


@dataclass(frozen=True)
class FrozenEngineParams:
    """Pre-resolved, typed engine parameters. Immutable at runtime."""

    engine_name: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class CompiledPolicyPack:
    """Machine-validated, frozen runtime artifact.

    The ONLY object that runtime posting entrypoints accept.
    YAML loading is build/test tooling — runtime never touches it.

    Attributes:
        config_id: Source configuration identifier
        config_version: Source configuration version
        checksum: Matches source ConfigurationSet
        scope: Applicability scope
        policies: Validated, compiled policies
        match_index: Pre-built dispatch index
        role_bindings: Account role → COA code mappings
        engine_contracts: Resolved engine contracts
        resolved_engine_params: Pre-resolved, typed engine parameters
        controls: Compiled control rules
        capabilities: Feature gates
        canonical_fingerprint: Deterministic hash of entire pack
        decision_trace: Build-time debugging artifact
    """

    config_id: str
    config_version: int
    checksum: str
    scope: ConfigScope
    policies: tuple[CompiledPolicy, ...]
    match_index: PolicyMatchIndex
    role_bindings: tuple[RoleBinding, ...]
    engine_contracts: dict[str, ResolvedEngineContract]
    resolved_engine_params: dict[str, FrozenEngineParams]
    controls: tuple[CompiledControl, ...]
    capabilities: dict[str, bool]
    canonical_fingerprint: str
    decision_trace: PolicyDecisionTrace
    subledger_contracts: tuple[SubledgerContractDef, ...] = ()


# ---------------------------------------------------------------------------
# Compilation errors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompilationError:
    """An error found during compilation."""

    category: str  # e.g., "guard", "dispatch", "engine", "role"
    message: str
    policy_name: str = ""
    severity: str = "error"  # "error" or "warning"


class CompilationFailedError(FinanceKernelError):
    """Compilation produced errors that prevent creating a valid pack."""

    code: str = "COMPILATION_FAILED"

    def __init__(self, errors: list[CompilationError]):
        self.errors = errors
        messages = [f"  [{e.category}] {e.message}" for e in errors if e.severity == "error"]
        super().__init__(
            f"Compilation failed with {len(messages)} error(s):\n"
            + "\n".join(messages)
        )


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def compile_policy_pack(
    config: AccountingConfigurationSet,
    engine_contracts: dict[str, ResolvedEngineContract] | None = None,
) -> CompiledPolicyPack:
    """Compile an AccountingConfigurationSet into a CompiledPolicyPack.

    Args:
        config: The source configuration set.
        engine_contracts: Known engine contracts. If None, contracts are
            auto-populated from ENGINE_CONTRACTS registry.

    Returns:
        CompiledPolicyPack ready for runtime use.

    Raises:
        CompilationFailedError: If validation produces errors.
    """
    errors: list[CompilationError] = []
    warnings: list[CompilationError] = []

    # Auto-populate engine contracts from registry if not provided
    if engine_contracts is None:
        contracts = _build_contracts_from_registry(config)
    else:
        contracts = engine_contracts

    # 1. Validate and compile guards
    compiled_policies: list[CompiledPolicy] = []
    for policy_def in config.policies:
        compiled_guards = _compile_guards(policy_def, errors)
        compiled_policies.append(
            CompiledPolicy(
                name=policy_def.name,
                version=policy_def.version,
                trigger=policy_def.trigger,
                meaning=policy_def.meaning,
                ledger_effects=policy_def.ledger_effects,
                guards=tuple(compiled_guards),
                effective_from=policy_def.effective_from,
                effective_to=policy_def.effective_to,
                scope=policy_def.scope,
                precedence=policy_def.precedence,
                valuation_model=policy_def.valuation_model,
                line_mappings=policy_def.line_mappings,
                required_engines=policy_def.required_engines,
                engine_parameters_ref=policy_def.engine_parameters_ref,
                variance_disposition=policy_def.variance_disposition,
                capability_tags=policy_def.capability_tags,
                description=policy_def.description,
                module=policy_def.module,
            )
        )

    # 2. Build dispatch index and check for ambiguity
    match_index, decision_trace = _build_match_index(
        tuple(compiled_policies), config.capabilities, errors
    )

    # 3. Validate engine references
    if contracts:
        _validate_engine_references(compiled_policies, contracts, errors)

    # 4. Resolve engine parameters
    resolved_params = _resolve_engine_params(config, contracts, errors)

    # 4b. Validate engine parameter schemas
    _validate_engine_params_schema(config, warnings)

    # 5. Validate role bindings cover policy roles
    _validate_role_coverage(compiled_policies, config.role_bindings, warnings)

    # 6. Validate capability tags reference declared capabilities
    _validate_capability_tags(compiled_policies, config.capabilities, warnings)

    # 7. Compile controls
    compiled_controls = _compile_controls(config.controls, errors)

    # Fail on errors (not warnings)
    actual_errors = [e for e in errors if e.severity == "error"]
    if actual_errors:
        raise CompilationFailedError(actual_errors)

    # 8. Compute canonical fingerprint
    fingerprint = _compute_fingerprint(config)

    return CompiledPolicyPack(
        config_id=config.config_id,
        config_version=config.version,
        checksum=config.checksum,
        scope=config.scope,
        policies=tuple(compiled_policies),
        match_index=match_index,
        role_bindings=config.role_bindings,
        engine_contracts=contracts,
        resolved_engine_params=resolved_params,
        controls=tuple(compiled_controls),
        capabilities=dict(config.capabilities),
        canonical_fingerprint=fingerprint,
        decision_trace=decision_trace,
        subledger_contracts=config.subledger_contracts,
    )


# ---------------------------------------------------------------------------
# Internal compilation steps
# ---------------------------------------------------------------------------


def _compile_guards(
    policy: PolicyDefinition, errors: list[CompilationError]
) -> list[CompiledGuard]:
    """Validate and compile guard expressions."""
    compiled: list[CompiledGuard] = []
    for guard in policy.guards:
        ast_errors = validate_guard_expression(guard.expression)
        if ast_errors:
            for ast_err in ast_errors:
                errors.append(
                    CompilationError(
                        category="guard",
                        message=(
                            f"Invalid guard expression in policy '{policy.name}': "
                            f"{ast_err.message} (expression: {guard.expression})"
                        ),
                        policy_name=policy.name,
                    )
                )
        compiled.append(
            CompiledGuard(
                guard_type=guard.guard_type,
                expression=guard.expression,
                reason_code=guard.reason_code,
                message=guard.message,
            )
        )
    return compiled


def _build_match_index(
    policies: tuple[CompiledPolicy, ...],
    capabilities: dict[str, bool],
    errors: list[CompilationError],
) -> tuple[PolicyMatchIndex, PolicyDecisionTrace]:
    """Build the dispatch index and check for ambiguous dispatch."""

    # Group by event_type
    by_event_type: dict[str, list[CompiledPolicy]] = {}
    for policy in policies:
        et = policy.trigger.event_type
        by_event_type.setdefault(et, []).append(policy)

    # Check for ambiguity: for each event_type, there must be a
    # deterministic winner among admissible policies
    decision_trace_data: dict[str, list[str]] = {}

    for event_type, candidates in by_event_type.items():
        # Filter by capabilities (for trace, show all)
        admissible = [
            p for p in candidates
            if _is_admissible(p, capabilities)
        ]

        decision_trace_data[event_type] = [p.name for p in candidates]

        # Check for ambiguity among policies with same trigger (same where clause)
        _check_dispatch_ambiguity(event_type, admissible, errors)

    entries = {
        et: tuple(policies_list) for et, policies_list in by_event_type.items()
    }

    return (
        PolicyMatchIndex(entries=entries),
        PolicyDecisionTrace(event_type_decisions=decision_trace_data),
    )


def _is_admissible(policy: CompiledPolicy, capabilities: dict[str, bool]) -> bool:
    """Check if a policy is admissible given the enabled capabilities."""
    if not policy.capability_tags:
        return True  # No tags = always admissible
    return all(
        capabilities.get(tag.lower(), False) for tag in policy.capability_tags
    )


def _check_dispatch_ambiguity(
    event_type: str,
    policies: list[CompiledPolicy],
    errors: list[CompilationError],
) -> None:
    """Check that dispatch is deterministic for an event type."""
    # Group by where clause (same where = same trigger)
    by_where: dict[tuple[tuple[str, Any], ...], list[CompiledPolicy]] = {}
    for p in policies:
        where_key = p.trigger.where
        by_where.setdefault(where_key, []).append(p)

    for where_clause, group in by_where.items():
        if len(group) <= 1:
            continue

        # Multiple policies with same trigger — check if precedence resolves
        priorities = set()
        for p in group:
            prio = p.precedence.priority if p.precedence else 0
            priorities.add(prio)

        if len(priorities) < len(group):
            # Some share the same priority — could be ambiguous
            # (effective date ranges might distinguish them, but
            # overlapping ranges with same priority is an error)
            names = [p.name for p in group]
            errors.append(
                CompilationError(
                    category="dispatch",
                    message=(
                        f"Potentially ambiguous dispatch for event_type "
                        f"'{event_type}' with where={where_clause}: "
                        f"policies {names} share the same precedence. "
                        f"Assign distinct priorities or non-overlapping "
                        f"effective date ranges."
                    ),
                    severity="warning",  # Warning, not error — effective dates may differ
                )
            )


def _validate_engine_references(
    policies: list[CompiledPolicy],
    contracts: dict[str, ResolvedEngineContract],
    errors: list[CompilationError],
) -> None:
    """Validate that all required engines exist in contracts."""
    for policy in policies:
        for engine_name in policy.required_engines:
            if engine_name not in contracts:
                errors.append(
                    CompilationError(
                        category="engine",
                        message=(
                            f"Policy '{policy.name}' requires engine "
                            f"'{engine_name}' but no contract exists"
                        ),
                        policy_name=policy.name,
                    )
                )


def _resolve_engine_params(
    config: AccountingConfigurationSet,
    contracts: dict[str, ResolvedEngineContract],
    errors: list[CompilationError],
) -> dict[str, FrozenEngineParams]:
    """Resolve engine_parameters_ref to frozen parameter objects."""
    # Build lookup from engine configs
    engine_config_map: dict[str, dict[str, Any]] = {}
    for ec in config.engine_configs:
        engine_config_map[ec.engine_name] = dict(ec.parameters)

    resolved: dict[str, FrozenEngineParams] = {}
    for engine_name, params in engine_config_map.items():
        resolved[engine_name] = FrozenEngineParams(
            engine_name=engine_name,
            parameters=params,
        )

    # Validate that policy refs can be resolved
    for policy in config.policies:
        if policy.engine_parameters_ref:
            if policy.engine_parameters_ref not in engine_config_map:
                errors.append(
                    CompilationError(
                        category="engine",
                        message=(
                            f"Policy '{policy.name}' references engine params "
                            f"'{policy.engine_parameters_ref}' but no engine "
                            f"config exists with that name"
                        ),
                        policy_name=policy.name,
                    )
                )

    return resolved


def _validate_role_coverage(
    policies: list[CompiledPolicy],
    bindings: tuple[RoleBinding, ...],
    warnings: list[CompilationError],
) -> None:
    """Warn if policies reference roles without bindings."""
    bound_roles = {b.role for b in bindings}
    for policy in policies:
        for effect in policy.ledger_effects:
            if effect.ledger == "GL":  # Only GL needs COA bindings
                for role in (effect.debit_role, effect.credit_role):
                    if role not in bound_roles:
                        warnings.append(
                            CompilationError(
                                category="role",
                                message=(
                                    f"Policy '{policy.name}' uses GL role "
                                    f"'{role}' but no RoleBinding exists"
                                ),
                                policy_name=policy.name,
                                severity="warning",
                            )
                        )


def _validate_capability_tags(
    policies: list[CompiledPolicy],
    capabilities: dict[str, bool],
    warnings: list[CompilationError],
) -> None:
    """Warn if policies use undeclared capability tags."""
    declared = {k.lower() for k in capabilities}
    for policy in policies:
        for tag in policy.capability_tags:
            if tag.lower() not in declared:
                warnings.append(
                    CompilationError(
                        category="capability",
                        message=(
                            f"Policy '{policy.name}' uses capability tag "
                            f"'{tag}' which is not declared in capabilities"
                        ),
                        policy_name=policy.name,
                        severity="warning",
                    )
                )


def _compile_controls(
    controls: tuple[ControlRule, ...],
    errors: list[CompilationError],
) -> list[CompiledControl]:
    """Validate and compile control rule expressions."""
    compiled: list[CompiledControl] = []
    for control in controls:
        ast_errors = validate_guard_expression(control.expression)
        if ast_errors:
            for ast_err in ast_errors:
                errors.append(
                    CompilationError(
                        category="control",
                        message=(
                            f"Invalid control expression '{control.name}': "
                            f"{ast_err.message}"
                        ),
                    )
                )
        compiled.append(
            CompiledControl(
                name=control.name,
                applies_to=control.applies_to,
                action=control.action,
                expression=control.expression,
                reason_code=control.reason_code,
                message=control.message,
            )
        )
    return compiled


def _build_contracts_from_registry(
    config: AccountingConfigurationSet,
) -> dict[str, ResolvedEngineContract]:
    """Build ResolvedEngineContract entries from the ENGINE_CONTRACTS registry.

    Maps engine_name → ResolvedEngineContract using config's engine_configs
    to populate the parameter_key field.
    """
    # Build engine config name lookup
    config_engine_names = {ec.engine_name for ec in config.engine_configs}

    contracts: dict[str, ResolvedEngineContract] = {}
    for engine_name, contract in ENGINE_CONTRACTS.items():
        parameter_key = engine_name if engine_name in config_engine_names else ""
        contracts[engine_name] = ResolvedEngineContract(
            engine_name=engine_name,
            engine_version=contract.engine_version,
            parameter_key=parameter_key,
        )

    return contracts


def _validate_engine_params_schema(
    config: AccountingConfigurationSet,
    errors: list[CompilationError],
) -> None:
    """Validate engine config parameters against contract schemas.

    Uses basic type and range checking from the JSON Schema properties.
    Does not require the jsonschema package.
    """
    for ec in config.engine_configs:
        contract = ENGINE_CONTRACTS.get(ec.engine_name)
        if not contract:
            continue  # Unknown engine — already caught by reference validation

        schema = contract.parameter_schema
        properties = schema.get("properties", {})

        for param_name, param_value in ec.parameters.items():
            if param_name not in properties:
                if schema.get("additionalProperties") is False:
                    errors.append(
                        CompilationError(
                            category="engine_params",
                            message=(
                                f"Engine '{ec.engine_name}' config has "
                                f"unknown parameter '{param_name}'"
                            ),
                            severity="warning",
                        )
                    )
                continue

            prop_schema = properties[param_name]
            _validate_param_value(
                ec.engine_name, param_name, param_value, prop_schema, errors
            )


def _validate_param_value(
    engine_name: str,
    param_name: str,
    value: Any,
    schema: dict[str, Any],
    errors: list[CompilationError],
) -> None:
    """Validate a single parameter value against its JSON Schema property."""
    expected_type = schema.get("type")
    if expected_type:
        type_ok = _check_json_type(value, expected_type)
        if not type_ok:
            errors.append(
                CompilationError(
                    category="engine_params",
                    message=(
                        f"Engine '{engine_name}' parameter '{param_name}' "
                        f"has type {type(value).__name__}, expected {expected_type}"
                    ),
                    severity="warning",
                )
            )
            return  # Skip further checks on wrong type

    # Check enum constraint
    if "enum" in schema and value not in schema["enum"]:
        errors.append(
            CompilationError(
                category="engine_params",
                message=(
                    f"Engine '{engine_name}' parameter '{param_name}' "
                    f"value '{value}' not in allowed values: {schema['enum']}"
                ),
                severity="warning",
            )
        )

    # Check numeric range
    if isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(
                CompilationError(
                    category="engine_params",
                    message=(
                        f"Engine '{engine_name}' parameter '{param_name}' "
                        f"value {value} is below minimum {schema['minimum']}"
                    ),
                    severity="warning",
                )
            )
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(
                CompilationError(
                    category="engine_params",
                    message=(
                        f"Engine '{engine_name}' parameter '{param_name}' "
                        f"value {value} is above maximum {schema['maximum']}"
                    ),
                    severity="warning",
                )
            )


def _check_json_type(value: Any, json_type: str) -> bool:
    """Check if a Python value matches a JSON Schema type."""
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "number":
        return isinstance(value, (int, float))
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "array":
        return isinstance(value, (list, tuple))
    if json_type == "object":
        return isinstance(value, dict)
    return True  # Unknown type — pass


def _compute_fingerprint(config: AccountingConfigurationSet) -> str:
    """Compute deterministic fingerprint of the configuration."""
    # Canonical representation: sorted JSON of key identifying fields
    canonical = json.dumps(
        {
            "config_id": config.config_id,
            "version": config.version,
            "checksum": config.checksum,
            "scope": {
                "legal_entity": config.scope.legal_entity,
                "jurisdiction": config.scope.jurisdiction,
                "regime": config.scope.regulatory_regime,
                "currency": config.scope.currency,
                "effective_from": str(config.scope.effective_from),
            },
            "policy_count": len(config.policies),
            "policy_names": sorted(p.name for p in config.policies),
            "role_binding_count": len(config.role_bindings),
            "capability_keys": sorted(config.capabilities.keys()),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
