"""
DCAA Rate Control Types (``finance_modules.contracts.rate_types``).

Responsibility
--------------
Frozen dataclass value objects for DCAA-compliant rate management:
labor rate schedules, contract billing rate ceilings, rate verification
results, indirect rate records, and provisional-to-final reconciliation.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``GovernmentContractsService`` and the ``rate_compliance`` engine.  No
dependency on kernel services, database, or engines.

Invariants enforced
-------------------
* D8 -- RATE_CEILING: labor rates capped at contract maximums (FAR 31.201-3)
* All models are ``frozen=True`` (immutable after construction).
* All rate/monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.
* Negative rates raise ``ValueError``.

Audit relevance
---------------
* Rate schedules document approved billing rates for DCAA audits.
* Rate verification results prove compliance at charge time.
* Reconciliation records support year-end incurred cost submissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RateSource(str, Enum):
    """Source/status of an approved rate."""
    NEGOTIATED = "negotiated"  # government-negotiated rate
    PROVISIONAL = "provisional"  # pending final DCAA determination
    FINAL = "final"  # DCAA-audited final rate


class RateViolationType(str, Enum):
    """Type of rate violation detected."""
    EXCEEDS_CLASSIFICATION = "exceeds_classification"
    EXCEEDS_CONTRACT_CEILING = "exceeds_contract_ceiling"
    PROVISIONAL_NOT_APPROVED = "provisional_not_approved"
    RATE_EXPIRED = "rate_expired"


class IndirectRateType(str, Enum):
    """Indirect cost pool rate types."""
    FRINGE = "fringe"
    OVERHEAD = "overhead"
    G_AND_A = "g_and_a"
    MATERIAL_HANDLING = "material_handling"


class ReconciliationDirection(str, Enum):
    """Direction of provisional-to-final rate adjustment."""
    UNDERAPPLIED = "underapplied"  # final > provisional, owes more
    OVERAPPLIED = "overapplied"  # final < provisional, credit due
    EXACT = "exact"  # no adjustment needed


# ---------------------------------------------------------------------------
# Labor rate schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaborRateSchedule:
    """Approved labor rate per employee classification (D8).

    Associates an employee classification (e.g., "Senior Engineer") with
    approved billing rates.  Used to verify that charged rates do not
    exceed approved amounts.
    """
    schedule_id: UUID
    employee_classification: str
    labor_category: str  # DCAA labor category code
    base_rate: Decimal  # unloaded hourly rate
    loaded_rate: Decimal  # base + fringe + overhead
    effective_from: date
    effective_to: date | None = None
    rate_source: RateSource = RateSource.PROVISIONAL

    def __post_init__(self) -> None:
        if self.base_rate < Decimal("0"):
            raise ValueError(
                f"base_rate cannot be negative: {self.base_rate}"
            )
        if self.loaded_rate < Decimal("0"):
            raise ValueError(
                f"loaded_rate cannot be negative: {self.loaded_rate}"
            )
        if self.loaded_rate < self.base_rate:
            raise ValueError(
                f"loaded_rate ({self.loaded_rate}) cannot be less than "
                f"base_rate ({self.base_rate})"
            )

    def is_effective(self, as_of: date) -> bool:
        """Check if this rate schedule is effective on the given date."""
        if as_of < self.effective_from:
            return False
        if self.effective_to is not None and as_of > self.effective_to:
            return False
        return True


# ---------------------------------------------------------------------------
# Contract rate ceiling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractRateCeiling:
    """Maximum billing rate per contract per labor category (D8).

    Contract clauses may cap the rate that can be billed, regardless of
    the approved rate schedule.  This is an additional constraint on top
    of the labor rate schedule.
    """
    contract_id: UUID
    labor_category: str
    max_hourly_rate: Decimal
    max_loaded_rate: Decimal | None = None
    effective_from: date = date(2024, 1, 1)
    effective_to: date | None = None
    ceiling_source: str = ""  # e.g., "contract_clause_H.4", "mod_03"

    def __post_init__(self) -> None:
        if self.max_hourly_rate < Decimal("0"):
            raise ValueError(
                f"max_hourly_rate cannot be negative: {self.max_hourly_rate}"
            )
        if (
            self.max_loaded_rate is not None
            and self.max_loaded_rate < Decimal("0")
        ):
            raise ValueError(
                f"max_loaded_rate cannot be negative: {self.max_loaded_rate}"
            )

    def is_effective(self, as_of: date) -> bool:
        """Check if this ceiling is effective on the given date."""
        if as_of < self.effective_from:
            return False
        if self.effective_to is not None and as_of > self.effective_to:
            return False
        return True


# ---------------------------------------------------------------------------
# Rate verification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateVerificationResult:
    """Result of verifying a labor charge against approved rates (D8)."""
    is_valid: bool
    employee_id: UUID
    charged_rate: Decimal
    approved_rate: Decimal
    ceiling_rate: Decimal | None = None  # contract ceiling if applicable
    excess_amount: Decimal = Decimal("0")  # amount over approved/ceiling
    violation_type: RateViolationType | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Indirect rate records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndirectRateRecord:
    """Indirect cost rate for a given fiscal year (D8).

    Tracks both provisional rates (used during the year) and final rates
    (determined after DCAA audit) for year-end reconciliation.
    """
    rate_id: UUID
    rate_type: IndirectRateType
    rate_value: Decimal  # percentage (e.g., 0.35 for 35%)
    base_description: str  # e.g., "direct_labor_dollars"
    fiscal_year: int
    rate_status: RateSource = RateSource.PROVISIONAL
    effective_from: date = date(2024, 1, 1)
    effective_to: date | None = None
    approved_by: UUID | None = None
    approval_date: date | None = None

    def __post_init__(self) -> None:
        if self.rate_value < Decimal("0"):
            raise ValueError(
                f"rate_value cannot be negative: {self.rate_value}"
            )


# ---------------------------------------------------------------------------
# Rate reconciliation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateReconciliationRecord:
    """Year-end provisional-to-final indirect rate reconciliation.

    Computes the adjustment needed when final audited rates differ from
    the provisional rates used throughout the fiscal year.
    """
    reconciliation_id: UUID
    fiscal_year: int
    rate_type: IndirectRateType
    provisional_rate: Decimal
    final_rate: Decimal
    rate_difference: Decimal = Decimal("0")
    base_amount: Decimal = Decimal("0")  # total base for the year
    adjustment_amount: Decimal = Decimal("0")  # base * rate_difference
    direction: ReconciliationDirection = ReconciliationDirection.EXACT

    def __post_init__(self) -> None:
        # Compute derived fields
        computed_diff = self.final_rate - self.provisional_rate
        computed_adjustment = self.base_amount * computed_diff
        if computed_diff > Decimal("0"):
            computed_direction = ReconciliationDirection.UNDERAPPLIED
        elif computed_diff < Decimal("0"):
            computed_direction = ReconciliationDirection.OVERAPPLIED
        else:
            computed_direction = ReconciliationDirection.EXACT
        object.__setattr__(self, "rate_difference", computed_diff)
        object.__setattr__(self, "adjustment_amount", computed_adjustment)
        object.__setattr__(self, "direction", computed_direction)
