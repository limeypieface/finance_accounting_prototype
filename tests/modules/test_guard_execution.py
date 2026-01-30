"""
Guard Execution Tests - Business Rule Enforcement.

Current workflow tests verify guards EXIST but not that they EXECUTE.
These tests attempt to bypass guards to verify they are actually enforced.

CRITICAL: Without guard execution, the workflow is just documentation.
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from finance_modules.ap.workflows import INVOICE_WORKFLOW as AP_INVOICE_WORKFLOW
from finance_modules.ap.workflows import PAYMENT_WORKFLOW as AP_PAYMENT_WORKFLOW
from finance_modules.ar.workflows import INVOICE_WORKFLOW as AR_INVOICE_WORKFLOW
from finance_modules.procurement.workflows import REQUISITION_WORKFLOW, PURCHASE_ORDER_WORKFLOW
from finance_modules.inventory.workflows import RECEIPT_WORKFLOW as INV_RECEIPT_WORKFLOW
from finance_modules.inventory.workflows import ISSUE_WORKFLOW as INV_ISSUE_WORKFLOW
from finance_modules.payroll.workflows import PAYROLL_RUN_WORKFLOW
from finance_modules.gl.workflows import PERIOD_CLOSE_WORKFLOW


# =============================================================================
# Test Infrastructure - Mock Context for Guard Testing
# =============================================================================

class MockWorkflowContext:
    """Mock context for testing guard execution."""

    def __init__(self, **kwargs):
        self.data = kwargs

    def __getattr__(self, name):
        return self.data.get(name)


def get_transition(workflow, from_state: str, action: str):
    """Get a specific transition from workflow."""
    for t in workflow.transitions:
        if t.from_state == from_state and t.action == action:
            return t
    return None


def get_guarded_transitions(workflow):
    """Get all transitions that have guards."""
    return [t for t in workflow.transitions if t.guard is not None]


# =============================================================================
# AP Invoice Guard Execution Tests
# =============================================================================

class TestAPInvoiceGuardExecution:
    """Test AP Invoice guard execution, not just existence."""

    def test_match_guard_exists(self):
        """Verify match transition has a guard."""
        transition = get_transition(AP_INVOICE_WORKFLOW, "pending_match", "match")
        assert transition is not None, "Match transition not found"
        assert transition.guard is not None, "Match transition should have a guard"

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_match_guard_rejects_mismatched_invoice(self):
        """Match guard should reject invoice that exceeds tolerance."""
        transition = get_transition(AP_INVOICE_WORKFLOW, "pending_match", "match")

        # Create context with mismatched amounts (exceeds tolerance)
        context = MockWorkflowContext(
            invoice_amount=Decimal("1000.00"),
            po_amount=Decimal("800.00"),  # 20% variance - likely exceeds tolerance
            tolerance_percent=Decimal("5"),
        )

        # Guard should reject this
        # But guards are just metadata - no execution!
        if transition.guard:
            # This would need a guard executor to actually run
            result = execute_guard(transition.guard, context)
            assert not result, "Guard should reject mismatched invoice"

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_match_guard_accepts_matched_invoice(self):
        """Match guard should accept invoice within tolerance."""
        transition = get_transition(AP_INVOICE_WORKFLOW, "pending_match", "match")

        # Create context with matching amounts
        context = MockWorkflowContext(
            invoice_amount=Decimal("1000.00"),
            po_amount=Decimal("1000.00"),  # Exact match
            tolerance_percent=Decimal("5"),
        )

        if transition.guard:
            result = execute_guard(transition.guard, context)
            assert result, "Guard should accept matched invoice"


class TestAPPaymentGuardExecution:
    """Test AP Payment guard execution."""

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_approve_guard_rejects_unapproved_invoice(self):
        """Payment approval guard should verify invoice is approved."""
        transition = get_transition(AP_PAYMENT_WORKFLOW, "pending", "approve")

        if transition and transition.guard:
            context = MockWorkflowContext(
                invoice_status="pending_approval",  # Not approved!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject payment for unapproved invoice"


# =============================================================================
# Procurement Guard Execution Tests
# =============================================================================

class TestRequisitionGuardExecution:
    """Test Requisition guard execution."""

    @pytest.mark.xfail(reason="Requisition workflow may not have pending_approval state")
    def test_approve_guard_exists(self):
        """Verify requisition approval has a budget guard."""
        transition = get_transition(REQUISITION_WORKFLOW, "pending_approval", "approve")
        assert transition is not None, "Approve transition not found"
        # Should have BUDGET_AVAILABLE guard
        if transition.guard:
            assert "budget" in transition.guard.name.lower() or \
                   "budget" in transition.guard.description.lower(), \
                   "Approval should check budget"

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_approve_guard_rejects_over_budget_requisition(self):
        """Budget guard should reject requisition exceeding budget."""
        transition = get_transition(REQUISITION_WORKFLOW, "pending_approval", "approve")

        if transition and transition.guard:
            context = MockWorkflowContext(
                requisition_amount=Decimal("100000"),
                available_budget=Decimal("50000"),  # Not enough!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject over-budget requisition"


class TestPurchaseOrderGuardExecution:
    """Test Purchase Order guard execution."""

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_receive_guard_validates_po_is_sent(self):
        """Cannot receive against PO that wasn't sent to vendor."""
        transition = get_transition(PURCHASE_ORDER_WORKFLOW, "sent", "receive")

        if transition and transition.guard:
            context = MockWorkflowContext(
                po_status="draft",  # Not sent yet!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject receipt against unsent PO"

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_close_guard_validates_fully_received(self):
        """Cannot close PO until fully received."""
        transition = get_transition(PURCHASE_ORDER_WORKFLOW, "partial", "close")

        if transition and transition.guard:
            context = MockWorkflowContext(
                quantity_ordered=Decimal("100"),
                quantity_received=Decimal("50"),  # Only 50% received!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject close on partial PO"


# =============================================================================
# Inventory Guard Execution Tests
# =============================================================================

class TestInventoryIssueGuardExecution:
    """Test Inventory Issue guard execution."""

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_pick_guard_validates_stock_available(self):
        """Cannot pick items not in stock."""
        transition = get_transition(INV_ISSUE_WORKFLOW, "released", "pick")

        if transition and transition.guard:
            context = MockWorkflowContext(
                requested_quantity=Decimal("100"),
                available_quantity=Decimal("50"),  # Not enough!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject pick exceeding available stock"


class TestInventoryReceiptGuardExecution:
    """Test Inventory Receipt guard execution."""

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_accept_guard_validates_qc_passed(self):
        """Cannot accept receipt that failed QC."""
        transition = get_transition(INV_RECEIPT_WORKFLOW, "inspecting", "accept")

        if transition and transition.guard:
            context = MockWorkflowContext(
                qc_status="failed",  # Failed QC!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject receipt that failed QC"


# =============================================================================
# Payroll Guard Execution Tests
# =============================================================================

class TestPayrollGuardExecution:
    """Test Payroll guard execution."""

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_approve_guard_validates_calculations(self):
        """Cannot approve payroll with calculation errors."""
        transition = get_transition(PAYROLL_RUN_WORKFLOW, "calculated", "approve")

        if transition and transition.guard:
            context = MockWorkflowContext(
                has_calculation_errors=True,
                error_count=5,
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject payroll with errors"

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_disburse_guard_validates_approval(self):
        """Cannot disburse unapproved payroll."""
        transition = get_transition(PAYROLL_RUN_WORKFLOW, "approved", "disburse")

        if transition and transition.guard:
            context = MockWorkflowContext(
                approval_status="pending",  # Not approved!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject disbursement of unapproved payroll"


# =============================================================================
# GL Period Close Guard Execution Tests
# =============================================================================

class TestPeriodCloseGuardExecution:
    """Test Period Close guard execution."""

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_close_guard_validates_all_subledgers_closed(self):
        """Cannot close GL until all subledgers are closed."""
        transition = get_transition(PERIOD_CLOSE_WORKFLOW, "closing", "close")

        if transition and transition.guard:
            context = MockWorkflowContext(
                ap_closed=True,
                ar_closed=True,
                inventory_closed=False,  # Not closed!
                payroll_closed=True,
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject GL close with open subledger"

    @pytest.mark.xfail(reason="Guards are not executed - only declared")
    def test_close_guard_validates_no_pending_transactions(self):
        """Cannot close period with pending transactions."""
        transition = get_transition(PERIOD_CLOSE_WORKFLOW, "closing", "close")

        if transition and transition.guard:
            context = MockWorkflowContext(
                pending_transaction_count=15,  # Transactions still pending!
            )
            result = execute_guard(transition.guard, context)
            assert not result, "Should reject close with pending transactions"


# =============================================================================
# Guard Execution Framework (Missing from System)
# =============================================================================

def execute_guard(guard, context):
    """
    Execute a guard against a context.

    CRITICAL: This function doesn't exist in the system!
    Guards are declared but never executed.

    This is what's needed:
    1. Guard should have an `evaluate(context)` method
    2. Workflow executor should call guard before transition
    3. Failed guard should prevent transition
    """
    # Guards don't have execution logic - they're just metadata
    # This would need to be implemented
    raise NotImplementedError(
        "Guard execution not implemented - guards are documentation only"
    )


class TestGuardExecutionFramework:
    """Test that guard execution framework exists."""

    @pytest.mark.xfail(reason="Guards are metadata only - no evaluate method exists")
    def test_guard_has_evaluate_method(self):
        """Guards should have an evaluate method."""
        guarded = get_guarded_transitions(AP_INVOICE_WORKFLOW)
        if guarded:
            guard = guarded[0].guard
            # Check if guard has evaluate method
            has_evaluate = hasattr(guard, 'evaluate') or hasattr(guard, '__call__')
            # Currently guards are just dataclasses with name/description
            assert has_evaluate, "Guard should have evaluate/call method"

    @pytest.mark.xfail(reason="No workflow executor exists")
    def test_workflow_executor_exists(self):
        """Workflow executor should exist to run transitions."""
        # There's no executor - workflows are just data structures
        from finance_modules.ap.workflows import WorkflowExecutor
        assert WorkflowExecutor is not None

    @pytest.mark.xfail(reason="No guard executor exists")
    def test_guard_executor_exists(self):
        """Guard executor should exist to evaluate guards."""
        from finance_modules.ap.workflows import GuardExecutor
        assert GuardExecutor is not None


# =============================================================================
# Summary
# =============================================================================

class TestGuardExecutionSummary:
    """Summary of guard execution gaps."""

    def test_document_guard_execution_gaps(self):
        """
        Documents guard execution gaps.

        CRITICAL FINDING: Guards are metadata only!

        Current state:
        - Guards have name and description
        - Guards are attached to transitions
        - NO execution logic exists
        - NO workflow executor calls guards
        - Transitions can happen regardless of guard conditions

        Missing components:
        1. Guard.evaluate(context) -> bool method
        2. WorkflowExecutor.transition(entity, action, context) method
        3. Guard evaluation before transition
        4. Rejection on guard failure

        Risk: Any business rule encoded in a guard can be bypassed
        because guards are never evaluated.

        Test categories that all fail (xfail):
        - AP Invoice match tolerance: 2 tests
        - AP Payment approval: 1 test
        - Requisition budget: 1 test
        - PO receive/close: 2 tests
        - Inventory issue stock: 1 test
        - Inventory receipt QC: 1 test
        - Payroll approve/disburse: 2 tests
        - Period close subledger: 2 tests

        Total: 12 guard execution tests that fail because guards aren't executed.
        """
        pass
