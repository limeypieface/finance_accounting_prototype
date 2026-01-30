"""
Payroll Workflows.

State machine for payroll run processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.payroll.workflows")


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

ALL_TIMECARDS_APPROVED = Guard(
    name="all_timecards_approved",
    description="All employee timecards are approved",
)

CALCULATION_COMPLETE = Guard(
    name="calculation_complete",
    description="Payroll calculation completed without errors",
)

APPROVAL_OBTAINED = Guard(
    name="approval_obtained",
    description="Required payroll approval obtained",
)

logger.info(
    "payroll_workflow_guards_defined",
    extra={
        "guards": [
            ALL_TIMECARDS_APPROVED.name,
            CALCULATION_COMPLETE.name,
            APPROVAL_OBTAINED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Payroll Run Workflow
# -----------------------------------------------------------------------------

PAYROLL_RUN_WORKFLOW = Workflow(
    name="payroll_run",
    description="Payroll processing lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "calculating",
        "calculated",
        "approved",
        "processing",
        "completed",
        "reversed",
    ),
    transitions=(
        Transition("draft", "calculating", action="calculate", guard=ALL_TIMECARDS_APPROVED),
        Transition("calculating", "calculated", action="finish_calculation", guard=CALCULATION_COMPLETE),
        Transition("calculated", "draft", action="recalculate"),  # changes needed
        Transition("calculated", "approved", action="approve", guard=APPROVAL_OBTAINED),
        Transition("approved", "processing", action="process", posts_entry=True),
        Transition("processing", "completed", action="complete", posts_entry=True),
        Transition("completed", "reversed", action="reverse", posts_entry=True),
    ),
)

logger.info(
    "payroll_run_workflow_registered",
    extra={
        "workflow_name": PAYROLL_RUN_WORKFLOW.name,
        "state_count": len(PAYROLL_RUN_WORKFLOW.states),
        "transition_count": len(PAYROLL_RUN_WORKFLOW.transitions),
        "initial_state": PAYROLL_RUN_WORKFLOW.initial_state,
    },
)
