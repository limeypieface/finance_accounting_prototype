"""
Restricted AST for guard and control expressions.

Guard expressions in configuration must use a fixed operator set.
This module parses and validates expressions, rejecting anything
that could execute arbitrary code.

Allowed:
  - Comparisons: <, <=, >, >=, ==, !=, is, is not
  - Logical: and, or, not
  - Field access: context_root.field_name (payload, party, contract, event)
  - Literals: numbers, strings, booleans, None
  - Functions: abs(), len(), check_credit_limit()
  - Membership: in, not in
  - Conditional: ternary (a if b else c)

Rejected:
  - imports, arbitrary function calls, deep attribute chains,
    lambda, eval, exec, arbitrary names
"""

import ast
from dataclasses import dataclass


# Functions allowed in guard expressions
ALLOWED_FUNCTIONS: frozenset[str] = frozenset({"abs", "len", "check_credit_limit"})

# Context roots allowed for field access (root.field_name)
ALLOWED_CONTEXT_ROOTS: frozenset[str] = frozenset({
    "payload", "party", "contract", "event",
})

# Names allowed as bare identifiers
ALLOWED_NAMES: frozenset[str] = frozenset(
    {"True", "False", "None"} | ALLOWED_CONTEXT_ROOTS
)


@dataclass(frozen=True)
class GuardASTError:
    """A validation error found in a guard expression."""

    expression: str
    message: str
    node_type: str = ""
    lineno: int = 0
    col_offset: int = 0


def validate_guard_expression(expression: str) -> list[GuardASTError]:
    """Validate a guard expression against the restricted AST.

    Returns a list of errors. Empty list means the expression is valid.
    """
    errors: list[GuardASTError] = []

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        return [
            GuardASTError(
                expression=expression,
                message=f"Syntax error: {e.msg}",
                lineno=e.lineno or 0,
                col_offset=e.offset or 0,
            )
        ]

    _validate_node(tree.body, expression, errors)
    return errors


def _validate_node(
    node: ast.AST, expression: str, errors: list[GuardASTError]
) -> None:
    """Recursively validate an AST node."""

    if isinstance(node, ast.BoolOp):
        # and, or
        for value in node.values:
            _validate_node(value, expression, errors)

    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not):
            errors.append(
                GuardASTError(
                    expression=expression,
                    message=f"Disallowed unary operator: {type(node.op).__name__}",
                    node_type=type(node.op).__name__,
                )
            )
        _validate_node(node.operand, expression, errors)

    elif isinstance(node, ast.Compare):
        _validate_node(node.left, expression, errors)
        for comparator in node.comparators:
            _validate_node(comparator, expression, errors)
        for op in node.ops:
            if not isinstance(
                op,
                (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                 ast.In, ast.NotIn, ast.Is, ast.IsNot),
            ):
                errors.append(
                    GuardASTError(
                        expression=expression,
                        message=f"Disallowed comparison: {type(op).__name__}",
                        node_type=type(op).__name__,
                    )
                )

    elif isinstance(node, ast.BinOp):
        # Allow basic arithmetic for threshold calculations
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            _validate_node(node.left, expression, errors)
            _validate_node(node.right, expression, errors)
        else:
            errors.append(
                GuardASTError(
                    expression=expression,
                    message=f"Disallowed binary operator: {type(node.op).__name__}",
                    node_type=type(node.op).__name__,
                )
            )

    elif isinstance(node, ast.Call):
        # Only allow whitelisted functions
        if isinstance(node.func, ast.Name) and node.func.id in ALLOWED_FUNCTIONS:
            for arg in node.args:
                _validate_node(arg, expression, errors)
            for kw in getattr(node, "keywords", []):
                _validate_node(kw.value, expression, errors)
        else:
            func_name = _get_name(node.func)
            errors.append(
                GuardASTError(
                    expression=expression,
                    message=f"Disallowed function call: {func_name}",
                    node_type="Call",
                )
            )

    elif isinstance(node, ast.Attribute):
        # Allow context_root.field_name (one level of dotted access)
        if isinstance(node.value, ast.Name) and node.value.id in ALLOWED_CONTEXT_ROOTS:
            pass  # payload.field, party.field, etc. are allowed
        else:
            errors.append(
                GuardASTError(
                    expression=expression,
                    message=(
                        f"Disallowed attribute access: {_get_name(node)}. "
                        f"Only {', '.join(sorted(ALLOWED_CONTEXT_ROOTS))}.field_name is allowed."
                    ),
                    node_type="Attribute",
                )
            )

    elif isinstance(node, ast.Name):
        if node.id not in ALLOWED_NAMES:
            errors.append(
                GuardASTError(
                    expression=expression,
                    message=f"Disallowed name: {node.id}",
                    node_type="Name",
                )
            )

    elif isinstance(node, ast.Constant):
        # Numbers, strings, booleans, None are all fine
        if not isinstance(node.value, (int, float, str, bool, type(None))):
            errors.append(
                GuardASTError(
                    expression=expression,
                    message=f"Disallowed constant type: {type(node.value).__name__}",
                    node_type="Constant",
                )
            )

    elif isinstance(node, (ast.List, ast.Tuple)):
        # Allow lists/tuples for 'in' operator
        for elt in node.elts:
            _validate_node(elt, expression, errors)

    elif isinstance(node, ast.IfExp):
        # Allow ternary: value_if_true if condition else value_if_false
        _validate_node(node.test, expression, errors)
        _validate_node(node.body, expression, errors)
        _validate_node(node.orelse, expression, errors)

    elif isinstance(node, ast.Lambda):
        errors.append(
            GuardASTError(
                expression=expression,
                message="Lambda expressions are not allowed",
                node_type="Lambda",
            )
        )

    else:
        errors.append(
            GuardASTError(
                expression=expression,
                message=f"Disallowed AST node type: {type(node).__name__}",
                node_type=type(node).__name__,
            )
        )


def _get_name(node: ast.AST) -> str:
    """Extract a human-readable name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_get_name(node.value)}.{node.attr}"
    return type(node).__name__
