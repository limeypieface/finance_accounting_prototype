"""
Canonical workflow types (``finance_kernel.domain.workflow``).

Responsibility
--------------
Pure value objects for workflow state machines.  Used by all modules
(AP, AR, etc.) so that Guard, Transition, and Workflow are defined
once.  Transitions may declare requires_approval and approval_policy
for the approval engine (Phase 10).

Architecture position
---------------------
**Kernel domain layer** -- pure value objects.  ZERO I/O.  No imports
from ``db/``, ``services/``, ``selectors/``, or outer layers.

Invariants enforced
-------------------
* Transitions reference only states in ``Workflow.states``.
* ``initial_state`` is a member of ``states``.
* When ``requires_approval=True``, ``approval_policy`` must be set
  (enforced at module definition / config time).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Guard:
    """A condition that must be satisfied before a transition fires.

    Contract: frozen, descriptive only.
    Guarantees: name and description are non-empty at construction.
    Non-goals: does not evaluate the condition -- the workflow engine does.
    """
    name: str
    description: str


@dataclass(frozen=True)
class ApprovalPolicyRef:
    """Typed reference from a workflow transition to an approval policy.

    min_version prevents silent weakening of controls when policies evolve.
    A transition declaring min_version=2 will reject resolution under policy v1.
    """
    policy_name: str
    min_version: int | None = None


@dataclass(frozen=True)
class Transition:
    """A valid state transition in a workflow.

    Contract: frozen.  ``posts_entry=True`` indicates a journal-posting transition.
    ``requires_approval=True`` indicates the transition is gated by the approval
    engine; ``approval_policy`` must be set when requires_approval is True.
    """
    from_state: str
    to_state: str
    action: str
    guard: Guard | None = None
    posts_entry: bool = False
    requires_approval: bool = False
    approval_policy: ApprovalPolicyRef | None = None


@dataclass(frozen=True)
class Workflow:
    """A state machine definition for a document lifecycle.

    Contract: frozen; ``transitions`` reference only states in ``states``.
    Guarantees: ``initial_state`` is a member of ``states``.
    ``terminal_states`` are states with no outgoing transitions (optional).
    """
    name: str
    description: str
    initial_state: str
    states: tuple[str, ...]
    transitions: tuple[Transition, ...]
    terminal_states: tuple[str, ...] = ()
