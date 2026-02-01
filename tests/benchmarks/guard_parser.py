"""
Guard expression constraint parser.

Parses guard expressions (same restricted AST as finance_config/guard_ast.py)
and extracts FieldConstraint objects that describe what payload values
will AVOID triggering rejection.

Example:
    Guard: "payload.amount <= 0" (reject)
    Constraint: FieldConstraint("amount", "gt", 0)
    Meaning: amount must be > 0 to avoid rejection.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from typing import Any

from tests.benchmarks.event_shape import FieldConstraint


# Operator inversion: if guard rejects when `X op Y`, then to AVOID rejection
# the payload must satisfy the INVERSE.
_INVERT_OP = {
    ast.Lt: "gte",     # reject when < Y   -> must be >= Y
    ast.LtE: "gt",     # reject when <= Y  -> must be > Y
    ast.Gt: "lte",     # reject when > Y   -> must be <= Y
    ast.GtE: "lt",     # reject when >= Y  -> must be < Y
    ast.Eq: "neq",     # reject when == Y  -> must be != Y
    ast.NotEq: "eq",   # reject when != Y  -> must be == Y
}


def parse_guard_constraints(expression: str) -> list[FieldConstraint]:
    """Parse a reject guard expression and return constraints for safe values.

    Only extracts constraints for `payload.*` fields (skips party.*, contract.*, etc.).
    Skips function calls (check_credit_limit), complex ternaries, and
    non-payload references since those depend on external test state.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []

    constraints: list[FieldConstraint] = []
    _extract_from_node(tree.body, constraints)
    return constraints


def _extract_from_node(node: ast.AST, out: list[FieldConstraint]) -> None:
    """Recursively extract constraints from an AST node."""

    if isinstance(node, ast.BoolOp):
        # For `A and B` (reject): both must be true to reject.
        # To avoid: we can avoid either. Conservatively, try to avoid both.
        # For `A or B` (reject): either can trigger rejection.
        # To avoid: must avoid BOTH.
        # In practice, extract all sub-constraints and let the generator satisfy all.
        for value in node.values:
            _extract_from_node(value, out)

    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        # `not X` as a reject guard means "reject when X is falsy"
        # This is uncommon for payload fields; skip unless it's a simple comparison
        _extract_not_node(node.operand, out)

    elif isinstance(node, ast.Compare):
        _extract_comparison(node, out)

    elif isinstance(node, ast.Attribute):
        # Bare `payload.field` used as truthy check in a reject guard
        # means "reject when payload.field is truthy" -> set to falsy
        field = _get_payload_field(node)
        if field:
            out.append(FieldConstraint(field, "eq", False))

    elif isinstance(node, ast.IfExp):
        # Ternary: `value if condition else fallback`
        # Extract from condition and body, best-effort
        _extract_from_node(node.body, out)


def _extract_not_node(node: ast.AST, out: list[FieldConstraint]) -> None:
    """Handle `not X` — inversion of the inner expression."""
    if isinstance(node, ast.Call):
        # `not check_credit_limit(...)` -> skip, external function
        return
    if isinstance(node, ast.Attribute):
        # `not payload.field` -> reject when field is falsy -> field must be truthy
        field = _get_payload_field(node)
        if field:
            out.append(FieldConstraint(field, "is_not_none", None))
    if isinstance(node, ast.Compare):
        # `not (X < Y)` -> reject when X >= Y -> need X < Y
        # This is the opposite of the normal inversion
        _extract_comparison_inverted(node, out)


def _extract_comparison(node: ast.Compare, out: list[FieldConstraint]) -> None:
    """Extract constraints from a comparison node.

    Handles: payload.X <op> <literal>
    The guard REJECTS when the comparison is True, so we need the INVERSE.
    """
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return

    op = node.ops[0]
    left = node.left
    right = node.comparators[0]

    # Pattern 1: payload.field <op> literal
    field = _get_payload_field(left)
    if field:
        value = _get_literal(right)
        if value is not None:
            inv_op = _INVERT_OP.get(type(op))
            if inv_op:
                out.append(FieldConstraint(field, inv_op, value))
            elif isinstance(op, ast.Is):
                # `payload.X is None` (reject) -> must be is_not_none
                if value is None or (isinstance(right, ast.Constant) and right.value is None):
                    out.append(FieldConstraint(field, "is_not_none", None))
            elif isinstance(op, ast.IsNot):
                # `payload.X is not None` (reject) -> must be is_none
                if value is None or (isinstance(right, ast.Constant) and right.value is None):
                    out.append(FieldConstraint(field, "is_none", None))
            return

    # Pattern 2: literal <op> payload.field (reversed)
    field = _get_payload_field(right)
    if field:
        value = _get_literal(left)
        if value is not None:
            # Flip the comparison direction
            flipped = {
                ast.Lt: ast.Gt,
                ast.LtE: ast.GtE,
                ast.Gt: ast.Lt,
                ast.GtE: ast.LtE,
                ast.Eq: ast.Eq,
                ast.NotEq: ast.NotEq,
            }
            flipped_op = flipped.get(type(op))
            if flipped_op:
                inv_op = _INVERT_OP.get(flipped_op)
                if inv_op:
                    out.append(FieldConstraint(field, inv_op, value))


def _extract_comparison_inverted(node: ast.Compare, out: list[FieldConstraint]) -> None:
    """Extract constraints from `not (comparison)` — don't invert, use directly."""
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return

    op = node.ops[0]
    left = node.left
    right = node.comparators[0]

    field = _get_payload_field(left)
    if field:
        value = _get_literal(right)
        if value is not None:
            # The guard says `not (X op Y)` -> reject when X op Y is False
            # So X op Y must be True to avoid rejection -> use the op directly
            direct_op = {
                ast.Lt: "lt",
                ast.LtE: "lte",
                ast.Gt: "gt",
                ast.GtE: "gte",
                ast.Eq: "eq",
                ast.NotEq: "neq",
            }
            mapped = direct_op.get(type(op))
            if mapped:
                out.append(FieldConstraint(field, mapped, value))


def _get_payload_field(node: ast.AST) -> str | None:
    """Extract field name from `payload.field_name` attribute access.

    Returns None for non-payload roots (party.*, contract.*, etc.).
    """
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "payload":
            return node.attr
    return None


def _get_literal(node: ast.AST) -> Any:
    """Extract a literal value from a Constant node.

    Returns the value, or a sentinel if not a simple literal.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        if isinstance(node.operand, ast.Constant):
            return -node.operand.value
    return None
