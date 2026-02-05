"""Inventory Workflows.

State machines for receipt, issue, and transfer processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.inventory.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

QC_PASSED = Guard(
    name="qc_passed",
    description="Quality control inspection passed",
)

QC_FAILED = Guard(
    name="qc_failed",
    description="Quality control inspection failed",
)

STOCK_AVAILABLE = Guard(
    name="stock_available",
    description="Sufficient stock is available",
)

logger.info(
    "inventory_workflow_guards_defined",
    extra={
        "guards": [
            QC_PASSED.name,
            QC_FAILED.name,
            STOCK_AVAILABLE.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Receipt Workflow
# -----------------------------------------------------------------------------

RECEIPT_WORKFLOW = Workflow(
    name="inv_receipt",
    description="Inventory receipt processing",
    initial_state="pending",
    states=(
        "pending",
        "inspecting",
        "accepted",
        "rejected",
        "putaway",
    ),
    transitions=(
        Transition("pending", "inspecting", action="begin_inspection"),
        Transition("pending", "accepted", action="accept"),  # no inspection required
        Transition("inspecting", "accepted", action="pass_qc", guard=QC_PASSED, posts_entry=True),
        Transition("inspecting", "rejected", action="fail_qc", guard=QC_FAILED),
        Transition("accepted", "putaway", action="complete_putaway", posts_entry=True),
        Transition("rejected", "pending", action="return_to_vendor"),
    ),
)

logger.info(
    "inventory_receipt_workflow_registered",
    extra={
        "workflow_name": RECEIPT_WORKFLOW.name,
        "state_count": len(RECEIPT_WORKFLOW.states),
        "transition_count": len(RECEIPT_WORKFLOW.transitions),
        "initial_state": RECEIPT_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Issue Workflow
# -----------------------------------------------------------------------------

ISSUE_WORKFLOW = Workflow(
    name="inv_issue",
    description="Inventory issue processing",
    initial_state="requested",
    states=(
        "requested",
        "picked",
        "shipped",
        "delivered",
    ),
    transitions=(
        Transition("requested", "picked", action="pick", guard=STOCK_AVAILABLE, posts_entry=True),
        Transition("picked", "shipped", action="ship"),
        Transition("shipped", "delivered", action="confirm_delivery"),
    ),
)

logger.info(
    "inventory_issue_workflow_registered",
    extra={
        "workflow_name": ISSUE_WORKFLOW.name,
        "state_count": len(ISSUE_WORKFLOW.states),
        "transition_count": len(ISSUE_WORKFLOW.transitions),
        "initial_state": ISSUE_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Transfer Workflow
# -----------------------------------------------------------------------------

TRANSFER_WORKFLOW = Workflow(
    name="inv_transfer",
    description="Inventory transfer between locations",
    initial_state="requested",
    states=(
        "requested",
        "in_transit",
        "received",
    ),
    transitions=(
        Transition("requested", "in_transit", action="ship", posts_entry=True),
        Transition("in_transit", "received", action="receive", posts_entry=True),
    ),
)

logger.info(
    "inventory_transfer_workflow_registered",
    extra={
        "workflow_name": TRANSFER_WORKFLOW.name,
        "state_count": len(TRANSFER_WORKFLOW.states),
        "transition_count": len(TRANSFER_WORKFLOW.transitions),
        "initial_state": TRANSFER_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Directive: no generic workflows. Each financial action has its own lifecycle.
# See docs/WORKFLOW_DIRECTIVE.md.
# -----------------------------------------------------------------------------

def _inv_draft_posted(name: str, description: str) -> Workflow:
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post"),),
    )

INVENTORY_RECEIPT_WORKFLOW = _inv_draft_posted("inv_receipt", "Inventory receipt")
INVENTORY_RECEIPT_VARIANCE_WORKFLOW = _inv_draft_posted("inv_receipt_variance", "Inventory receipt with variance")
INVENTORY_RECEIPT_FROM_PRODUCTION_WORKFLOW = _inv_draft_posted(
    "inv_receipt_from_production", "Receipt from production"
)
INVENTORY_TRANSFER_IN_WORKFLOW = _inv_draft_posted("inv_transfer_in", "Transfer in")
INVENTORY_ADJUSTMENT_WORKFLOW = _inv_draft_posted("inv_adjustment", "Inventory adjustment")
INVENTORY_REVALUATION_WORKFLOW = _inv_draft_posted("inv_revaluation", "Inventory revaluation")
INVENTORY_CYCLE_COUNT_WORKFLOW = _inv_draft_posted("inv_cycle_count", "Cycle count")
INVENTORY_ISSUE_WORKFLOW = _inv_draft_posted("inv_issue", "Inventory issue")

_inv_action_workflows = (
    INVENTORY_RECEIPT_WORKFLOW,
    INVENTORY_RECEIPT_VARIANCE_WORKFLOW,
    INVENTORY_RECEIPT_FROM_PRODUCTION_WORKFLOW,
    INVENTORY_TRANSFER_IN_WORKFLOW,
    INVENTORY_ADJUSTMENT_WORKFLOW,
    INVENTORY_REVALUATION_WORKFLOW,
    INVENTORY_CYCLE_COUNT_WORKFLOW,
    INVENTORY_ISSUE_WORKFLOW,
)
for wf in _inv_action_workflows:
    logger.info(
        "inventory_workflow_registered",
        extra={
            "workflow_name": wf.name,
            "state_count": len(wf.states),
            "transition_count": len(wf.transitions),
            "initial_state": wf.initial_state,
        },
    )
