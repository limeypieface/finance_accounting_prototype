"""
Shared helpers for module posting flows.

Used by finance_modules/*/service.py to reduce duplication when handling
workflow guard results and session commit/rollback after post_event.

Architecture: Modules layer. Imports only from finance_kernel (domain, services).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from finance_kernel.domain.approval import TransitionResult
from finance_kernel.services.module_posting_service import (
    ModulePostingResult,
    ModulePostingStatus,
)


@runtime_checkable
class WorkflowExecutorLike(Protocol):
    """Protocol for workflow executor used by run_workflow_guard."""

    def execute_transition(
        self,
        workflow: Any,
        entity_type: str,
        entity_id: UUID,
        current_state: str,
        action: str,
        actor_id: UUID,
        actor_role: str,
        amount: Decimal | None = ...,
        currency: str = ...,
        context: dict[str, Any] | None = ...,
        approval_request_id: UUID | None = ...,
        outcome_sink: Callable[[dict], None] | None = ...,
    ) -> TransitionResult: ...


def guard_failure_result(
    transition_result: TransitionResult,
    event_id: UUID | None = None,
) -> ModulePostingResult | None:
    """Convert a failed transition into a ModulePostingResult for guard block/guard reject.

    Returns None if transition_result.success is True (caller should proceed).
    Returns a ModulePostingResult with GUARD_BLOCKED or GUARD_REJECTED when the
    transition failed.
    """
    if transition_result.success:
        return None
    status = (
        ModulePostingStatus.GUARD_BLOCKED
        if transition_result.approval_required
        else ModulePostingStatus.GUARD_REJECTED
    )
    return ModulePostingResult(
        status=status,
        event_id=event_id or uuid4(),
        message=transition_result.reason or "Guard not satisfied",
    )


def run_workflow_guard(
    executor: WorkflowExecutorLike,
    workflow: Any,
    entity_type: str,
    entity_id: UUID,
    *,
    current_state: str = "draft",
    action: str = "post",
    actor_id: UUID,
    actor_role: str = "",
    amount: Decimal | None = None,
    currency: str = "USD",
    context: dict[str, Any] | None = None,
    approval_request_id: UUID | None = None,
    outcome_sink: Callable[[dict], None] | None = None,
    event_id: UUID | None = None,
) -> ModulePostingResult | None:
    """Run execute_transition and return a guard-failure result if the transition failed.

    Returns None if the transition succeeded (caller should proceed to build payload and
    post_event). Returns a ModulePostingResult (GUARD_BLOCKED or GUARD_REJECTED) if the
    transition failed, so the caller can return it immediately.

    Use this to replace the repeated pattern: execute_transition(...); failure = guard_failure_result(...); if failure is not None: return failure.
    """
    transition_result = executor.execute_transition(
        workflow=workflow,
        entity_type=entity_type,
        entity_id=entity_id,
        current_state=current_state,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        amount=amount,
        currency=currency,
        context=context,
        approval_request_id=approval_request_id,
        outcome_sink=outcome_sink,
    )
    return guard_failure_result(transition_result, event_id=event_id)


def commit_or_rollback(session: Session, result: ModulePostingResult) -> None:
    """Commit the session if posting succeeded, otherwise rollback (R7 transaction boundary)."""
    if result.is_success:
        session.commit()
    else:
        session.rollback()
