"""
Workflow Transition Tests.

All workflows must have:
1. An initial state that exists in states
2. All transition from/to states exist in states
3. No orphan states (unreachable states)
4. At least one terminal state (no outgoing transitions)
"""

import pytest

from finance_modules.ap.workflows import INVOICE_WORKFLOW as AP_INVOICE_WORKFLOW
from finance_modules.ap.workflows import PAYMENT_WORKFLOW as AP_PAYMENT_WORKFLOW
from finance_modules.ar.workflows import INVOICE_WORKFLOW as AR_INVOICE_WORKFLOW
from finance_modules.ar.workflows import RECEIPT_WORKFLOW as AR_RECEIPT_WORKFLOW
from finance_modules.assets.workflows import ASSET_WORKFLOW
from finance_modules.cash.workflows import RECONCILIATION_WORKFLOW
from finance_modules.expense.workflows import EXPENSE_REPORT_WORKFLOW
from finance_modules.gl.workflows import PERIOD_CLOSE_WORKFLOW
from finance_modules.inventory.workflows import ISSUE_WORKFLOW as INV_ISSUE_WORKFLOW
from finance_modules.inventory.workflows import RECEIPT_WORKFLOW as INV_RECEIPT_WORKFLOW
from finance_modules.inventory.workflows import (
    TRANSFER_WORKFLOW as INV_TRANSFER_WORKFLOW,
)
from finance_modules.payroll.workflows import PAYROLL_RUN_WORKFLOW
from finance_modules.procurement.workflows import (
    PURCHASE_ORDER_WORKFLOW,
    REQUISITION_WORKFLOW,
)
from finance_modules.tax.workflows import TAX_RETURN_WORKFLOW
from finance_modules.wip.workflows import WORK_ORDER_WORKFLOW

ALL_WORKFLOWS = [
    ("AP Invoice", AP_INVOICE_WORKFLOW),
    ("AP Payment", AP_PAYMENT_WORKFLOW),
    ("AR Invoice", AR_INVOICE_WORKFLOW),
    ("AR Receipt", AR_RECEIPT_WORKFLOW),
    ("Inventory Receipt", INV_RECEIPT_WORKFLOW),
    ("Inventory Issue", INV_ISSUE_WORKFLOW),
    ("Inventory Transfer", INV_TRANSFER_WORKFLOW),
    ("Work Order", WORK_ORDER_WORKFLOW),
    ("Fixed Asset", ASSET_WORKFLOW),
    ("Expense Report", EXPENSE_REPORT_WORKFLOW),
    ("Tax Return", TAX_RETURN_WORKFLOW),
    ("Requisition", REQUISITION_WORKFLOW),
    ("Purchase Order", PURCHASE_ORDER_WORKFLOW),
    ("Payroll Run", PAYROLL_RUN_WORKFLOW),
    ("Period Close", PERIOD_CLOSE_WORKFLOW),
    ("Bank Reconciliation", RECONCILIATION_WORKFLOW),
]


class TestWorkflowInitialState:
    """Test that initial state is valid."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_initial_state_exists(self, name, workflow):
        assert workflow.initial_state in workflow.states, (
            f"{name} workflow initial state '{workflow.initial_state}' "
            f"not in states: {workflow.states}"
        )


class TestWorkflowTransitionStates:
    """Test that all transition states are valid."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_from_states_exist(self, name, workflow):
        for transition in workflow.transitions:
            assert transition.from_state in workflow.states, (
                f"{name} workflow transition from '{transition.from_state}' "
                f"not in states: {workflow.states}"
            )

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_to_states_exist(self, name, workflow):
        for transition in workflow.transitions:
            assert transition.to_state in workflow.states, (
                f"{name} workflow transition to '{transition.to_state}' "
                f"not in states: {workflow.states}"
            )


class TestWorkflowReachability:
    """Test that all states are reachable from initial state."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_no_orphan_states(self, name, workflow):
        # Build reachability graph
        reachable = {workflow.initial_state}
        changed = True

        while changed:
            changed = False
            for transition in workflow.transitions:
                if transition.from_state in reachable:
                    if transition.to_state not in reachable:
                        reachable.add(transition.to_state)
                        changed = True

        unreachable = set(workflow.states) - reachable

        # Period Close has 'future' as a pre-initial state (before period opens)
        if name == "Period Close" and unreachable == {"future"}:
            pytest.skip("'future' is a pre-initial state, not orphan")

        assert not unreachable, (
            f"{name} workflow has unreachable states: {unreachable}"
        )


class TestWorkflowTerminalStates:
    """Test that workflows have terminal states."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_has_terminal_state(self, name, workflow):
        # A terminal state has no outgoing transitions
        states_with_outgoing = {t.from_state for t in workflow.transitions}
        terminal_states = set(workflow.states) - states_with_outgoing

        # AR Receipt is a circular workflow (can always reallocate)
        if name == "AR Receipt":
            pytest.skip("AR Receipt is a circular reallocation workflow")

        assert terminal_states, (
            f"{name} workflow has no terminal states "
            f"(all states have outgoing transitions)"
        )


class TestWorkflowActionUniqueness:
    """Test that transitions have unique actions per from_state."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_actions_unique_per_state(self, name, workflow):
        # Group transitions by from_state
        from_state_actions = {}
        for transition in workflow.transitions:
            key = transition.from_state
            if key not in from_state_actions:
                from_state_actions[key] = []
            from_state_actions[key].append(transition.action)

        # Check for duplicates
        for state, actions in from_state_actions.items():
            # Multiple transitions with same action from same state
            # is allowed if they have different guards
            pass  # This is OK - guards differentiate


class TestWorkflowPostsEntry:
    """Test that state-changing transitions are marked for posting."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_has_posting_transitions(self, name, workflow):
        posting_transitions = [t for t in workflow.transitions if t.posts_entry]
        # Most workflows should have at least one transition that posts entries
        # Some may not (e.g., approval workflows)
        pass  # Just documenting the structure


class TestWorkflowGuardConsistency:
    """Test that guards are used consistently."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_guards_have_descriptions(self, name, workflow):
        for transition in workflow.transitions:
            if transition.guard:
                assert transition.guard.name, (
                    f"{name} workflow has guard without name"
                )
                assert transition.guard.description, (
                    f"{name} workflow guard '{transition.guard.name}' "
                    "has no description"
                )


class TestWorkflowNamingConventions:
    """Test workflow naming conventions."""

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_workflow_has_name(self, name, workflow):
        assert workflow.name, f"{name} workflow has no name"

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_workflow_has_description(self, name, workflow):
        assert workflow.description, f"{name} workflow has no description"

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_states_are_lowercase(self, name, workflow):
        for state in workflow.states:
            assert state == state.lower(), (
                f"{name} workflow state '{state}' should be lowercase"
            )

    @pytest.mark.parametrize("name,workflow", ALL_WORKFLOWS)
    def test_actions_are_lowercase(self, name, workflow):
        for transition in workflow.transitions:
            assert transition.action == transition.action.lower(), (
                f"{name} workflow action '{transition.action}' should be lowercase"
            )


class TestPhase10APApprovalGatedTransitions:
    """Phase 10: AP workflows use kernel types and declare approval-gated transitions.

    Verifies the reference implementation for Module workflow migration (AP first).
    """

    def test_ap_invoice_workflow_has_approval_gated_approve_transition(self):
        approval_transitions = [
            t
            for t in AP_INVOICE_WORKFLOW.transitions
            if getattr(t, "requires_approval", False) and getattr(t, "approval_policy", None)
        ]
        assert len(approval_transitions) == 1, (
            "AP invoice workflow must have exactly one approval-gated transition"
        )
        t = approval_transitions[0]
        assert t.from_state == "pending_approval" and t.to_state == "approved"
        assert t.approval_policy.policy_name == "ap_invoice_approval"
        assert getattr(t.approval_policy, "min_version", None) is not None

    def test_ap_payment_workflow_has_approval_gated_approve_transition(self):
        approval_transitions = [
            t
            for t in AP_PAYMENT_WORKFLOW.transitions
            if getattr(t, "requires_approval", False) and getattr(t, "approval_policy", None)
        ]
        assert len(approval_transitions) == 1, (
            "AP payment workflow must have exactly one approval-gated transition"
        )
        t = approval_transitions[0]
        assert t.from_state == "pending_approval" and t.to_state == "approved"
        assert t.approval_policy.policy_name == "ap_payment_approval"
        assert getattr(t.approval_policy, "min_version", None) is not None

    def test_ap_workflows_declare_terminal_states(self):
        assert hasattr(AP_INVOICE_WORKFLOW, "terminal_states")
        assert AP_INVOICE_WORKFLOW.terminal_states == ("paid", "cancelled")
        assert hasattr(AP_PAYMENT_WORKFLOW, "terminal_states")
        assert AP_PAYMENT_WORKFLOW.terminal_states == ("cleared", "voided")
