"""
Tests for ApprovalService -- approval lifecycle management.

Covers:
- create_request(): happy path, AL-3 currency mismatch, AL-10 duplicate
  pending request, audit trail
- record_decision(): approve/reject/escalate, AL-1 terminal status guard,
  AL-7 duplicate decision, AL-5 policy drift (downgrade and upgrade)
- record_auto_approval(): happy path, audit payload with threshold details
- get_request(): happy path, AL-8 tamper detection, not found
- get_pending_for_entity(): filtering and ordering
- cancel_request(): happy path, AL-1 already-resolved guard
- expire_stale_requests(): timeout logic, recent requests unaffected
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRule,
    ApprovalStatus,
)
from finance_kernel.exceptions import (
    ApprovalAlreadyResolvedError,
    ApprovalCurrencyMismatchError,
    ApprovalNotFoundError,
    DuplicateApprovalError,
    DuplicateApprovalRequestError,
    InvalidApprovalTransitionError,
    PolicyDriftError,
    TamperDetectedError,
)
from finance_kernel.models.approval import ApprovalRequestModel
from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.services.approval_service import ApprovalService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(currency=None, version=1, hash_val=None):
    """Build a minimal ApprovalPolicy for testing."""
    return ApprovalPolicy(
        policy_name="test-policy",
        version=version,
        applies_to_workflow="test_workflow",
        rules=(
            ApprovalRule(
                rule_name="default",
                priority=1,
                required_roles=("manager",),
                min_approvers=1,
            ),
        ),
        policy_currency=currency,
        policy_hash=hash_val,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def approval_service(session, auditor_service, deterministic_clock):
    """Provide an ApprovalService wired to the test session."""
    return ApprovalService(session, auditor_service, deterministic_clock)


@pytest.fixture
def sample_entity_id():
    """Return a stable entity UUID for test requests."""
    return uuid4()


@pytest.fixture
def sample_requestor_id():
    """Return a stable requestor UUID."""
    return uuid4()


@pytest.fixture
def create_request(approval_service, sample_entity_id, sample_requestor_id):
    """Factory fixture to create a standard pending approval request.

    Returns a callable that creates a request with sensible defaults
    and returns the DTO.
    """

    def _create(
        *,
        workflow_name="invoice_approval",
        entity_type="Invoice",
        entity_id=None,
        transition_action="submit",
        from_state="draft",
        to_state="pending_approval",
        policy=None,
        matched_rule_name="default",
        requestor_id=None,
        amount=Decimal("1000.00"),
        currency="USD",
    ):
        return approval_service.create_request(
            workflow_name=workflow_name,
            entity_type=entity_type,
            entity_id=entity_id or sample_entity_id,
            transition_action=transition_action,
            from_state=from_state,
            to_state=to_state,
            policy=policy or make_policy(),
            matched_rule_name=matched_rule_name,
            requestor_id=requestor_id or sample_requestor_id,
            amount=amount,
            currency=currency,
        )

    return _create


# =========================================================================
# create_request()
# =========================================================================


class TestCreateRequest:
    """Tests for ApprovalService.create_request()."""

    def test_happy_path_creates_pending_request(self, create_request):
        """A valid create_request produces a pending DTO with all fields set."""
        dto = create_request()

        assert dto.status == ApprovalStatus.PENDING
        assert dto.workflow_name == "invoice_approval"
        assert dto.entity_type == "Invoice"
        assert dto.transition_action == "submit"
        assert dto.from_state == "draft"
        assert dto.to_state == "pending_approval"
        assert dto.policy_name == "test-policy"
        assert dto.policy_version == 1
        assert dto.amount == Decimal("1000.00")
        assert dto.currency == "USD"
        assert dto.matched_rule == "default"
        assert dto.resolved_at is None
        assert dto.created_at is not None
        assert dto.request_id is not None

    def test_hash_is_computed(self, create_request):
        """AL-8: The request hash is computed and stored at creation."""
        dto = create_request()
        assert dto.original_request_hash is not None
        assert len(dto.original_request_hash) == 64  # SHA-256 hex digest

    def test_policy_version_snapshotted(self, create_request):
        """AL-2: Policy version is captured at creation time."""
        policy = make_policy(version=7, hash_val="abc123")
        dto = create_request(policy=policy)

        assert dto.policy_version == 7
        assert dto.policy_hash == "abc123"

    def test_al3_currency_mismatch_raises(self, approval_service, sample_entity_id, sample_requestor_id):
        """AL-3: Currency mismatch between request and policy raises error."""
        policy = make_policy(currency="EUR")

        with pytest.raises(ApprovalCurrencyMismatchError):
            approval_service.create_request(
                workflow_name="test_wf",
                entity_type="Invoice",
                entity_id=sample_entity_id,
                transition_action="submit",
                from_state="draft",
                to_state="pending",
                policy=policy,
                matched_rule_name="default",
                requestor_id=sample_requestor_id,
                amount=Decimal("100.00"),
                currency="USD",
            )

    def test_al3_no_policy_currency_accepts_any(self, create_request):
        """AL-3: When policy_currency is None, any currency is accepted."""
        dto = create_request(currency="JPY")
        assert dto.currency == "JPY"

    def test_al10_duplicate_pending_raises(self, create_request):
        """AL-10: Creating a second pending request for the same transition raises."""
        create_request()

        with pytest.raises(DuplicateApprovalRequestError):
            create_request()

    def test_al10_different_transition_allowed(self, create_request):
        """AL-10: Different transition actions do not collide."""
        create_request(transition_action="submit")
        dto2 = create_request(transition_action="escalate")
        assert dto2.transition_action == "escalate"

    def test_audit_event_created(self, session, create_request):
        """An APPROVAL_REQUESTED audit event is emitted on creation."""
        dto = create_request()

        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "ApprovalRequest",
                AuditEvent.entity_id == dto.request_id,
                AuditEvent.action == AuditAction.APPROVAL_REQUESTED,
            )
        ).scalars().all()

        assert len(audit_events) == 1
        payload = audit_events[0].payload
        assert payload["workflow_name"] == "invoice_approval"
        assert payload["policy_name"] == "test-policy"


# =========================================================================
# record_decision()
# =========================================================================


class TestRecordDecision:
    """Tests for ApprovalService.record_decision()."""

    def test_approve_sets_status_and_resolved_at(self, approval_service, create_request):
        """Approve decision transitions status to approved with resolved_at."""
        dto = create_request()
        actor = uuid4()

        result = approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor,
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
            comment="Looks good",
        )

        assert result.status == ApprovalStatus.APPROVED
        assert result.resolved_at is not None

    def test_reject_sets_status(self, approval_service, create_request):
        """Reject decision transitions status to rejected."""
        dto = create_request()
        actor = uuid4()

        result = approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor,
            actor_role="manager",
            decision=ApprovalDecision.REJECT,
            comment="Insufficient documentation",
        )

        assert result.status == ApprovalStatus.REJECTED
        assert result.resolved_at is not None

    def test_escalate_sets_status(self, approval_service, create_request):
        """Escalate decision transitions status to escalated."""
        dto = create_request()
        actor = uuid4()

        result = approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor,
            actor_role="manager",
            decision=ApprovalDecision.ESCALATE,
            comment="Needs VP review",
        )

        assert result.status == ApprovalStatus.ESCALATED
        # Escalated is not terminal, so resolved_at should be None
        assert result.resolved_at is None

    def test_al1_decision_on_approved_raises(self, approval_service, create_request):
        """AL-1: Cannot record a decision on an already-approved request."""
        dto = create_request()
        actor1 = uuid4()
        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor1,
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
        )

        with pytest.raises(ApprovalAlreadyResolvedError):
            approval_service.record_decision(
                request_id=dto.request_id,
                actor_id=uuid4(),
                actor_role="director",
                decision=ApprovalDecision.APPROVE,
            )

    def test_al1_decision_on_rejected_raises(self, approval_service, create_request):
        """AL-1: Cannot record a decision on an already-rejected request."""
        dto = create_request()
        actor = uuid4()
        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor,
            actor_role="manager",
            decision=ApprovalDecision.REJECT,
        )

        with pytest.raises(ApprovalAlreadyResolvedError):
            approval_service.record_decision(
                request_id=dto.request_id,
                actor_id=uuid4(),
                actor_role="director",
                decision=ApprovalDecision.APPROVE,
            )

    def test_al7_duplicate_decision_from_same_actor_raises(
        self, approval_service, create_request,
    ):
        """AL-7: Same actor cannot decide twice on the same request."""
        dto = create_request()
        actor = uuid4()

        # First decision: escalate (non-terminal, so request stays open)
        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor,
            actor_role="manager",
            decision=ApprovalDecision.ESCALATE,
        )

        with pytest.raises(DuplicateApprovalError):
            approval_service.record_decision(
                request_id=dto.request_id,
                actor_id=actor,
                actor_role="manager",
                decision=ApprovalDecision.APPROVE,
            )

    def test_al5_policy_downgrade_raises(self, approval_service, create_request):
        """AL-5: Policy drift (version downgrade) raises PolicyDriftError."""
        # Create request with policy version 3
        policy_v3 = make_policy(version=3)
        dto = create_request(policy=policy_v3)

        # Attempt to approve with an older active policy (version 2)
        downgraded_policy = make_policy(version=2)

        with pytest.raises(PolicyDriftError):
            approval_service.record_decision(
                request_id=dto.request_id,
                actor_id=uuid4(),
                actor_role="manager",
                decision=ApprovalDecision.APPROVE,
                active_policy=downgraded_policy,
            )

    def test_al5_policy_upgrade_succeeds_with_audit(
        self, session, approval_service, create_request,
    ):
        """AL-5: Policy upgrade is allowed but emits an audit event."""
        policy_v1 = make_policy(version=1)
        dto = create_request(policy=policy_v1)

        upgraded_policy = make_policy(version=5)
        result = approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
            active_policy=upgraded_policy,
        )

        assert result.status == ApprovalStatus.APPROVED

        # Verify the drift audit event was emitted
        drift_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "ApprovalRequest",
                AuditEvent.entity_id == dto.request_id,
                AuditEvent.action == AuditAction.APPROVAL_POLICY_DRIFT,
            )
        ).scalars().all()

        assert len(drift_events) == 1
        assert drift_events[0].payload["original_version"] == 1
        assert drift_events[0].payload["active_version"] == 5

    def test_decision_on_escalated_request(self, approval_service, create_request):
        """Escalated requests can still receive approve/reject decisions."""
        dto = create_request()
        actor1 = uuid4()

        # Escalate first
        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor1,
            actor_role="manager",
            decision=ApprovalDecision.ESCALATE,
        )

        # Then approve from a different actor
        actor2 = uuid4()
        result = approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor2,
            actor_role="director",
            decision=ApprovalDecision.APPROVE,
        )

        assert result.status == ApprovalStatus.APPROVED
        assert result.resolved_at is not None

    def test_approve_audit_event(self, session, approval_service, create_request):
        """An APPROVAL_GRANTED audit event is emitted on approve."""
        dto = create_request()
        actor = uuid4()

        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=actor,
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
            comment="LGTM",
        )

        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "ApprovalRequest",
                AuditEvent.entity_id == dto.request_id,
                AuditEvent.action == AuditAction.APPROVAL_GRANTED,
            )
        ).scalars().all()

        assert len(audit_events) == 1
        assert audit_events[0].payload["decision"] == "approve"
        assert audit_events[0].payload["comment"] == "LGTM"


# =========================================================================
# record_auto_approval()
# =========================================================================


class TestRecordAutoApproval:
    """Tests for ApprovalService.record_auto_approval()."""

    def test_happy_path(self, approval_service, create_request):
        """Auto-approval sets status to auto_approved with resolved_at."""
        dto = create_request()
        actor = uuid4()
        policy = make_policy()

        result = approval_service.record_auto_approval(
            request_id=dto.request_id,
            matched_rule_name="low_value",
            threshold_value=Decimal("5000.00"),
            evaluated_amount=Decimal("1000.00"),
            policy=policy,
            actor_id=actor,
        )

        assert result.status == ApprovalStatus.AUTO_APPROVED
        assert result.resolved_at is not None

    def test_auto_approval_audit_payload_contains_threshold(
        self, session, approval_service, create_request,
    ):
        """The auto-approval audit event includes threshold and amount details."""
        dto = create_request()
        actor = uuid4()
        policy = make_policy(hash_val="policy-hash-abc")

        approval_service.record_auto_approval(
            request_id=dto.request_id,
            matched_rule_name="low_value",
            threshold_value=Decimal("5000.00"),
            evaluated_amount=Decimal("1000.00"),
            policy=policy,
            actor_id=actor,
        )

        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "ApprovalRequest",
                AuditEvent.entity_id == dto.request_id,
                AuditEvent.action == AuditAction.APPROVAL_AUTO_APPROVED,
            )
        ).scalars().all()

        assert len(audit_events) == 1
        payload = audit_events[0].payload
        assert payload["matched_rule"] == "low_value"
        assert payload["threshold_value"] == "5000.00"
        assert payload["evaluated_amount"] == "1000.00"
        assert payload["policy_name"] == "test-policy"
        assert payload["policy_version"] == 1
        assert payload["policy_hash"] == "policy-hash-abc"


# =========================================================================
# get_request()
# =========================================================================


class TestGetRequest:
    """Tests for ApprovalService.get_request()."""

    def test_happy_path_returns_dto(self, approval_service, create_request):
        """Retrieving an existing request returns a correct DTO."""
        original = create_request()

        fetched = approval_service.get_request(original.request_id)

        assert fetched.request_id == original.request_id
        assert fetched.workflow_name == original.workflow_name
        assert fetched.status == ApprovalStatus.PENDING
        assert fetched.original_request_hash == original.original_request_hash

    def test_al8_tamper_detection_raises(self, session, approval_service, create_request):
        """AL-8: Tamper detection raises TamperDetectedError when hash mismatches."""
        dto = create_request()

        # Tamper with the stored request by directly modifying a field
        model = session.execute(
            select(ApprovalRequestModel).where(
                ApprovalRequestModel.request_id == dto.request_id,
            )
        ).scalar_one()

        # Modify a field that is covered by the hash
        model.amount = Decimal("999999.99")
        session.flush()

        with pytest.raises(TamperDetectedError):
            approval_service.get_request(dto.request_id)

    def test_not_found_raises(self, approval_service):
        """Looking up a nonexistent request raises ApprovalNotFoundError."""
        with pytest.raises(ApprovalNotFoundError):
            approval_service.get_request(uuid4())


# =========================================================================
# get_pending_for_entity()
# =========================================================================


class TestGetPendingForEntity:
    """Tests for ApprovalService.get_pending_for_entity()."""

    def test_returns_pending_and_escalated(
        self, approval_service, create_request, deterministic_clock,
    ):
        """Pending and escalated requests are returned, ordered by created_at."""
        entity_id = uuid4()

        # Create first request (pending)
        dto1 = create_request(
            entity_id=entity_id,
            transition_action="action_a",
        )

        deterministic_clock.advance(10)

        # Create second request (will be escalated)
        dto2 = create_request(
            entity_id=entity_id,
            transition_action="action_b",
        )
        approval_service.record_decision(
            request_id=dto2.request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.ESCALATE,
        )

        results = approval_service.get_pending_for_entity("Invoice", entity_id)

        assert len(results) == 2
        assert results[0].request_id == dto1.request_id
        assert results[1].request_id == dto2.request_id
        assert results[0].status == ApprovalStatus.PENDING
        assert results[1].status == ApprovalStatus.ESCALATED

    def test_excludes_resolved_requests(
        self, approval_service, create_request,
    ):
        """Approved/rejected/cancelled requests are excluded."""
        entity_id = uuid4()

        # Create and approve a request
        dto_approved = create_request(
            entity_id=entity_id,
            transition_action="action_a",
        )
        approval_service.record_decision(
            request_id=dto_approved.request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
        )

        # Create a pending request for a different action
        create_request(
            entity_id=entity_id,
            transition_action="action_b",
        )

        results = approval_service.get_pending_for_entity("Invoice", entity_id)

        # Only the pending one should show up
        assert len(results) == 1
        assert results[0].transition_action == "action_b"

    def test_empty_when_no_pending(self, approval_service):
        """Returns empty list when entity has no pending requests."""
        results = approval_service.get_pending_for_entity("Invoice", uuid4())
        assert results == []


# =========================================================================
# cancel_request()
# =========================================================================


class TestCancelRequest:
    """Tests for ApprovalService.cancel_request()."""

    def test_happy_path_pending_to_cancelled(self, approval_service, create_request):
        """Cancelling a pending request sets status to cancelled."""
        dto = create_request()
        actor = uuid4()

        result = approval_service.cancel_request(dto.request_id, actor)

        assert result.status == ApprovalStatus.CANCELLED
        assert result.resolved_at is not None

    def test_al1_cancel_approved_raises(self, approval_service, create_request):
        """AL-1: Cannot cancel an already-approved request."""
        dto = create_request()
        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
        )

        with pytest.raises(ApprovalAlreadyResolvedError):
            approval_service.cancel_request(dto.request_id, uuid4())

    def test_cancel_audit_event(self, session, approval_service, create_request):
        """Cancellation emits an APPROVAL_CANCELLED audit event."""
        dto = create_request()
        actor = uuid4()

        approval_service.cancel_request(dto.request_id, actor)

        audit_events = session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "ApprovalRequest",
                AuditEvent.entity_id == dto.request_id,
                AuditEvent.action == AuditAction.APPROVAL_CANCELLED,
            )
        ).scalars().all()

        assert len(audit_events) == 1
        assert audit_events[0].payload["previous_status"] == "pending"


# =========================================================================
# expire_stale_requests()
# =========================================================================


class TestExpireStaleRequests:
    """Tests for ApprovalService.expire_stale_requests()."""

    def test_expires_requests_past_timeout(
        self, approval_service, create_request, deterministic_clock,
    ):
        """Requests older than the timeout are expired."""
        dto = create_request()

        # Advance clock well past a 24-hour timeout
        deterministic_clock.advance(90_000)  # 25 hours
        now = deterministic_clock.now()

        expired_ids = approval_service.expire_stale_requests(
            as_of=now,
            timeout_hours=24,
        )

        assert dto.request_id in expired_ids

        # Verify the request is now expired
        model = approval_service._load_request_model(dto.request_id)
        assert model.status == ApprovalStatus.EXPIRED.value

    def test_does_not_expire_recent_requests(
        self, approval_service, create_request, deterministic_clock,
    ):
        """Recently created requests are not expired."""
        create_request()

        # Advance only 1 hour for a 24-hour timeout
        deterministic_clock.advance(3600)
        now = deterministic_clock.now()

        expired_ids = approval_service.expire_stale_requests(
            as_of=now,
            timeout_hours=24,
        )

        assert expired_ids == []

    def test_expire_emits_audit_events(
        self, session, approval_service, create_request, deterministic_clock,
    ):
        """Each expired request gets an APPROVAL_EXPIRED audit event."""
        entity_id = uuid4()
        dto1 = create_request(entity_id=entity_id, transition_action="a1")

        deterministic_clock.advance(1)
        entity_id2 = uuid4()
        dto2 = create_request(entity_id=entity_id2, transition_action="a2")

        # Advance past timeout
        deterministic_clock.advance(90_000)
        now = deterministic_clock.now()

        expired_ids = approval_service.expire_stale_requests(
            as_of=now,
            timeout_hours=24,
        )

        assert len(expired_ids) == 2

        for rid in expired_ids:
            audit_events = session.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "ApprovalRequest",
                    AuditEvent.entity_id == rid,
                    AuditEvent.action == AuditAction.APPROVAL_EXPIRED,
                )
            ).scalars().all()
            assert len(audit_events) == 1
            assert audit_events[0].payload["timeout_hours"] == 24

    def test_expire_skips_already_resolved(
        self, approval_service, create_request, deterministic_clock,
    ):
        """Already-resolved requests are not expired even if old."""
        dto = create_request()
        approval_service.record_decision(
            request_id=dto.request_id,
            actor_id=uuid4(),
            actor_role="manager",
            decision=ApprovalDecision.APPROVE,
        )

        deterministic_clock.advance(90_000)
        now = deterministic_clock.now()

        expired_ids = approval_service.expire_stale_requests(
            as_of=now,
            timeout_hours=24,
        )

        assert expired_ids == []
