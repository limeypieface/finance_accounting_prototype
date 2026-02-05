"""Procurement Workflows.

State machines for requisition and purchase order processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.procurement.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

BUDGET_AVAILABLE = Guard(
    name="budget_available",
    description="Sufficient budget is available",
)

APPROVAL_COMPLETE = Guard(
    name="approval_complete",
    description="All required approvals obtained",
)

VENDOR_APPROVED = Guard(
    name="vendor_approved",
    description="Vendor is on approved vendor list",
)

ALL_LINES_RECEIVED = Guard(
    name="all_lines_received",
    description="All PO lines fully received",
)

FULLY_INVOICED = Guard(
    name="fully_invoiced",
    description="All receipts matched to invoices",
)

logger.info(
    "procurement_workflow_guards_defined",
    extra={
        "guards": [
            BUDGET_AVAILABLE.name,
            APPROVAL_COMPLETE.name,
            VENDOR_APPROVED.name,
            ALL_LINES_RECEIVED.name,
            FULLY_INVOICED.name,
        ],
    },
)


def _procurement_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for procurement actions (no guards)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
PROCUREMENT_CREATE_PO_WORKFLOW = _procurement_draft_posted(
    "procurement_create_po", "Create purchase order and post encumbrance"
)
PROCUREMENT_RECORD_COMMITMENT_WORKFLOW = _procurement_draft_posted(
    "procurement_record_commitment", "Record purchase commitment (memo)"
)
PROCUREMENT_RELIEVE_COMMITMENT_WORKFLOW = _procurement_draft_posted(
    "procurement_relieve_commitment", "Relieve purchase commitment"
)
PROCUREMENT_RECEIVE_GOODS_WORKFLOW = _procurement_draft_posted(
    "procurement_receive_goods", "Receive goods against PO and relieve encumbrance"
)
PROCUREMENT_RECORD_PRICE_VARIANCE_WORKFLOW = _procurement_draft_posted(
    "procurement_record_price_variance", "Record purchase price variance (PPV)"
)
PROCUREMENT_CREATE_REQUISITION_WORKFLOW = _procurement_draft_posted(
    "procurement_create_requisition", "Create purchase requisition"
)
PROCUREMENT_CONVERT_REQUISITION_TO_PO_WORKFLOW = _procurement_draft_posted(
    "procurement_convert_requisition_to_po", "Convert requisition to PO (relief + encumbrance)"
)
PROCUREMENT_AMEND_PO_WORKFLOW = _procurement_draft_posted(
    "procurement_amend_po", "Amend PO and adjust encumbrance"
)
PROCUREMENT_MATCH_RECEIPT_TO_PO_WORKFLOW = _procurement_draft_posted(
    "procurement_match_receipt_to_po", "Match receipt to PO (3-way match)"
)
PROCUREMENT_RECORD_QUANTITY_VARIANCE_WORKFLOW = _procurement_draft_posted(
    "procurement_record_quantity_variance", "Record quantity variance PO vs receipt"
)


# -----------------------------------------------------------------------------
# Requisition Workflow
# -----------------------------------------------------------------------------

REQUISITION_WORKFLOW = Workflow(
    name="requisition",
    description="Purchase requisition lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "submitted",
        "approved",
        "rejected",
        "converted",
        "cancelled",
    ),
    transitions=(
        Transition("draft", "submitted", action="submit"),
        Transition("submitted", "approved", action="approve", guard=BUDGET_AVAILABLE),
        Transition("submitted", "rejected", action="reject"),
        Transition("rejected", "draft", action="revise"),
        Transition("approved", "converted", action="convert_to_po"),
        Transition("draft", "cancelled", action="cancel"),
        Transition("submitted", "cancelled", action="cancel"),
    ),
)

logger.info(
    "procurement_requisition_workflow_registered",
    extra={
        "workflow_name": REQUISITION_WORKFLOW.name,
        "state_count": len(REQUISITION_WORKFLOW.states),
        "transition_count": len(REQUISITION_WORKFLOW.transitions),
        "initial_state": REQUISITION_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Purchase Order Workflow
# -----------------------------------------------------------------------------

PURCHASE_ORDER_WORKFLOW = Workflow(
    name="purchase_order",
    description="Purchase order lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "pending_approval",
        "approved",
        "sent",
        "acknowledged",
        "partially_received",
        "received",
        "invoiced",
        "closed",
        "cancelled",
    ),
    transitions=(
        Transition("draft", "pending_approval", action="submit"),
        Transition("pending_approval", "approved", action="approve", posts_entry=True),
        Transition("pending_approval", "draft", action="reject"),
        Transition("approved", "sent", action="send", guard=VENDOR_APPROVED),
        Transition("sent", "acknowledged", action="acknowledge"),
        Transition("sent", "partially_received", action="receive"),
        Transition("acknowledged", "partially_received", action="receive"),
        Transition("partially_received", "received", action="receive", guard=ALL_LINES_RECEIVED),
        Transition("received", "invoiced", action="match_invoice", guard=FULLY_INVOICED, posts_entry=True),
        Transition("invoiced", "closed", action="close"),
        Transition("draft", "cancelled", action="cancel"),
        Transition("approved", "cancelled", action="cancel", posts_entry=True),  # relieve encumbrance
    ),
)

logger.info(
    "procurement_po_workflow_registered",
    extra={
        "workflow_name": PURCHASE_ORDER_WORKFLOW.name,
        "state_count": len(PURCHASE_ORDER_WORKFLOW.states),
        "transition_count": len(PURCHASE_ORDER_WORKFLOW.transitions),
        "initial_state": PURCHASE_ORDER_WORKFLOW.initial_state,
    },
)
