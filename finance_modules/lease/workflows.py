"""Lease Accounting Workflows.

State machine for lease lifecycle per ASC 842.
"""

from finance_kernel.logging_config import get_logger
from finance_kernel.domain.workflow import Guard, Transition, Workflow

logger = get_logger("modules.lease.workflows")


CLASSIFICATION_COMPLETE = Guard("classification_complete", "Lease classification determined")


def _lease_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for lease actions."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


LEASE_RECORD_INITIAL_RECOGNITION_WORKFLOW = _lease_draft_posted(
    "lease_record_initial_recognition", "Record initial recognition"
)
LEASE_RECORD_PERIODIC_PAYMENT_WORKFLOW = _lease_draft_posted(
    "lease_record_periodic_payment", "Record periodic payment"
)
LEASE_ACCRUE_INTEREST_WORKFLOW = _lease_draft_posted(
    "lease_accrue_interest", "Accrue interest"
)
LEASE_RECORD_AMORTIZATION_WORKFLOW = _lease_draft_posted(
    "lease_record_amortization", "Record amortization"
)
LEASE_MODIFY_LEASE_WORKFLOW = _lease_draft_posted(
    "lease_modify_lease", "Modify lease"
)
LEASE_TERMINATE_EARLY_WORKFLOW = _lease_draft_posted(
    "lease_terminate_early", "Terminate early"
)


LEASE_LIFECYCLE_WORKFLOW = Workflow(
    name="lease_lifecycle",
    description="Lease lifecycle per ASC 842",
    initial_state="draft",
    states=(
        "draft",
        "active",
        "modified",
        "terminated",
        "expired",
    ),
    transitions=(
        Transition("draft", "active", action="commence", guard=CLASSIFICATION_COMPLETE, posts_entry=True),
        Transition("active", "modified", action="modify", posts_entry=True),
        Transition("active", "terminated", action="terminate_early", posts_entry=True),
        Transition("active", "expired", action="expire"),
        Transition("modified", "active", action="resume"),
        Transition("modified", "terminated", action="terminate_early", posts_entry=True),
    ),
)

logger.info(
    "lease_lifecycle_workflow_registered",
    extra={
        "workflow_name": LEASE_LIFECYCLE_WORKFLOW.name,
        "state_count": len(LEASE_LIFECYCLE_WORKFLOW.states),
        "transition_count": len(LEASE_LIFECYCLE_WORKFLOW.transitions),
    },
)
