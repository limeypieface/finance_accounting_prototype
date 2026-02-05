"""
PackPolicySource -- Resolves AccountingPolicy from CompiledPolicyPack.

When the orchestrator is built with a pack, this source is used so that
guards, trigger, and meaning come from config (policies/*.yaml) instead
of Python-registered profiles.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from finance_config.bridges import policy_from_compiled
from finance_config.compiler import CompiledPolicy, CompiledPolicyPack, is_policy_admissible
from finance_kernel.domain.accounting_policy import AccountingPolicy
from finance_kernel.domain.policy_selector import (
    MultiplePoliciesMatchError,
    PolicyNotFoundError,
)
from finance_kernel.logging_config import get_logger

logger = get_logger("services.pack_policy_source")

# Expression where-clause: "payload.<field> <op> <literal>" e.g. payload.quantity_change > 0
_EXPR_WHERE = re.compile(
    r"^payload\.([a-zA-Z_][a-zA-Z0-9_]*)"
    r"\s*(>|<|>=|<=|==|!=)\s*"
    r"(.+)$"
)


def _get_payload_value(payload: dict[str, Any], field_path: str) -> Any:
    """Extract value from payload by dot-path."""
    path = field_path.strip()
    if path.startswith("payload."):
        path = path[8:]
    parts = path.split(".")
    current: Any = payload
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _coerce_literal(s: str) -> Any:
    """Coerce string literal to int, float, or bool for comparison."""
    s = s.strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    try:
        return float(s)
    except ValueError:
        return s


def _evaluate_expression_where(
    payload: dict[str, Any], field_path: str, expected_result: Any
) -> bool:
    """Evaluate expression where-clause (e.g. payload.quantity_change > 0) and compare to expected_result."""
    m = _EXPR_WHERE.match(field_path.strip())
    if not m:
        return False
    field_name, op, literal_str = m.group(1), m.group(2), m.group(3)
    actual = payload.get(field_name)
    literal = _coerce_literal(literal_str)
    if actual is None and op in (">", "<", ">=", "<="):
        return False
    if op == ">":
        result = actual is not None and actual > literal
    elif op == "<":
        result = actual is not None and actual < literal
    elif op == ">=":
        result = actual is not None and actual >= literal
    elif op == "<=":
        result = actual is not None and actual <= literal
    elif op == "==":
        result = actual == literal
    elif op == "!=":
        result = actual != literal
    else:
        return False
    return result == expected_result


def _matches_where(cp: CompiledPolicy, payload: dict[str, Any]) -> bool:
    """True if all where-clause conditions match the payload."""
    if not cp.trigger.where:
        return True
    for field_path, expected_value in cp.trigger.where:
        # Expression form: "payload.x > 0" with value true/false
        if _EXPR_WHERE.match(field_path.strip()):
            if not _evaluate_expression_where(payload, field_path, expected_value):
                return False
            continue
        # Simple path form: payload.x with value v
        actual = _get_payload_value(payload, field_path)
        if expected_value is None:
            if actual is not None:
                return False
        else:
            if str(actual) != str(expected_value):
                return False
    return True


def _scope_specificity(scope: str) -> int:
    """Higher = more specific (for precedence)."""
    if scope == "*":
        return 0
    if scope.endswith(":*"):
        return len(scope) - 1
    return len(scope) + 100


def _resolve_precedence(candidates: list[CompiledPolicy], event_type: str) -> CompiledPolicy:
    """Pick one policy from multiple matches (same logic as PolicySelector)."""
    if len(candidates) == 1:
        return candidates[0]

    # PrecedenceDef: mode "normal" | "override", priority, overrides
    overrides = [p for p in candidates if p.precedence and getattr(p.precedence, "mode", "").lower() == "override"]
    normal = [p for p in candidates if not p.precedence or getattr(p.precedence, "mode", "").lower() != "override"]

    if overrides:
        overridden_names: set[str] = set()
        for p in overrides:
            overridden_names.update(getattr(p.precedence, "overrides", ()) or ())
        remaining = [p for p in overrides if p.name not in overridden_names]
        if len(remaining) == 1:
            return remaining[0]
        if remaining:
            candidates = remaining
    else:
        candidates = normal

    if len(candidates) == 1:
        return candidates[0]

    # Scope specificity (higher first)
    candidates = sorted(candidates, key=lambda p: _scope_specificity(p.scope), reverse=True)
    if len(candidates) >= 2 and _scope_specificity(candidates[0].scope) > _scope_specificity(candidates[1].scope):
        return candidates[0]

    # Priority (higher first)
    def priority(p: CompiledPolicy) -> int:
        return getattr(p.precedence, "priority", 0) if p.precedence else 0

    candidates = sorted(candidates, key=priority, reverse=True)
    if len(candidates) >= 2 and priority(candidates[0]) > priority(candidates[1]):
        return candidates[0]

    # Tiebreaker: name
    candidates = sorted(candidates, key=lambda p: p.name)
    if len(candidates) >= 2 and priority(candidates[0]) == priority(candidates[1]) and _scope_specificity(candidates[0].scope) == _scope_specificity(candidates[1].scope):
        raise MultiplePoliciesMatchError(event_type, [p.name for p in candidates])

    return candidates[0]


class PackPolicySource:
    """PolicySource that resolves profile from CompiledPolicyPack."""

    def __init__(self, pack: CompiledPolicyPack) -> None:
        self._pack = pack

    def get_profile(
        self,
        event_type: str,
        effective_date: date,
        payload: dict[str, Any] | None = None,
        scope_value: str = "*",
    ) -> AccountingPolicy:
        """Return the matching AccountingPolicy from the pack."""
        candidates = self._pack.match_index.get_candidates(event_type)
        if not candidates:
            raise PolicyNotFoundError(event_type, effective_date)

        # Filter by capability_tags: only policies whose tags are all enabled in pack capabilities
        capabilities = getattr(self._pack, "capabilities", {}) or {}
        admissible = [p for p in candidates if is_policy_admissible(p, capabilities)]
        if not admissible:
            raise PolicyNotFoundError(event_type, effective_date)
        candidates = admissible

        # Filter by effective date
        effective = [
            p for p in candidates
            if effective_date >= p.effective_from and (p.effective_to is None or effective_date <= p.effective_to)
        ]
        if not effective:
            raise PolicyNotFoundError(event_type, effective_date)

        # Filter by scope
        def matches_scope(p: CompiledPolicy) -> bool:
            if p.scope == "*":
                return True
            if p.scope.endswith(":*"):
                return scope_value.startswith(p.scope[:-1])
            return p.scope == scope_value

        matching = [p for p in effective if matches_scope(p)]
        if not matching:
            raise PolicyNotFoundError(event_type, effective_date)

        # Filter by where-clause
        if payload is not None:
            with_where = [p for p in matching if p.trigger.where]
            without_where = [p for p in matching if not p.trigger.where]
            specific = [p for p in with_where if _matches_where(p, payload)]
            if specific:
                matching = specific
            else:
                matching = without_where
        else:
            matching = [p for p in matching if not p.trigger.where]

        if not matching:
            raise PolicyNotFoundError(event_type, effective_date)

        selected = _resolve_precedence(matching, event_type)
        return policy_from_compiled(selected)
