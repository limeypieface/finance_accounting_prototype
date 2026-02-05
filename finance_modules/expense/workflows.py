"""Travel & Expense Workflows.

State machine for expense report processing.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.expense.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

RECEIPTS_ATTACHED = Guard(
    name="receipts_attached",
    description="Required receipts are attached",
)

WITHIN_POLICY = Guard(
    name="within_policy",
    description="All expenses are within policy limits",
)

APPROVAL_AUTHORITY = Guard(
    name="approval_authority",
    description="Approver has sufficient authority",
)

logger.info(
    "expense_workflow_guards_defined",
    extra={
        "guards": [
            RECEIPTS_ATTACHED.name,
            WITHIN_POLICY.name,
            APPROVAL_AUTHORITY.name,
        ],
    },
)


def _expense_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for expense actions (no guards)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
EXPENSE_RECORD_EXPENSE_WORKFLOW = _expense_draft_posted(
    "expense_record_expense", "Record single expense"
)
EXPENSE_RECORD_EXPENSE_REPORT_WORKFLOW = _expense_draft_posted(
    "expense_record_report", "Record expense report"
)
EXPENSE_ALLOCATE_EXPENSE_WORKFLOW = _expense_draft_posted(
    "expense_allocate", "Allocate expense to cost centers"
)
EXPENSE_RECORD_REIMBURSEMENT_WORKFLOW = _expense_draft_posted(
    "expense_record_reimbursement", "Record reimbursement"
)
EXPENSE_RECORD_CARD_STATEMENT_WORKFLOW = _expense_draft_posted(
    "expense_record_card_statement", "Record corporate card statement"
)
EXPENSE_RECORD_CARD_PAYMENT_WORKFLOW = _expense_draft_posted(
    "expense_record_card_payment", "Record corporate card payment"
)
EXPENSE_ISSUE_ADVANCE_WORKFLOW = _expense_draft_posted(
    "expense_issue_advance", "Issue expense advance"
)
EXPENSE_CLEAR_ADVANCE_WORKFLOW = _expense_draft_posted(
    "expense_clear_advance", "Clear expense advance"
)
EXPENSE_RECORD_RECEIPT_MATCH_WORKFLOW = _expense_draft_posted(
    "expense_record_receipt_match", "Match receipt to card transaction"
)
EXPENSE_SUBMIT_TRAVEL_AUTH_WORKFLOW = _expense_draft_posted(
    "expense_submit_travel_auth", "Submit travel authorization (D6)"
)
EXPENSE_APPROVE_TRAVEL_AUTH_WORKFLOW = _expense_draft_posted(
    "expense_approve_travel_auth", "Approve travel authorization (D6)"
)
EXPENSE_RECORD_REPORT_WITH_GSA_CHECK_WORKFLOW = _expense_draft_posted(
    "expense_record_report_gsa", "Record expense report with GSA check (D6/D7)"
)


# -----------------------------------------------------------------------------
# Expense Report Workflow
# -----------------------------------------------------------------------------

EXPENSE_REPORT_WORKFLOW = Workflow(
    name="expense_report",
    description="Expense report lifecycle",
    initial_state="draft",
    states=(
        "draft",
        "submitted",
        "pending_approval",
        "approved",
        "rejected",
        "processing",
        "paid",
    ),
    transitions=(
        Transition("draft", "submitted", action="submit", guard=RECEIPTS_ATTACHED),
        Transition("submitted", "pending_approval", action="route_for_approval"),
        Transition("pending_approval", "approved", action="approve", guard=APPROVAL_AUTHORITY, posts_entry=True),
        Transition("pending_approval", "rejected", action="reject"),
        Transition("rejected", "draft", action="revise"),
        Transition("approved", "processing", action="process_payment"),
        Transition("processing", "paid", action="mark_paid", posts_entry=True),
    ),
)

logger.info(
    "expense_report_workflow_registered",
    extra={
        "workflow_name": EXPENSE_REPORT_WORKFLOW.name,
        "state_count": len(EXPENSE_REPORT_WORKFLOW.states),
        "transition_count": len(EXPENSE_REPORT_WORKFLOW.transitions),
        "initial_state": EXPENSE_REPORT_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Travel Authorization Guards (DCAA D6)
# -----------------------------------------------------------------------------

PRE_TRAVEL_VALID = Guard(
    name="pre_travel_valid",
    description="Travel dates are in the future and destination is specified (D6)",
)

TRAVEL_AUTH_AUTHORITY = Guard(
    name="travel_auth_authority",
    description="Approver has authority for travel authorization amount (D6)",
)

logger.info(
    "travel_auth_workflow_guards_defined",
    extra={
        "guards": [PRE_TRAVEL_VALID.name, TRAVEL_AUTH_AUTHORITY.name],
    },
)


# -----------------------------------------------------------------------------
# Travel Authorization Workflow (DCAA D6 / FAR 31.205-46)
# -----------------------------------------------------------------------------

TRAVEL_AUTH_WORKFLOW = Workflow(
    name="travel_authorization",
    description="Pre-travel authorization lifecycle (DCAA D6)",
    initial_state="draft",
    states=(
        "draft",
        "submitted",
        "pending_approval",
        "approved",
        "rejected",
    ),
    transitions=(
        Transition(
            "draft", "submitted",
            action="submit",
            guard=PRE_TRAVEL_VALID,
        ),
        Transition(
            "submitted", "pending_approval",
            action="route_for_approval",
        ),
        Transition(
            "pending_approval", "approved",
            action="approve",
            guard=TRAVEL_AUTH_AUTHORITY,
        ),
        Transition(
            "pending_approval", "rejected",
            action="reject",
        ),
        Transition(
            "rejected", "draft",
            action="revise",
        ),
    ),
)

logger.info(
    "travel_auth_workflow_registered",
    extra={
        "workflow_name": TRAVEL_AUTH_WORKFLOW.name,
        "state_count": len(TRAVEL_AUTH_WORKFLOW.states),
        "transition_count": len(TRAVEL_AUTH_WORKFLOW.transitions),
        "initial_state": TRAVEL_AUTH_WORKFLOW.initial_state,
    },
)
