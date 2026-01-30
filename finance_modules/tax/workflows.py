"""
Tax Workflows.

State machine for tax return processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.tax.workflows")


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

PERIOD_CLOSED = Guard(
    name="period_closed",
    description="Reporting period is closed",
)

RECONCILED = Guard(
    name="reconciled",
    description="Tax transactions reconciled to GL",
)

REVIEWED = Guard(
    name="reviewed",
    description="Return has been reviewed",
)

logger.info(
    "tax_workflow_guards_defined",
    extra={
        "guards": [
            PERIOD_CLOSED.name,
            RECONCILED.name,
            REVIEWED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Tax Return Workflow
# -----------------------------------------------------------------------------

TAX_RETURN_WORKFLOW = Workflow(
    name="tax_return",
    description="Tax return lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "calculated",
        "reviewed",
        "filed",
        "paid",
        "amended",
    ),
    transitions=(
        Transition("draft", "calculated", action="calculate", guard=PERIOD_CLOSED),
        Transition("calculated", "reviewed", action="review", guard=RECONCILED),
        Transition("reviewed", "filed", action="file", guard=REVIEWED),
        Transition("filed", "paid", action="pay", posts_entry=True),
        Transition("filed", "amended", action="amend"),
        Transition("amended", "filed", action="refile"),
    ),
)

logger.info(
    "tax_return_workflow_registered",
    extra={
        "workflow_name": TAX_RETURN_WORKFLOW.name,
        "state_count": len(TAX_RETURN_WORKFLOW.states),
        "transition_count": len(TAX_RETURN_WORKFLOW.transitions),
        "initial_state": TAX_RETURN_WORKFLOW.initial_state,
    },
)
