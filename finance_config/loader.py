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
    ConfigScope,
    ControlRule,
    EngineConfigDef,
    GuardDef,
    LedgerDefinition,
    LedgerEffectDef,
    LineMappingDef,
    PolicyDefinition,
    PolicyMeaningDef,
    PolicyTriggerDef,
    PrecedenceDef,
    PrecedenceRule,
    RoleBinding,
    SubledgerContractDef,
)


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
