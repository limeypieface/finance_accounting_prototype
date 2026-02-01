"""
Lease Accounting Workflows.

State machine for lease lifecycle per ASC 842.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.lease.workflows")


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


CLASSIFICATION_COMPLETE = Guard("classification_complete", "Lease classification determined")

LEASE_LIFECYCLE_WORKFLOW = Workflow(
    name="lease_lifecycle",
    description="Lease lifecycle per ASC 842",
    initial_state="draft",
    states=(
        "draft",
        "active",
        "modified",
        "terminated",
        "expired",
    ),
    transitions=(
        Transition("draft", "active", action="commence", guard=CLASSIFICATION_COMPLETE, posts_entry=True),
        Transition("active", "modified", action="modify", posts_entry=True),
        Transition("active", "terminated", action="terminate_early", posts_entry=True),
        Transition("active", "expired", action="expire"),
        Transition("modified", "active", action="resume"),
        Transition("modified", "terminated", action="terminate_early", posts_entry=True),
    ),
)

logger.info(
    "lease_lifecycle_workflow_registered",
    extra={
        "workflow_name": LEASE_LIFECYCLE_WORKFLOW.name,
        "state_count": len(LEASE_LIFECYCLE_WORKFLOW.states),
        "transition_count": len(LEASE_LIFECYCLE_WORKFLOW.transitions),
    },
)
