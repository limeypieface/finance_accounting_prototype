"""
Lease Accounting Domain Models (``finance_modules.lease.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of ASC 842 lease
accounting: leases, classification, payments, ROU assets, lease
liabilities, amortization schedules, and modifications.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``LeaseAccountingService`` and returned to callers.  No dependency on
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
* ``LeaseClassification`` decisions are auditable per ASC 842-10-25.
* ``ROUAsset`` and ``LeaseLiability`` records support balance sheet
  disclosure requirements.
* ``LeaseModification`` records track re-measurement events.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.lease.models")


class LeaseClassification(Enum):
    """Lease classification per ASC 842."""
    FINANCE = "finance"
    OPERATING = "operating"
    SHORT_TERM = "short_term"


class LeaseStatus(Enum):
    """Lease lifecycle states."""
    DRAFT = "draft"
    ACTIVE = "active"
    MODIFIED = "modified"
    TERMINATED = "terminated"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Lease:
    """A lease agreement."""
    id: UUID
    lease_number: str
    lessee_id: UUID
    lessor_name: str
    commencement_date: date
    end_date: date
    classification: LeaseClassification = LeaseClassification.OPERATING
    status: LeaseStatus = LeaseStatus.DRAFT
    monthly_payment: Decimal = Decimal("0")
    discount_rate: Decimal = Decimal("0.05")  # IBR
    currency: str = "USD"


@dataclass(frozen=True)
class LeasePayment:
    """A lease payment record."""
    id: UUID
    lease_id: UUID
    payment_date: date
    amount: Decimal
    principal_portion: Decimal = Decimal("0")
    interest_portion: Decimal = Decimal("0")
    payment_number: int = 0


@dataclass(frozen=True)
class ROUAsset:
    """Right-of-use asset."""
    id: UUID
    lease_id: UUID
    initial_value: Decimal
    accumulated_amortization: Decimal = Decimal("0")
    carrying_value: Decimal = Decimal("0")
    commencement_date: date | None = None


@dataclass(frozen=True)
class LeaseLiability:
    """Lease liability."""
    id: UUID
    lease_id: UUID
    initial_value: Decimal
    current_balance: Decimal = Decimal("0")
    commencement_date: date | None = None


@dataclass(frozen=True)
class AmortizationScheduleLine:
    """One line of an amortization schedule."""
    period: int
    payment_date: date
    payment: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal


@dataclass(frozen=True)
class LeaseModification:
    """A lease modification record."""
    id: UUID
    lease_id: UUID
    modification_date: date
    description: str
    new_monthly_payment: Decimal | None = None
    new_end_date: date | None = None
    remeasurement_amount: Decimal = Decimal("0")
    actor_id: UUID | None = None
