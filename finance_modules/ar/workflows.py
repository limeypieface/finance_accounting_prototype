"""
Accounts Receivable Workflows.

State machines for invoice and receipt processing.
"""

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.ar.workflows")


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

CREDIT_CHECK_PASSED = Guard(
    name="credit_check_passed",
    description="Customer is within credit limit",
)

BALANCE_ZERO = Guard(
    name="balance_zero",
    description="Invoice balance is zero",
)

WRITE_OFF_APPROVED = Guard(
    name="write_off_approved",
    description="Write-off has required approval",
)

logger.info(
    "ar_workflow_guards_defined",
    extra={
        "guards": [
            CREDIT_CHECK_PASSED.name,
            BALANCE_ZERO.name,
            WRITE_OFF_APPROVED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Invoice Workflow
# -----------------------------------------------------------------------------

INVOICE_WORKFLOW = Workflow(
    name="ar_invoice",
    description="Customer invoice lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "issued",
        "delivered",
        "partially_paid",
        "paid",
        "written_off",
        "cancelled",
    ),
    transitions=(
        Transition("draft", "issued", action="issue", guard=CREDIT_CHECK_PASSED, posts_entry=True),
        Transition("draft", "cancelled", action="cancel"),
        Transition("issued", "delivered", action="mark_delivered"),
        Transition("issued", "partially_paid", action="apply_payment"),
        Transition("issued", "paid", action="apply_payment", guard=BALANCE_ZERO),
        Transition("delivered", "partially_paid", action="apply_payment"),
        Transition("delivered", "paid", action="apply_payment", guard=BALANCE_ZERO),
        Transition("partially_paid", "paid", action="apply_payment", guard=BALANCE_ZERO),
        Transition("partially_paid", "written_off", action="write_off", guard=WRITE_OFF_APPROVED, posts_entry=True),
        Transition("issued", "written_off", action="write_off", guard=WRITE_OFF_APPROVED, posts_entry=True),
        Transition("delivered", "written_off", action="write_off", guard=WRITE_OFF_APPROVED, posts_entry=True),
        Transition("issued", "cancelled", action="cancel", posts_entry=True),  # reversal
    ),
)

logger.info(
    "ar_invoice_workflow_registered",
    extra={
        "workflow_name": INVOICE_WORKFLOW.name,
        "state_count": len(INVOICE_WORKFLOW.states),
        "transition_count": len(INVOICE_WORKFLOW.transitions),
        "initial_state": INVOICE_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Receipt Workflow
# -----------------------------------------------------------------------------

RECEIPT_WORKFLOW = Workflow(
    name="ar_receipt",
    description="Receipt processing workflow",
    initial_state="unallocated",
    states=(
        "unallocated",
        "partially_allocated",
        "fully_allocated",
    ),
    transitions=(
        Transition("unallocated", "partially_allocated", action="allocate", posts_entry=True),
        Transition("unallocated", "fully_allocated", action="allocate", posts_entry=True),
        Transition("partially_allocated", "fully_allocated", action="allocate", posts_entry=True),
        Transition("partially_allocated", "unallocated", action="unallocate", posts_entry=True),
        Transition("fully_allocated", "partially_allocated", action="unallocate", posts_entry=True),
    ),
)

logger.info(
    "ar_receipt_workflow_registered",
    extra={
        "workflow_name": RECEIPT_WORKFLOW.name,
        "state_count": len(RECEIPT_WORKFLOW.states),
        "transition_count": len(RECEIPT_WORKFLOW.transitions),
        "initial_state": RECEIPT_WORKFLOW.initial_state,
    },
)
