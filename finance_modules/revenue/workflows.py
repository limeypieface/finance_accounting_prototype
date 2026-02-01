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

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.revenue.workflows")


@dataclass(frozen=True)
class Guard:
    """
    A condition that must be met before a transition may fire.

    Contract:
        Frozen dataclass -- immutable.
    Guarantees:
        - ``name`` and ``description`` are non-empty strings.
    """
    name: str
    description: str


@dataclass(frozen=True)
class Transition:
    """
    A valid state transition in a workflow state machine.

    Contract:
        Frozen dataclass -- immutable.
    Guarantees:
        - ``from_state`` and ``to_state`` are valid state names.
        - ``posts_entry=True`` means this transition generates a journal entry.
    """
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False


@dataclass(frozen=True)
class Workflow:
    """
    A state machine definition for a module lifecycle.

    Contract:
        Frozen dataclass -- immutable.
    Guarantees:
        - ``initial_state`` is one of ``states``.
        - All transition from/to states are members of ``states``.
    """
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]


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

logger.info(
    "revenue_contract_workflow_registered",
    extra={
        "workflow_name": CONTRACT_LIFECYCLE_WORKFLOW.name,
        "state_count": len(CONTRACT_LIFECYCLE_WORKFLOW.states),
        "transition_count": len(CONTRACT_LIFECYCLE_WORKFLOW.transitions),
    },
)
