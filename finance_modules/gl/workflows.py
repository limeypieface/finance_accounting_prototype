"""
General Ledger Workflows.

State machine for period close process.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.gl.workflows")


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

ALL_SUBLEDGERS_CLOSED = Guard(
    name="all_subledgers_closed",
    description="All subledger periods are closed",
)

TRIAL_BALANCE_BALANCED = Guard(
    name="trial_balance_balanced",
    description="Trial balance debits equal credits",
)

ADJUSTMENTS_POSTED = Guard(
    name="adjustments_posted",
    description="All period-end adjustments are posted",
)

YEAR_END_ENTRIES_POSTED = Guard(
    name="year_end_entries_posted",
    description="Year-end closing entries are posted",
)

logger.info(
    "gl_workflow_guards_defined",
    extra={
        "guards": [
            ALL_SUBLEDGERS_CLOSED.name,
            TRIAL_BALANCE_BALANCED.name,
            ADJUSTMENTS_POSTED.name,
            YEAR_END_ENTRIES_POSTED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Period Close Workflow
# -----------------------------------------------------------------------------

PERIOD_CLOSE_WORKFLOW = Workflow(
    name="gl_period_close",
    description="GL period close process",
    initial_state="open",
    states=(
        "future",
        "open",
        "closing",
        "closed",
        "locked",
    ),
    transitions=(
        Transition("future", "open", action="open_period"),
        Transition("open", "closing", action="begin_close", guard=ALL_SUBLEDGERS_CLOSED),
        Transition("closing", "closed", action="close", guard=TRIAL_BALANCE_BALANCED, posts_entry=True),
        Transition("closed", "open", action="reopen"),  # for adjustments
        Transition("closed", "locked", action="lock", guard=YEAR_END_ENTRIES_POSTED),  # year-end only
    ),
)

logger.info(
    "gl_period_close_workflow_registered",
    extra={
        "workflow_name": PERIOD_CLOSE_WORKFLOW.name,
        "state_count": len(PERIOD_CLOSE_WORKFLOW.states),
        "transition_count": len(PERIOD_CLOSE_WORKFLOW.transitions),
        "initial_state": PERIOD_CLOSE_WORKFLOW.initial_state,
    },
)
