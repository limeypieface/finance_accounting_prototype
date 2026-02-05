"""
Tests for WorkflowExecutor (finance_services.workflow_executor).

Covers:
- execute_transition() with no policy, auto-approve, approval required,
  and pre-existing approval request
- resume_after_approval() for all terminal and non-terminal statuses
- record_approval_decision() for approve, reject, and still-pending cases
- _resolve_policy() priority: action-specific > workflow-level > None

Test infrastructure:
- PostgreSQL with session fixture (auto-rollback via savepoint)
- DeterministicClock for reproducible timestamps
- AuditorService for audit trail
- ApprovalService + WorkflowExecutor wired via fixtures
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRule,
    ApprovalStatus,
)
from finance_config.compiler import CompiledRbacConfig
from finance_kernel.services.approval_service import ApprovalService
from finance_services.workflow_executor import StaticRoleProvider, WorkflowExecutor


# ---------------------------------------------------------------------------
# Structural fakes for WorkflowLike / TransitionLike protocols
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeTransition:
    from_state: str
    to_state: str
    action: str
    posts_entry: bool


@dataclass(frozen=True)
class FakeWorkflow:
    name: str
    transitions: tuple


# ---------------------------------------------------------------------------
# Reusable test data builders
# ---------------------------------------------------------------------------


def _simple_workflow(
    name: str = "invoice_wf",
    transitions: tuple | None = None,
) -> FakeWorkflow:
    if transitions is None:
        transitions = (
            FakeTransition(
                from_state="draft",
                to_state="approved",
                action="approve",
                posts_entry=True,
            ),
            FakeTransition(
                from_state="draft",
                to_state="submitted",
                action="submit",
                posts_entry=False,
            ),
            FakeTransition(
                from_state="submitted",
                to_state="approved",
                action="approve",
                posts_entry=True,
            ),
        )
    return FakeWorkflow(name=name, transitions=transitions)


def _auto_approve_policy(
    workflow_name: str = "invoice_wf",
    action: str | None = None,
    threshold: Decimal = Decimal("1000"),
) -> ApprovalPolicy:
    """Policy with a single rule that auto-approves below threshold."""
    return ApprovalPolicy(
        policy_name="auto_small",
        version=1,
        applies_to_workflow=workflow_name,
        applies_to_action=action,
        rules=(
            ApprovalRule(
                rule_name="small_amount",
                priority=1,
                min_amount=Decimal("0"),
                max_amount=Decimal("100000"),
                auto_approve_below=threshold,
                required_roles=("manager",),
            ),
        ),
    )


def _manual_approval_policy(
    workflow_name: str = "invoice_wf",
    action: str | None = None,
    required_roles: tuple[str, ...] = ("manager",),
) -> ApprovalPolicy:
    """Policy with a single rule that always requires manual approval."""
    return ApprovalPolicy(
        policy_name="manual_review",
        version=1,
        applies_to_workflow=workflow_name,
        applies_to_action=action,
        rules=(
            ApprovalRule(
                rule_name="standard_review",
                priority=1,
                min_amount=Decimal("0"),
                max_amount=Decimal("1000000"),
                required_roles=required_roles,
                min_approvers=1,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def approval_service(session, auditor_service, deterministic_clock):
    return ApprovalService(session, auditor_service, deterministic_clock)


@pytest.fixture
def workflow_executor(approval_service, deterministic_clock):
    return WorkflowExecutor(
        approval_service=approval_service,
        approval_policies={},
        clock=deterministic_clock,
    )


@pytest.fixture
def make_executor(approval_service, deterministic_clock):
    """Factory fixture: build a WorkflowExecutor with custom policies."""

    def _make(policies: dict[str, ApprovalPolicy]) -> WorkflowExecutor:
        return WorkflowExecutor(
            approval_service=approval_service,
            approval_policies=policies,
            clock=deterministic_clock,
        )

    return _make


# =========================================================================
# execute_transition() -- no policy
# =========================================================================


class TestExecuteTransitionNoPolicy:
    """execute_transition() when no approval policy is configured."""

    def test_no_policy_transition_succeeds(self, workflow_executor):
        """No approval policy -> transition succeeds immediately."""
        wf = _simple_workflow()
        result = workflow_executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="accountant",
            amount=Decimal("500.00"),
        )

        assert result.success is True
        assert result.new_state == "approved"
        assert result.posts_entry is True
        assert result.approval_required is False
        assert "No approval policy" in result.reason

    def test_invalid_action_fails(self, workflow_executor):
        """Action not matching any transition -> failure with reason."""
        wf = _simple_workflow()
        result = workflow_executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="nonexistent_action",
            actor_id=uuid4(),
            actor_role="accountant",
        )

        assert result.success is False
        assert result.new_state is None
        assert "No transition" in result.reason
        assert "nonexistent_action" in result.reason

    def test_invalid_state_fails(self, workflow_executor):
        """Current state not matching any transition -> failure."""
        wf = _simple_workflow()
        result = workflow_executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="closed",
            action="approve",
            actor_id=uuid4(),
            actor_role="accountant",
        )

        assert result.success is False
        assert "No transition" in result.reason
        assert "'closed'" in result.reason


# =========================================================================
# execute_transition() -- with policy, auto-approve
# =========================================================================


class TestExecuteTransitionAutoApprove:
    """execute_transition() when amount is below auto_approve_below threshold."""

    def test_auto_approve_below_threshold(self, make_executor):
        """Amount below auto_approve_below -> auto-approved, success=True."""
        policy = _auto_approve_policy(threshold=Decimal("1000"))
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("500.00"),
        )

        assert result.success is True
        assert result.new_state == "approved"
        assert result.posts_entry is True
        assert result.approval_request_id is not None
        assert "Auto-approved" in result.reason

    def test_auto_approve_returns_request_id(self, make_executor):
        """Auto-approval still creates and returns an approval request ID."""
        policy = _auto_approve_policy(threshold=Decimal("5000"))
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("100.00"),
        )

        assert result.success is True
        assert result.approval_request_id is not None


# =========================================================================
# execute_transition() -- with policy, approval required
# =========================================================================


class TestExecuteTransitionApprovalRequired:
    """execute_transition() when amount exceeds threshold and manual approval is needed."""

    def test_approval_required_above_threshold(self, make_executor):
        """Amount above auto_approve_below -> blocked, approval_required=True."""
        policy = _auto_approve_policy(threshold=Decimal("1000"))
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("5000.00"),
        )

        assert result.success is False
        assert result.approval_required is True
        assert result.approval_request_id is not None
        assert "Approval required" in result.reason

    def test_manual_policy_always_requires_approval(self, make_executor):
        """Manual policy with no auto-approve -> always blocked."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("100.00"),
        )

        assert result.success is False
        assert result.approval_required is True
        assert result.approval_request_id is not None


# =========================================================================
# execute_transition() -- with pre-existing approval
# =========================================================================


class TestExecuteTransitionPreExistingApproval:
    """execute_transition() when caller passes an existing approval_request_id."""

    def test_approved_request_succeeds(self, make_executor, approval_service):
        """Pass approval_request_id that is APPROVED -> success=True."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()
        entity_id = uuid4()
        requestor_id = uuid4()

        # First call: create the approval request (blocked)
        result1 = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("500.00"),
        )
        assert result1.success is False
        request_id = result1.approval_request_id

        # Approve the request via ApprovalService
        approver_id = uuid4()
        approval_service.record_decision(
            request_id=request_id,
            actor_id=approver_id,
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
            comment="Looks good",
            active_policy=policy,
        )

        # Second call: pass the approved request_id
        result2 = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("500.00"),
            approval_request_id=request_id,
        )

        assert result2.success is True
        assert result2.new_state == "approved"
        assert result2.posts_entry is True
        assert "Pre-approved" in result2.reason

    def test_rejected_request_fails(self, make_executor, approval_service):
        """Pass approval_request_id that is REJECTED -> success=False."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()
        entity_id = uuid4()
        requestor_id = uuid4()

        # Create the request
        result1 = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("500.00"),
        )
        request_id = result1.approval_request_id

        # Reject the request
        approver_id = uuid4()
        approval_service.record_decision(
            request_id=request_id,
            actor_id=approver_id,
            actor_role="manager",
            decision=ApprovalDecision.REJECT,
            comment="Not justified",
            active_policy=policy,
        )

        # Pass the rejected request_id
        result2 = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("500.00"),
            approval_request_id=request_id,
        )

        assert result2.success is False
        assert "rejected" in result2.reason.lower()

    def test_pending_request_still_requires_approval(
        self, make_executor, approval_service,
    ):
        """Pass approval_request_id that is PENDING -> success=False, approval_required=True."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()
        entity_id = uuid4()
        requestor_id = uuid4()

        # Create the request (blocked, stays PENDING)
        result1 = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("500.00"),
        )
        request_id = result1.approval_request_id

        # Pass the still-pending request_id
        result2 = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("500.00"),
            approval_request_id=request_id,
        )

        assert result2.success is False
        assert result2.approval_required is True
        assert result2.approval_request_id == request_id
        assert "not yet resolved" in result2.reason.lower()


# =========================================================================
# resume_after_approval()
# =========================================================================


class TestResumeAfterApproval:
    """resume_after_approval() for all possible request statuses."""

    def _create_pending_request(
        self, make_executor, approval_service, entity_id=None,
    ):
        """Helper: create a pending approval request and return (executor, request_id)."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()
        entity_id = entity_id or uuid4()
        requestor_id = uuid4()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=entity_id,
            current_state="draft",
            action="approve",
            actor_id=requestor_id,
            actor_role="clerk",
            amount=Decimal("2000.00"),
        )
        return executor, result.approval_request_id, policy

    def test_resume_approved_succeeds(
        self, make_executor, approval_service,
    ):
        """Approved request -> success=True, new_state set."""
        executor, request_id, policy = self._create_pending_request(
            make_executor, approval_service,
        )

        # Approve the request
        approval_service.record_decision(
            request_id=request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
            active_policy=policy,
        )

        result = executor.resume_after_approval(request_id)
        assert result.success is True
        assert result.new_state == "approved"
        assert "Approved" in result.reason

    def test_resume_rejected_fails(
        self, make_executor, approval_service,
    ):
        """Rejected request -> success=False."""
        executor, request_id, policy = self._create_pending_request(
            make_executor, approval_service,
        )

        # Reject the request
        approval_service.record_decision(
            request_id=request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.REJECT,
            active_policy=policy,
        )

        result = executor.resume_after_approval(request_id)
        assert result.success is False
        assert "rejected" in result.reason.lower()

    def test_resume_expired_fails(
        self, make_executor, approval_service, deterministic_clock,
    ):
        """Expired request -> success=False."""
        executor, request_id, _policy = self._create_pending_request(
            make_executor, approval_service,
        )

        # Advance clock so created_at < cutoff when timeout_hours=0 (cutoff = as_of)
        deterministic_clock.advance(1)
        approval_service.expire_stale_requests(
            as_of=deterministic_clock.now(),
            timeout_hours=0,  # expire immediately
        )

        result = executor.resume_after_approval(request_id)
        assert result.success is False
        assert "expired" in result.reason.lower()

    def test_resume_cancelled_fails(
        self, make_executor, approval_service,
    ):
        """Cancelled request -> success=False."""
        executor, request_id, _policy = self._create_pending_request(
            make_executor, approval_service,
        )

        # Cancel the request
        approval_service.cancel_request(request_id, actor_id=uuid4())

        result = executor.resume_after_approval(request_id)
        assert result.success is False
        assert "cancelled" in result.reason.lower()

    def test_resume_still_pending(
        self, make_executor, approval_service,
    ):
        """Still pending -> success=False, approval_required=True."""
        executor, request_id, _policy = self._create_pending_request(
            make_executor, approval_service,
        )

        result = executor.resume_after_approval(request_id)
        assert result.success is False
        assert result.approval_required is True
        assert result.approval_request_id == request_id
        assert "pending" in result.reason.lower()


# =========================================================================
# record_approval_decision()
# =========================================================================


class TestRecordApprovalDecision:
    """record_approval_decision() for approve, reject, and pending results."""

    def _create_pending_request(self, make_executor, approval_service):
        """Helper: create a pending approval request."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("2000.00"),
        )
        return executor, result.approval_request_id, policy

    def test_approve_decision_succeeds(self, make_executor, approval_service):
        """Valid approval -> success=True, new_state set."""
        executor, request_id, _policy = self._create_pending_request(
            make_executor, approval_service,
        )

        result = executor.record_approval_decision(
            request_id=request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
            comment="Approved for processing",
        )

        assert result.success is True
        assert result.new_state == "approved"
        assert "Approved" in result.reason

    def test_reject_decision_fails(self, make_executor, approval_service):
        """Rejection -> success=False."""
        executor, request_id, _policy = self._create_pending_request(
            make_executor, approval_service,
        )

        result = executor.record_approval_decision(
            request_id=request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.REJECT,
            comment="Insufficient justification",
        )

        assert result.success is False
        assert "Rejected" in result.reason

    def test_escalation_still_pending(self, make_executor, approval_service):
        """Escalation -> success=False, approval_required=True."""
        executor, request_id, _policy = self._create_pending_request(
            make_executor, approval_service,
        )

        result = executor.record_approval_decision(
            request_id=request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.ESCALATE,
            comment="Needs VP review",
        )

        assert result.success is False
        assert result.approval_required is True
        assert result.approval_request_id == request_id
        assert "escalated" in result.reason.lower()

    def test_unauthorized_approver_bug_documented(
        self, make_executor, approval_service,
    ):
        """Document the bug at line 305: UnauthorizedApproverError called with 2 args, needs 3.

        The constructor expects (actor_id, actor_role, required_roles) but
        the code only passes (actor_id, actor_role). This test documents
        the bug by verifying it raises TypeError instead of
        UnauthorizedApproverError.
        """
        # Build a policy with required_roles that do NOT include 'intern'
        policy = _manual_approval_policy(required_roles=("director", "vp"))
        executor = make_executor({"invoice_wf:approve": policy})
        wf = _simple_workflow()

        # Create a pending request
        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="approve",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("2000.00"),
        )
        request_id = result.approval_request_id

        # Attempt to approve with an unauthorized role.
        # BUG: line 305 calls UnauthorizedApproverError(str(actor_id), actor_role)
        # but the constructor requires 3 positional args: (actor_id, actor_role, required_roles).
        # This causes a TypeError rather than the intended UnauthorizedApproverError.
        with pytest.raises(TypeError):
            executor.record_approval_decision(
                request_id=request_id,
                actor_id=uuid4(),
                actor_role="intern",
                decision=ApprovalDecision.APPROVE,
            )


# =========================================================================
# _resolve_policy()
# =========================================================================


class TestResolvePolicy:
    """_resolve_policy() priority: action-specific > workflow-level > None."""

    def test_action_specific_policy_found(self, make_executor):
        """Action-specific key 'workflow:action' is preferred."""
        action_policy = _manual_approval_policy()
        workflow_policy = _auto_approve_policy(threshold=Decimal("999999"))
        executor = make_executor({
            "invoice_wf:approve": action_policy,
            "invoice_wf": workflow_policy,
        })

        resolved = executor._resolve_policy("invoice_wf", "approve")
        assert resolved is action_policy

    def test_workflow_level_policy_fallback(self, make_executor):
        """Workflow-level key used when no action-specific key exists."""
        workflow_policy = _manual_approval_policy()
        executor = make_executor({
            "invoice_wf": workflow_policy,
        })

        resolved = executor._resolve_policy("invoice_wf", "submit")
        assert resolved is workflow_policy

    def test_no_policy_returns_none(self, make_executor):
        """No matching key at all -> None."""
        executor = make_executor({})

        resolved = executor._resolve_policy("invoice_wf", "approve")
        assert resolved is None

    def test_action_specific_does_not_match_other_actions(self, make_executor):
        """Action-specific policy for 'approve' does not match 'submit'."""
        action_policy = _manual_approval_policy()
        executor = make_executor({
            "invoice_wf:approve": action_policy,
        })

        resolved = executor._resolve_policy("invoice_wf", "submit")
        assert resolved is None


# =========================================================================
# Integration / edge-case tests
# =========================================================================


class TestWorkflowExecutorIntegration:
    """Cross-cutting integration and edge-case tests."""

    def test_transition_without_amount(self, make_executor):
        """Transition with no amount and no policy -> succeeds."""
        executor = make_executor({})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="submit",
            actor_id=uuid4(),
            actor_role="clerk",
        )

        assert result.success is True
        assert result.new_state == "submitted"
        assert result.posts_entry is False

    def test_workflow_level_policy_triggers_approval(self, make_executor):
        """Workflow-level policy applies to any action in that workflow."""
        policy = _manual_approval_policy()
        executor = make_executor({"invoice_wf": policy})
        wf = _simple_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="submit",
            actor_id=uuid4(),
            actor_role="clerk",
            amount=Decimal("500.00"),
        )

        assert result.success is False
        assert result.approval_required is True


# =========================================================================
# execute_transition() -- RBAC enforcement
# =========================================================================


def _minimal_rbac_config() -> CompiledRbacConfig:
    """RBAC config that grants ap.invoice.enter to role ap_enter, nothing to viewer."""
    return CompiledRbacConfig(
        rbac_version="1",
        authority_role_required=False,
        multi_role_actions_allowed=True,
        role_permissions=(
            ("ap_enter", frozenset({"ap.invoice.enter"})),
            ("viewer", frozenset()),
        ),
        role_conflicts=(),
        permission_conflicts_hard=(),
        permission_conflicts_soft=(),
        lifecycle_conflicts=(),
        override_roles=(),
        inheritance_depth_limit=5,
    )


def _ap_invoice_submit_workflow():
    """Workflow that maps to permission ap.invoice.enter for action submit."""
    return FakeWorkflow(
        name="ap_invoice",
        transitions=(
            FakeTransition(
                from_state="draft",
                to_state="submitted",
                action="submit",
                posts_entry=False,
            ),
        ),
    )


class TestExecuteTransitionRbac:
    """execute_transition() when compiled_rbac is set enforces permission checks."""

    def test_rbac_allows_when_actor_has_permission(self, approval_service, deterministic_clock):
        """Actor with role that has ap.invoice.enter can execute ap_invoice/submit."""
        actor_id = uuid4()
        org = StaticRoleProvider(role_map={actor_id: ("ap_enter",)})
        executor = WorkflowExecutor(
            approval_service=approval_service,
            approval_policies={},
            clock=deterministic_clock,
            org_hierarchy=org,
            compiled_rbac=_minimal_rbac_config(),
        )
        wf = _ap_invoice_submit_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="submit",
            actor_id=actor_id,
            actor_role="",
        )

        assert result.success is True
        assert result.new_state == "submitted"

    def test_rbac_denies_when_actor_lacks_permission(self, approval_service, deterministic_clock):
        """Actor with role that lacks permission is denied."""
        actor_id = uuid4()
        org = StaticRoleProvider(role_map={actor_id: ("viewer",)})
        executor = WorkflowExecutor(
            approval_service=approval_service,
            approval_policies={},
            clock=deterministic_clock,
            org_hierarchy=org,
            compiled_rbac=_minimal_rbac_config(),
        )
        wf = _ap_invoice_submit_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="submit",
            actor_id=actor_id,
            actor_role="",
        )

        assert result.success is False
        assert "RBAC" in result.reason
        assert "permission" in result.reason.lower()

    def test_rbac_skipped_when_compiled_rbac_none(self, approval_service, deterministic_clock):
        """When compiled_rbac is None, no RBAC check; transition can succeed."""
        actor_id = uuid4()
        org = StaticRoleProvider(role_map={actor_id: ("viewer",)})
        executor = WorkflowExecutor(
            approval_service=approval_service,
            approval_policies={},
            clock=deterministic_clock,
            org_hierarchy=org,
            compiled_rbac=None,
        )
        wf = _ap_invoice_submit_workflow()

        result = executor.execute_transition(
            workflow=wf,
            entity_type="Invoice",
            entity_id=uuid4(),
            current_state="draft",
            action="submit",
            actor_id=actor_id,
            actor_role="",
        )

        assert result.success is True
        assert result.new_state == "submitted"
