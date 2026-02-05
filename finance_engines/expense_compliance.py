"""
Expense DCAA Compliance Engine (``finance_engines.expense_compliance``).

Responsibility
--------------
Pure validation functions for DCAA-compliant expense management:

* D6 -- pre-travel authorization validation (FAR 31.205-46)
* D7 -- GSA per diem rate cap enforcement (JTR / FAR 31.205-46)

Architecture position
---------------------
**Engines layer** -- pure functional core.  ZERO I/O, ZERO database,
ZERO clock reads.  May only import from ``finance_kernel.domain.values``
and module-level DTO types.

Invariants enforced
-------------------
* No ``datetime.now()`` or ``date.today()`` calls.
* No ORM, no services, no config imports.
* All functions are deterministic: same inputs = same outputs.

Failure modes
-------------
* Returns validation results (not exceptions) for business rule violations.
* Raises ``ValueError`` only for programming errors (invalid arguments).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from finance_modules.expense.dcaa_types import (
    GSAComplianceResult,
    GSARate,
    GSARateTable,
    GSAViolation,
    TravelAuthStatus,
    TravelAuthorization,
    TravelExpenseCategory,
)


# ---------------------------------------------------------------------------
# D6: Pre-travel authorization validation (FAR 31.205-46)
# ---------------------------------------------------------------------------


def validate_pre_travel_authorization(
    has_travel_expenses: bool,
    authorization: TravelAuthorization | None,
    require_pre_auth: bool,
) -> tuple[bool, str | None]:
    """Validate that travel expenses have an approved pre-authorization.

    Args:
        has_travel_expenses: Whether the expense report contains travel items.
        authorization: The linked travel authorization, if any.
        require_pre_auth: Whether pre-authorization is required (config flag).

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not require_pre_auth:
        return True, None

    if not has_travel_expenses:
        return True, None  # no travel expenses, no authorization needed

    if authorization is None:
        return False, (
            "Travel expenses require pre-travel authorization (FAR 31.205-46). "
            "No authorization found for this expense report."
        )

    if authorization.status != TravelAuthStatus.APPROVED:
        return False, (
            f"Travel authorization {authorization.authorization_id} has "
            f"status '{authorization.status.value}' -- must be 'approved' "
            f"before travel expenses can be submitted."
        )

    return True, None


def validate_expense_within_authorization(
    actual_total: Decimal,
    authorization: TravelAuthorization,
    overage_tolerance_pct: Decimal = Decimal("10"),
) -> tuple[bool, str | None]:
    """Check if actual expenses are within the authorized amount.

    Allows a configurable overage tolerance (default 10%).

    Args:
        actual_total: Total actual expenses claimed.
        authorization: The approved travel authorization.
        overage_tolerance_pct: Percentage overage allowed (e.g., 10 for 10%).

    Returns:
        Tuple of (is_within_budget, error_message).
    """
    max_allowed = authorization.total_estimated * (
        Decimal("1") + overage_tolerance_pct / Decimal("100")
    )

    if actual_total > max_allowed:
        overage = actual_total - authorization.total_estimated
        return False, (
            f"Actual expenses ({actual_total}) exceed authorized amount "
            f"({authorization.total_estimated}) by {overage}. "
            f"Maximum allowed with {overage_tolerance_pct}% tolerance: "
            f"{max_allowed}."
        )

    return True, None


# ---------------------------------------------------------------------------
# D7: GSA per diem rate cap enforcement (JTR / FAR 31.205-46)
# ---------------------------------------------------------------------------


def lookup_gsa_rate(
    gsa_rate_table: GSARateTable,
    location: str,
    travel_date: date,
) -> GSARate:
    """Find the applicable GSA rate for a location and date.

    Falls back to the default CONUS rate if no location-specific rate
    is found.

    Args:
        gsa_rate_table: The loaded GSA rate table.
        location: Destination city/state.
        travel_date: The date of travel.

    Returns:
        The applicable GSARate (location-specific or default CONUS).
    """
    specific = gsa_rate_table.lookup(location, travel_date)
    if specific is not None:
        return specific
    return gsa_rate_table.get_default_rate(travel_date)


def validate_lodging_against_gsa(
    nightly_amounts: tuple[tuple[date, Decimal], ...],
    gsa_rate: GSARate,
) -> tuple[bool, list[tuple[date, Decimal, Decimal]]]:
    """Check each night's lodging against the GSA maximum rate.

    Args:
        nightly_amounts: Sequence of (date, amount) for each night.
        gsa_rate: The applicable GSA rate.

    Returns:
        Tuple of (all_compliant, violations) where violations is a list
        of (date, amount, gsa_limit) for non-compliant nights.
    """
    violations = []
    for night_date, amount in nightly_amounts:
        if amount > gsa_rate.lodging_rate:
            violations.append((night_date, amount, gsa_rate.lodging_rate))

    return len(violations) == 0, violations


def compute_allowable_per_diem(
    total_days: int,
    gsa_rate: GSARate,
    include_first_last_day_reduction: bool = True,
) -> Decimal:
    """Compute the maximum allowable per diem for a trip.

    First and last day of travel receive 75% of the standard M&IE rate
    (per Federal Travel Regulation).

    Args:
        total_days: Total number of travel days.
        gsa_rate: The applicable GSA rate.
        include_first_last_day_reduction: Whether to apply the 75% rule
            for first and last day of travel.

    Returns:
        Maximum allowable M&IE (meals and incidental expenses) total.
    """
    if total_days <= 0:
        return Decimal("0")

    if total_days == 1:
        # Single day: 75% of full rate
        if include_first_last_day_reduction:
            return gsa_rate.first_last_day_meals
        return gsa_rate.meals_rate

    if include_first_last_day_reduction and total_days >= 2:
        # First and last day at 75%, middle days at full rate
        middle_days = total_days - 2
        return (
            gsa_rate.first_last_day_meals * 2  # first + last day
            + gsa_rate.meals_rate * middle_days
        )

    return gsa_rate.meals_rate * total_days


def validate_gsa_compliance(
    expense_lines: tuple[tuple[UUID, TravelExpenseCategory, Decimal, date], ...],
    gsa_rate_table: GSARateTable,
    travel_location: str,
    travel_start: date,
    travel_end: date,
) -> GSAComplianceResult:
    """Validate all travel expenses against GSA per diem limits.

    Checks lodging (per night) and meals (per day) against GSA rates
    for the travel location.

    Args:
        expense_lines: Tuple of (line_id, category, amount, expense_date).
        gsa_rate_table: The loaded GSA rate table.
        travel_location: Destination for GSA rate lookup.
        travel_start: First day of travel.
        travel_end: Last day of travel.

    Returns:
        GSAComplianceResult with compliance status and any violations.
    """
    gsa_rate = lookup_gsa_rate(gsa_rate_table, travel_location, travel_start)

    total_days = (travel_end - travel_start).days + 1
    max_meals = compute_allowable_per_diem(total_days, gsa_rate)

    violations: list[GSAViolation] = []
    total_claimed = Decimal("0")
    total_allowed = Decimal("0")

    # Accumulate meals and lodging separately
    total_meals_claimed = Decimal("0")
    total_lodging_claimed = Decimal("0")
    lodging_nights = 0

    for line_id, category, amount, expense_date in expense_lines:
        total_claimed += amount

        if category == TravelExpenseCategory.MEALS:
            total_meals_claimed += amount

        elif category == TravelExpenseCategory.LODGING:
            total_lodging_claimed += amount
            lodging_nights += 1

            # Per-night lodging check
            if amount > gsa_rate.lodging_rate:
                violations.append(GSAViolation(
                    expense_line_id=line_id,
                    category=category,
                    claimed_amount=amount,
                    gsa_limit=gsa_rate.lodging_rate,
                    excess=amount - gsa_rate.lodging_rate,
                    location=travel_location,
                    expense_date=expense_date,
                ))

    # Total meals check against per diem
    if total_meals_claimed > max_meals:
        # Create a single violation for total meals overage
        violations.append(GSAViolation(
            expense_line_id=UUID(int=0),  # aggregate violation
            category=TravelExpenseCategory.MEALS,
            claimed_amount=total_meals_claimed,
            gsa_limit=max_meals,
            excess=total_meals_claimed - max_meals,
            location=travel_location,
            expense_date=travel_start,
        ))

    # Compute allowed totals
    max_lodging = gsa_rate.lodging_rate * max(lodging_nights, 0)
    total_allowed = max_meals + max_lodging
    # Add non-capped categories to both claimed and allowed
    non_capped = total_claimed - total_meals_claimed - total_lodging_claimed
    total_allowed += non_capped

    excess = max(total_claimed - total_allowed, Decimal("0"))

    return GSAComplianceResult(
        is_compliant=len(violations) == 0,
        total_claimed=total_claimed,
        total_allowed=total_allowed,
        excess_amount=excess,
        violations=tuple(violations),
    )
