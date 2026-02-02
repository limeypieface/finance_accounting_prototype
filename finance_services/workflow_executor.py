"""
finance_services.workflow_executor -- Workflow transition execution.

Responsibility:
    Executes state transitions with approval gate enforcement.
    Thin coordinator (AL-4) -- delegates rule evaluation to the pure
    approval engine, persistence to ApprovalService, role checks to
    OrgHierarchyProvider.

Architecture position:
    Services layer.  May import from finance_engines/ (pure engines)
    and finance_kernel/ (domain, services, models).

Invariants enforced:
    AL-4 -- Executor is a thin coordinator.  No Decimal comparison logic,
            no direct ORM queries.
    AL-6 -- Rule ordering delegated to engine (select_matching_rule).
    AL-9 -- Role diversity delegated to engine (evaluate_approval_status).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from finance_engines.approval import (
    evaluate_approval_requirement,
    select_matching_rule,
    validate_actor_authority,
)
from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalStatus,
    TransitionResult,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.exceptions import UnauthorizedApproverError
from finance_kernel.services.approval_service import ApprovalService

logger = logging.getLogger("services.workflow_executor")


# ---------------------------------------------------------------------------
# Structural protocols for module-level workflow types. Modules may use
# canonical Guard/Transition/Workflow from finance_kernel.domain.workflow
# (Phase 10) or declare their own compatible types.
# ---------------------------------------------------------------------------


@runtime_checkable
class TransitionLike(Protocol):
    from_state: str
    to_state: str
    action: str
    posts_entry: bool
    # Phase 10: optional; when present, transition is gated by approval engine
    requires_approval: bool = False
    approval_policy: Any | None = None


@runtime_checkable
class WorkflowLike(Protocol):
    name: str
    transitions: tuple


# ---------------------------------------------------------------------------
# Default OrgHierarchyProvider (static dict-based)
# ---------------------------------------------------------------------------


class StaticRoleProvider:
    """Default OrgHierarchyProvider backed by a simple dict.

    Satisfies the OrgHierarchyProvider protocol from domain/approval.py.
    Can be replaced with a database-backed or LDAP-backed implementation.
    """

    def __init__(self, role_map: dict[UUID, tuple[str, ...]] | None = None) -> None:
        self._role_map: dict[UUID, tuple[str, ...]] = role_map or {}

    def get_actor_roles(self, actor_id: UUID) -> tuple[str, ...]:
        return self._role_map.get(actor_id, ())

    def get_approval_chain(self, actor_id: UUID) -> tuple[UUID, ...]:
        return ()

    def has_role(self, actor_id: UUID, role: str) -> bool:
        return role in self._role_map.get(actor_id, ())


# ---------------------------------------------------------------------------
# WorkflowExecutor
# ---------------------------------------------------------------------------


class WorkflowExecutor:
    """Executes workflow transitions with approval gate enforcement.

    AL-4: Thin coordinator -- delegates all domain logic to the approval
    engine, all persistence to ApprovalService, and all role resolution
    to OrgHierarchyProvider.
    """

    def __init__(
        self,
        approval_service: ApprovalService,
        approval_policies: dict[str, ApprovalPolicy] | None = None,
        clock: Clock | None = None,
        org_hierarchy: StaticRoleProvider | None = None,
    ) -> None:
        self._approval_service = approval_service
        self._policies = approval_policies or {}
        self._clock = clock or SystemClock()
        self._org_hierarchy = org_hierarchy or StaticRoleProvider()

    def execute_transition(
        self,
        workflow: WorkflowLike,
        entity_type: str,
        entity_id: UUID,
        current_state: str,
        action: str,
        actor_id: UUID,
        actor_role: str,
        amount: Decimal | None = None,
        currency: str = "USD",
        context: dict[str, Any] | None = None,
        approval_request_id: UUID | None = None,
    ) -> TransitionResult:
        """Execute a state transition, checking approval gates.

        Returns TransitionResult indicating success, approval-required, or
        failure.  If approval is required and not yet granted, an approval
        request is created and returned in the result.
        """
        # 1. Find the matching transition in the workflow
        transition = self._find_transition(workflow, current_state, action)
        if transition is None:
            return TransitionResult(
                success=False,
                reason=f"No transition from '{current_state}' via action '{action}' "
                       f"in workflow '{workflow.name}'",
            )

        # 2. Look up approval policy for this workflow/action
        policy = self._resolve_policy(workflow.name, action)

        # 3. If no policy, transition proceeds directly
        if policy is None:
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                posts_entry=transition.posts_entry,
                reason="No approval policy -- transition allowed",
            )

        # 4. If caller provided an approval_request_id, check if it's approved
        if approval_request_id is not None:
            return self._check_existing_approval(
                approval_request_id=approval_request_id,
                transition=transition,
            )

        # 5. Evaluate whether approval is needed (delegated to pure engine)
        evaluation = evaluate_approval_requirement(policy, amount, context)

        if not evaluation.needs_approval:
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                posts_entry=transition.posts_entry,
                reason=evaluation.reason,
            )

        # 6. Auto-approved case
        if evaluation.auto_approved and evaluation.matched_rule is not None:
            request = self._approval_service.create_request(
                workflow_name=workflow.name,
                entity_type=entity_type,
                entity_id=entity_id,
                transition_action=action,
                from_state=current_state,
                to_state=transition.to_state,
                policy=policy,
                matched_rule_name=evaluation.matched_rule.rule_name,
                requestor_id=actor_id,
                amount=amount,
                currency=currency,
            )
            self._approval_service.record_auto_approval(
                request_id=request.request_id,
                matched_rule_name=evaluation.matched_rule.rule_name,
                threshold_value=evaluation.matched_rule.auto_approve_below or Decimal(0),
                evaluated_amount=amount or Decimal(0),
                policy=policy,
                actor_id=actor_id,
            )
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                approval_request_id=request.request_id,
                posts_entry=transition.posts_entry,
                reason=evaluation.reason,
            )

        # 7. Approval required -- create request and block
        if evaluation.matched_rule is None:
            return TransitionResult(
                success=False,
                reason="Approval required but no matching rule found",
            )

        request = self._approval_service.create_request(
            workflow_name=workflow.name,
            entity_type=entity_type,
            entity_id=entity_id,
            transition_action=action,
            from_state=current_state,
            to_state=transition.to_state,
            policy=policy,
            matched_rule_name=evaluation.matched_rule.rule_name,
            requestor_id=actor_id,
            amount=amount,
            currency=currency,
        )

        logger.info(
            "transition_blocked_approval_required",
            extra={
                "workflow": workflow.name,
                "action": action,
                "entity_id": str(entity_id),
                "request_id": str(request.request_id),
                "matched_rule": evaluation.matched_rule.rule_name,
            },
        )

        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=request.request_id,
            reason=f"Approval required: {evaluation.reason}",
        )

    def resume_after_approval(
        self,
        approval_request_id: UUID,
    ) -> TransitionResult:
        """Resume a transition that was blocked pending approval.

        Checks if the approval request is approved/auto-approved, then
        returns a success result so the caller can proceed with the
        state change and optional posting.
        """
        request = self._approval_service.get_request(approval_request_id)

        if request.status in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
            return TransitionResult(
                success=True,
                new_state=request.to_state,
                posts_entry=False,  # Caller determines from workflow
                reason=f"Approved (status={request.status.value})",
            )

        if request.status == ApprovalStatus.REJECTED:
            return TransitionResult(
                success=False,
                reason="Approval request was rejected",
            )

        if request.status in (ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED):
            return TransitionResult(
                success=False,
                reason=f"Approval request is {request.status.value}",
            )

        # Still pending/escalated
        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=approval_request_id,
            reason=f"Approval still pending (status={request.status.value})",
        )

    def record_approval_decision(
        self,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        decision: ApprovalDecision,
        comment: str = "",
    ) -> TransitionResult:
        """Record an approval decision and return the resulting state.

        Validates actor authority against the matched rule before
        delegating to ApprovalService.
        """
        request = self._approval_service.get_request(request_id)

        # Resolve the policy and matched rule for authority check
        policy = self._resolve_policy(request.workflow_name, request.transition_action)
        if policy is not None and request.matched_rule is not None:
            rule = select_matching_rule(policy.rules, request.amount)
            if rule is not None and not validate_actor_authority(actor_role, rule):
                raise UnauthorizedApproverError(str(actor_id), actor_role)

        # Record the decision
        updated = self._approval_service.record_decision(
            request_id=request_id,
            actor_id=actor_id,
            actor_role=actor_role,
            decision=decision,
            comment=comment,
            active_policy=policy,
        )

        if updated.status == ApprovalStatus.APPROVED:
            return TransitionResult(
                success=True,
                new_state=updated.to_state,
                reason="Approved",
            )

        if updated.status == ApprovalStatus.REJECTED:
            return TransitionResult(
                success=False,
                reason="Rejected",
            )

        # Still pending more approvals or escalated
        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=request_id,
            reason=f"Decision recorded; status={updated.status.value}",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_transition(
        self,
        workflow: WorkflowLike,
        current_state: str,
        action: str,
    ) -> TransitionLike | None:
        """Find a matching transition in the workflow."""
        for t in workflow.transitions:
            if t.from_state == current_state and t.action == action:
                return t
        return None

    def _resolve_policy(
        self,
        workflow_name: str,
        action: str,
    ) -> ApprovalPolicy | None:
        """Resolve the approval policy for a workflow/action pair.

        Checks for action-specific policy first, then workflow-level.
        """
        key = f"{workflow_name}:{action}"
        if key in self._policies:
            return self._policies[key]
        if workflow_name in self._policies:
            return self._policies[workflow_name]
        return None

    def _check_existing_approval(
        self,
        approval_request_id: UUID,
        transition: TransitionLike,
    ) -> TransitionResult:
        """Check if a pre-existing approval request is resolved."""
        request = self._approval_service.get_request(approval_request_id)

        if request.status in (ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED):
            return TransitionResult(
                success=True,
                new_state=transition.to_state,
                posts_entry=transition.posts_entry,
                reason=f"Pre-approved (status={request.status.value})",
            )

        if request.status == ApprovalStatus.REJECTED:
            return TransitionResult(
                success=False,
                reason="Approval request was rejected",
            )

        return TransitionResult(
            success=False,
            approval_required=True,
            approval_request_id=approval_request_id,
            reason=f"Approval not yet resolved (status={request.status.value})",
        )
