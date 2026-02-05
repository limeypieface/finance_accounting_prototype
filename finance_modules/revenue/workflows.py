"""
Module: finance_modules.revenue.workflows
Responsibility:
    Declarative state machine definition for the revenue contract
    lifecycle per ASC 606.  Defines valid states, transitions, guards,
    and which transitions trigger journal postings.

Architecture:
    finance_modules layer -- purely declarative frozen dataclasses.
    No I/O, no side effects.  The workflow definition is consumed by
    orchestration code to validate state transitions.

    Guards are named conditions that must be satisfied before a
    transition may fire.  ``posts_entry=True`` indicates that the
    transition triggers a journal posting via the kernel pipeline.

Invariants:
    - All workflow components are frozen dataclasses (immutable).
    - State names correspond to ContractStatus enum values in models.py.
    - Only transitions with ``posts_entry=True`` generate journal entries.

Failure modes:
    - Invalid transition (state not in ``states``) detected at runtime
      by workflow enforcement code.

Audit relevance:
    - The workflow defines the ONLY valid paths through the contract
      lifecycle; any deviation is a protocol violation.
    - Transitions that post entries are auditable via the kernel's
      immutable journal and audit chain (R1, R10, R11).
    - Guard conditions ensure prerequisites (e.g., contract approval,
      price determination) are met before state advancement.
"""


from finance_kernel.logging_config import get_logger

logger = get_logger("modules.revenue.workflows")


from finance_kernel.domain.workflow import Guard, Transition, Workflow


# Guards
CONTRACT_APPROVED = Guard("contract_approved", "Contract terms approved by both parties")
OBLIGATIONS_IDENTIFIED = Guard("obligations_identified", "All POs identified and priced")
PRICE_DETERMINED = Guard("price_determined", "Transaction price determined")

CONTRACT_LIFECYCLE_WORKFLOW = Workflow(
    name="revenue_contract",
    description="Revenue contract lifecycle per ASC 606",
    initial_state="identified",
    states=(
        "identified",
        "obligations_identified",
        "price_determined",
        "allocated",
        "recognizing",
        "completed",
        "terminated",
    ),
    transitions=(
        Transition("identified", "obligations_identified", action="identify_obligations", guard=CONTRACT_APPROVED),
        Transition("obligations_identified", "price_determined", action="determine_price", guard=OBLIGATIONS_IDENTIFIED),
        Transition("price_determined", "allocated", action="allocate_price", guard=PRICE_DETERMINED, posts_entry=True),
        Transition("allocated", "recognizing", action="begin_recognition"),
        Transition("recognizing", "completed", action="complete_contract"),
        Transition("identified", "terminated", action="terminate"),
        Transition("obligations_identified", "terminated", action="terminate"),
    ),
)

def _revenue_draft_posted(name: str, description: str) -> Workflow:
    """Simple draft -> posted lifecycle for revenue posting actions (R28)."""
    return Workflow(
        name=name,
        description=description,
        initial_state="draft",
        states=("draft", "posted"),
        transitions=(Transition("draft", "posted", action="post", posts_entry=True),),
    )


# Action-specific workflows for posting methods (R28: no generic workflow)
REVENUE_ALLOCATE_PRICE_WORKFLOW = _revenue_draft_posted(
    "revenue_allocate_price", "Allocate transaction price to obligations (Step 4)"
)
REVENUE_RECOGNIZE_REVENUE_WORKFLOW = _revenue_draft_posted(
    "revenue_recognize_revenue", "Recognize revenue (Step 5)"
)
REVENUE_MODIFY_CONTRACT_WORKFLOW = _revenue_draft_posted(
    "revenue_modify_contract", "Record contract modification"
)
REVENUE_UPDATE_VARIABLE_CONSIDERATION_WORKFLOW = _revenue_draft_posted(
    "revenue_update_variable_consideration", "Update variable consideration estimate"
)

logger.info(
    "revenue_contract_workflow_registered",
    extra={
        "workflow_name": CONTRACT_LIFECYCLE_WORKFLOW.name,
        "state_count": len(CONTRACT_LIFECYCLE_WORKFLOW.states),
        "transition_count": len(CONTRACT_LIFECYCLE_WORKFLOW.transitions),
    },
)
