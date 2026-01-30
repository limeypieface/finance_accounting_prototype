"""
Procurement Workflows.

State machines for requisition and purchase order processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.procurement.workflows")


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
        Transition("pending_approval", "approved", action="approve", guard=APPROVAL_COMPLETE, posts_entry=True),
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
