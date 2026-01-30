"""
Accounts Payable Workflows.

State machines for invoice and payment processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.ap.workflows")


@dataclass(frozen=True)
class Guard:
    """A condition for a transition."""
    name: str
    description: str


@dataclass(frozen=True)
class Transition:
    """A valid state transition."""
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False


@dataclass(frozen=True)
class Workflow:
    """A state machine definition."""
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

MATCH_WITHIN_TOLERANCE = Guard(
    name="match_within_tolerance",
    description="Three-way match variance within configured tolerance",
)

APPROVAL_THRESHOLD_MET = Guard(
    name="approval_threshold_met",
    description="Required approval level obtained for invoice amount",
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
            APPROVAL_THRESHOLD_MET.name,
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
    transitions=(
        Transition("draft", "pending_match", action="submit"),
        Transition("draft", "cancelled", action="cancel"),
        Transition("pending_match", "matched", action="match", guard=MATCH_WITHIN_TOLERANCE, posts_entry=True),
        Transition("pending_match", "pending_approval", action="match_override"),  # manual override
        Transition("pending_match", "cancelled", action="cancel"),
        Transition("matched", "pending_approval", action="request_approval"),
        Transition("pending_approval", "approved", action="approve", guard=APPROVAL_THRESHOLD_MET),
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
    transitions=(
        Transition("draft", "pending_approval", action="submit", guard=SUFFICIENT_FUNDS),
        Transition("draft", "voided", action="void"),
        Transition("pending_approval", "approved", action="approve", guard=PAYMENT_APPROVED),
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
