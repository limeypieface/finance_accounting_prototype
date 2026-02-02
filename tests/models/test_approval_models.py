"""
ORM model tests for the approval persistence layer.

Tests: ApprovalRequestModel, ApprovalDecisionModel -- CRUD, DTO round-trips,
relationship loading, immutability enforcement, and structural constraints.

These are ORM-level tests only.  Service-layer behaviour is tested elsewhere.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from finance_kernel.domain.approval import (
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalRequest,
    ApprovalStatus,
)
from finance_kernel.exceptions import ImmutabilityViolationError
from finance_kernel.models.approval import (
    ApprovalDecisionModel,
    ApprovalRequestModel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request_model(
    *,
    request_id=None,
    workflow_name="invoice_approval",
    entity_type="APInvoice",
    entity_id=None,
    transition_action="submit",
    from_state="draft",
    to_state="pending_approval",
    policy_name="standard_approval",
    policy_version=1,
    policy_hash="abc123",
    matched_rule="rule_over_5k",
    amount=Decimal("10000.00"),
    currency="USD",
    requestor_id=None,
    status="pending",
    created_at=None,
    resolved_at=None,
    original_request_hash="hash_xyz",
):
    return ApprovalRequestModel(
        request_id=request_id or uuid4(),
        workflow_name=workflow_name,
        entity_type=entity_type,
        entity_id=entity_id or uuid4(),
        transition_action=transition_action,
        from_state=from_state,
        to_state=to_state,
        policy_name=policy_name,
        policy_version=policy_version,
        policy_hash=policy_hash,
        matched_rule=matched_rule,
        amount=amount,
        currency=currency,
        requestor_id=requestor_id or uuid4(),
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
        resolved_at=resolved_at,
        original_request_hash=original_request_hash,
    )


def _make_decision_model(
    *,
    decision_id=None,
    request_id,
    actor_id=None,
    actor_role="finance_manager",
    decision="approve",
    comment="Looks good.",
    decided_at=None,
):
    return ApprovalDecisionModel(
        decision_id=decision_id or uuid4(),
        request_id=request_id,
        actor_id=actor_id or uuid4(),
        actor_role=actor_role,
        decision=decision,
        comment=comment,
        decided_at=decided_at or datetime.now(timezone.utc),
    )


# ===========================================================================
# 1. ApprovalRequestModel CRUD
# ===========================================================================


class TestApprovalRequestModelCRUD:
    """Create, flush, and read back ApprovalRequestModel instances."""

    def test_create_and_flush_persists_all_fields(self, session):
        """Create an approval request, flush, and verify all fields persist."""
        req_id = uuid4()
        entity_id = uuid4()
        requestor_id = uuid4()
        now = datetime.now(timezone.utc)

        model = ApprovalRequestModel(
            request_id=req_id,
            workflow_name="po_approval",
            entity_type="PurchaseOrder",
            entity_id=entity_id,
            transition_action="approve",
            from_state="draft",
            to_state="approved",
            policy_name="po_policy_v2",
            policy_version=2,
            policy_hash="sha256abc",
            matched_rule="over_10k",
            amount=Decimal("25000.50"),
            currency="EUR",
            requestor_id=requestor_id,
            status="pending",
            created_at=now,
            resolved_at=None,
            original_request_hash="orig_hash_123",
        )
        session.add(model)
        session.flush()

        queried = session.get(ApprovalRequestModel, model.id)
        assert queried is not None
        assert queried.request_id == req_id
        assert queried.workflow_name == "po_approval"
        assert queried.entity_type == "PurchaseOrder"
        assert queried.entity_id == entity_id
        assert queried.transition_action == "approve"
        assert queried.from_state == "draft"
        assert queried.to_state == "approved"
        assert queried.policy_name == "po_policy_v2"
        assert queried.policy_version == 2
        assert queried.policy_hash == "sha256abc"
        assert queried.matched_rule == "over_10k"
        assert queried.amount == Decimal("25000.50")
        assert queried.currency == "EUR"
        assert queried.requestor_id == requestor_id
        assert queried.status == "pending"
        assert queried.created_at == now
        assert queried.resolved_at is None
        assert queried.original_request_hash == "orig_hash_123"

    def test_nullable_fields_persist_as_none(self, session):
        """Optional fields (policy_hash, matched_rule, amount, resolved_at,
        original_request_hash) may be None."""
        model = ApprovalRequestModel(
            request_id=uuid4(),
            workflow_name="wf",
            entity_type="Entity",
            entity_id=uuid4(),
            transition_action="submit",
            from_state="a",
            to_state="b",
            policy_name="pol",
            policy_version=1,
            policy_hash=None,
            matched_rule=None,
            amount=None,
            currency="USD",
            requestor_id=uuid4(),
            status="pending",
            created_at=datetime.now(timezone.utc),
            resolved_at=None,
            original_request_hash=None,
        )
        session.add(model)
        session.flush()

        queried = session.get(ApprovalRequestModel, model.id)
        assert queried.policy_hash is None
        assert queried.matched_rule is None
        assert queried.amount is None
        assert queried.resolved_at is None
        assert queried.original_request_hash is None

    def test_to_dto_round_trips_all_fields(self, session):
        """Model -> flush -> to_dto produces a correct ApprovalRequest DTO."""
        req_id = uuid4()
        entity_id = uuid4()
        requestor_id = uuid4()
        now = datetime.now(timezone.utc)

        model = _make_request_model(
            request_id=req_id,
            entity_id=entity_id,
            requestor_id=requestor_id,
            created_at=now,
            amount=Decimal("7500.00"),
            currency="GBP",
            status="approved",
            resolved_at=now,
        )
        session.add(model)
        session.flush()

        dto = model.to_dto()
        assert isinstance(dto, ApprovalRequest)
        assert dto.request_id == req_id
        assert dto.workflow_name == "invoice_approval"
        assert dto.entity_type == "APInvoice"
        assert dto.entity_id == entity_id
        assert dto.transition_action == "submit"
        assert dto.from_state == "draft"
        assert dto.to_state == "pending_approval"
        assert dto.policy_name == "standard_approval"
        assert dto.policy_version == 1
        assert dto.policy_hash == "abc123"
        assert dto.matched_rule == "rule_over_5k"
        assert dto.amount == Decimal("7500.00")
        assert dto.currency == "GBP"
        assert dto.requestor_id == requestor_id
        assert dto.status == ApprovalStatus.APPROVED
        assert dto.created_at == now
        assert dto.resolved_at == now
        assert dto.original_request_hash == "hash_xyz"
        # No decisions attached
        assert dto.decisions == ()

    def test_from_dto_creates_correct_model(self, session):
        """ApprovalRequestModel.from_dto(dto) produces correct ORM model."""
        req_id = uuid4()
        entity_id = uuid4()
        requestor_id = uuid4()
        now = datetime.now(timezone.utc)

        dto = ApprovalRequest(
            request_id=req_id,
            workflow_name="expense_approval",
            entity_type="ExpenseReport",
            entity_id=entity_id,
            transition_action="submit",
            from_state="draft",
            to_state="pending",
            policy_name="expense_policy",
            policy_version=3,
            policy_hash="hash_456",
            amount=Decimal("500.00"),
            currency="USD",
            requestor_id=requestor_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
            resolved_at=None,
            matched_rule="under_1k",
            original_request_hash="orig_hash",
        )
        model = ApprovalRequestModel.from_dto(dto)

        assert model.request_id == req_id
        assert model.workflow_name == "expense_approval"
        assert model.entity_type == "ExpenseReport"
        assert model.entity_id == entity_id
        assert model.transition_action == "submit"
        assert model.from_state == "draft"
        assert model.to_state == "pending"
        assert model.policy_name == "expense_policy"
        assert model.policy_version == 3
        assert model.policy_hash == "hash_456"
        assert model.amount == Decimal("500.00")
        assert model.currency == "USD"
        assert model.requestor_id == requestor_id
        assert model.status == "pending"
        assert model.created_at == now
        assert model.resolved_at is None
        assert model.matched_rule == "under_1k"
        assert model.original_request_hash == "orig_hash"

    def test_from_dto_persists_and_round_trips(self, session):
        """from_dto -> add -> flush -> to_dto produces equivalent DTO."""
        req_id = uuid4()
        entity_id = uuid4()
        requestor_id = uuid4()
        now = datetime.now(timezone.utc)

        original = ApprovalRequest(
            request_id=req_id,
            workflow_name="wf",
            entity_type="E",
            entity_id=entity_id,
            transition_action="act",
            from_state="s0",
            to_state="s1",
            policy_name="pol",
            policy_version=1,
            policy_hash=None,
            amount=Decimal("99.99"),
            currency="USD",
            requestor_id=requestor_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
        )
        model = ApprovalRequestModel.from_dto(original)
        session.add(model)
        session.flush()

        recovered = model.to_dto()
        assert recovered.request_id == original.request_id
        assert recovered.workflow_name == original.workflow_name
        assert recovered.entity_type == original.entity_type
        assert recovered.entity_id == original.entity_id
        assert recovered.amount == original.amount
        assert recovered.status == original.status

    def test_repr(self, session):
        """__repr__ includes request_id, workflow/action, and status."""
        req_id = uuid4()
        model = _make_request_model(
            request_id=req_id,
            workflow_name="test_wf",
            transition_action="approve",
            status="escalated",
        )
        r = repr(model)
        assert str(req_id) in r
        assert "test_wf" in r
        assert "approve" in r
        assert "escalated" in r
        assert r.startswith("<ApprovalRequest ")
        assert r.endswith(">")


# ===========================================================================
# 2. ApprovalDecisionModel CRUD
# ===========================================================================


class TestApprovalDecisionModelCRUD:
    """Create, flush, and read back ApprovalDecisionModel instances."""

    def test_create_and_flush_persists_all_fields(self, session):
        """Create a decision, flush, verify all fields."""
        # Parent request first
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec_id = uuid4()
        actor_id = uuid4()
        now = datetime.now(timezone.utc)

        decision = ApprovalDecisionModel(
            decision_id=dec_id,
            request_id=req.request_id,
            actor_id=actor_id,
            actor_role="director",
            decision="reject",
            comment="Budget exceeded.",
            decided_at=now,
        )
        session.add(decision)
        session.flush()

        queried = session.get(ApprovalDecisionModel, decision.id)
        assert queried is not None
        assert queried.decision_id == dec_id
        assert queried.request_id == req.request_id
        assert queried.actor_id == actor_id
        assert queried.actor_role == "director"
        assert queried.decision == "reject"
        assert queried.comment == "Budget exceeded."
        assert queried.decided_at == now

    def test_to_dto_round_trips_correctly(self, session):
        """Decision model -> flush -> to_dto produces correct DTO."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec_id = uuid4()
        actor_id = uuid4()
        now = datetime.now(timezone.utc)

        model = ApprovalDecisionModel(
            decision_id=dec_id,
            request_id=req.request_id,
            actor_id=actor_id,
            actor_role="vp_finance",
            decision="approve",
            comment="Approved per policy.",
            decided_at=now,
        )
        session.add(model)
        session.flush()

        dto = model.to_dto()
        assert isinstance(dto, ApprovalDecisionRecord)
        assert dto.decision_id == dec_id
        assert dto.request_id == req.request_id
        assert dto.actor_id == actor_id
        assert dto.actor_role == "vp_finance"
        assert dto.decision == ApprovalDecision.APPROVE
        assert dto.comment == "Approved per policy."
        assert dto.decided_at == now

    def test_from_dto_creates_correct_model(self, session):
        """ApprovalDecisionModel.from_dto(dto) creates correct ORM model."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec_id = uuid4()
        actor_id = uuid4()
        now = datetime.now(timezone.utc)

        dto = ApprovalDecisionRecord(
            decision_id=dec_id,
            request_id=req.request_id,
            actor_id=actor_id,
            actor_role="controller",
            decision=ApprovalDecision.ESCALATE,
            comment="Needs VP sign-off.",
            decided_at=now,
        )
        model = ApprovalDecisionModel.from_dto(dto)

        assert model.decision_id == dec_id
        assert model.request_id == req.request_id
        assert model.actor_id == actor_id
        assert model.actor_role == "controller"
        assert model.decision == "escalate"
        assert model.comment == "Needs VP sign-off."
        assert model.decided_at == now

    def test_from_dto_persists_and_round_trips(self, session):
        """from_dto -> add -> flush -> to_dto produces equivalent DTO."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec_id = uuid4()
        actor_id = uuid4()
        now = datetime.now(timezone.utc)

        original = ApprovalDecisionRecord(
            decision_id=dec_id,
            request_id=req.request_id,
            actor_id=actor_id,
            actor_role="cfo",
            decision=ApprovalDecision.REJECT,
            comment="Rejected.",
            decided_at=now,
        )
        model = ApprovalDecisionModel.from_dto(original)
        session.add(model)
        session.flush()

        recovered = model.to_dto()
        assert recovered.decision_id == original.decision_id
        assert recovered.request_id == original.request_id
        assert recovered.actor_id == original.actor_id
        assert recovered.actor_role == original.actor_role
        assert recovered.decision == original.decision
        assert recovered.comment == original.comment
        assert recovered.decided_at == original.decided_at

    def test_repr(self, session):
        """__repr__ includes decision_id, request_id, and decision value."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec_id = uuid4()
        model = _make_decision_model(
            decision_id=dec_id,
            request_id=req.request_id,
            decision="reject",
        )
        r = repr(model)
        assert str(dec_id) in r
        assert str(req.request_id) in r
        assert "reject" in r
        assert r.startswith("<ApprovalDecision ")
        assert r.endswith(">")


# ===========================================================================
# 3. Relationship Loading
# ===========================================================================


class TestRelationshipLoading:
    """Verify ORM relationship between request and decisions."""

    def test_request_decisions_loads_children_ordered_by_decided_at(self, session):
        """request.decisions loads both decisions, ordered by decided_at."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        early = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        late = datetime(2024, 6, 2, 14, 0, 0, tzinfo=timezone.utc)

        actor_a = uuid4()
        actor_b = uuid4()

        # Insert the later decision first to verify ordering is by decided_at
        dec_late = _make_decision_model(
            request_id=req.request_id,
            actor_id=actor_b,
            actor_role="vp",
            decision="approve",
            decided_at=late,
        )
        dec_early = _make_decision_model(
            request_id=req.request_id,
            actor_id=actor_a,
            actor_role="manager",
            decision="approve",
            decided_at=early,
        )
        session.add_all([dec_late, dec_early])
        session.flush()

        # Expire to force reload
        session.expire(req)
        loaded = req.decisions
        assert len(loaded) == 2
        assert loaded[0].decided_at == early
        assert loaded[1].decided_at == late
        assert loaded[0].actor_id == actor_a
        assert loaded[1].actor_id == actor_b

    def test_decision_request_backref_loads_parent(self, session):
        """decision.request backref loads the parent ApprovalRequestModel."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec = _make_decision_model(request_id=req.request_id)
        session.add(dec)
        session.flush()

        # Expire to force load through relationship
        session.expire(dec)
        parent = dec.request
        assert parent is not None
        assert parent.request_id == req.request_id

    def test_to_dto_includes_decisions(self, session):
        """request.to_dto() includes child decision DTOs."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec1 = _make_decision_model(
            request_id=req.request_id,
            actor_role="manager",
            decision="approve",
        )
        dec2 = _make_decision_model(
            request_id=req.request_id,
            actor_role="director",
            decision="approve",
        )
        session.add_all([dec1, dec2])
        session.flush()

        session.expire(req)
        dto = req.to_dto()
        assert len(dto.decisions) == 2
        # Decisions are ApprovalDecisionRecord DTOs
        assert all(isinstance(d, ApprovalDecisionRecord) for d in dto.decisions)


# ===========================================================================
# 4. Immutability Enforcement (ORM Events)
# ===========================================================================


class TestDecisionImmutability:
    """Verify ORM-level immutability on ApprovalDecisionModel."""

    def test_update_decision_raises_immutability_error(self, session):
        """Updating a flushed decision raises ImmutabilityViolationError."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec = _make_decision_model(request_id=req.request_id)
        session.add(dec)
        session.flush()

        dec.comment = "Changed my mind."
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "ApprovalDecision" in str(exc_info.value)
        assert "immutable" in str(exc_info.value).lower()

    def test_delete_decision_raises_immutability_error(self, session):
        """Deleting a flushed decision raises ImmutabilityViolationError."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec = _make_decision_model(request_id=req.request_id)
        session.add(dec)
        session.flush()

        session.delete(dec)
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "ApprovalDecision" in str(exc_info.value)
        assert "cannot delete" in str(exc_info.value).lower()

    def test_update_decision_field_raises_with_correct_entity_id(self, session):
        """ImmutabilityViolationError contains the decision_id."""
        req = _make_request_model()
        session.add(req)
        session.flush()

        dec_id = uuid4()
        dec = _make_decision_model(decision_id=dec_id, request_id=req.request_id)
        session.add(dec)
        session.flush()

        dec.actor_role = "changed_role"
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert str(dec_id) in str(exc_info.value)


# ===========================================================================
# 5. Table Args / Constraints (Structural)
# ===========================================================================


class TestTableStructure:
    """Verify structural properties of the ORM models."""

    def test_request_tablename(self):
        """ApprovalRequestModel.__tablename__ is 'approval_requests'."""
        assert ApprovalRequestModel.__tablename__ == "approval_requests"

    def test_decision_tablename(self):
        """ApprovalDecisionModel.__tablename__ is 'approval_decisions'."""
        assert ApprovalDecisionModel.__tablename__ == "approval_decisions"

    def test_request_check_constraint_exists(self):
        """The status check constraint 'ck_approval_requests_valid_status' exists."""
        constraints = ApprovalRequestModel.__table__.constraints
        check_names = {
            c.name for c in constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "ck_approval_requests_valid_status" in check_names

    def test_decision_unique_constraint_exists(self):
        """The unique constraint 'uq_approval_decisions_actor' exists."""
        constraints = ApprovalDecisionModel.__table__.constraints
        uq_names = {
            c.name for c in constraints
            if hasattr(c, "name") and c.name is not None
        }
        assert "uq_approval_decisions_actor" in uq_names

    def test_request_pending_unique_index_exists(self):
        """The partial unique index 'ix_approval_requests_pending_unique' exists."""
        indexes = ApprovalRequestModel.__table__.indexes
        idx_names = {idx.name for idx in indexes}
        assert "ix_approval_requests_pending_unique" in idx_names

    def test_request_entity_status_index_exists(self):
        """The covering index 'ix_approval_requests_entity_status' exists."""
        indexes = ApprovalRequestModel.__table__.indexes
        idx_names = {idx.name for idx in indexes}
        assert "ix_approval_requests_entity_status" in idx_names

    def test_request_expiry_index_exists(self):
        """The expiry index 'ix_approval_requests_expiry' exists."""
        indexes = ApprovalRequestModel.__table__.indexes
        idx_names = {idx.name for idx in indexes}
        assert "ix_approval_requests_expiry" in idx_names

    def test_decision_request_id_index_exists(self):
        """The index 'ix_approval_decisions_request_id' exists."""
        indexes = ApprovalDecisionModel.__table__.indexes
        idx_names = {idx.name for idx in indexes}
        assert "ix_approval_decisions_request_id" in idx_names

    def test_request_id_column_is_unique(self):
        """ApprovalRequestModel.request_id has unique=True."""
        col = ApprovalRequestModel.__table__.c.request_id
        assert col.unique is True

    def test_decision_id_column_is_unique(self):
        """ApprovalDecisionModel.decision_id has unique=True."""
        col = ApprovalDecisionModel.__table__.c.decision_id
        assert col.unique is True

    def test_decision_request_id_has_foreign_key(self):
        """ApprovalDecisionModel.request_id references approval_requests.request_id."""
        col = ApprovalDecisionModel.__table__.c.request_id
        fk_targets = {fk.target_fullname for fk in col.foreign_keys}
        assert "approval_requests.request_id" in fk_targets
