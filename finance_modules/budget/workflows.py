"""Budget Workflows.

State machine for budget version lifecycle.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.budget.workflows")


APPROVED_BY_AUTHORITY = Guard("approved_by_authority", "Budget approved by authorized approver")


def _budget_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for budget actions (no guards)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
BUDGET_POST_ENTRY_WORKFLOW = _budget_draft_posted("budget_post_entry", "Post budget entry")
BUDGET_TRANSFER_WORKFLOW = _budget_draft_posted("budget_transfer", "Transfer budget")
BUDGET_RECORD_ENCUMBRANCE_WORKFLOW = _budget_draft_posted("budget_record_encumbrance", "Record encumbrance")
BUDGET_RELIEVE_ENCUMBRANCE_WORKFLOW = _budget_draft_posted("budget_relieve_encumbrance", "Relieve encumbrance")
BUDGET_CANCEL_ENCUMBRANCE_WORKFLOW = _budget_draft_posted("budget_cancel_encumbrance", "Cancel encumbrance")
BUDGET_UPDATE_FORECAST_WORKFLOW = _budget_draft_posted("budget_update_forecast", "Update forecast")


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
