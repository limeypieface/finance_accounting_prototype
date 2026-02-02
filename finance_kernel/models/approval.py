"""
Module: finance_kernel.models.approval
Responsibility: ORM persistence for approval requests and decisions.

Architecture position: Kernel > Models.  May import from db/base.py only.

Invariants enforced:
    AL-1  -- Lifecycle state machine: DB check constraint limits status
             values; service layer enforces transition rules; DB trigger
             prevents mutation of terminal states.
    AL-7  -- Decision uniqueness: UNIQUE(request_id, actor_id) prevents
             same actor approving twice.
    AL-8  -- Request tamper evidence: original_request_hash is write-once.
    AL-10 -- Request idempotency: unique constraint prevents duplicate
             pending requests for the same entity/transition.
    AL-11 -- Covering index for get_pending_for_entity().

Failure modes:
    - IntegrityError on duplicate pending request (AL-10).
    - IntegrityError on duplicate actor decision (AL-7).
    - ImmutabilityViolationError on decision UPDATE/DELETE.

Audit relevance:
    Approval requests and decisions form the governance audit trail.
    Decisions are append-only.  Request status changes are lifecycle-
    constrained and audited via AuditorService.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import Base, UUIDString
from finance_kernel.exceptions import ImmutabilityViolationError

if TYPE_CHECKING:
    from finance_kernel.domain.approval import (
        ApprovalDecisionRecord,
        ApprovalRequest,
    )


class ApprovalRequestModel(Base):
    """Persistent approval request.

    Contract:
        Status transitions are lifecycle-constrained (AL-1).
        Terminal statuses (approved, rejected, expired, cancelled,
        auto_approved) cannot be changed once set.

    Guarantees:
        - policy_version and policy_hash are write-once (AL-2).
        - original_request_hash is write-once (AL-8).
        - No duplicate pending requests per entity/transition (AL-10).
    """

    __tablename__ = "approval_requests"

    __table_args__ = (
        # AL-1: Valid status values
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'escalated', "
            "'expired', 'cancelled', 'auto_approved')",
            name="ck_approval_requests_valid_status",
        ),
        # AL-10: Prevent duplicate pending requests for same transition
        Index(
            "ix_approval_requests_pending_unique",
            "workflow_name", "entity_type", "entity_id",
            "transition_action", "from_state", "to_state",
            unique=True,
            postgresql_where="status IN ('pending', 'escalated')",
        ),
        # AL-11: Covering index for get_pending_for_entity()
        Index(
            "ix_approval_requests_entity_status",
            "entity_type", "entity_id", "status", "created_at",
        ),
        # Expiry enforcement index
        Index(
            "ix_approval_requests_expiry",
            "status", "created_at",
        ),
    )

    request_id: Mapped[UUID] = mapped_column(
        UUIDString(), nullable=False, unique=True,
    )
    workflow_name: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUIDString(), nullable=False)
    transition_action: Mapped[str] = mapped_column(String(100), nullable=False)
    from_state: Mapped[str] = mapped_column(String(50), nullable=False)
    to_state: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_name: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_version: Mapped[int] = mapped_column(nullable=False)
    policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_rule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(
        Numeric(38, 9), nullable=True,
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    requestor_id: Mapped[UUID] = mapped_column(UUIDString(), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    original_request_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    decisions: Mapped[list["ApprovalDecisionModel"]] = relationship(
        "ApprovalDecisionModel",
        back_populates="request",
        primaryjoin="ApprovalRequestModel.request_id == ApprovalDecisionModel.request_id",
        order_by="ApprovalDecisionModel.decided_at",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<ApprovalRequest {self.request_id} "
            f"{self.workflow_name}/{self.transition_action} "
            f"status={self.status}>"
        )

    def to_dto(self) -> ApprovalRequest:
        """Convert ORM model to frozen domain DTO."""
        from finance_kernel.domain.approval import (
            ApprovalRequest as ApprovalRequestDTO,
            ApprovalStatus,
        )

        return ApprovalRequestDTO(
            request_id=self.request_id,
            workflow_name=self.workflow_name,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            transition_action=self.transition_action,
            from_state=self.from_state,
            to_state=self.to_state,
            policy_name=self.policy_name,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
            amount=self.amount,
            currency=self.currency,
            requestor_id=self.requestor_id,
            status=ApprovalStatus(self.status),
            created_at=self.created_at,
            resolved_at=self.resolved_at,
            matched_rule=self.matched_rule,
            original_request_hash=self.original_request_hash,
            decisions=tuple(d.to_dto() for d in self.decisions),
        )

    @classmethod
    def from_dto(cls, dto: ApprovalRequest) -> ApprovalRequestModel:
        """Create ORM model from domain DTO."""
        return cls(
            request_id=dto.request_id,
            workflow_name=dto.workflow_name,
            entity_type=dto.entity_type,
            entity_id=dto.entity_id,
            transition_action=dto.transition_action,
            from_state=dto.from_state,
            to_state=dto.to_state,
            policy_name=dto.policy_name,
            policy_version=dto.policy_version,
            policy_hash=dto.policy_hash,
            amount=dto.amount,
            currency=dto.currency,
            requestor_id=dto.requestor_id,
            status=dto.status.value,
            created_at=dto.created_at,
            resolved_at=dto.resolved_at,
            matched_rule=dto.matched_rule,
            original_request_hash=dto.original_request_hash,
        )


class ApprovalDecisionModel(Base):
    """Persistent approval decision record. Append-only.

    Contract:
        Decisions are immutable once created -- no UPDATE, no DELETE.

    Guarantees:
        - AL-7: UNIQUE(request_id, actor_id) prevents duplicate decisions.
    """

    __tablename__ = "approval_decisions"

    __table_args__ = (
        Index("ix_approval_decisions_request_id", "request_id"),
        # AL-7: Same actor cannot approve the same request twice
        UniqueConstraint(
            "request_id", "actor_id",
            name="uq_approval_decisions_actor",
        ),
    )

    decision_id: Mapped[UUID] = mapped_column(
        UUIDString(), nullable=False, unique=True,
    )
    request_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("approval_requests.request_id"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(UUIDString(), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    request: Mapped["ApprovalRequestModel"] = relationship(
        "ApprovalRequestModel",
        back_populates="decisions",
        foreign_keys=[request_id],
        primaryjoin="ApprovalDecisionModel.request_id == ApprovalRequestModel.request_id",
    )

    def __repr__(self) -> str:
        return (
            f"<ApprovalDecision {self.decision_id} "
            f"request={self.request_id} "
            f"decision={self.decision}>"
        )

    def to_dto(self) -> ApprovalDecisionRecord:
        """Convert ORM model to frozen domain DTO."""
        from finance_kernel.domain.approval import (
            ApprovalDecision as ApprovalDecisionEnum,
            ApprovalDecisionRecord as DecisionDTO,
        )

        return DecisionDTO(
            decision_id=self.decision_id,
            request_id=self.request_id,
            actor_id=self.actor_id,
            actor_role=self.actor_role,
            decision=ApprovalDecisionEnum(self.decision),
            comment=self.comment,
            decided_at=self.decided_at,
        )

    @classmethod
    def from_dto(cls, dto: ApprovalDecisionRecord) -> ApprovalDecisionModel:
        """Create ORM model from domain DTO."""
        return cls(
            decision_id=dto.decision_id,
            request_id=dto.request_id,
            actor_id=dto.actor_id,
            actor_role=dto.actor_role,
            decision=dto.decision.value,
            comment=dto.comment,
            decided_at=dto.decided_at,
        )


# =============================================================================
# ORM-Level Immutability for Decisions (Append-Only)
# =============================================================================


@event.listens_for(ApprovalDecisionModel, "before_update")
def prevent_decision_update(mapper, connection, target):
    """Prevent updates to approval decision records."""
    raise ImmutabilityViolationError(
        entity_type="ApprovalDecision",
        entity_id=str(target.decision_id),
        reason="Approval decisions are immutable -- cannot modify",
    )


@event.listens_for(ApprovalDecisionModel, "before_delete")
def prevent_decision_delete(mapper, connection, target):
    """Prevent deletion of approval decision records."""
    raise ImmutabilityViolationError(
        entity_type="ApprovalDecision",
        entity_id=str(target.decision_id),
        reason="Approval decisions are immutable -- cannot delete",
    )
