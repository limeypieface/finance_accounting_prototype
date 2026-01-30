"""
Work-in-Process Workflows.

State machine for work order processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.wip.workflows")


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

MATERIALS_AVAILABLE = Guard(
    name="materials_available",
    description="All required materials are available",
)

ALL_OPERATIONS_COMPLETE = Guard(
    name="all_operations_complete",
    description="All operations are completed",
)

VARIANCE_CALCULATED = Guard(
    name="variance_calculated",
    description="All variances have been calculated",
)

logger.info(
    "wip_workflow_guards_defined",
    extra={
        "guards": [
            MATERIALS_AVAILABLE.name,
            ALL_OPERATIONS_COMPLETE.name,
            VARIANCE_CALCULATED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Work Order Workflow
# -----------------------------------------------------------------------------

WORK_ORDER_WORKFLOW = Workflow(
    name="wip_work_order",
    description="Manufacturing work order lifecycle",
    initial_state="planned",
    states=(
        "planned",
        "released",
        "in_progress",
        "completed",
        "closed",
        "cancelled",
    ),
    transitions=(
        Transition("planned", "released", action="release", guard=MATERIALS_AVAILABLE),
        Transition("planned", "cancelled", action="cancel"),
        Transition("released", "in_progress", action="start", posts_entry=True),
        Transition("released", "cancelled", action="cancel"),
        Transition("in_progress", "completed", action="complete", guard=ALL_OPERATIONS_COMPLETE, posts_entry=True),
        Transition("completed", "closed", action="close", guard=VARIANCE_CALCULATED, posts_entry=True),
        Transition("completed", "in_progress", action="reopen"),  # found defects
    ),
)

logger.info(
    "wip_work_order_workflow_registered",
    extra={
        "workflow_name": WORK_ORDER_WORKFLOW.name,
        "state_count": len(WORK_ORDER_WORKFLOW.states),
        "transition_count": len(WORK_ORDER_WORKFLOW.transitions),
        "initial_state": WORK_ORDER_WORKFLOW.initial_state,
    },
)
