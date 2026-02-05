"""
finance_modules.cash.workflows
================================

Responsibility:
    Declarative state-machine definitions for cash management processes
    (bank reconciliation).  The shared workflow engine executes transitions;
    this module only declares the graph and guards.

Architecture:
    Module layer (finance_modules).  Pure data declarations -- no I/O,
    no imports from services or engines.

Invariants enforced:
    - Workflow transitions are immutable (frozen dataclasses).
    - ``posts_entry=True`` transitions trigger journal entries via the
      posting pipeline, ensuring R4 (balanced entries) and R7 (transaction
      boundaries) compliance.

Failure modes:
    - Invalid transition request -> workflow engine rejects (not defined here).

Audit relevance:
    Reconciliation workflow state changes are auditable events.  The
    ``approve`` transition (pending_review -> completed) is the control
    point that triggers posting of reconciliation adjustments.
"""


from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.cash.workflows")


# -----------------------------------------------------------------------------
# Guards
# -----------------------------------------------------------------------------

VARIANCE_WITHIN_TOLERANCE = Guard(
    name="variance_within_tolerance",
    description="Reconciliation variance is within configured tolerance",
)

ALL_ITEMS_MATCHED = Guard(
    name="all_items_matched",
    description="All bank transactions matched to book entries",
)

logger.info(
    "cash_workflow_guards_defined",
    extra={
        "guards": [
            VARIANCE_WITHIN_TOLERANCE.name,
            ALL_ITEMS_MATCHED.name,
        ],
    },
)


# -----------------------------------------------------------------------------
# Reconciliation Workflow
# -----------------------------------------------------------------------------

RECONCILIATION_WORKFLOW = Workflow(
    name="bank_reconciliation",
    description="Bank account reconciliation process",
    initial_state="draft",
    states=(
        "draft",
        "in_progress",
        "pending_review",
        "completed",
    ),
    transitions=(
        Transition(
            from_state="draft",
            to_state="in_progress",
            action="start",
        ),
        Transition(
            from_state="in_progress",
            to_state="pending_review",
            action="submit",
            guard=ALL_ITEMS_MATCHED,
        ),
        Transition(
            from_state="in_progress",
            to_state="draft",
            action="cancel",
        ),
        Transition(
            from_state="pending_review",
            to_state="completed",
            action="approve",
            guard=VARIANCE_WITHIN_TOLERANCE,
            posts_entry=True,  # posts reconciliation adjustment if variance exists
        ),
        Transition(
            from_state="pending_review",
            to_state="in_progress",
            action="reject",
        ),
    ),
)

logger.info(
    "cash_reconciliation_workflow_registered",
    extra={
        "workflow_name": RECONCILIATION_WORKFLOW.name,
        "state_count": len(RECONCILIATION_WORKFLOW.states),
        "transition_count": len(RECONCILIATION_WORKFLOW.transitions),
        "initial_state": RECONCILIATION_WORKFLOW.initial_state,
    },
)


# -----------------------------------------------------------------------------
# Directive: no generic workflows. Each financial action has its own lifecycle.
# See docs/WORKFLOW_DIRECTIVE.md.
# -----------------------------------------------------------------------------

def _cash_draft_posted(name: str, description: str) -> Workflow:
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post"),),
    )

CASH_RECEIPT_WORKFLOW = _cash_draft_posted("cash_receipt", "Cash receipt")
CASH_DISBURSEMENT_WORKFLOW = _cash_draft_posted("cash_disbursement", "Cash disbursement")
CASH_BANK_FEE_WORKFLOW = _cash_draft_posted("cash_bank_fee", "Bank fee")
CASH_INTEREST_EARNED_WORKFLOW = _cash_draft_posted("cash_interest_earned", "Interest earned")
CASH_TRANSFER_WORKFLOW = _cash_draft_posted("cash_transfer", "Transfer between accounts")
CASH_WIRE_TRANSFER_OUT_WORKFLOW = _cash_draft_posted("cash_wire_transfer_out", "Wire transfer out")
CASH_WIRE_TRANSFER_CLEARED_WORKFLOW = _cash_draft_posted("cash_wire_transfer_cleared", "Wire transfer cleared")
CASH_RECONCILIATION_WORKFLOW = _cash_draft_posted("cash_reconciliation", "Bank reconciliation (post adjustment)")
CASH_AUTO_RECONCILE_WORKFLOW = _cash_draft_posted("cash_auto_reconcile", "Auto-reconciliation")
CASH_NSF_RETURN_WORKFLOW = _cash_draft_posted("cash_nsf_return", "NSF return")

for wf in (
    CASH_RECEIPT_WORKFLOW,
    CASH_DISBURSEMENT_WORKFLOW,
    CASH_BANK_FEE_WORKFLOW,
    CASH_INTEREST_EARNED_WORKFLOW,
    CASH_TRANSFER_WORKFLOW,
    CASH_WIRE_TRANSFER_OUT_WORKFLOW,
    CASH_WIRE_TRANSFER_CLEARED_WORKFLOW,
    CASH_RECONCILIATION_WORKFLOW,
    CASH_AUTO_RECONCILE_WORKFLOW,
    CASH_NSF_RETURN_WORKFLOW,
):
    logger.info(
        "cash_workflow_registered",
        extra={
            "workflow_name": wf.name,
            "state_count": len(wf.states),
            "transition_count": len(wf.transitions),
            "initial_state": wf.initial_state,
        },
    )
