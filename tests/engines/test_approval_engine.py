"""
Tests for the pure approval rule evaluation engine.

Tests cover:
- evaluate_approval_requirement: policy/rule matching, auto-approve, thresholds
- evaluate_approval_status: rejection blocking, approval counting, role diversity (AL-9)
- select_matching_rule: priority ordering (AL-6), amount range matching, guard expressions
- validate_actor_authority: role authorization checks
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.approval import (
    evaluate_approval_requirement,
    evaluate_approval_status,
    select_matching_rule,
    validate_actor_authority,
)
from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalPolicy,
    ApprovalRule,
)


# =========================================================================
# Factory helpers
# =========================================================================


def make_rule(
    rule_name: str = "default-rule",
    priority: int = 10,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    required_roles: tuple[str, ...] = (),
    min_approvers: int = 1,
    require_distinct_roles: bool = False,
    guard_expression: str | None = None,
    auto_approve_below: Decimal | None = None,
) -> ApprovalRule:
    return ApprovalRule(
        rule_name=rule_name,
        priority=priority,
        min_amount=min_amount,
        max_amount=max_amount,
        required_roles=required_roles,
        min_approvers=min_approvers,
        require_distinct_roles=require_distinct_roles,
        guard_expression=guard_expression,
        auto_approve_below=auto_approve_below,
    )


def make_policy(
    rules: tuple[ApprovalRule, ...] = (),
    policy_name: str = "test-policy",
    workflow: str = "invoice",
) -> ApprovalPolicy:
    return ApprovalPolicy(
        policy_name=policy_name,
        version=1,
        applies_to_workflow=workflow,
        rules=rules,
    )


def make_decision(
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    actor_role: str = "manager",
    actor_id=None,
) -> ApprovalDecisionRecord:
    return ApprovalDecisionRecord(
        decision_id=uuid4(),
        request_id=uuid4(),
        actor_id=actor_id or uuid4(),
        actor_role=actor_role,
        decision=decision,
    )


# =========================================================================
# 1. evaluate_approval_requirement
# =========================================================================


class TestEvaluateApprovalRequirement:
    """Tests for evaluate_approval_requirement."""

    def test_no_policy_returns_no_approval_needed(self):
        """No policy configured -> needs_approval=False."""
        result = evaluate_approval_requirement(None, Decimal("500"))

        assert result.needs_approval is False
        assert result.matched_rule is None
        assert result.auto_approved is False

    def test_no_matching_rule_returns_no_approval_needed(self):
        """Policy exists but amount below all rules -> needs_approval=False."""
        rule = make_rule(min_amount=Decimal("1000"), max_amount=Decimal("5000"))
        policy = make_policy(rules=(rule,))

        result = evaluate_approval_requirement(policy, Decimal("500"))

        assert result.needs_approval is False
        assert result.matched_rule is None

    def test_matching_rule_returns_approval_needed(self):
        """Amount within rule range -> needs_approval=True with matched_rule."""
        rule = make_rule(
            rule_name="mid-range",
            min_amount=Decimal("100"),
            max_amount=Decimal("5000"),
            min_approvers=2,
        )
        policy = make_policy(rules=(rule,))

        result = evaluate_approval_requirement(policy, Decimal("1000"))

        assert result.needs_approval is True
        assert result.matched_rule is rule
        assert result.required_approvers == 2
        assert result.auto_approved is False

    def test_auto_approve_below_threshold(self):
        """Amount below auto_approve_below -> auto_approved=True."""
        rule = make_rule(
            rule_name="auto-small",
            auto_approve_below=Decimal("500"),
            min_approvers=1,
        )
        policy = make_policy(rules=(rule,))

        result = evaluate_approval_requirement(policy, Decimal("200"))

        assert result.needs_approval is True
        assert result.is_approved is True
        assert result.auto_approved is True
        assert result.matched_rule is rule
        assert result.required_approvers == 0

    def test_amount_at_auto_approve_threshold_not_auto_approved(self):
        """Amount exactly at auto_approve_below -> normal approval (not auto)."""
        rule = make_rule(
            rule_name="threshold-exact",
            auto_approve_below=Decimal("500"),
            min_amount=Decimal("0"),
            min_approvers=2,
        )
        policy = make_policy(rules=(rule,))

        result = evaluate_approval_requirement(policy, Decimal("500"))

        assert result.needs_approval is True
        assert result.auto_approved is False
        assert result.required_approvers == 2

    def test_amount_above_auto_approve_threshold_normal_approval(self):
        """Amount above auto_approve_below -> normal approval required."""
        rule = make_rule(
            rule_name="above-auto",
            auto_approve_below=Decimal("500"),
            min_amount=Decimal("0"),
            min_approvers=3,
        )
        policy = make_policy(rules=(rule,))

        result = evaluate_approval_requirement(policy, Decimal("750"))

        assert result.needs_approval is True
        assert result.auto_approved is False
        assert result.required_approvers == 3

    def test_none_amount_skips_threshold_checks(self):
        """When amount is None, threshold checks are skipped; rule still matches."""
        rule = make_rule(rule_name="no-amount-rule", min_approvers=1)
        policy = make_policy(rules=(rule,))

        result = evaluate_approval_requirement(policy, None)

        assert result.needs_approval is True
        assert result.matched_rule is rule


# =========================================================================
# 2. evaluate_approval_status
# =========================================================================


class TestEvaluateApprovalStatus:
    """Tests for evaluate_approval_status."""

    def test_single_rejection_blocks(self):
        """A single REJECT decision -> is_rejected=True regardless of approvals."""
        rule = make_rule(min_approvers=1)
        decisions = (
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
            make_decision(ApprovalDecision.REJECT, actor_role="director"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_rejected is True
        assert result.is_approved is False

    def test_enough_approvals_is_approved(self):
        """Sufficient approvals -> is_approved=True."""
        rule = make_rule(min_approvers=2)
        decisions = (
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
            make_decision(ApprovalDecision.APPROVE, actor_role="director"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_approved is True
        assert result.is_rejected is False
        assert result.current_approvers == 2
        assert result.required_approvers == 2

    def test_not_enough_approvals_is_not_approved(self):
        """Fewer approvals than min_approvers -> is_approved=False."""
        rule = make_rule(min_approvers=3)
        decisions = (
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_approved is False
        assert result.current_approvers == 1
        assert result.required_approvers == 3

    def test_no_decisions_not_approved(self):
        """Empty decisions tuple -> is_approved=False."""
        rule = make_rule(min_approvers=1)

        result = evaluate_approval_status(rule, ())

        assert result.is_approved is False
        assert result.is_rejected is False
        assert result.current_approvers == 0

    def test_distinct_roles_required_same_role_counts_as_one(self):
        """AL-9: require_distinct_roles=True, two approvals with same role count as 1."""
        rule = make_rule(min_approvers=2, require_distinct_roles=True)
        decisions = (
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_approved is False
        assert result.current_approvers == 1

    def test_distinct_roles_required_different_roles_counted(self):
        """AL-9: require_distinct_roles=True, different roles each count."""
        rule = make_rule(min_approvers=2, require_distinct_roles=True)
        decisions = (
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
            make_decision(ApprovalDecision.APPROVE, actor_role="director"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_approved is True
        assert result.current_approvers == 2

    def test_distinct_roles_false_same_role_counts_independently(self):
        """require_distinct_roles=False: each approval counts even if same role."""
        rule = make_rule(min_approvers=2, require_distinct_roles=False)
        decisions = (
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_approved is True
        assert result.current_approvers == 2

    def test_escalation_decision_ignored_for_counts(self):
        """ESCALATE decisions are neither approvals nor rejections; they are ignored."""
        rule = make_rule(min_approvers=1)
        decisions = (
            make_decision(ApprovalDecision.ESCALATE, actor_role="manager"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_approved is False
        assert result.is_rejected is False
        assert result.current_approvers == 0

    def test_rejection_precedes_approval_order(self):
        """Rejection found before approval in tuple -> still blocks."""
        rule = make_rule(min_approvers=1)
        decisions = (
            make_decision(ApprovalDecision.REJECT, actor_role="director"),
            make_decision(ApprovalDecision.APPROVE, actor_role="manager"),
        )

        result = evaluate_approval_status(rule, decisions)

        assert result.is_rejected is True


# =========================================================================
# 3. select_matching_rule
# =========================================================================


class TestSelectMatchingRule:
    """Tests for select_matching_rule."""

    def test_sorted_by_priority_lower_wins(self):
        """AL-6: Lower priority number = higher precedence, first match wins."""
        high_priority = make_rule(rule_name="high", priority=1)
        low_priority = make_rule(rule_name="low", priority=10)

        # Pass in unsorted order to verify sorting
        result = select_matching_rule((low_priority, high_priority), Decimal("100"))

        assert result is high_priority

    def test_amount_below_min_amount_no_match(self):
        """Amount below min_amount -> rule does not match."""
        rule = make_rule(min_amount=Decimal("1000"))

        result = select_matching_rule((rule,), Decimal("999"))

        assert result is None

    def test_amount_at_min_amount_matches(self):
        """Amount exactly at min_amount -> rule matches (inclusive lower bound)."""
        rule = make_rule(min_amount=Decimal("1000"))

        result = select_matching_rule((rule,), Decimal("1000"))

        assert result is rule

    def test_amount_at_max_amount_no_match(self):
        """Amount at max_amount -> no match (exclusive upper bound)."""
        rule = make_rule(max_amount=Decimal("5000"))

        result = select_matching_rule((rule,), Decimal("5000"))

        assert result is None

    def test_amount_just_below_max_amount_matches(self):
        """Amount just below max_amount -> matches."""
        rule = make_rule(max_amount=Decimal("5000"))

        result = select_matching_rule((rule,), Decimal("4999.99"))

        assert result is rule

    def test_amount_within_range_matches(self):
        """Amount within [min_amount, max_amount) -> matches."""
        rule = make_rule(min_amount=Decimal("100"), max_amount=Decimal("1000"))

        result = select_matching_rule((rule,), Decimal("500"))

        assert result is rule

    def test_no_rules_returns_none(self):
        """Empty rules tuple -> None."""
        result = select_matching_rule((), Decimal("100"))

        assert result is None

    def test_guard_expression_matching(self):
        """Rule with guard expression that matches context."""
        rule = make_rule(
            rule_name="high-risk",
            guard_expression="risk_level == high",
        )

        context = {"risk_level": "high"}
        result = select_matching_rule((rule,), Decimal("100"), context)

        assert result is rule

    def test_guard_expression_not_matching(self):
        """Rule with guard expression that does not match context."""
        rule = make_rule(
            rule_name="high-risk",
            guard_expression="risk_level == high",
        )

        context = {"risk_level": "low"}
        result = select_matching_rule((rule,), Decimal("100"), context)

        assert result is None

    def test_guard_expression_nested_field(self):
        """Guard expression with dotted field path resolves nested context."""
        rule = make_rule(
            rule_name="dept-check",
            guard_expression="payload.department == finance",
        )

        context = {"payload": {"department": "finance"}}
        result = select_matching_rule((rule,), Decimal("100"), context)

        assert result is rule

    def test_priority_ordering_with_multiple_matching_rules(self):
        """When multiple rules match, the one with lowest priority wins (AL-6)."""
        broad = make_rule(rule_name="broad", priority=100)
        narrow = make_rule(
            rule_name="narrow",
            priority=5,
            min_amount=Decimal("0"),
            max_amount=Decimal("10000"),
        )
        medium = make_rule(rule_name="medium", priority=50)

        result = select_matching_rule((broad, medium, narrow), Decimal("500"))

        assert result is narrow
        assert result.rule_name == "narrow"

    def test_none_amount_matches_rule_without_thresholds(self):
        """When amount is None, a rule without thresholds matches."""
        rule = make_rule(rule_name="no-threshold")

        result = select_matching_rule((rule,), None)

        assert result is rule


# =========================================================================
# 4. validate_actor_authority
# =========================================================================


class TestValidateActorAuthority:
    """Tests for validate_actor_authority."""

    def test_role_in_required_roles_returns_true(self):
        """Actor role present in required_roles -> True."""
        rule = make_rule(required_roles=("manager", "director"))

        assert validate_actor_authority("manager", rule) is True

    def test_role_not_in_required_roles_returns_false(self):
        """Actor role not in required_roles -> False."""
        rule = make_rule(required_roles=("director", "vp"))

        assert validate_actor_authority("analyst", rule) is False

    def test_empty_required_roles_any_role_accepted(self):
        """Empty required_roles -> any role is accepted."""
        rule = make_rule(required_roles=())

        assert validate_actor_authority("intern", rule) is True

    def test_single_required_role_match(self):
        """Single required role that matches -> True."""
        rule = make_rule(required_roles=("cfo",))

        assert validate_actor_authority("cfo", rule) is True

    def test_single_required_role_no_match(self):
        """Single required role that does not match -> False."""
        rule = make_rule(required_roles=("cfo",))

        assert validate_actor_authority("analyst", rule) is False
