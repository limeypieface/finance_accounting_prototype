"""Work-in-Process Workflows.

State machine for manufacturing order processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.wip.workflows")


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
# Manufacturing Order Workflow
# -----------------------------------------------------------------------------

MANUFACTURING_ORDER_WORKFLOW = Workflow(
    name="wip_manufacturing_order",
    description="Manufacturing order lifecycle",
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
    "wip_manufacturing_order_workflow_registered",
    extra={
        "workflow_name": MANUFACTURING_ORDER_WORKFLOW.name,
        "state_count": len(MANUFACTURING_ORDER_WORKFLOW.states),
        "transition_count": len(MANUFACTURING_ORDER_WORKFLOW.transitions),
        "initial_state": MANUFACTURING_ORDER_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Directive: no generic workflows. Each financial action has its own lifecycle.
# See docs/WORKFLOW_DIRECTIVE.md.
# -----------------------------------------------------------------------------

def _wip_draft_posted(name: str, description: str) -> Workflow:
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post"),),
    )

WIP_MATERIAL_ISSUE_WORKFLOW = _wip_draft_posted("wip_material_issue", "Material issued to manufacturing order")
WIP_LABOR_CHARGE_WORKFLOW = _wip_draft_posted("wip_labor_charge", "Labor charged to manufacturing order")
WIP_OVERHEAD_WORKFLOW = _wip_draft_posted("wip_overhead", "Overhead applied to manufacturing order")
WIP_COMPLETION_WORKFLOW = _wip_draft_posted("wip_completion", "Manufacturing order completion")
WIP_SCRAP_WORKFLOW = _wip_draft_posted("wip_scrap", "Scrap on manufacturing order")
WIP_REWORK_WORKFLOW = _wip_draft_posted("wip_rework", "Rework costs on manufacturing order")
WIP_LABOR_VARIANCE_WORKFLOW = _wip_draft_posted("wip_labor_variance", "Labor variance")
WIP_MATERIAL_VARIANCE_WORKFLOW = _wip_draft_posted("wip_material_variance", "Material variance")
WIP_OVERHEAD_VARIANCE_WORKFLOW = _wip_draft_posted("wip_overhead_variance", "Overhead variance")
WIP_BYPRODUCT_WORKFLOW = _wip_draft_posted("wip_byproduct", "Byproduct recorded")

_wip_action_workflows = (
    WIP_MATERIAL_ISSUE_WORKFLOW,
    WIP_LABOR_CHARGE_WORKFLOW,
    WIP_OVERHEAD_WORKFLOW,
    WIP_COMPLETION_WORKFLOW,
    WIP_SCRAP_WORKFLOW,
    WIP_REWORK_WORKFLOW,
    WIP_LABOR_VARIANCE_WORKFLOW,
    WIP_MATERIAL_VARIANCE_WORKFLOW,
    WIP_OVERHEAD_VARIANCE_WORKFLOW,
    WIP_BYPRODUCT_WORKFLOW,
)
for wf in _wip_action_workflows:
    logger.info(
        "wip_workflow_registered",
        extra={
            "workflow_name": wf.name,
            "state_count": len(wf.states),
            "transition_count": len(wf.transitions),
            "initial_state": wf.initial_state,
        },
    )
