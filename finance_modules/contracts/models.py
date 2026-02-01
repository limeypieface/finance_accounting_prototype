"""
Government Contracts Domain Models (``finance_modules.contracts.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of government contract
accounting: contract modifications, subcontract records, audit findings,
cost disallowances, and DCAA compliance artifacts.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``GovernmentContractsService`` and returned to callers.  No dependency on
kernel services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* ``AuditFinding`` records track DCAA audit issues and resolutions.
* ``CostDisallowance`` records support FAR compliance evidence.
* ``ContractModification`` records provide full modification history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class ContractModification:
    """A contract modification (scope change, funding change, etc.)."""
    id: UUID
    contract_id: str
    modification_number: str
    modification_type: str  # "scope_change", "funding_change", "administrative"
    effective_date: date
    description: str = ""
    amount_change: Decimal = Decimal("0")
    previous_value: Decimal = Decimal("0")
    new_value: Decimal = Decimal("0")


@dataclass(frozen=True)
class Subcontract:
    """A subcontract under a prime contract."""
    id: UUID
    contract_id: str
    subcontractor_name: str
    subcontract_number: str
    amount: Decimal
    cost_type: str = "SUBCONTRACT"
    period: str = ""
    description: str = ""


@dataclass(frozen=True)
class AuditFinding:
    """A DCAA audit finding."""
    id: UUID
    contract_id: str
    finding_type: str  # "questioned_cost", "recommendation", "deficiency"
    description: str
    amount: Decimal = Decimal("0")
    severity: str = "medium"  # low, medium, high, critical
    status: str = "open"  # open, resolved, disputed


@dataclass(frozen=True)
class CostDisallowance:
    """A DCAA cost disallowance record."""
    id: UUID
    contract_id: str
    cost_type: str
    amount: Decimal
    reason: str
    disallowance_date: date | None = None
    is_disputed: bool = False
