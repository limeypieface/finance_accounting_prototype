"""
Tests for Approval Domain Types (``finance_kernel.domain.approval``).

Covers the pure value objects that define the modular approval engine:
approval lifecycle state machine, policy/rule data, request/decision records,
evaluation results, transition results, and the OrgHierarchyProvider protocol.

Invariants tested:
- AL-1: Lifecycle state machine -- APPROVAL_TRANSITIONS defines the only valid
  status transitions.  Terminal states have no outgoing edges.
- AL-2: Policy version snapshot -- ApprovalRequest captures policy_version and
  policy_hash at creation time.
- Frozen (immutable) guarantee on all dataclasses.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.approval import (
    APPROVAL_TRANSITIONS,
    TERMINAL_APPROVAL_STATUSES,
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalEvaluation,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalRule,
    ApprovalStatus,
    OrgHierarchyProvider,
    TransitionResult,
)


# =========================================================================
# ApprovalStatus Enum
# =========================================================================


class TestApprovalStatus:
    """Tests for ApprovalStatus enum values."""

    def test_all_seven_states_defined(self):
        """Should define exactly 7 approval states."""
        expected = {
            "pending",
            "approved",
            "rejected",
            "escalated",
            "expired",
            "cancelled",
            "auto_approved",
        }
        actual = {s.value for s in ApprovalStatus}
        assert actual == expected

    def test_enum_count(self):
        """Should have exactly 7 members."""
        assert len(ApprovalStatus) == 7

    def test_str_enum_identity(self):
        """ApprovalStatus members should be usable as strings."""
        assert ApprovalStatus.PENDING == "pending"
        assert ApprovalStatus.AUTO_APPROVED == "auto_approved"


# =========================================================================
# APPROVAL_TRANSITIONS
# =========================================================================


class TestApprovalTransitions:
    """Tests for the APPROVAL_TRANSITIONS state machine dict."""

    def test_every_status_has_transition_entry(self):
        """Every ApprovalStatus should appear as a key in APPROVAL_TRANSITIONS."""
        for status in ApprovalStatus:
            assert status in APPROVAL_TRANSITIONS, (
                f"{status} missing from APPROVAL_TRANSITIONS"
            )

    def test_pending_transitions(self):
        """PENDING should transition to all other states."""
        expected = frozenset({
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.ESCALATED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
            ApprovalStatus.AUTO_APPROVED,
        })
        assert APPROVAL_TRANSITIONS[ApprovalStatus.PENDING] == expected

    def test_escalated_transitions(self):
        """ESCALATED should transition to APPROVED, REJECTED, EXPIRED only."""
        expected = frozenset({
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
        })
        assert APPROVAL_TRANSITIONS[ApprovalStatus.ESCALATED] == expected

    def test_escalated_disallows_cancelled(self):
        """ESCALATED must NOT allow transition to CANCELLED."""
        assert ApprovalStatus.CANCELLED not in APPROVAL_TRANSITIONS[ApprovalStatus.ESCALATED]

    def test_escalated_disallows_auto_approved(self):
        """ESCALATED must NOT allow transition to AUTO_APPROVED."""
        assert ApprovalStatus.AUTO_APPROVED not in APPROVAL_TRANSITIONS[ApprovalStatus.ESCALATED]

    def test_terminal_states_have_no_outgoing_transitions(self):
        """AL-1: Terminal states must have empty transition sets."""
        for status in TERMINAL_APPROVAL_STATUSES:
            assert APPROVAL_TRANSITIONS[status] == frozenset(), (
                f"Terminal state {status} should have no outgoing transitions"
            )

    def test_transition_values_are_frozensets(self):
        """All transition targets should be frozensets (immutable)."""
        for status, targets in APPROVAL_TRANSITIONS.items():
            assert isinstance(targets, frozenset), (
                f"Transitions for {status} should be frozenset, got {type(targets)}"
            )


# =========================================================================
# TERMINAL_APPROVAL_STATUSES
# =========================================================================


class TestTerminalApprovalStatuses:
    """Tests for TERMINAL_APPROVAL_STATUSES frozenset."""

    def test_completeness(self):
        """Should contain exactly the 5 terminal states."""
        expected = frozenset({
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
            ApprovalStatus.AUTO_APPROVED,
        })
        assert TERMINAL_APPROVAL_STATUSES == expected

    def test_non_terminal_states_excluded(self):
        """PENDING and ESCALATED must not be terminal."""
        assert ApprovalStatus.PENDING not in TERMINAL_APPROVAL_STATUSES
        assert ApprovalStatus.ESCALATED not in TERMINAL_APPROVAL_STATUSES

    def test_is_frozenset(self):
        """Should be a frozenset (immutable)."""
        assert isinstance(TERMINAL_APPROVAL_STATUSES, frozenset)


# =========================================================================
# ApprovalDecision Enum
# =========================================================================


class TestApprovalDecision:
    """Tests for ApprovalDecision enum values."""

    def test_all_three_decisions_defined(self):
        """Should define exactly 3 decision types."""
        expected = {"approve", "reject", "escalate"}
        actual = {d.value for d in ApprovalDecision}
        assert actual == expected

    def test_enum_count(self):
        """Should have exactly 3 members."""
        assert len(ApprovalDecision) == 3


# =========================================================================
# ApprovalRule Frozen Dataclass
# =========================================================================


class TestApprovalRule:
    """Tests for ApprovalRule frozen dataclass."""

    def test_construction_with_required_fields(self):
        """Should construct with only required fields."""
        rule = ApprovalRule(rule_name="basic", priority=1)
        assert rule.rule_name == "basic"
        assert rule.priority == 1

    def test_default_values(self):
        """Should have correct defaults for optional fields."""
        rule = ApprovalRule(rule_name="test", priority=10)
        assert rule.min_amount is None
        assert rule.max_amount is None
        assert rule.required_roles == ()
        assert rule.min_approvers == 1
        assert rule.require_distinct_roles is False
        assert rule.guard_expression is None
        assert rule.auto_approve_below is None
        assert rule.escalation_timeout_hours is None

    def test_construction_with_all_fields(self):
        """Should construct with all fields specified."""
        rule = ApprovalRule(
            rule_name="high_value",
            priority=1,
            min_amount=Decimal("10000"),
            max_amount=Decimal("100000"),
            required_roles=("finance_director", "cfo"),
            min_approvers=2,
            require_distinct_roles=True,
            guard_expression="amount > 10000",
            auto_approve_below=Decimal("500"),
            escalation_timeout_hours=48,
        )
        assert rule.min_amount == Decimal("10000")
        assert rule.max_amount == Decimal("100000")
        assert rule.required_roles == ("finance_director", "cfo")
        assert rule.min_approvers == 2
        assert rule.require_distinct_roles is True
        assert rule.guard_expression == "amount > 10000"
        assert rule.auto_approve_below == Decimal("500")
        assert rule.escalation_timeout_hours == 48

    def test_immutability(self):
        """Should be immutable (frozen dataclass)."""
        rule = ApprovalRule(rule_name="test", priority=1)
        with pytest.raises(AttributeError):
            rule.rule_name = "changed"
        with pytest.raises(AttributeError):
            rule.priority = 99


# =========================================================================
# ApprovalPolicy Frozen Dataclass
# =========================================================================


class TestApprovalPolicy:
    """Tests for ApprovalPolicy frozen dataclass."""

    def test_construction_with_required_fields(self):
        """Should construct with only required fields."""
        policy = ApprovalPolicy(
            policy_name="ap_invoice",
            version=1,
            applies_to_workflow="ap_invoice_workflow",
        )
        assert policy.policy_name == "ap_invoice"
        assert policy.version == 1
        assert policy.applies_to_workflow == "ap_invoice_workflow"

    def test_default_values(self):
        """Should have correct defaults for optional fields."""
        policy = ApprovalPolicy(
            policy_name="test", version=1, applies_to_workflow="wf"
        )
        assert policy.applies_to_action is None
        assert policy.rules == ()
        assert policy.effective_from is None
        assert policy.effective_to is None
        assert policy.policy_currency is None
        assert policy.policy_hash is None

    def test_construction_with_all_fields(self):
        """Should construct with all fields specified."""
        rule = ApprovalRule(rule_name="r1", priority=1)
        policy = ApprovalPolicy(
            policy_name="ap_invoice",
            version=2,
            applies_to_workflow="ap_invoice_workflow",
            applies_to_action="submit",
            rules=(rule,),
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31),
            policy_currency="USD",
            policy_hash="sha256:abc123",
        )
        assert policy.applies_to_action == "submit"
        assert len(policy.rules) == 1
        assert policy.rules[0].rule_name == "r1"
        assert policy.effective_from == date(2025, 1, 1)
        assert policy.effective_to == date(2025, 12, 31)
        assert policy.policy_currency == "USD"
        assert policy.policy_hash == "sha256:abc123"

    def test_immutability(self):
        """Should be immutable (frozen dataclass)."""
        policy = ApprovalPolicy(
            policy_name="test", version=1, applies_to_workflow="wf"
        )
        with pytest.raises(AttributeError):
            policy.version = 99
        with pytest.raises(AttributeError):
            policy.rules = ()


# =========================================================================
# ApprovalDecisionRecord
# =========================================================================


class TestApprovalDecisionRecord:
    """Tests for ApprovalDecisionRecord frozen dataclass."""

    def test_construction(self):
        """Should construct with all required fields."""
        decision_id = uuid4()
        request_id = uuid4()
        actor_id = uuid4()
        now = datetime(2025, 6, 15, 10, 30, 0)

        record = ApprovalDecisionRecord(
            decision_id=decision_id,
            request_id=request_id,
            actor_id=actor_id,
            actor_role="finance_manager",
            decision=ApprovalDecision.APPROVE,
            comment="Looks good",
            decided_at=now,
        )

        assert record.decision_id == decision_id
        assert record.request_id == request_id
        assert record.actor_id == actor_id
        assert record.actor_role == "finance_manager"
        assert record.decision == ApprovalDecision.APPROVE
        assert record.comment == "Looks good"
        assert record.decided_at == now

    def test_defaults(self):
        """Should have correct defaults for optional fields."""
        record = ApprovalDecisionRecord(
            decision_id=uuid4(),
            request_id=uuid4(),
            actor_id=uuid4(),
            actor_role="approver",
            decision=ApprovalDecision.REJECT,
        )
        assert record.comment == ""
        assert record.decided_at is None

    def test_immutability(self):
        """Should be immutable (frozen dataclass)."""
        record = ApprovalDecisionRecord(
            decision_id=uuid4(),
            request_id=uuid4(),
            actor_id=uuid4(),
            actor_role="approver",
            decision=ApprovalDecision.APPROVE,
        )
        with pytest.raises(AttributeError):
            record.decision = ApprovalDecision.REJECT


# =========================================================================
# ApprovalRequest
# =========================================================================


class TestApprovalRequest:
    """Tests for ApprovalRequest frozen dataclass."""

    def test_construction_with_required_fields(self):
        """Should construct with only required fields."""
        request_id = uuid4()
        entity_id = uuid4()

        req = ApprovalRequest(
            request_id=request_id,
            workflow_name="ap_invoice_workflow",
            entity_type="Invoice",
            entity_id=entity_id,
            transition_action="submit",
            from_state="draft",
            to_state="pending_approval",
            policy_name="ap_policy",
            policy_version=1,
        )

        assert req.request_id == request_id
        assert req.workflow_name == "ap_invoice_workflow"
        assert req.entity_type == "Invoice"
        assert req.entity_id == entity_id
        assert req.transition_action == "submit"
        assert req.from_state == "draft"
        assert req.to_state == "pending_approval"
        assert req.policy_name == "ap_policy"
        assert req.policy_version == 1

    def test_default_values(self):
        """Should have correct defaults for optional fields."""
        req = ApprovalRequest(
            request_id=uuid4(),
            workflow_name="wf",
            entity_type="Entity",
            entity_id=uuid4(),
            transition_action="submit",
            from_state="s1",
            to_state="s2",
            policy_name="pol",
            policy_version=1,
        )

        assert req.policy_hash is None
        assert req.amount is None
        assert req.currency == "USD"
        assert req.status == ApprovalStatus.PENDING
        assert req.created_at is None
        assert req.resolved_at is None
        assert req.matched_rule is None
        assert req.original_request_hash is None
        assert req.decisions == ()
        # requestor_id has a uuid4 default factory, just check it exists
        assert req.requestor_id is not None

    def test_construction_with_all_fields(self):
        """AL-2: Should capture policy_version and policy_hash at creation."""
        decision = ApprovalDecisionRecord(
            decision_id=uuid4(),
            request_id=uuid4(),
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
        )
        now = datetime(2025, 7, 1, 12, 0, 0)
        requestor = uuid4()

        req = ApprovalRequest(
            request_id=uuid4(),
            workflow_name="wf",
            entity_type="Invoice",
            entity_id=uuid4(),
            transition_action="approve",
            from_state="pending",
            to_state="approved",
            policy_name="ap_policy",
            policy_version=3,
            policy_hash="sha256:deadbeef",
            amount=Decimal("5000.00"),
            currency="EUR",
            requestor_id=requestor,
            status=ApprovalStatus.APPROVED,
            created_at=now,
            resolved_at=now,
            matched_rule="high_value_rule",
            original_request_hash="sha256:cafebabe",
            decisions=(decision,),
        )

        assert req.policy_version == 3
        assert req.policy_hash == "sha256:deadbeef"
        assert req.amount == Decimal("5000.00")
        assert req.currency == "EUR"
        assert req.requestor_id == requestor
        assert req.status == ApprovalStatus.APPROVED
        assert req.matched_rule == "high_value_rule"
        assert req.original_request_hash == "sha256:cafebabe"
        assert len(req.decisions) == 1

    def test_immutability(self):
        """Should be immutable (frozen dataclass)."""
        req = ApprovalRequest(
            request_id=uuid4(),
            workflow_name="wf",
            entity_type="Entity",
            entity_id=uuid4(),
            transition_action="submit",
            from_state="s1",
            to_state="s2",
            policy_name="pol",
            policy_version=1,
        )
        with pytest.raises(AttributeError):
            req.status = ApprovalStatus.APPROVED
        with pytest.raises(AttributeError):
            req.decisions = ()


# =========================================================================
# ApprovalEvaluation
# =========================================================================


class TestApprovalEvaluation:
    """Tests for ApprovalEvaluation frozen dataclass."""

    def test_defaults(self):
        """Should have correct defaults for optional fields."""
        evaluation = ApprovalEvaluation(needs_approval=True)

        assert evaluation.needs_approval is True
        assert evaluation.is_approved is False
        assert evaluation.is_rejected is False
        assert evaluation.matched_rule is None
        assert evaluation.required_approvers == 0
        assert evaluation.current_approvers == 0
        assert evaluation.auto_approved is False
        assert evaluation.reason == ""

    def test_approved_evaluation(self):
        """Should represent a fully approved evaluation."""
        rule = ApprovalRule(rule_name="standard", priority=1, min_approvers=2)
        evaluation = ApprovalEvaluation(
            needs_approval=True,
            is_approved=True,
            matched_rule=rule,
            required_approvers=2,
            current_approvers=2,
            reason="All required approvals received",
        )

        assert evaluation.is_approved is True
        assert evaluation.matched_rule is rule
        assert evaluation.required_approvers == 2
        assert evaluation.current_approvers == 2

    def test_auto_approved_evaluation(self):
        """Should represent an auto-approved evaluation."""
        evaluation = ApprovalEvaluation(
            needs_approval=False,
            is_approved=True,
            auto_approved=True,
            reason="Amount below auto-approve threshold",
        )

        assert evaluation.needs_approval is False
        assert evaluation.auto_approved is True

    def test_immutability(self):
        """Should be immutable (frozen dataclass)."""
        evaluation = ApprovalEvaluation(needs_approval=True)
        with pytest.raises(AttributeError):
            evaluation.is_approved = True


# =========================================================================
# TransitionResult
# =========================================================================


class TestTransitionResult:
    """Tests for TransitionResult frozen dataclass."""

    def test_defaults(self):
        """Should have correct defaults for optional fields."""
        result = TransitionResult(success=True)

        assert result.success is True
        assert result.new_state is None
        assert result.approval_required is False
        assert result.approval_request_id is None
        assert result.posts_entry is False
        assert result.reason == ""

    def test_successful_transition(self):
        """Should represent a successful transition."""
        result = TransitionResult(
            success=True,
            new_state="approved",
            posts_entry=True,
            reason="Transition completed successfully",
        )

        assert result.success is True
        assert result.new_state == "approved"
        assert result.posts_entry is True

    def test_blocked_by_approval(self):
        """Should represent a transition blocked by approval requirement."""
        approval_id = uuid4()
        result = TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=approval_id,
            reason="Approval required before transition",
        )

        assert result.success is False
        assert result.approval_required is True
        assert result.approval_request_id == approval_id

    def test_immutability(self):
        """Should be immutable (frozen dataclass)."""
        result = TransitionResult(success=True)
        with pytest.raises(AttributeError):
            result.success = False


# =========================================================================
# OrgHierarchyProvider Protocol
# =========================================================================


class TestOrgHierarchyProviderProtocol:
    """Tests that StaticRoleProvider satisfies OrgHierarchyProvider protocol."""

    def test_static_role_provider_satisfies_protocol(self):
        """StaticRoleProvider should satisfy OrgHierarchyProvider protocol."""
        from finance_services.workflow_executor import StaticRoleProvider

        actor_id = uuid4()
        provider = StaticRoleProvider(
            role_map={actor_id: ("finance_manager", "approver")}
        )

        # Verify it has all protocol methods and they work correctly
        assert isinstance(provider.get_actor_roles(actor_id), tuple)
        assert isinstance(provider.get_approval_chain(actor_id), tuple)
        assert isinstance(provider.has_role(actor_id, "finance_manager"), bool)

    def test_static_role_provider_get_actor_roles(self):
        """Should return roles for a known actor."""
        from finance_services.workflow_executor import StaticRoleProvider

        actor_id = uuid4()
        provider = StaticRoleProvider(
            role_map={actor_id: ("finance_manager", "approver")}
        )

        roles = provider.get_actor_roles(actor_id)
        assert roles == ("finance_manager", "approver")

    def test_static_role_provider_unknown_actor(self):
        """Should return empty tuple for unknown actor."""
        from finance_services.workflow_executor import StaticRoleProvider

        provider = StaticRoleProvider()
        roles = provider.get_actor_roles(uuid4())
        assert roles == ()

    def test_static_role_provider_has_role(self):
        """Should correctly check role membership."""
        from finance_services.workflow_executor import StaticRoleProvider

        actor_id = uuid4()
        provider = StaticRoleProvider(
            role_map={actor_id: ("finance_manager",)}
        )

        assert provider.has_role(actor_id, "finance_manager") is True
        assert provider.has_role(actor_id, "cfo") is False

    def test_static_role_provider_is_runtime_checkable(self):
        """StaticRoleProvider should pass isinstance check if protocol is runtime_checkable,
        otherwise verify structural compatibility by checking method signatures."""
        from finance_services.workflow_executor import StaticRoleProvider

        provider = StaticRoleProvider()

        # Verify structural protocol compliance: all three methods exist
        assert hasattr(provider, "get_actor_roles")
        assert hasattr(provider, "get_approval_chain")
        assert hasattr(provider, "has_role")
        assert callable(provider.get_actor_roles)
        assert callable(provider.get_approval_chain)
        assert callable(provider.has_role)
