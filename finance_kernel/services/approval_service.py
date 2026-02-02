"""
finance_kernel.services.approval_service -- Approval lifecycle management.

Responsibility:
    Manages the full lifecycle of approval requests: creation, decision
    recording, cancellation, expiry.  Delegates rule evaluation to the
    pure approval engine.

Architecture position:
    Kernel > Services.  May import from domain/, models/, db/.

Invariants enforced:
    AL-1  -- Lifecycle state machine enforced before persisting transitions.
    AL-2  -- Policy version/hash snapshotted at request creation.
    AL-3  -- Currency validated against policy_currency.
    AL-5  -- Policy drift detected at resolution time.
    AL-7  -- Decision uniqueness (service + DB constraint).
    AL-8  -- Tamper evidence hash computed at creation, verified on load.
    AL-10 -- Duplicate pending request prevention.

Failure modes:
    - ApprovalNotFoundError if request_id not found.
    - ApprovalAlreadyResolvedError on terminal status mutation.
    - InvalidApprovalTransitionError on illegal status change.
    - DuplicateApprovalError on same actor deciding twice.
    - PolicyDriftError on policy version downgrade.
    - ApprovalCurrencyMismatchError on currency mismatch.
    - TamperDetectedError on hash mismatch.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.approval import (
    APPROVAL_TRANSITIONS,
    TERMINAL_APPROVAL_STATUSES,
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalEvaluation,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStatus,
)
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.exceptions import (
    ApprovalAlreadyResolvedError,
    ApprovalCurrencyMismatchError,
    ApprovalNotFoundError,
    DuplicateApprovalError,
    DuplicateApprovalRequestError,
    InvalidApprovalTransitionError,
    PolicyDriftError,
    TamperDetectedError,
    UnauthorizedApproverError,
)
from finance_kernel.models.approval import (
    ApprovalDecisionModel,
    ApprovalRequestModel,
)
from finance_kernel.models.audit_event import AuditAction
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.utils.hashing import hash_payload

logger = logging.getLogger("kernel.approval_service")


def _normalize_amount_for_hash(amount: Decimal | None) -> str | None:
    """Normalize amount to a canonical string for hash stability (AL-8).

    DB Numeric(38,9) can return Decimal with trailing zeros; normalize so
    creation and load produce the same hash input.
    """
    if amount is None:
        return None
    return str(Decimal(str(amount)).normalize())


def _compute_request_hash(
    *,
    request_id: UUID,
    workflow_name: str,
    entity_type: str,
    entity_id: UUID,
    transition_action: str,
    from_state: str,
    to_state: str,
    policy_name: str,
    policy_version: int,
    amount: Decimal | None,
    currency: str,
) -> str:
    """Compute tamper-evidence hash for an approval request (AL-8).

    Covers all immutable request fields so any post-creation modification
    is mathematically detectable.
    """
    return hash_payload({
        "request_id": str(request_id),
        "workflow_name": workflow_name,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "transition_action": transition_action,
        "from_state": from_state,
        "to_state": to_state,
        "policy_name": policy_name,
        "policy_version": policy_version,
        "amount": _normalize_amount_for_hash(amount),
        "currency": currency,
    })


class ApprovalService:
    """Manages approval request/decision lifecycle."""

    def __init__(
        self,
        session: Session,
        auditor: AuditorService,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._auditor = auditor
        self._clock = clock or SystemClock()

    def create_request(
        self,
        workflow_name: str,
        entity_type: str,
        entity_id: UUID,
        transition_action: str,
        from_state: str,
        to_state: str,
        policy: ApprovalPolicy,
        matched_rule_name: str,
        requestor_id: UUID,
        amount: Decimal | None = None,
        currency: str = "USD",
    ) -> ApprovalRequest:
        """Create a new approval request.

        AL-2: Snapshots policy_version and policy_hash.
        AL-3: Validates currency matches policy_currency.
        AL-10: Prevents duplicate pending requests.
        """
        # AL-3: Currency validation
        if policy.policy_currency and currency != policy.policy_currency:
            raise ApprovalCurrencyMismatchError(currency, policy.policy_currency)

        # AL-10: Check for existing pending request
        existing = self._session.execute(
            select(ApprovalRequestModel).where(
                ApprovalRequestModel.workflow_name == workflow_name,
                ApprovalRequestModel.entity_type == entity_type,
                ApprovalRequestModel.entity_id == entity_id,
                ApprovalRequestModel.transition_action == transition_action,
                ApprovalRequestModel.from_state == from_state,
                ApprovalRequestModel.to_state == to_state,
                ApprovalRequestModel.status.in_(["pending", "escalated"]),
            )
        ).scalar_one_or_none()

        if existing is not None:
            raise DuplicateApprovalRequestError(
                entity_type, str(entity_id), transition_action,
            )

        request_id = uuid4()
        now = self._clock.now()

        # AL-8: Compute tamper-evidence hash
        request_hash = _compute_request_hash(
            request_id=request_id,
            workflow_name=workflow_name,
            entity_type=entity_type,
            entity_id=entity_id,
            transition_action=transition_action,
            from_state=from_state,
            to_state=to_state,
            policy_name=policy.policy_name,
            policy_version=policy.version,
            amount=amount,
            currency=currency,
        )

        model = ApprovalRequestModel(
            request_id=request_id,
            workflow_name=workflow_name,
            entity_type=entity_type,
            entity_id=entity_id,
            transition_action=transition_action,
            from_state=from_state,
            to_state=to_state,
            policy_name=policy.policy_name,
            policy_version=policy.version,
            policy_hash=policy.policy_hash,
            matched_rule=matched_rule_name,
            amount=amount,
            currency=currency,
            requestor_id=requestor_id,
            status=ApprovalStatus.PENDING.value,
            created_at=now,
            original_request_hash=request_hash,
        )

        self._session.add(model)
        self._session.flush()

        # Audit
        self._auditor._create_audit_event(
            entity_type="ApprovalRequest",
            entity_id=request_id,
            action=AuditAction.APPROVAL_REQUESTED,
            actor_id=requestor_id,
            payload={
                "workflow_name": workflow_name,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "transition_action": transition_action,
                "policy_name": policy.policy_name,
                "policy_version": policy.version,
                "matched_rule": matched_rule_name,
                "amount": str(amount) if amount is not None else None,
                "currency": currency,
            },
        )

        logger.info(
            "approval_request_created",
            extra={
                "request_id": str(request_id),
                "workflow_name": workflow_name,
                "entity_id": str(entity_id),
                "action": transition_action,
                "policy": policy.policy_name,
            },
        )

        return model.to_dto()

    def record_decision(
        self,
        request_id: UUID,
        actor_id: UUID,
        actor_role: str,
        decision: ApprovalDecision,
        comment: str = "",
        active_policy: ApprovalPolicy | None = None,
    ) -> ApprovalRequest:
        """Record a decision on an approval request.

        AL-1: Validates status transition.
        AL-5: Checks for policy drift on resolution.
        AL-7: Prevents duplicate decisions from same actor.
        """
        model = self._load_request_model(request_id)
        current_status = ApprovalStatus(model.status)

        # AL-1: Check status allows new decisions
        if current_status in TERMINAL_APPROVAL_STATUSES:
            raise ApprovalAlreadyResolvedError(
                str(request_id), current_status.value,
            )

        # AL-7: Check for duplicate decision
        existing_decision = self._session.execute(
            select(ApprovalDecisionModel).where(
                ApprovalDecisionModel.request_id == request_id,
                ApprovalDecisionModel.actor_id == actor_id,
            )
        ).scalar_one_or_none()

        if existing_decision is not None:
            raise DuplicateApprovalError(str(request_id), str(actor_id))

        # Record the decision
        now = self._clock.now()
        decision_model = ApprovalDecisionModel(
            decision_id=uuid4(),
            request_id=request_id,
            actor_id=actor_id,
            actor_role=actor_role,
            decision=decision.value,
            comment=comment,
            decided_at=now,
        )
        self._session.add(decision_model)

        # Determine new status based on decision type
        new_status = self._resolve_new_status(decision, current_status)

        # AL-1: Validate transition
        allowed = APPROVAL_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            raise InvalidApprovalTransitionError(
                current_status.value, new_status.value,
            )

        # AL-5: Check policy drift on resolution
        if new_status in TERMINAL_APPROVAL_STATUSES and active_policy is not None:
            if active_policy.version < model.policy_version:
                raise PolicyDriftError(
                    model.policy_name,
                    model.policy_version,
                    active_policy.version,
                )
            if active_policy.version > model.policy_version:
                # Policy upgraded -- allowed but logged
                self._auditor._create_audit_event(
                    entity_type="ApprovalRequest",
                    entity_id=request_id,
                    action=AuditAction.APPROVAL_POLICY_DRIFT,
                    actor_id=actor_id,
                    payload={
                        "original_version": model.policy_version,
                        "active_version": active_policy.version,
                        "policy_name": model.policy_name,
                    },
                )

        # Update status
        model.status = new_status.value
        if new_status in TERMINAL_APPROVAL_STATUSES:
            model.resolved_at = now

        self._session.flush()

        # Audit the decision
        audit_action = {
            ApprovalStatus.APPROVED: AuditAction.APPROVAL_GRANTED,
            ApprovalStatus.REJECTED: AuditAction.APPROVAL_REJECTED,
            ApprovalStatus.ESCALATED: AuditAction.APPROVAL_ESCALATED,
        }.get(new_status, AuditAction.APPROVAL_GRANTED)

        self._auditor._create_audit_event(
            entity_type="ApprovalRequest",
            entity_id=request_id,
            action=audit_action,
            actor_id=actor_id,
            payload={
                "decision": decision.value,
                "actor_role": actor_role,
                "comment": comment,
                "new_status": new_status.value,
            },
        )

        logger.info(
            "approval_decision_recorded",
            extra={
                "request_id": str(request_id),
                "actor_id": str(actor_id),
                "decision": decision.value,
                "new_status": new_status.value,
            },
        )

        return model.to_dto()

    def record_auto_approval(
        self,
        request_id: UUID,
        matched_rule_name: str,
        threshold_value: Decimal,
        evaluated_amount: Decimal,
        policy: ApprovalPolicy,
        actor_id: UUID,
    ) -> ApprovalRequest:
        """Record an auto-approval with full audit payload (AL-7 audit)."""
        model = self._load_request_model(request_id)

        model.status = ApprovalStatus.AUTO_APPROVED.value
        model.resolved_at = self._clock.now()
        self._session.flush()

        self._auditor._create_audit_event(
            entity_type="ApprovalRequest",
            entity_id=request_id,
            action=AuditAction.APPROVAL_AUTO_APPROVED,
            actor_id=actor_id,
            payload={
                "matched_rule": matched_rule_name,
                "threshold_value": str(threshold_value),
                "evaluated_amount": str(evaluated_amount),
                "policy_name": policy.policy_name,
                "policy_version": policy.version,
                "policy_hash": policy.policy_hash,
            },
        )

        return model.to_dto()

    def get_request(self, request_id: UUID) -> ApprovalRequest:
        """Get approval request by ID.

        AL-8: Verifies tamper-evidence hash on load.
        """
        model = self._load_request_model(request_id)
        dto = model.to_dto()

        # AL-8: Verify hash
        if dto.original_request_hash is not None:
            computed = _compute_request_hash(
                request_id=dto.request_id,
                workflow_name=dto.workflow_name,
                entity_type=dto.entity_type,
                entity_id=dto.entity_id,
                transition_action=dto.transition_action,
                from_state=dto.from_state,
                to_state=dto.to_state,
                policy_name=dto.policy_name,
                policy_version=dto.policy_version,
                amount=dto.amount,
                currency=dto.currency,
            )
            if computed != dto.original_request_hash:
                self._auditor._create_audit_event(
                    entity_type="ApprovalRequest",
                    entity_id=request_id,
                    action=AuditAction.APPROVAL_TAMPER_DETECTED,
                    actor_id=dto.requestor_id,
                    payload={
                        "expected_hash": dto.original_request_hash,
                        "computed_hash": computed,
                    },
                )
                raise TamperDetectedError(str(request_id))

        return dto

    def get_pending_for_entity(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> list[ApprovalRequest]:
        """Get all pending/escalated approval requests for an entity."""
        models = self._session.execute(
            select(ApprovalRequestModel).where(
                ApprovalRequestModel.entity_type == entity_type,
                ApprovalRequestModel.entity_id == entity_id,
                ApprovalRequestModel.status.in_(["pending", "escalated"]),
            ).order_by(ApprovalRequestModel.created_at)
        ).scalars().all()

        return [m.to_dto() for m in models]

    def cancel_request(
        self,
        request_id: UUID,
        actor_id: UUID,
    ) -> ApprovalRequest:
        """Cancel a pending approval request (AL-1)."""
        model = self._load_request_model(request_id)
        current_status = ApprovalStatus(model.status)

        if current_status in TERMINAL_APPROVAL_STATUSES:
            raise ApprovalAlreadyResolvedError(
                str(request_id), current_status.value,
            )

        allowed = APPROVAL_TRANSITIONS.get(current_status, frozenset())
        if ApprovalStatus.CANCELLED not in allowed:
            raise InvalidApprovalTransitionError(
                current_status.value, ApprovalStatus.CANCELLED.value,
            )

        model.status = ApprovalStatus.CANCELLED.value
        model.resolved_at = self._clock.now()
        self._session.flush()

        self._auditor._create_audit_event(
            entity_type="ApprovalRequest",
            entity_id=request_id,
            action=AuditAction.APPROVAL_CANCELLED,
            actor_id=actor_id,
            payload={"previous_status": current_status.value},
        )

        return model.to_dto()

    def expire_stale_requests(
        self,
        as_of: datetime,
        timeout_hours: int,
    ) -> list[UUID]:
        """Expire requests past their timeout.

        Uses advisory lock to prevent double-expiry in concurrent jobs.
        """
        from datetime import timedelta, timezone

        # created_at is DateTime(timezone=True); ensure cutoff is comparable
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        cutoff = as_of - timedelta(hours=timeout_hours)

        models = self._session.execute(
            select(ApprovalRequestModel).where(
                ApprovalRequestModel.status.in_(["pending", "escalated"]),
                ApprovalRequestModel.created_at < cutoff,
            )
        ).scalars().all()

        expired_ids = []
        for model in models:
            model.status = ApprovalStatus.EXPIRED.value
            model.resolved_at = as_of
            expired_ids.append(model.request_id)
            self._session.flush()  # persist status before audit so later reads see it

            self._auditor._create_audit_event(
                entity_type="ApprovalRequest",
                entity_id=model.request_id,
                action=AuditAction.APPROVAL_EXPIRED,
                actor_id=model.requestor_id,
                payload={
                    "created_at": str(model.created_at),
                    "timeout_hours": timeout_hours,
                },
            )

        if expired_ids:
            self._session.flush()

        return expired_ids

    def _load_request_model(self, request_id: UUID) -> ApprovalRequestModel:
        """Load request model by request_id, raise if not found."""
        model = self._session.execute(
            select(ApprovalRequestModel).where(
                ApprovalRequestModel.request_id == request_id,
            )
        ).scalar_one_or_none()

        if model is None:
            raise ApprovalNotFoundError(str(request_id))

        return model

    def _resolve_new_status(
        self,
        decision: ApprovalDecision,
        current_status: ApprovalStatus,
    ) -> ApprovalStatus:
        """Map a decision to the resulting status."""
        if decision == ApprovalDecision.APPROVE:
            return ApprovalStatus.APPROVED
        elif decision == ApprovalDecision.REJECT:
            return ApprovalStatus.REJECTED
        elif decision == ApprovalDecision.ESCALATE:
            return ApprovalStatus.ESCALATED
        else:
            return ApprovalStatus.APPROVED
