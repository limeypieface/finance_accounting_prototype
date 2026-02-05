"""Accounts Receivable Workflows.

State machines for invoice and receipt processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.ar.workflows")


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
        Transition("draft", "issued", action="post", guard=CREDIT_CHECK_PASSED, posts_entry=True),
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
        Transition("unallocated", "unallocated", action="post", posts_entry=True),
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


# -----------------------------------------------------------------------------
# Directive: no generic workflows. Each financial action has its own lifecycle.
# See docs/WORKFLOW_DIRECTIVE.md.
# -----------------------------------------------------------------------------

# Aliases for directive naming (AR_INVOICE_WORKFLOW, AR_RECEIPT_WORKFLOW)
AR_INVOICE_WORKFLOW = INVOICE_WORKFLOW
AR_RECEIPT_WORKFLOW = RECEIPT_WORKFLOW


# -----------------------------------------------------------------------------
# Receipt application (applying cash to open receivables)
# -----------------------------------------------------------------------------

AR_RECEIPT_APPLICATION_WORKFLOW = Workflow(
    name="ar_receipt_application",
    description="Applying cash to open receivables",
    initial_state="draft",
    states=("draft", "posted"),
    transitions=(
        Transition("draft", "posted", action="post"),
    ),
)

logger.info(
    "ar_receipt_application_workflow_registered",
    extra={
        "workflow_name": AR_RECEIPT_APPLICATION_WORKFLOW.name,
        "state_count": len(AR_RECEIPT_APPLICATION_WORKFLOW.states),
        "transition_count": len(AR_RECEIPT_APPLICATION_WORKFLOW.transitions),
        "initial_state": AR_RECEIPT_APPLICATION_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Credit memo (revenue reversal and customer credit)
# -----------------------------------------------------------------------------

AR_CREDIT_MEMO_WORKFLOW = Workflow(
    name="ar_credit_memo",
    description="Revenue reversal and customer credit",
    initial_state="draft",
    states=("draft", "posted"),
    transitions=(
        Transition("draft", "posted", action="post"),
    ),
)

logger.info(
    "ar_credit_memo_workflow_registered",
    extra={
        "workflow_name": AR_CREDIT_MEMO_WORKFLOW.name,
        "state_count": len(AR_CREDIT_MEMO_WORKFLOW.states),
        "transition_count": len(AR_CREDIT_MEMO_WORKFLOW.transitions),
        "initial_state": AR_CREDIT_MEMO_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Write-off (bad debt governance)
# -----------------------------------------------------------------------------

AR_WRITE_OFF_WORKFLOW = Workflow(
    name="ar_write_off",
    description="Bad debt governance",
    initial_state="draft",
    states=("draft", "posted"),
    transitions=(
        Transition("draft", "posted", action="post"),
    ),
)

logger.info(
    "ar_write_off_workflow_registered",
    extra={
        "workflow_name": AR_WRITE_OFF_WORKFLOW.name,
        "state_count": len(AR_WRITE_OFF_WORKFLOW.states),
        "transition_count": len(AR_WRITE_OFF_WORKFLOW.transitions),
        "initial_state": AR_WRITE_OFF_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Deferred revenue (cash vs revenue timing)
# -----------------------------------------------------------------------------

AR_DEFERRED_REVENUE_WORKFLOW = Workflow(
    name="ar_deferred_revenue",
    description="Cash vs revenue timing",
    initial_state="draft",
    states=("draft", "posted"),
    transitions=(
        Transition("draft", "posted", action="post"),
    ),
)

logger.info(
    "ar_deferred_revenue_workflow_registered",
    extra={
        "workflow_name": AR_DEFERRED_REVENUE_WORKFLOW.name,
        "state_count": len(AR_DEFERRED_REVENUE_WORKFLOW.states),
        "transition_count": len(AR_DEFERRED_REVENUE_WORKFLOW.transitions),
        "initial_state": AR_DEFERRED_REVENUE_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Refund (cash outflow control)
# -----------------------------------------------------------------------------

AR_REFUND_WORKFLOW = Workflow(
    name="ar_refund",
    description="Cash outflow control",
    initial_state="draft",
    states=("draft", "posted"),
    transitions=(
        Transition("draft", "posted", action="post"),
    ),
)

logger.info(
    "ar_refund_workflow_registered",
    extra={
        "workflow_name": AR_REFUND_WORKFLOW.name,
        "state_count": len(AR_REFUND_WORKFLOW.states),
        "transition_count": len(AR_REFUND_WORKFLOW.transitions),
        "initial_state": AR_REFUND_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Finance charge (penalty and interest policy)
# -----------------------------------------------------------------------------

AR_FINANCE_CHARGE_WORKFLOW = Workflow(
    name="ar_finance_charge",
    description="Penalty and interest policy",
    initial_state="draft",
    states=("draft", "posted"),
    transitions=(
        Transition("draft", "posted", action="post"),
    ),
)

logger.info(
    "ar_finance_charge_workflow_registered",
    extra={
        "workflow_name": AR_FINANCE_CHARGE_WORKFLOW.name,
        "state_count": len(AR_FINANCE_CHARGE_WORKFLOW.states),
        "transition_count": len(AR_FINANCE_CHARGE_WORKFLOW.transitions),
        "initial_state": AR_FINANCE_CHARGE_WORKFLOW.initial_state,
    },
)
