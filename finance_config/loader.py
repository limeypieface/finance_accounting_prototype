"""
Configuration Loader (``finance_config.loader``).

Responsibility
--------------
Loads individual YAML fragment files and parses them into typed
``finance_config.schema`` dataclass instances.  This is **build/test
tooling only** -- no service or orchestrator should call this directly.
The single public entry point for runtime config is
``finance_config.get_active_config()``.

Architecture position
---------------------
**Config layer** -- infrastructure tooling.  The loader is consumed by
``finance_config.assembler`` during configuration set assembly.  It has
no dependency on kernel, modules, or engines.

Invariants enforced
-------------------
* R18 -- All parse errors raise ``ValueError`` or ``KeyError`` with
          descriptive messages; no silent defaults for required fields.
* Every parsed object is a frozen dataclass from ``schema.py``.
* ``compute_checksum`` produces a deterministic SHA-256 hash for
  configuration identity and change detection.

Failure modes
-------------
* Missing YAML file  -> ``FileNotFoundError`` propagates.
* Malformed YAML  -> ``yaml.YAMLError`` propagates.
* Missing required keys in parsed dict  -> ``KeyError`` propagates.
* Invalid date format  -> ``ValueError`` from ``date.fromisoformat``.

Audit relevance
---------------
``compute_checksum`` enables auditors to verify that the active
configuration matches a known, version-controlled baseline.  Every
parsed configuration artifact is traceable back to a specific YAML file.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from finance_config.lifecycle import ConfigStatus
from finance_config.schema import (
    AccountingConfigurationSet,
    ApprovalPolicyDef,
    ApprovalRuleDef,
    AuthorityRulesDef,
    BatchScheduleDef,
    ConfigScope,
    ControlRule,
    EngineConfigDef,
    GuardDef,
    HierarchyRulesDef,
    ImportFieldDef,
    ImportMappingDef,
    ImportValidationDef,
    LedgerDefinition,
    LedgerEffectDef,
    LineMappingDef,
    OverrideRoleDef,
    PermissionConflictsDef,
    PolicyDefinition,
    PolicyMeaningDef,
    PolicyTriggerDef,
    PrecedenceDef,
    PrecedenceRule,
    RbacConfigDef,
    RbacConfigMetadata,
    RoleBinding,
    RoleDef,
    SegregationOfDutiesDef,
    SubledgerContractDef,
)
from finance_kernel.domain.schemas.base import EventFieldType


def load_yaml_file(path: Path) -> dict[str, Any]:
    """
    Load a single YAML file and return its contents as a dict.

    Preconditions:
        - ``path`` must point to an existing, readable YAML file.
    Postconditions:
        - Returns a ``dict`` (possibly empty if the YAML is empty).
    Raises:
        FileNotFoundError: if the file does not exist.
        yaml.YAMLError: if the file contains invalid YAML.
    """
    with open(path) as f:
        return yaml.safe_load(f) or {}


def parse_date(value: Any) -> date:
    """
    Parse a date from YAML (string or date object).

    Preconditions:
        - ``value`` must be a ``date`` instance or an ISO-format date string.
    Postconditions:
        - Returns a ``date`` object.
    Raises:
        ValueError: if ``value`` is not a valid date representation.
    """
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"Cannot parse date from {value!r}")


def parse_scope(data: dict[str, Any]) -> ConfigScope:
    """Parse a ConfigScope from a dict."""
    return ConfigScope(
        legal_entity=data["legal_entity"],
        jurisdiction=data["jurisdiction"],
        regulatory_regime=data["regulatory_regime"],
        currency=data["currency"],
        effective_from=parse_date(data["effective_from"]),
        effective_to=parse_date(data["effective_to"]) if data.get("effective_to") else None,
    )


def parse_policy(data: dict[str, Any]) -> PolicyDefinition:
    """
    Parse a ``PolicyDefinition`` from a dict.

    Preconditions:
        - ``data`` must contain at minimum ``name``, ``trigger.event_type``,
          ``meaning.economic_type``, and at least one ``ledger_effects`` entry.
    Postconditions:
        - Returns a fully populated ``PolicyDefinition`` frozen dataclass.
    Raises:
        KeyError: if required keys are missing.
        ValueError: if date fields cannot be parsed.
    """
    trigger_data = data.get("trigger", {})
    where_raw = trigger_data.get("where", [])
    where_tuples = tuple(
        (item["field"], item["value"]) for item in where_raw
    ) if where_raw else ()

    trigger = PolicyTriggerDef(
        event_type=trigger_data["event_type"],
        schema_version=trigger_data.get("schema_version", 1),
        where=where_tuples,
    )

    meaning_data = data.get("meaning", {})
    meaning = PolicyMeaningDef(
        economic_type=meaning_data["economic_type"],
        quantity_field=meaning_data.get("quantity_field"),
        dimensions=tuple(meaning_data.get("dimensions", ())),
    )

    ledger_effects = tuple(
        LedgerEffectDef(
            ledger=e["ledger"],
            debit_role=e["debit_role"],
            credit_role=e["credit_role"],
        )
        for e in data.get("ledger_effects", [])
    )

    guards = tuple(
        GuardDef(
            guard_type=g["guard_type"],
            expression=g["expression"],
            reason_code=g["reason_code"],
            message=g.get("message", ""),
        )
        for g in data.get("guards", [])
    )

    line_mappings = tuple(
        LineMappingDef(
            role=m["role"],
            side=m["side"],
            ledger=m.get("ledger", "GL"),
            from_context=m.get("from_context"),
            foreach=m.get("foreach"),
        )
        for m in data.get("line_mappings", [])
    )

    precedence_data = data.get("precedence")
    precedence = None
    if precedence_data:
        precedence = PrecedenceDef(
            mode=precedence_data.get("mode", "normal"),
            priority=precedence_data.get("priority", 0),
            overrides=tuple(precedence_data.get("overrides", ())),
        )

    return PolicyDefinition(
        name=data["name"],
        version=data.get("version", 1),
        trigger=trigger,
        meaning=meaning,
        ledger_effects=ledger_effects,
        guards=guards,
        effective_from=parse_date(data.get("effective_from", "2024-01-01")),
        effective_to=parse_date(data["effective_to"]) if data.get("effective_to") else None,
        scope=data.get("scope", "*"),
        precedence=precedence,
        valuation_model=data.get("valuation_model"),
        line_mappings=line_mappings,
        intent_source=data.get("intent_source"),
        required_engines=tuple(data.get("required_engines", ())),
        engine_parameters_ref=data.get("engine_parameters_ref"),
        variance_disposition=data.get("variance_disposition"),
        capability_tags=tuple(data.get("capability_tags", ())),
        description=data.get("description", ""),
        module=data.get("module", ""),
    )


def parse_role_binding(data: dict[str, Any]) -> RoleBinding:
    """
    Parse a ``RoleBinding`` from a dict.

    Preconditions:
        - ``data`` must contain ``role`` and ``account_code`` keys.
    Postconditions:
        - Returns a ``RoleBinding`` frozen dataclass.
    Raises:
        KeyError: if required keys are missing.
    """
    return RoleBinding(
        role=data["role"],
        ledger=data.get("ledger", "GL"),
        account_code=data["account_code"],
        effective_from=parse_date(data.get("effective_from", "2024-01-01")),
        effective_to=parse_date(data["effective_to"]) if data.get("effective_to") else None,
    )


def parse_ledger_definition(data: dict[str, Any]) -> LedgerDefinition:
    """Parse a LedgerDefinition from a dict."""
    return LedgerDefinition(
        ledger_id=data["ledger_id"],
        name=data["name"],
        required_roles=tuple(data.get("required_roles", ())),
    )


def parse_engine_config(data: dict[str, Any]) -> EngineConfigDef:
    """Parse an EngineConfigDef from a dict."""
    return EngineConfigDef(
        engine_name=data["engine_name"],
        version_constraint=data.get("version_constraint", "*"),
        parameters=data.get("parameters", {}),
    )


def parse_control_rule(data: dict[str, Any]) -> ControlRule:
    """Parse a ControlRule from a dict."""
    return ControlRule(
        name=data["name"],
        applies_to=data["applies_to"],
        action=data["action"],
        expression=data["expression"],
        reason_code=data["reason_code"],
        message=data.get("message", ""),
    )


def parse_subledger_contract(data: dict[str, Any]) -> SubledgerContractDef:
    """Parse a SubledgerContractDef from a dict."""
    return SubledgerContractDef(
        subledger_id=data["subledger_id"],
        owner_module=data["owner_module"],
        control_account_role=data["control_account_role"],
        entry_types=tuple(data.get("entry_types", ())),
        is_debit_normal=data.get("is_debit_normal", True),
        timing=data.get("timing", "real_time"),
        tolerance_type=data.get("tolerance_type", "none"),
        tolerance_amount=str(data.get("tolerance_amount", "0")),
        tolerance_percentage=str(data.get("tolerance_percentage", "0")),
        enforce_on_post=data.get("enforce_on_post", True),
        enforce_on_close=data.get("enforce_on_close", True),
    )


def _parse_event_field_type(value: Any) -> EventFieldType:
    """Parse EventFieldType from YAML string (e.g. 'string', 'integer', 'currency')."""
    if isinstance(value, EventFieldType):
        return value
    s = (value or "string").lower().strip()
    try:
        return EventFieldType(s)
    except ValueError:
        raise ValueError(f"Invalid field_type {value!r}; must be one of {[e.value for e in EventFieldType]}")


def parse_import_field_def(data: dict[str, Any]) -> ImportFieldDef:
    """Parse an ImportFieldDef from a dict (YAML field_mappings entry)."""
    return ImportFieldDef(
        source=data["source"],
        target=data["target"],
        field_type=_parse_event_field_type(data.get("field_type", "string")),
        required=data.get("required", False),
        default=data.get("default"),
        format=data.get("format"),
        transform=data.get("transform"),
    )


def parse_import_validation_def(data: dict[str, Any]) -> ImportValidationDef:
    """Parse an ImportValidationDef from a dict (YAML validations entry)."""
    return ImportValidationDef(
        rule_type=data["rule_type"],
        fields=tuple(data.get("fields", [])),
        scope=data.get("scope", "batch"),
        reference_entity=data.get("reference_entity"),
        expression=data.get("expression"),
        message=data.get("message", ""),
    )


def parse_import_mapping(data: dict[str, Any]) -> ImportMappingDef:
    """Parse an ImportMappingDef from a dict (YAML import_mappings entry)."""
    return ImportMappingDef(
        name=data["name"],
        version=data.get("version", 1),
        entity_type=data.get("entity_type", ""),
        source_format=data.get("source_format", "csv"),
        source_options=dict(data.get("source_options", {})),
        field_mappings=tuple(parse_import_field_def(f) for f in data.get("field_mappings", [])),
        validations=tuple(parse_import_validation_def(v) for v in data.get("validations", [])),
        dependency_tier=data.get("dependency_tier", 0),
    )


def parse_approval_policy(data: dict[str, Any]) -> ApprovalPolicyDef:
    """Parse an ApprovalPolicyDef from a dict."""
    rules = tuple(
        ApprovalRuleDef(
            rule_name=r["rule_name"],
            priority=r["priority"],
            min_amount=str(r["min_amount"]) if r.get("min_amount") is not None else None,
            max_amount=str(r["max_amount"]) if r.get("max_amount") is not None else None,
            required_roles=tuple(r.get("required_roles", ())),
            min_approvers=r.get("min_approvers", 1),
            require_distinct_roles=r.get("require_distinct_roles", False),
            guard_expression=r.get("guard_expression"),
            auto_approve_below=str(r["auto_approve_below"]) if r.get("auto_approve_below") is not None else None,
            escalation_timeout_hours=r.get("escalation_timeout_hours"),
        )
        for r in data.get("rules", [])
    )

    return ApprovalPolicyDef(
        policy_name=data["policy_name"],
        version=data.get("version", 1),
        applies_to_workflow=data.get("applies_to_workflow", ""),
        applies_to_action=data.get("applies_to_action"),
        policy_currency=data.get("policy_currency"),
        rules=rules,
        effective_from=data.get("effective_from"),
        effective_to=data.get("effective_to"),
    )


def parse_batch_schedule(data: dict[str, Any]) -> BatchScheduleDef:
    """Parse a BatchScheduleDef from a dict (YAML batch_schedules entry)."""
    return BatchScheduleDef(
        name=data["name"],
        task_type=data["task_type"],
        frequency=data["frequency"],
        parameters=dict(data.get("parameters", {})),
        cron_expression=data.get("cron_expression"),
        max_retries=data.get("max_retries", 3),
        is_active=data.get("is_active", True),
        legal_entity=data.get("legal_entity"),
    )


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def parse_rbac_config_metadata(data: dict[str, Any]) -> RbacConfigMetadata:
    """Parse RbacConfigMetadata from rbac_config section."""
    return RbacConfigMetadata(
        version=data["version"],
        effective_from=parse_date(data["effective_from"]),
        supersedes=data.get("supersedes"),
    )


def parse_authority_rules(data: dict[str, Any]) -> AuthorityRulesDef:
    """Parse AuthorityRulesDef from authority_rules section."""
    return AuthorityRulesDef(
        authority_role_required=data.get("authority_role_required", True),
        multi_role_actions_allowed=data.get("multi_role_actions_allowed", False),
    )


def parse_role_def(name: str, data: dict[str, Any]) -> RoleDef:
    """Parse a single RoleDef from roles section (name is key)."""
    perms = data.get("permissions") or []
    return RoleDef(
        name=name,
        permissions=tuple(perms) if isinstance(perms, list) else (str(perms),),
        inherits=tuple(data.get("inherits") or ()),
    )


def parse_permission_conflicts(data: dict[str, Any]) -> PermissionConflictsDef:
    """Parse PermissionConflictsDef (hard_block / soft_warn lists)."""
    def _tuples(v: Any) -> tuple[tuple[str, ...], ...]:
        if not v:
            return ()
        return tuple(tuple(x) if isinstance(x, (list, tuple)) else (str(x),) for x in v)
    return PermissionConflictsDef(
        hard_block=_tuples(data.get("hard_block")),
        soft_warn=_tuples(data.get("soft_warn")),
    )


def parse_segregation_of_duties(data: dict[str, Any]) -> SegregationOfDutiesDef:
    """Parse SegregationOfDutiesDef from segregation_of_duties section."""
    def _role_tuples(v: Any) -> tuple[tuple[str, ...], ...]:
        if not v:
            return ()
        return tuple(tuple(x) if isinstance(x, (list, tuple)) else (str(x),) for x in v)
    role_conflicts = _role_tuples(data.get("role_conflicts"))
    perm_data = data.get("permission_conflicts") or {}
    permission_conflicts = parse_permission_conflicts(perm_data)
    lifecycle_raw = data.get("lifecycle_conflicts") or {}
    lifecycle_conflicts: list[tuple[str, tuple[str, ...]]] = []
    for obj_type, pairs in lifecycle_raw.items():
        for pair in pairs or []:
            actions = tuple(pair) if isinstance(pair, (list, tuple)) else (str(pair),)
            lifecycle_conflicts.append((obj_type, actions))
    return SegregationOfDutiesDef(
        role_conflicts=role_conflicts,
        permission_conflicts=permission_conflicts,
        lifecycle_conflicts=tuple(lifecycle_conflicts),
    )


def parse_override_role(name: str, data: dict[str, Any]) -> OverrideRoleDef:
    """Parse a single OverrideRoleDef from override_roles section."""
    perms = data.get("permissions") or []
    return OverrideRoleDef(
        name=name,
        permissions=tuple(perms) if isinstance(perms, list) else (str(perms),),
        expiry=data.get("expiry", "24h"),
        requires_dual_approval=data.get("requires_dual_approval", True),
    )


def parse_hierarchy_rules(data: dict[str, Any]) -> HierarchyRulesDef:
    """Parse HierarchyRulesDef from hierarchy_rules section."""
    return HierarchyRulesDef(
        inheritance_depth_limit=data.get("inheritance_depth_limit", 2),
    )


def parse_rbac_config(data: dict[str, Any]) -> RbacConfigDef:
    """Parse full RbacConfigDef from rbac.yaml root."""
    rbac_config_data = data.get("rbac_config") or {}
    effective_from = rbac_config_data.get("effective_from")
    rbac_config = RbacConfigMetadata(
        version=rbac_config_data.get("version", "v1"),
        effective_from=parse_date(effective_from) if effective_from else date(2026, 1, 1),
        supersedes=rbac_config_data.get("supersedes"),
    )
    authority_rules_data = data.get("authority_rules") or {}
    authority_rules = parse_authority_rules(authority_rules_data)
    roles_data = data.get("roles") or {}
    roles = tuple(
        parse_role_def(name, role_data)
        for name, role_data in roles_data.items()
        if isinstance(role_data, dict)
    )
    sod_data = data.get("segregation_of_duties") or {}
    segregation_of_duties = parse_segregation_of_duties(sod_data)
    override_data = data.get("override_roles") or {}
    override_roles = tuple(
        parse_override_role(name, ov_data)
        for name, ov_data in override_data.items()
        if isinstance(ov_data, dict)
    )
    hierarchy_data = data.get("hierarchy_rules") or {}
    hierarchy_rules = parse_hierarchy_rules(hierarchy_data)
    return RbacConfigDef(
        rbac_config=rbac_config,
        authority_rules=authority_rules,
        roles=roles,
        segregation_of_duties=segregation_of_duties,
        override_roles=override_roles,
        hierarchy_rules=hierarchy_rules,
    )


def compute_checksum(data: dict[str, Any]) -> str:
    """
    Compute SHA-256 checksum of canonical JSON serialization.

    Preconditions:
        - ``data`` must be JSON-serializable (via ``json.dumps`` with
          ``default=str``).
    Postconditions:
        - Returns a hex-encoded SHA-256 hash string.
        - Identical ``data`` always produces identical checksums
          (deterministic).
    """
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
