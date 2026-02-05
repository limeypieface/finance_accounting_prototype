"""Project Accounting Workflows."""

from __future__ import annotations

from finance_kernel.domain.workflow import Guard, Transition, Workflow


def _project_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for project actions."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


PROJECT_RECORD_COST_WORKFLOW = _project_draft_posted("project_record_cost", "Record cost")
PROJECT_BILL_MILESTONE_WORKFLOW = _project_draft_posted("project_bill_milestone", "Bill milestone")
PROJECT_BILL_TIME_MATERIALS_WORKFLOW = _project_draft_posted("project_bill_time_materials", "Bill time & materials")
PROJECT_RECOGNIZE_REVENUE_WORKFLOW = _project_draft_posted("project_recognize_revenue", "Recognize revenue")
PROJECT_REVISE_BUDGET_WORKFLOW = _project_draft_posted("project_revise_budget", "Revise budget")
PROJECT_COMPLETE_PHASE_WORKFLOW = _project_draft_posted("project_complete_phase", "Complete phase")


PROJECT_WORKFLOW = {
    "name": "project_lifecycle",
    "states": ["planning", "active", "on_hold", "completed", "cancelled"],
    "transitions": {
        "planning": ["active", "cancelled"],
        "active": ["on_hold", "completed", "cancelled"],
        "on_hold": ["active", "cancelled"],
    },
}
