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

from dataclasses import dataclass

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.cash.workflows")


@dataclass(frozen=True)
class Guard:
    """
    A condition that must be true for a transition to be allowed.

    Contract:
        Immutable predicate declaration.  The workflow engine evaluates the
        named guard at transition time; this dataclass only stores metadata.
    """
    name: str
    description: str


@dataclass(frozen=True)
class Transition:
    """
    A valid state transition in a workflow.

    Contract:
        Immutable edge in the workflow graph.  When ``posts_entry`` is True,
        the transition triggers a journal entry via the posting pipeline.
    """
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False  # if True, triggers journal entry on transition


@dataclass(frozen=True)
class Workflow:
    """
    A state machine definition.

    Contract:
        Frozen declaration of states and transitions.  ``initial_state``
        must be an element of ``states``.  All ``from_state`` / ``to_state``
        values in ``transitions`` must be elements of ``states``.
    """
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]


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
