"""
Configuration Validator (``finance_config.validator``).

Responsibility
--------------
Validates an ``AccountingConfigurationSet`` at build time, ensuring
structural integrity before the configuration is compiled and deployed.

Architecture position
---------------------
**Config layer** -- build-time validation.  Called by
``finance_config.assembler`` after loading and before compilation.  Has
no dependency on kernel, modules, or engines.

Invariants enforced
-------------------
* Policy name uniqueness -- duplicate ``(name, version)`` pairs are errors.
* Guard expression safety -- all guard/control expressions must pass the
  restricted AST validator (``guard_ast.py``).
* Role coverage -- GL roles referenced by policies should have bindings.
* Capability consistency -- capability tags must reference declared
  capabilities, and disabling a capability must not leave event types
  without an admissible policy.
* Ledger effect presence -- every policy must have at least one effect.

Failure modes
-------------
* Validation errors (``ConfigValidationResult.errors``)  -> configuration
  MUST NOT be compiled.
* Validation warnings (``ConfigValidationResult.warnings``)  ->
  configuration may be compiled but should be reviewed.

Audit relevance
---------------
The validation result is a machine-readable record of configuration
health.  Errors and warnings provide traceable evidence that guard
expressions, role bindings, and capability settings have been reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from finance_config.guard_ast import validate_guard_expression
from finance_config.schema import AccountingConfigurationSet


@dataclass
class ConfigValidationResult:
    """
    Result of configuration validation.

    Contract
    --------
    * ``is_valid`` returns ``True`` only when ``errors`` is empty.
    * Warnings do not block compilation but should be reviewed.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_configuration(config: AccountingConfigurationSet) -> ConfigValidationResult:
    """
    Validate a configuration set.

    Preconditions:
        - ``config`` must be a fully assembled ``AccountingConfigurationSet``.
    Postconditions:
        - Returns a ``ConfigValidationResult`` with errors and warnings.
        - A configuration with errors MUST NOT be compiled.
    """
    result = ConfigValidationResult()

    _validate_policy_uniqueness(config, result)
    _validate_guard_expressions(config, result)
    _validate_control_expressions(config, result)
    _validate_role_coverage(config, result)
    _validate_capability_tags(config, result)
    _validate_capability_coverage(config, result)
    _validate_ledger_effects(config, result)

    return result


def _validate_policy_uniqueness(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Check that policy names are unique."""
    seen: dict[str, int] = {}
    for policy in config.policies:
        key = f"{policy.name}:v{policy.version}"
        if key in seen:
            result.add_error(
                f"Duplicate policy: {key} appears more than once"
            )
        seen[key] = seen.get(key, 0) + 1


def _validate_guard_expressions(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Validate all guard expressions against restricted AST."""
    for policy in config.policies:
        for guard in policy.guards:
            errors = validate_guard_expression(guard.expression)
            for err in errors:
                result.add_error(
                    f"Policy '{policy.name}' guard: {err.message} "
                    f"(expression: {guard.expression})"
                )


def _validate_control_expressions(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Validate all control rule expressions."""
    for control in config.controls:
        errors = validate_guard_expression(control.expression)
        for err in errors:
            result.add_error(
                f"Control '{control.name}': {err.message} "
                f"(expression: {control.expression})"
            )


def _validate_role_coverage(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Check that GL roles used by policies have bindings."""
    bound_roles = {b.role for b in config.role_bindings}
    for policy in config.policies:
        for effect in policy.ledger_effects:
            if effect.ledger == "GL":
                for role in (effect.debit_role, effect.credit_role):
                    if role not in bound_roles:
                        result.add_warning(
                            f"Policy '{policy.name}' uses GL role '{role}' "
                            f"with no RoleBinding"
                        )


def _validate_capability_tags(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Check that capability tags reference declared capabilities."""
    declared = {k.lower() for k in config.capabilities}
    for policy in config.policies:
        for tag in policy.capability_tags:
            if tag.lower() not in declared:
                result.add_warning(
                    f"Policy '{policy.name}' uses capability tag '{tag}' "
                    f"not declared in capabilities"
                )


def _validate_capability_coverage(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Warn if disabling a capability leaves event types uncovered."""
    # Build event_type → policies mapping
    by_event_type: dict[str, list[str]] = {}
    for policy in config.policies:
        et = policy.trigger.event_type
        by_event_type.setdefault(et, [])

    # For each capability, check what happens if disabled
    for cap_name, enabled in config.capabilities.items():
        if not enabled:
            continue  # Already disabled

        # Find policies that require this capability
        affected_event_types: set[str] = set()
        for policy in config.policies:
            if cap_name.lower() in {t.lower() for t in policy.capability_tags}:
                affected_event_types.add(policy.trigger.event_type)

        # Check if those event types have fallback policies (without the tag)
        for et in affected_event_types:
            fallback_exists = any(
                p.trigger.event_type == et
                and cap_name.lower() not in {t.lower() for t in p.capability_tags}
                for p in config.policies
            )
            if not fallback_exists:
                result.add_warning(
                    f"Disabling capability '{cap_name}' would leave "
                    f"event_type '{et}' with no admissible policy"
                )


def _validate_ledger_effects(
    config: AccountingConfigurationSet, result: ConfigValidationResult
) -> None:
    """Check that policies have at least one ledger effect."""
    for policy in config.policies:
        if not policy.ledger_effects:
            result.add_error(
                f"Policy '{policy.name}' has no ledger effects"
            )
