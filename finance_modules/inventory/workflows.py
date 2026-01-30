"""
Inventory Workflows.

State machines for receipt, issue, and transfer processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.inventory.workflows")


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
