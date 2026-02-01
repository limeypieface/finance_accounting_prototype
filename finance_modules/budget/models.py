"""
Budgeting Domain Models (``finance_modules.budget.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of budgeting: budget
versions, entries, locks, encumbrances, variances, and forecasts.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``BudgetService`` and returned to callers.  No dependency on kernel
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
* ``BudgetVersion`` records maintain full version history for accountability.
* ``Encumbrance`` lifecycle transitions are auditable via status field.
* ``BudgetVariance`` records support budget control compliance.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.budget.models")


class BudgetStatus(Enum):
    """Budget version states."""
    DRAFT = "draft"
    APPROVED = "approved"
    LOCKED = "locked"
    ARCHIVED = "archived"


class EncumbranceStatus(Enum):
    """Encumbrance states."""
    OPEN = "open"
    PARTIALLY_RELIEVED = "partially_relieved"
    RELIEVED = "relieved"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BudgetVersion:
    """A budget version (e.g., original, revised, forecast)."""
    id: UUID
    name: str
    fiscal_year: int
    status: BudgetStatus = BudgetStatus.DRAFT
    description: str | None = None
    created_date: date | None = None


@dataclass(frozen=True)
class BudgetEntry:
    """A single budget line item."""
    id: UUID
    version_id: UUID
    account_code: str
    period: str
    amount: Decimal
    currency: str = "USD"
    dimensions: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True)
class BudgetLock:
    """A budget lock preventing further changes."""
    id: UUID
    version_id: UUID
    period_range_start: str
    period_range_end: str
    locked_by: UUID | None = None
    locked_date: date | None = None


@dataclass(frozen=True)
class Encumbrance:
    """An encumbrance (commitment against budget)."""
    id: UUID
    po_id: UUID
    account_code: str
    amount: Decimal
    period: str
    status: EncumbranceStatus = EncumbranceStatus.OPEN
    relieved_amount: Decimal = Decimal("0")
    currency: str = "USD"


@dataclass(frozen=True)
class BudgetVariance:
    """Budget vs actual variance result."""
    account_code: str
    period: str
    budget_amount: Decimal
    actual_amount: Decimal
    variance_amount: Decimal
    variance_percentage: Decimal
    is_favorable: bool


@dataclass(frozen=True)
class ForecastEntry:
    """A forecast update entry."""
    id: UUID
    version_id: UUID
    account_code: str
    period: str
    forecast_amount: Decimal
    basis: str = "trend"  # trend, manual, statistical
    currency: str = "USD"
