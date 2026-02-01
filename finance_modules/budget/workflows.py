"""
Budget Workflows.

State machine for budget version lifecycle.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.budget.workflows")


@dataclass(frozen=True)
class Guard:
    name: str
    description: str


@dataclass(frozen=True)
class Transition:
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False


@dataclass(frozen=True)
class Workflow:
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]


APPROVED_BY_AUTHORITY = Guard("approved_by_authority", "Budget approved by authorized approver")

BUDGET_VERSION_WORKFLOW = Workflow(
    name="budget_version",
    description="Budget version lifecycle",
    initial_state="draft",
    states=("draft", "approved", "locked", "archived"),
    transitions=(
        Transition("draft", "approved", action="approve", guard=APPROVED_BY_AUTHORITY),
        Transition("approved", "locked", action="lock"),
        Transition("approved", "draft", action="reopen"),
        Transition("locked", "archived", action="archive"),
    ),
)

logger.info("budget_version_workflow_registered", extra={
    "workflow_name": BUDGET_VERSION_WORKFLOW.name,
    "state_count": len(BUDGET_VERSION_WORKFLOW.states),
})
