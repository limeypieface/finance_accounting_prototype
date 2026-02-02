"""
End-to-end integration tests: Approval engine + Posting pipeline.

Closes Approval Engine Plan verification items 6–8:
- Item 6: AP invoice → request approval → approve (with policy) → post → verify journal entry
- Item 7: Small AP invoice → auto-approved → post without human intervention
- Item 8: Reject approval → verify transition blocked → no journal entry for that flow

Uses real config (US-GAAP-2026-v1) and AP workflow with ap_invoice_approval policy.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.approval import ApprovalDecision
from finance_kernel.services.module_posting_service import ModulePostingStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def approval_service(session, auditor_service, deterministic_clock):
    """Provide ApprovalService for integration tests."""
    from finance_kernel.services.approval_service import ApprovalService
    return ApprovalService(session, auditor_service, deterministic_clock)


@pytest.fixture
def workflow_executor_with_config(test_config, approval_service, deterministic_clock):
    """WorkflowExecutor wired with approval policies from CompiledPolicyPack."""
    from finance_config.bridges import build_approval_policies_for_executor
    from finance_services.workflow_executor import WorkflowExecutor

    policies = build_approval_policies_for_executor(test_config)
    return WorkflowExecutor(
        approval_service=approval_service,
        approval_policies=policies,
        clock=deterministic_clock,
    )


# ---------------------------------------------------------------------------
# Item 7: Auto-approve path — small amount → auto-approved → post
# ---------------------------------------------------------------------------


class TestApprovalAutoApproveE2E:
    """Small AP invoice amount → auto-approved → post → verify journal entry."""

    def test_auto_approve_then_post_produces_journal_entry(
        self,
        workflow_executor_with_config,
        module_posting_service,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Execute pending_approval→approve with amount below threshold, then post AP invoice."""
        from finance_modules.ap.workflows import INVOICE_WORKFLOW

        executor = workflow_executor_with_config
        entity_id = uuid4()

        # 1. Execute transition with amount below auto_approve_below (500) → auto-approved
        result = executor.execute_transition(
            workflow=INVOICE_WORKFLOW,
            entity_type="APInvoice",
            entity_id=entity_id,
            current_state="pending_approval",
            action="approve",
            actor_id=test_actor_id,
            actor_role="ap_manager",
            amount=Decimal("400.00"),
            currency="USD",
        )
        assert result.success is True
        assert result.new_state == "approved"
        assert result.approval_required is False

        # 2. Post AP invoice (simulates posting after approval)
        post_result = module_posting_service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_number": "INV-E2E-001",
                "supplier_code": "SUP-100",
                "gross_amount": "400.00",
                "po_number": None,
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("400.00"),
        )
        assert post_result.status == ModulePostingStatus.POSTED
        assert post_result.is_success
        assert len(post_result.journal_entry_ids) > 0


# ---------------------------------------------------------------------------
# Item 6: Manual approve path — request → approve → post
# ---------------------------------------------------------------------------


class TestApprovalManualApproveE2E:
    """Request approval → record approve → resume → post → verify journal entry."""

    def test_approve_then_post_produces_journal_entry(
        self,
        workflow_executor_with_config,
        module_posting_service,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Amount above threshold → approval required → approve → resume → post."""
        from finance_modules.ap.workflows import INVOICE_WORKFLOW

        executor = workflow_executor_with_config
        entity_id = uuid4()
        approver_id = uuid4()

        # 1. Execute transition with amount above auto_approve (500) → blocked, request created
        result1 = executor.execute_transition(
            workflow=INVOICE_WORKFLOW,
            entity_type="APInvoice",
            entity_id=entity_id,
            current_state="pending_approval",
            action="approve",
            actor_id=test_actor_id,
            actor_role="ap_clerk",
            amount=Decimal("5000.00"),
            currency="USD",
        )
        assert result1.success is False
        assert result1.approval_required is True
        assert result1.approval_request_id is not None
        request_id = result1.approval_request_id

        # 2. Record approval decision (manager approves)
        executor.record_approval_decision(
            request_id=request_id,
            actor_id=approver_id,
            actor_role="ap_manager",
            decision=ApprovalDecision.APPROVE,
            comment="Approved for payment",
        )

        # 3. Resume after approval → success
        result2 = executor.resume_after_approval(request_id)
        assert result2.success is True
        assert result2.new_state == "approved"

        # 4. Post AP invoice
        post_result = module_posting_service.post_event(
            event_type="ap.invoice_received",
            payload={
                "invoice_number": "INV-E2E-002",
                "supplier_code": "SUP-100",
                "gross_amount": "5000.00",
                "po_number": None,
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("5000.00"),
        )
        assert post_result.status == ModulePostingStatus.POSTED
        assert post_result.is_success
        assert len(post_result.journal_entry_ids) > 0


# ---------------------------------------------------------------------------
# Item 8: Reject path — request → reject → transition blocked
# ---------------------------------------------------------------------------


class TestApprovalRejectE2E:
    """Reject approval → verify transition blocked (no journal entry from this flow)."""

    def test_reject_blocks_transition_no_journal_entry(
        self,
        workflow_executor_with_config,
        module_posting_service,
        current_period,
        test_actor_id,
        deterministic_clock,
        session,
    ):
        """Amount above threshold → approval required → reject → resume fails."""
        from finance_kernel.models.journal import JournalEntry
        from sqlalchemy import func

        from finance_modules.ap.workflows import INVOICE_WORKFLOW

        executor = workflow_executor_with_config
        entity_id = uuid4()
        approver_id = uuid4()

        # Count journal entries before
        count_before = session.query(func.count(JournalEntry.id)).scalar() or 0

        # 1. Execute transition → blocked, request created
        result1 = executor.execute_transition(
            workflow=INVOICE_WORKFLOW,
            entity_type="APInvoice",
            entity_id=entity_id,
            current_state="pending_approval",
            action="approve",
            actor_id=test_actor_id,
            actor_role="ap_clerk",
            amount=Decimal("5000.00"),
            currency="USD",
        )
        assert result1.success is False
        assert result1.approval_required is True
        request_id = result1.approval_request_id

        # 2. Reject the request
        executor.record_approval_decision(
            request_id=request_id,
            actor_id=approver_id,
            actor_role="ap_manager",
            decision=ApprovalDecision.REJECT,
            comment="Rejected",
        )

        # 3. Resume after reject → failure (transition blocked)
        result2 = executor.resume_after_approval(request_id)
        assert result2.success is False
        assert "rejected" in result2.reason.lower()

        # 4. We did not post anything in this flow; journal count unchanged
        session.expire_all()
        count_after = session.query(func.count(JournalEntry.id)).scalar() or 0
        assert count_after == count_before
