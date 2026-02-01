"""
Project Accounting Domain Models (``finance_modules.project.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of project accounting:
projects, WBS elements, budgets, milestones, and Earned Value Management
(EVM) snapshots.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``ProjectService`` and returned to callers.  No dependency on kernel
services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``EVMSnapshot`` records support project performance measurement.
* ``ProjectBudget`` records track authorized budget amounts.
* ``Milestone`` records support billing and progress tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Project:
    """A project with cost tracking."""
    id: UUID
    name: str
    project_type: str  # "fixed_price", "cost_plus", "time_and_materials"
    status: str = "active"  # active, on_hold, completed, cancelled
    start_date: date | None = None
    end_date: date | None = None
    total_budget: Decimal = Decimal("0")
    currency: str = "USD"


@dataclass(frozen=True)
class WBSElement:
    """A Work Breakdown Structure element."""
    id: UUID
    project_id: UUID
    code: str
    name: str
    parent_id: UUID | None = None
    budget_amount: Decimal = Decimal("0")
    actual_cost: Decimal = Decimal("0")
    level: int = 1


@dataclass(frozen=True)
class ProjectBudget:
    """Budget for a project or WBS element."""
    project_id: UUID
    wbs_code: str
    period: str
    budget_amount: Decimal
    actual_amount: Decimal = Decimal("0")
    committed_amount: Decimal = Decimal("0")
    available_amount: Decimal = Decimal("0")


@dataclass(frozen=True)
class Milestone:
    """A project milestone for billing."""
    id: UUID
    project_id: UUID
    name: str
    amount: Decimal
    completion_pct: Decimal = Decimal("0")
    is_billed: bool = False
    billed_date: date | None = None


@dataclass(frozen=True)
class EVMSnapshot:
    """Earned Value Management snapshot at a point in time."""
    project_id: UUID
    as_of_date: date
    bcws: Decimal  # Budgeted Cost of Work Scheduled (Planned Value)
    bcwp: Decimal  # Budgeted Cost of Work Performed (Earned Value)
    acwp: Decimal  # Actual Cost of Work Performed
    bac: Decimal   # Budget at Completion
    cpi: Decimal = Decimal("0")   # Cost Performance Index
    spi: Decimal = Decimal("0")   # Schedule Performance Index
    eac: Decimal = Decimal("0")   # Estimate at Completion
    etc: Decimal = Decimal("0")   # Estimate to Complete
    vac: Decimal = Decimal("0")   # Variance at Completion
    cv: Decimal = Decimal("0")    # Cost Variance
    sv: Decimal = Decimal("0")    # Schedule Variance
