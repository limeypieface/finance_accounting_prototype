"""
Hypothesis fuzzing across the full WorkflowExecutor.

Fuzzes events across a broad spectrum:
- All registered workflows (AP, AR, Inventory, GL, Procurement, etc.)
- Any (current_state, action) — valid transitions and invalid combinations
- Amount, currency, actor_role, context (payload-like dict)
- No crash; TransitionResult always well-formed; success/failure consistent
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False
    given = lambda *a, **k: (lambda f: f)
    settings = lambda *a, **k: (lambda f: f)
    st = None

from finance_kernel.domain.approval import TransitionResult


# All WorkflowLike workflows from modules (same set as test_workflow_transitions)
def _all_workflows():
    from tests.modules.test_workflow_transitions import ALL_WORKFLOWS

    return [w for _, w in ALL_WORKFLOWS]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def approval_service(session, auditor_service, deterministic_clock):
    from finance_kernel.services.approval_service import ApprovalService

    return ApprovalService(session, auditor_service, deterministic_clock)


@pytest.fixture
def workflow_executor(approval_service, deterministic_clock):
    from finance_services.workflow_executor import WorkflowExecutor

    return WorkflowExecutor(
        approval_service=approval_service,
        approval_policies={},
        clock=deterministic_clock,
    )


# ---------------------------------------------------------------------------
# Strategies (depend on workflow list)
# ---------------------------------------------------------------------------

if HYPOTHESIS_AVAILABLE:

    @st.composite
    def workflow_state_action(draw):
        """Draw (workflow, current_state, action) — state/action may or may not match a transition."""
        workflows = _all_workflows()
        workflow = draw(st.sampled_from(workflows))
        # Either a valid (state, action) from a transition, or random state + random action
        transitions_from = [(t.from_state, t.action) for t in workflow.transitions]
        if transitions_from:
            use_valid = draw(st.booleans())
            if use_valid:
                from_state, action = draw(st.sampled_from(transitions_from))
                return (workflow, from_state, action)
        current_state = draw(st.sampled_from(workflow.states))
        action = draw(st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu")), max_size=40))
        return (workflow, current_state, action)

    decimals_or_none = st.one_of(
        st.none(),
        st.decimals(min_value=Decimal("0"), max_value=Decimal("9999999.99"), places=2, allow_nan=False, allow_infinity=False),
    )
    currency_codes = st.sampled_from(["USD", "EUR", "GBP", "JPY"])
    actor_roles = st.sampled_from(["manager", "accountant", "ap_manager", "ar_manager", "clerk", "approver", "viewer"])
    context_dict = st.one_of(
        st.none(),
        st.dictionaries(
            keys=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=20),
            values=st.one_of(st.text(max_size=50), st.integers(), st.floats(), st.booleans(), st.none()),
            max_size=15,
        ),
    )


# ---------------------------------------------------------------------------
# Broad-spectrum: all workflows, any state/action, never crash
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestWorkflowExecutorBroadSpectrum:
    """Fuzz execute_transition across all workflows and a broad spectrum of inputs."""

    @given(
        data=st.data(),
        amount=decimals_or_none,
        currency=currency_codes,
        actor_role=actor_roles,
        context=context_dict,
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_execute_transition_never_crashes(
        self,
        data,
        amount,
        currency,
        actor_role,
        context,
        workflow_executor,
    ):
        """Execute_transition never raises; returns well-formed TransitionResult."""
        workflow, current_state, action = data.draw(workflow_state_action())
        entity_id = uuid4()
        actor_id = uuid4()

        result = workflow_executor.execute_transition(
            workflow=workflow,
            entity_type=workflow.name.replace(" ", "_") + "_Entity",
            entity_id=entity_id,
            current_state=current_state,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            amount=amount,
            currency=currency,
            context=context,
        )

        assert isinstance(result, TransitionResult)
        assert hasattr(result, "success")
        assert isinstance(result.success, bool)
        assert hasattr(result, "reason")
        assert isinstance(result.reason, str)
        assert hasattr(result, "approval_required")
        assert isinstance(result.approval_required, bool)
        assert hasattr(result, "posts_entry")
        assert isinstance(result.posts_entry, bool)
        if result.success:
            assert result.new_state is not None
            assert result.new_state in workflow.states
        else:
            assert len(result.reason) > 0

    @given(
        data=st.data(),
        amount=decimals_or_none,
        currency=currency_codes,
        context=context_dict,
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_same_input_same_result(
        self,
        data,
        amount,
        currency,
        context,
        workflow_executor,
    ):
        """Same (workflow, state, action, amount, ...) yields same result (determinism)."""
        workflow, current_state, action = data.draw(workflow_state_action())
        entity_id = uuid4()
        actor_id = uuid4()
        entity_type = workflow.name.replace(" ", "_") + "_Entity"
        actor_role = "manager"

        result1 = workflow_executor.execute_transition(
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
        )
        result2 = workflow_executor.execute_transition(
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
        )

        assert result1.success == result2.success
        assert result1.reason == result2.reason
        assert result1.new_state == result2.new_state
        assert result1.approval_required == result2.approval_required


# ---------------------------------------------------------------------------
# With approval policies: fuzz amount/currency/role for approval-gated workflows
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_executor_with_config(test_config, approval_service, deterministic_clock):
    """WorkflowExecutor with real approval policies (for approval-path fuzzing)."""
    from finance_config.bridges import build_approval_policies_for_executor
    from finance_services.workflow_executor import WorkflowExecutor

    policies = build_approval_policies_for_executor(test_config)
    return WorkflowExecutor(
        approval_service=approval_service,
        approval_policies=policies,
        clock=deterministic_clock,
    )


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestWorkflowExecutorWithApprovalPolicies:
    """Fuzz execute_transition with real approval policies (AP invoice/payment, etc.)."""

    @given(
        data=st.data(),
        amount=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("100000"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        actor_role=st.sampled_from(["ap_manager", "manager", "accountant", "clerk"]),
    )
    @settings(
        max_examples=200,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_execute_transition_with_policies_never_crashes(
        self,
        data,
        amount,
        actor_role,
        workflow_executor_with_config,
    ):
        """With approval policies, execute_transition never crashes; result well-formed.

        Currency fixed to USD to match US-GAAP config approval policies (avoid
        ApprovalCurrencyMismatchError when policy expects USD).
        """
        currency = "USD"
        workflows = _all_workflows()
        workflow = data.draw(st.sampled_from(workflows))
        # Pick a transition that exists (so we hit policy resolution)
        if not workflow.transitions:
            pytest.skip("Workflow has no transitions")
        transition = data.draw(st.sampled_from(workflow.transitions))
        current_state = transition.from_state
        action = transition.action

        entity_id = uuid4()
        actor_id = uuid4()

        result = workflow_executor_with_config.execute_transition(
            workflow=workflow,
            entity_type=workflow.name.replace(" ", "_") + "_Entity",
            entity_id=entity_id,
            current_state=current_state,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            amount=amount,
            currency=currency,
            context={"invoice_id": str(entity_id)},
        )

        assert isinstance(result, TransitionResult)
        assert isinstance(result.success, bool)
        assert isinstance(result.reason, str)
        assert isinstance(result.approval_required, bool)
        if result.success:
            assert result.new_state is not None
            assert result.new_state in workflow.states
