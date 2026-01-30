"""
Cash Module Workflows.

State machines for cash management processes.
The workflow engine (shared infrastructure) executes these.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.cash.workflows")


@dataclass(frozen=True)
class Guard:
    """A condition that must be true for a transition to be allowed."""
    name: str
    description: str


@dataclass(frozen=True)
class Transition:
    """A valid state transition in a workflow."""
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False  # if True, triggers journal entry on transition


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

VARIANCE_WITHIN_TOLERANCE = Guard(
    name="variance_within_tolerance",
    description="Reconciliation variance is within configured tolerance",
)

ALL_ITEMS_MATCHED = Guard(
    name="all_items_matched",
    description="All bank transactions matched to book entries",
)

logger.info(
    "cash_workflow_guards_defined",
    extra={
        "guards": [
            VARIANCE_WITHIN_TOLERANCE.name,
            ALL_ITEMS_MATCHED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Reconciliation Workflow
# -----------------------------------------------------------------------------

RECONCILIATION_WORKFLOW = Workflow(
    name="bank_reconciliation",
    description="Bank account reconciliation process",
    initial_state="draft",
    states=(
        "draft",
        "in_progress",
        "pending_review",
        "completed",
    ),
    transitions=(
        Transition(
            from_state="draft",
            to_state="in_progress",
            action="start",
        ),
        Transition(
            from_state="in_progress",
            to_state="pending_review",
            action="submit",
            guard=ALL_ITEMS_MATCHED,
        ),
        Transition(
            from_state="in_progress",
            to_state="draft",
            action="cancel",
        ),
        Transition(
            from_state="pending_review",
            to_state="completed",
            action="approve",
            guard=VARIANCE_WITHIN_TOLERANCE,
            posts_entry=True,  # posts reconciliation adjustment if variance exists
        ),
        Transition(
            from_state="pending_review",
            to_state="in_progress",
            action="reject",
        ),
    ),
)

logger.info(
    "cash_reconciliation_workflow_registered",
    extra={
        "workflow_name": RECONCILIATION_WORKFLOW.name,
        "state_count": len(RECONCILIATION_WORKFLOW.states),
        "transition_count": len(RECONCILIATION_WORKFLOW.transitions),
        "initial_state": RECONCILIATION_WORKFLOW.initial_state,
    },
)
