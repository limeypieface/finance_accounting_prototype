"""
DCAA Expense Compliance Types (``finance_modules.expense.dcaa_types``).

Responsibility
--------------
Frozen dataclass value objects for DCAA-compliant expense management:
pre-travel authorization, GSA per diem rate enforcement, and GSA
compliance validation results.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``ExpenseService`` and the ``expense_compliance`` engine.  No dependency
on kernel services, database, or engines.

Invariants enforced
-------------------
* D6 -- PRE_TRAVEL_AUTH: travel expenses require pre-authorization (FAR 31.205-46)
* D7 -- GSA_RATE_CAP: per diem/lodging capped at GSA rates by location (JTR/FAR 31.205-46)
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.
* Negative amounts raise ``ValueError``.

Audit relevance
---------------
* Travel authorizations are pre-approval artifacts for DCAA audits.
* GSA compliance results document rate cap enforcement for auditors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TravelAuthStatus(str, Enum):
    """Travel authorization lifecycle states (D6)."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"  # travel dates passed without expense report


class TravelExpenseCategory(str, Enum):
    """Categories of travel expenses for GSA enforcement (D7)."""
    AIRFARE = "airfare"
    LODGING = "lodging"
    MEALS = "meals"
    MILEAGE = "mileage"
    RENTAL_CAR = "rental_car"
    TAXI_RIDESHARE = "taxi_rideshare"
    PARKING = "parking"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Travel authorization (FAR 31.205-46)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TravelCostEstimate:
    """A line item in a travel authorization estimate."""
    category: TravelExpenseCategory
    estimated_amount: Decimal
    gsa_rate: Decimal | None = None  # applicable GSA rate for reference
    nights: int = 0  # for lodging
    days: int = 0  # for meals/per diem

    def __post_init__(self) -> None:
        if self.estimated_amount < Decimal("0"):
            raise ValueError(
                f"estimated_amount cannot be negative: {self.estimated_amount}"
            )
        if self.nights < 0:
            raise ValueError(f"nights cannot be negative: {self.nights}")
        if self.days < 0:
            raise ValueError(f"days cannot be negative: {self.days}")


@dataclass(frozen=True)
class TravelAuthorization:
    """Pre-travel authorization request (D6 / FAR 31.205-46).

    Must be approved BEFORE travel costs are incurred.  Expense reports
    for travel must reference an approved authorization.
    """
    authorization_id: UUID
    employee_id: UUID
    purpose: str
    destination: str  # city/state for GSA rate lookup
    travel_start: date
    travel_end: date
    estimated_costs: tuple[TravelCostEstimate, ...] = field(
        default_factory=tuple
    )
    total_estimated: Decimal = Decimal("0")
    currency: str = "USD"
    contract_id: UUID | None = None  # if travel is contract-chargeable
    status: TravelAuthStatus = TravelAuthStatus.DRAFT

    def __post_init__(self) -> None:
        if self.travel_end < self.travel_start:
            raise ValueError(
                f"travel_end ({self.travel_end}) cannot precede "
                f"travel_start ({self.travel_start})"
            )
        if self.total_estimated < Decimal("0"):
            raise ValueError(
                f"total_estimated cannot be negative: {self.total_estimated}"
            )


# ---------------------------------------------------------------------------
# GSA rate table (JTR / FAR 31.205-46)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GSARate:
    """GSA per diem rate by location and date range (D7).

    Rates are published annually by the General Services Administration
    for the federal fiscal year (October 1 through September 30).
    """
    location_code: str  # e.g., "CA-San Francisco"
    state: str
    city: str | None = None
    fiscal_year: int = 2026
    lodging_rate: Decimal = Decimal("0")  # max nightly lodging
    meals_rate: Decimal = Decimal("0")  # M&IE daily rate
    first_last_day_meals: Decimal = Decimal("0")  # 75% of M&IE
    incidentals_rate: Decimal = Decimal("0")
    effective_from: date = date(2025, 10, 1)
    effective_to: date = date(2026, 9, 30)

    def __post_init__(self) -> None:
        if self.lodging_rate < Decimal("0"):
            raise ValueError(
                f"lodging_rate cannot be negative: {self.lodging_rate}"
            )
        if self.meals_rate < Decimal("0"):
            raise ValueError(
                f"meals_rate cannot be negative: {self.meals_rate}"
            )
        # Compute first/last day meals if not provided (75% of M&IE)
        if self.first_last_day_meals == Decimal("0") and self.meals_rate > Decimal("0"):
            computed = (self.meals_rate * Decimal("75") / Decimal("100")).quantize(
                Decimal("0.01")
            )
            object.__setattr__(self, "first_last_day_meals", computed)


@dataclass(frozen=True)
class GSARateTable:
    """Full GSA rate table, loaded from configuration (D7).

    Contains location-specific rates and fallback CONUS defaults.
    """
    rates: tuple[GSARate, ...] = field(default_factory=tuple)
    default_conus_lodging: Decimal = Decimal("107")  # FY2026 standard
    default_conus_meals: Decimal = Decimal("68")  # FY2026 standard
    default_conus_incidentals: Decimal = Decimal("5")

    def lookup(self, location: str, travel_date: date) -> GSARate | None:
        """Find the rate for a specific location and date.

        Returns the first matching rate, or None if no location-specific
        rate exists (caller should fall back to default CONUS).
        """
        location_lower = location.lower()
        for rate in self.rates:
            code_lower = rate.location_code.lower()
            city_lower = (rate.city or "").lower()
            if (
                code_lower == location_lower
                or city_lower == location_lower
                or location_lower in code_lower
            ):
                if rate.effective_from <= travel_date <= rate.effective_to:
                    return rate
        return None

    def get_default_rate(self, travel_date: date) -> GSARate:
        """Return the default CONUS rate (standard federal per diem)."""
        fy_start = date(travel_date.year, 10, 1)
        if travel_date < fy_start:
            fy_start = date(travel_date.year - 1, 10, 1)
        fy_end = date(fy_start.year + 1, 9, 30)
        return GSARate(
            location_code="CONUS-DEFAULT",
            state="",
            fiscal_year=fy_end.year,
            lodging_rate=self.default_conus_lodging,
            meals_rate=self.default_conus_meals,
            incidentals_rate=self.default_conus_incidentals,
            effective_from=fy_start,
            effective_to=fy_end,
        )


# ---------------------------------------------------------------------------
# GSA compliance results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GSAViolation:
    """A specific GSA rate violation on an expense line (D7)."""
    expense_line_id: UUID
    category: TravelExpenseCategory
    claimed_amount: Decimal
    gsa_limit: Decimal
    excess: Decimal
    location: str
    expense_date: date


@dataclass(frozen=True)
class GSAComplianceResult:
    """Result of checking expenses against GSA per diem limits (D7)."""
    is_compliant: bool
    total_claimed: Decimal
    total_allowed: Decimal
    excess_amount: Decimal
    violations: tuple[GSAViolation, ...] = field(default_factory=tuple)
