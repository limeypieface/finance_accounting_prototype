"""Control rules and evaluator for config-driven controls (controls.yaml).

Evaluates global control rules (e.g. positive_amount_required) against
payload and event_type. Uses the same restricted expression evaluation
as MeaningBuilder guards (payload.*, simple comparisons).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.control")


@dataclass(frozen=True)
class ControlRule:
    """A single control rule from config (controls.yaml)."""

    name: str
    applies_to: str  # event_type or "*"
    action: str  # "reject" or "block"
    expression: str
    reason_code: str
    message: str = ""


@dataclass(frozen=True)
class ControlResult:
    """Result of evaluating controls."""

    passed: bool
    rejected: bool = False
    blocked: bool = False
    triggered_rule: ControlRule | None = None
    reason_code: str | None = None
    message: str | None = None

    @classmethod
    def success(cls) -> "ControlResult":
        return cls(passed=True)

    @classmethod
    def reject(cls, rule: ControlRule) -> "ControlResult":
        return cls(
            passed=False,
            rejected=True,
            triggered_rule=rule,
            reason_code=rule.reason_code,
            message=rule.message or rule.reason_code,
        )

    @classmethod
    def block(cls, rule: ControlRule) -> "ControlResult":
        return cls(
            passed=False,
            blocked=True,
            triggered_rule=rule,
            reason_code=rule.reason_code,
            message=rule.message or rule.reason_code,
        )


def _get_field_value(payload: dict[str, Any], field_path: str) -> Any:
    """Get value from payload by dot-path."""
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


def _compare(actual: Any, op: str, expected: str) -> bool:
    """Compare actual vs expected using operator."""
    if actual is None:
        return False
    if expected.lower() in ("true", "false"):
        expected_bool = expected.lower() == "true"
        if op in ("=", "=="):
            return bool(actual) == expected_bool
        if op == "!=":
            return bool(actual) != expected_bool
        return False
    try:
        actual_num = Decimal(str(actual))
        expected_num = Decimal(expected)
        if op == "<=":
            return actual_num <= expected_num
        if op == ">=":
            return actual_num >= expected_num
        if op == "<":
            return actual_num < expected_num
        if op == ">":
            return actual_num > expected_num
        if op in ("=", "=="):
            return actual_num == expected_num
        if op == "!=":
            return actual_num != expected_num
    except (InvalidOperation, ValueError):
        pass
    actual_str = str(actual)
    if op in ("=", "=="):
        return actual_str == expected
    if op == "!=":
        return actual_str != expected
    return False


def _evaluate_expression(payload: dict[str, Any], expression: str) -> bool:
    """Evaluate a simple expression against payload (true = condition met / triggered)."""
    expression = expression.strip()
    operators = ["<=", ">=", "!=", "==", "=", "<", ">"]
    for op in operators:
        if op in expression:
            parts = expression.split(op, 1)
            if len(parts) == 2:
                field_path = parts[0].strip()
                expected = parts[1].strip()
                actual = _get_field_value(payload, field_path)
                return _compare(actual, op, expected)
    value = _get_field_value(payload, expression)
    return bool(value)


def evaluate_controls(
    payload: dict[str, Any],
    event_type: str,
    rules: tuple[ControlRule, ...],
) -> ControlResult:
    """Evaluate control rules against payload and event_type.

    Only rules where applies_to is '*' or matches event_type are run.
    Returns the first reject or block; otherwise success.
    """
    for rule in rules:
        if rule.applies_to != "*" and rule.applies_to != event_type:
            continue
        try:
            triggered = _evaluate_expression(payload, rule.expression)
        except Exception as e:
            logger.warning(
                "control_evaluation_error",
                extra={"rule": rule.name, "expression": rule.expression, "error": str(e)},
            )
            triggered = False
        if not triggered:
            continue
        logger.debug(
            "control_triggered",
            extra={"rule": rule.name, "action": rule.action, "reason_code": rule.reason_code},
        )
        if rule.action.lower() == "reject":
            return ControlResult.reject(rule)
        return ControlResult.block(rule)
    return ControlResult.success()
