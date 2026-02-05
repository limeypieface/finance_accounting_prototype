"""
Accounts Payable Workflows (``finance_modules.ap.workflows``).

Responsibility
--------------
Declares the state-machine definitions for the AP invoice lifecycle and
AP payment lifecycle.  Guards express preconditions for transitions;
``posts_entry=True`` marks transitions that produce journal entries.
Transitions with ``requires_approval=True`` are gated by the approval
engine (Phase 10).

Architecture position
---------------------
**Modules layer** -- declarative workflow definitions.  Imports canonical
Guard, Transition, Workflow from ``finance_kernel.domain.workflow``.
Consumed by the workflow engine at runtime.

Invariants enforced
-------------------
* All ``Workflow``, ``Transition``, and ``Guard`` instances are
  ``frozen=True`` -- immutable after module load.
* Transitions with ``posts_entry=True`` correspond 1:1 to AP profiles
  that produce journal entries.

Failure modes
-------------
* Invalid transition (state not in ``states``) detected at runtime by
  the workflow engine, not by these definitions.

Audit relevance
---------------
Workflow definitions logged at module-load time with state counts and
transition counts for configuration audit.
"""

from finance_kernel.domain.workflow import ApprovalPolicyRef, Guard, Transition, Workflow
from finance_kernel.logging_config import get_logger

logger = get_logger("modules.ap.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

MATCH_WITHIN_TOLERANCE = Guard(
    name="match_within_tolerance",
    description="Three-way match variance within configured tolerance",
)

APPROVAL_THRESHOLD_MET = Guard(
    name="approval_threshold_met",
    description="(Deprecated) approval is enforced via approval engine, not guard",
)

SUFFICIENT_FUNDS = Guard(
    name="sufficient_funds",
    description="Bank account has sufficient funds for payment",
)

PAYMENT_APPROVED = Guard(
    name="payment_approved",
    description="Payment batch approved for release",
)


# -----------------------------------------------------------------------------
# Invoice Workflow
# -----------------------------------------------------------------------------

logger.info(
    "ap_workflow_guards_defined",
    extra={
        "guards": [
            MATCH_WITHIN_TOLERANCE.name,
            SUFFICIENT_FUNDS.name,
            PAYMENT_APPROVED.name,
        ],
    },
)

INVOICE_WORKFLOW = Workflow(
    name="ap_invoice",
    description="Vendor invoice processing workflow",
    initial_state="draft",
    states=(
        "draft",
        "pending_match",
        "matched",
        "pending_approval",
        "approved",
        "scheduled",
        "paid",
        "cancelled",
    ),
    terminal_states=("paid", "cancelled"),
    transitions=(
        Transition("draft", "pending_match", action="submit"),
        Transition("draft", "cancelled", action="cancel"),
        Transition("pending_match", "matched", action="match", guard=MATCH_WITHIN_TOLERANCE, posts_entry=True),
        Transition("pending_match", "pending_approval", action="match_override"),  # manual override
        Transition("pending_match", "cancelled", action="cancel"),
        Transition("matched", "pending_approval", action="request_approval"),
        Transition(
            "pending_approval",
            "approved",
            action="approve",
            guard=APPROVAL_THRESHOLD_MET,
            requires_approval=True,
            approval_policy=ApprovalPolicyRef(policy_name="ap_invoice_approval", min_version=1),
        ),
        Transition("pending_approval", "matched", action="reject"),
        Transition("approved", "scheduled", action="schedule_payment"),
        Transition("scheduled", "paid", action="mark_paid"),
        Transition("approved", "cancelled", action="cancel"),
    ),
)

logger.info(
    "ap_invoice_workflow_registered",
    extra={
        "workflow_name": INVOICE_WORKFLOW.name,
        "state_count": len(INVOICE_WORKFLOW.states),
        "transition_count": len(INVOICE_WORKFLOW.transitions),
        "initial_state": INVOICE_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Payment Workflow
# -----------------------------------------------------------------------------

PAYMENT_WORKFLOW = Workflow(
    name="ap_payment",
    description="Payment processing workflow",
    initial_state="draft",
    states=(
        "draft",
        "pending_approval",
        "approved",
        "submitted",
        "cleared",
        "voided",
    ),
    terminal_states=("cleared", "voided"),
    transitions=(
        Transition("draft", "pending_approval", action="submit", guard=SUFFICIENT_FUNDS),
        Transition("draft", "voided", action="void"),
        Transition(
            "pending_approval",
            "approved",
            action="approve",
            guard=PAYMENT_APPROVED,
            requires_approval=True,
            approval_policy=ApprovalPolicyRef(policy_name="ap_payment_approval", min_version=1),
        ),
        Transition("pending_approval", "draft", action="reject"),
        Transition("approved", "submitted", action="release", posts_entry=True),
        Transition("approved", "voided", action="void"),
        Transition("submitted", "cleared", action="confirm_cleared"),
        Transition("submitted", "voided", action="void", posts_entry=True),  # reversal entry
    ),
)

logger.info(
    "ap_payment_workflow_registered",
    extra={
        "workflow_name": PAYMENT_WORKFLOW.name,
        "state_count": len(PAYMENT_WORKFLOW.states),
        "transition_count": len(PAYMENT_WORKFLOW.transitions),
        "initial_state": PAYMENT_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Directive: no generic workflows. Each financial action has its own lifecycle.
# See docs/WORKFLOW_DIRECTIVE.md.
# -----------------------------------------------------------------------------

def _ap_draft_posted_workflow(name: str, description: str) -> Workflow:
    """Draft -> posted workflow for AP action-specific lifecycles."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        terminal_states=("posted",),
        transitions=(Transition("draft", "posted", action="post"),),
    )

AP_INVENTORY_INVOICE_WORKFLOW = _ap_draft_posted_workflow(
    "ap_inventory_invoice",
    "Inventory (receipt) invoice posting",
)
AP_ACCRUAL_WORKFLOW = _ap_draft_posted_workflow(
    "ap_accrual",
    "Accrual posting",
)
AP_ACCRUAL_REVERSAL_WORKFLOW = _ap_draft_posted_workflow(
    "ap_accrual_reversal",
    "Accrual reversal posting",
)
AP_PREPAYMENT_WORKFLOW = _ap_draft_posted_workflow(
    "ap_prepayment",
    "Prepayment posting",
)
AP_PREPAYMENT_APPLICATION_WORKFLOW = _ap_draft_posted_workflow(
    "ap_prepayment_application",
    "Prepayment application to invoice",
)

for wf in (
    AP_INVENTORY_INVOICE_WORKFLOW,
    AP_ACCRUAL_WORKFLOW,
    AP_ACCRUAL_REVERSAL_WORKFLOW,
    AP_PREPAYMENT_WORKFLOW,
    AP_PREPAYMENT_APPLICATION_WORKFLOW,
):
    logger.info(
        "ap_workflow_registered",
        extra={
            "workflow_name": wf.name,
            "state_count": len(wf.states),
            "transition_count": len(wf.transitions),
            "initial_state": wf.initial_state,
        },
    )
