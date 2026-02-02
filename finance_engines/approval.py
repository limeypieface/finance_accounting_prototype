"""
finance_engines.approval -- Pure approval rule evaluation engine.

Responsibility:
    Evaluate approval rules against amounts and context to determine
    whether a transition requires approval, which rule applies, whether
    an actor is authorized, and whether enough approvals have been
    collected.

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/ types.

Invariants enforced:
    - AL-6 (deterministic rule ordering): rules sorted by priority before
      evaluation; lower priority number = higher precedence; first match wins.
    - AL-9 (role diversity): when ``require_distinct_roles=True``, only
      distinct ``actor_role`` values count toward ``min_approvers``.
    - Purity: no clock access, no I/O, no database.

Failure modes:
    - Returns ``ApprovalEvaluation(needs_approval=False)`` when no policy
      or no matching rule exists (fail-open for unconfigured transitions).
    - ValueError if rule has ``auto_approve_below`` and ``min_amount``
      that conflict (caught at config compile time, not here).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalEvaluation,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRule,
)


def evaluate_approval_requirement(
    policy: ApprovalPolicy | None,
    amount: Decimal | None,
    context: dict[str, Any] | None = None,
) -> ApprovalEvaluation:
    """Determine if a transition requires approval and which rule applies.

    Args:
        policy: The approval policy for this workflow/action (None = no policy).
        amount: The monetary amount for threshold comparison.
        context: Optional context dict for guard expression evaluation.

    Returns:
        ApprovalEvaluation with ``needs_approval``, ``matched_rule``,
        and ``auto_approved`` fields populated.
    """
    if policy is None:
        return ApprovalEvaluation(needs_approval=False, reason="No policy configured")

    rule = select_matching_rule(policy.rules, amount, context)
    if rule is None:
        return ApprovalEvaluation(
            needs_approval=False,
            reason="No matching rule for amount/context",
        )

    # Check auto-approve threshold
    if rule.auto_approve_below is not None and amount is not None:
        if amount < rule.auto_approve_below:
            return ApprovalEvaluation(
                needs_approval=True,
                is_approved=True,
                auto_approved=True,
                matched_rule=rule,
                required_approvers=0,
                current_approvers=0,
                reason=f"Auto-approved: {amount} below threshold {rule.auto_approve_below}",
            )

    return ApprovalEvaluation(
        needs_approval=True,
        matched_rule=rule,
        required_approvers=rule.min_approvers,
        reason=f"Approval required by rule '{rule.rule_name}'",
    )


def evaluate_approval_status(
    rule: ApprovalRule,
    decisions: tuple[ApprovalDecisionRecord, ...],
) -> ApprovalEvaluation:
    """Given current decisions, determine if approval threshold is met.

    AL-9: If ``rule.require_distinct_roles`` is True, only counts
    approvals from distinct ``actor_role`` values toward ``min_approvers``.

    Args:
        rule: The matched approval rule.
        decisions: All decisions recorded for the request.

    Returns:
        ApprovalEvaluation with ``is_approved``/``is_rejected`` and
        current approver counts.
    """
    # Check for any rejection first -- a single rejection blocks
    for d in decisions:
        if d.decision == ApprovalDecision.REJECT:
            return ApprovalEvaluation(
                needs_approval=True,
                is_rejected=True,
                matched_rule=rule,
                required_approvers=rule.min_approvers,
                current_approvers=0,
                reason=f"Rejected by {d.actor_id}",
            )

    # Count approvals
    approvals = [d for d in decisions if d.decision == ApprovalDecision.APPROVE]

    if rule.require_distinct_roles:
        # AL-9: only distinct roles count
        distinct_roles = {d.actor_role for d in approvals}
        current_count = len(distinct_roles)
    else:
        current_count = len(approvals)

    is_approved = current_count >= rule.min_approvers

    return ApprovalEvaluation(
        needs_approval=True,
        is_approved=is_approved,
        matched_rule=rule,
        required_approvers=rule.min_approvers,
        current_approvers=current_count,
        reason="Approved" if is_approved else f"{current_count}/{rule.min_approvers} approvals",
    )


def select_matching_rule(
    rules: tuple[ApprovalRule, ...],
    amount: Decimal | None,
    context: dict[str, Any] | None = None,
) -> ApprovalRule | None:
    """Select the first matching rule based on amount thresholds.

    AL-6: Rules are sorted by ``priority`` (ascending) before evaluation.
    First match wins.

    Args:
        rules: Tuple of rules from the policy.
        amount: Monetary amount for threshold comparison (None skips threshold checks).
        context: Optional context for guard expression evaluation.

    Returns:
        The first matching rule, or None if no rule matches.
    """
    sorted_rules = sorted(rules, key=lambda r: r.priority)

    for rule in sorted_rules:
        if _rule_matches(rule, amount, context):
            return rule

    return None


def validate_actor_authority(
    actor_role: str,
    rule: ApprovalRule,
) -> bool:
    """Check if actor's role is authorized to approve under this rule.

    Args:
        actor_role: The role of the actor attempting to approve.
        rule: The approval rule defining required roles.

    Returns:
        True if the actor's role is in the rule's ``required_roles``,
        or if ``required_roles`` is empty (any role accepted).
    """
    if not rule.required_roles:
        return True
    return actor_role in rule.required_roles


def _rule_matches(
    rule: ApprovalRule,
    amount: Decimal | None,
    context: dict[str, Any] | None,
) -> bool:
    """Check if a rule matches the given amount and context.

    A rule matches if:
    1. Amount is within [min_amount, max_amount) range (if thresholds set)
    2. Guard expression evaluates to True (if set)
    3. If amount is None, threshold checks are skipped
    """
    # Auto-approve rules match by their auto_approve_below threshold
    if rule.auto_approve_below is not None and amount is not None:
        if amount < rule.auto_approve_below:
            return True

    # Threshold check
    if amount is not None:
        if rule.min_amount is not None and amount < rule.min_amount:
            return False
        if rule.max_amount is not None and amount >= rule.max_amount:
            return False

    # If rule has thresholds but no amount provided, skip threshold check
    # (rule still matches on other criteria)

    # Guard expression check (simplified -- full AST eval is in config layer)
    if rule.guard_expression is not None and context is not None:
        if not _evaluate_simple_guard(rule.guard_expression, context):
            return False

    return True


def _evaluate_simple_guard(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a simple guard expression against context.

    Supports basic comparisons: ``payload.field > value``.
    This mirrors the MeaningBuilder._evaluate_expression pattern.
    Full AST validation happens at config compile time via guard_ast.py.
    """
    expression = expression.strip()

    operators = ["<=", ">=", "!=", "==", "<", ">"]
    for op in operators:
        if op in expression:
            parts = expression.split(op, 1)
            if len(parts) == 2:
                field_path = parts[0].strip()
                expected_str = parts[1].strip()

                actual = _resolve_field(field_path, context)
                if actual is None:
                    return False

                try:
                    expected = type(actual)(expected_str)
                except (ValueError, TypeError):
                    return False

                if op == "<=":
                    return actual <= expected
                if op == ">=":
                    return actual >= expected
                if op == "!=":
                    return actual != expected
                if op == "==":
                    return actual == expected
                if op == "<":
                    return actual < expected
                if op == ">":
                    return actual > expected

    # Boolean field check
    value = _resolve_field(expression, context)
    return bool(value)


def _resolve_field(field_path: str, context: dict[str, Any]) -> Any:
    """Resolve a dotted field path against a context dict.

    ``payload.amount`` -> context["payload"]["amount"]
    """
    parts = field_path.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current
