"""
Approval domain types (``finance_kernel.domain.approval``).

Responsibility
--------------
Pure value objects for the modular approval engine.  Defines the
approval lifecycle state machine, policy/rule data, request/decision
records, and evaluation results.

Architecture position
---------------------
**Kernel domain layer** -- pure value objects.  ZERO I/O.  No imports
from ``db/``, ``services/``, ``selectors/``, or outer layers.  May
import only from ``domain/values`` and ``domain/clock``.

Invariants enforced
-------------------
* AL-1: Lifecycle state machine -- ``APPROVAL_TRANSITIONS`` defines
  the only valid status transitions.  Terminal states have no outgoing
  edges.
* AL-2: Policy version snapshot -- ``ApprovalRequest`` captures
  ``policy_version`` and ``policy_hash`` at creation time.
* AL-3: Currency normalization -- ``ApprovalRequest.currency`` must
  match the policy's ``policy_currency``.
* AL-6: Deterministic rule ordering -- rules evaluated by ``priority``.
* AL-8: Request tamper evidence -- ``original_request_hash`` is a
  SHA-256 computed at creation, verified on every load.
* AL-9: Role diversity -- ``ApprovalRule.require_distinct_roles``
  option for distinct-role counting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol
from uuid import UUID, uuid4


# =========================================================================
# Approval Status Lifecycle (AL-1)
# =========================================================================


class ApprovalStatus(str, Enum):
    """Approval request lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    AUTO_APPROVED = "auto_approved"


APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset({
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.ESCALATED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
        ApprovalStatus.AUTO_APPROVED,
    }),
    ApprovalStatus.ESCALATED: frozenset({
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
    }),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.CANCELLED: frozenset(),
    ApprovalStatus.AUTO_APPROVED: frozenset(),
}

TERMINAL_APPROVAL_STATUSES: frozenset[ApprovalStatus] = frozenset({
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
    ApprovalStatus.CANCELLED,
    ApprovalStatus.AUTO_APPROVED,
})


class ApprovalDecision(str, Enum):
    """Decision types that an approver can make."""

    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


# =========================================================================
# Policy and Rule Types
# =========================================================================


@dataclass(frozen=True)
class ApprovalRule:
    """A single rule in an approval policy.

    Rules are matched by amount threshold and optional guard expression.
    ``priority`` determines evaluation order (AL-6): lower number = higher
    priority, first match wins.
    """

    rule_name: str
    priority: int
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    required_roles: tuple[str, ...] = ()
    min_approvers: int = 1
    require_distinct_roles: bool = False
    guard_expression: str | None = None
    auto_approve_below: Decimal | None = None
    escalation_timeout_hours: int | None = None


@dataclass(frozen=True)
class ApprovalPolicy:
    """A named, versioned approval policy.

    Policies are compiled from YAML config and matched to workflows by
    ``applies_to_workflow`` + optional ``applies_to_action``.
    """

    policy_name: str
    version: int
    applies_to_workflow: str
    applies_to_action: str | None = None
    rules: tuple[ApprovalRule, ...] = ()
    effective_from: date | None = None
    effective_to: date | None = None
    policy_currency: str | None = None
    policy_hash: str | None = None


# =========================================================================
# Request and Decision Records
# =========================================================================


@dataclass(frozen=True)
class ApprovalDecisionRecord:
    """Record of a single approval decision. Immutable."""

    decision_id: UUID
    request_id: UUID
    actor_id: UUID
    actor_role: str
    decision: ApprovalDecision
    comment: str = ""
    decided_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    """Immutable snapshot of an approval request.

    AL-2: ``policy_version`` and ``policy_hash`` are snapshotted at
    creation and never modified.
    AL-3: ``currency`` must match the policy's ``policy_currency``.
    AL-8: ``original_request_hash`` is verified on every load.
    """

    request_id: UUID
    workflow_name: str
    entity_type: str
    entity_id: UUID
    transition_action: str
    from_state: str
    to_state: str
    policy_name: str
    policy_version: int
    policy_hash: str | None = None
    amount: Decimal | None = None
    currency: str = "USD"
    requestor_id: UUID = field(default_factory=uuid4)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    matched_rule: str | None = None
    original_request_hash: str | None = None
    decisions: tuple[ApprovalDecisionRecord, ...] = ()


# =========================================================================
# Evaluation Result
# =========================================================================


@dataclass(frozen=True)
class ApprovalEvaluation:
    """Result of evaluating whether a transition needs/has approval."""

    needs_approval: bool
    is_approved: bool = False
    is_rejected: bool = False
    matched_rule: ApprovalRule | None = None
    required_approvers: int = 0
    current_approvers: int = 0
    auto_approved: bool = False
    reason: str = ""


# =========================================================================
# Transition Result
# =========================================================================


@dataclass(frozen=True)
class TransitionResult:
    """Result of executing a workflow transition."""

    success: bool
    new_state: str | None = None
    approval_required: bool = False
    approval_request_id: UUID | None = None
    posts_entry: bool = False
    reason: str = ""


# =========================================================================
# OrgHierarchyProvider Protocol
# =========================================================================


class OrgHierarchyProvider(Protocol):
    """Pluggable interface for organizational hierarchy lookups."""

    def get_actor_roles(self, actor_id: UUID) -> tuple[str, ...]:
        """Return all roles for an actor."""
        ...

    def get_approval_chain(self, actor_id: UUID) -> tuple[UUID, ...]:
        """Return the chain of approvers above this actor."""
        ...

    def has_role(self, actor_id: UUID, role: str) -> bool:
        """Check if actor has a specific role."""
        ...
