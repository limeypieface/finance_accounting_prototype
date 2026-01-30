"""
Travel & Expense Workflows.

State machine for expense report processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.expense.workflows")


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

RECEIPTS_ATTACHED = Guard(
    name="receipts_attached",
    description="Required receipts are attached",
)

WITHIN_POLICY = Guard(
    name="within_policy",
    description="All expenses are within policy limits",
)

APPROVAL_AUTHORITY = Guard(
    name="approval_authority",
    description="Approver has sufficient authority",
)

logger.info(
    "expense_workflow_guards_defined",
    extra={
        "guards": [
            RECEIPTS_ATTACHED.name,
            WITHIN_POLICY.name,
            APPROVAL_AUTHORITY.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Expense Report Workflow
# -----------------------------------------------------------------------------

EXPENSE_REPORT_WORKFLOW = Workflow(
    name="expense_report",
    description="Expense report lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "submitted",
        "pending_approval",
        "approved",
        "rejected",
        "processing",
        "paid",
    ),
    transitions=(
        Transition("draft", "submitted", action="submit", guard=RECEIPTS_ATTACHED),
        Transition("submitted", "pending_approval", action="route_for_approval"),
        Transition("pending_approval", "approved", action="approve", guard=APPROVAL_AUTHORITY, posts_entry=True),
        Transition("pending_approval", "rejected", action="reject"),
        Transition("rejected", "draft", action="revise"),
        Transition("approved", "processing", action="process_payment"),
        Transition("processing", "paid", action="mark_paid", posts_entry=True),
    ),
)

logger.info(
    "expense_report_workflow_registered",
    extra={
        "workflow_name": EXPENSE_REPORT_WORKFLOW.name,
        "state_count": len(EXPENSE_REPORT_WORKFLOW.states),
        "transition_count": len(EXPENSE_REPORT_WORKFLOW.transitions),
        "initial_state": EXPENSE_REPORT_WORKFLOW.initial_state,
    },
)
