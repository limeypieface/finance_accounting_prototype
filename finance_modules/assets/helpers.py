"""
Fixed Assets Helpers (``finance_modules.assets.helpers``).

Responsibility
--------------
Pure calculation functions for depreciation (straight-line,
double-declining balance, sum-of-years'-digits, units-of-production)
and impairment testing.  These are textbook fixed-asset formulas with
no side effects.

Architecture position
---------------------
**Modules layer** -- pure helper functions.  No I/O, no session, no
clock, no database access.  Called by ``FixedAssetService`` or from tests.

Invariants enforced
-------------------
* All numeric inputs and outputs use ``Decimal`` -- NEVER ``float``.
* Division-by-zero cases return ``Decimal("0")`` instead of raising.
* Results are quantized to 2 decimal places.

Failure modes
-------------
* Zero or negative useful life  -> returns ``Decimal("0")``.
* Zero total estimated units  -> returns ``Decimal("0")``.
* Fair value >= carrying value  -> impairment loss is ``Decimal("0")``.

Audit relevance
---------------
These functions produce intermediate values consumed by
``FixedAssetService`` for journal entry amounts.  Depreciation
methodology must be documented and consistently applied per ASC 360.
"""

from __future__ import annotations

from decimal import Decimal


def straight_line(
    cost: Decimal,
    salvage_value: Decimal,
    useful_life_months: int,
) -> Decimal:
    """
    Calculate monthly straight-line depreciation.

    Preconditions:
        - ``cost`` and ``salvage_value`` are ``Decimal``.
        - ``useful_life_months`` is a positive integer (0 returns zero).
    Postconditions:
        - Returns monthly depreciation as ``Decimal`` quantized to 0.01.
        - Returns ``Decimal("0")`` if ``useful_life_months`` <= 0.
    """
    if useful_life_months <= 0:
        return Decimal("0")
    depreciable_base = cost - salvage_value
    return (depreciable_base / useful_life_months).quantize(Decimal("0.01"))


def double_declining_balance(
    cost: Decimal,
    accumulated_depreciation: Decimal,
    useful_life_months: int,
    salvage_value: Decimal = Decimal("0"),
) -> Decimal:
    """
    Calculate monthly double-declining-balance depreciation.

    Preconditions:
        - All monetary args are ``Decimal``.
        - ``useful_life_months`` is a positive integer.
    Postconditions:
        - Rate = 2 / useful_life_months, applied to net book value.
        - Will not depreciate below salvage value.
        - Returns ``Decimal("0")`` if NBV <= salvage or life <= 0.
    """
    if useful_life_months <= 0:
        return Decimal("0")
    net_book_value = cost - accumulated_depreciation
    if net_book_value <= salvage_value:
        return Decimal("0")
    rate = Decimal("2") / Decimal(str(useful_life_months))
    depreciation = (net_book_value * rate).quantize(Decimal("0.01"))
    # Don't go below salvage
    max_depreciation = net_book_value - salvage_value
    return min(depreciation, max_depreciation)


def sum_of_years_digits(
    cost: Decimal,
    salvage_value: Decimal,
    useful_life_years: int,
    current_year: int,
) -> Decimal:
    """
    Calculate annual sum-of-years'-digits depreciation.

    Preconditions:
        - ``cost`` and ``salvage_value`` are ``Decimal``.
        - ``useful_life_years`` > 0.
        - ``current_year`` is 1-based (1 = first year).
    Postconditions:
        - Returns annual depreciation for the given year.
        - Returns ``Decimal("0")`` if life <= 0 or year > life.
    """
    if useful_life_years <= 0 or current_year > useful_life_years:
        return Decimal("0")
    sum_digits = Decimal(str(useful_life_years * (useful_life_years + 1) // 2))
    remaining_years = Decimal(str(useful_life_years - current_year + 1))
    depreciable_base = cost - salvage_value
    return (depreciable_base * remaining_years / sum_digits).quantize(Decimal("0.01"))


def units_of_production(
    cost: Decimal,
    salvage_value: Decimal,
    total_estimated_units: Decimal,
    units_produced: Decimal,
) -> Decimal:
    """
    Calculate units-of-production depreciation for a period.

    Preconditions:
        - All args are ``Decimal``.
        - ``total_estimated_units`` > 0.
    Postconditions:
        - Returns depreciation for the units produced.
        - Returns ``Decimal("0")`` if total_estimated_units <= 0.
    """
    if total_estimated_units <= Decimal("0"):
        return Decimal("0")
    depreciable_base = cost - salvage_value
    rate_per_unit = depreciable_base / total_estimated_units
    return (rate_per_unit * units_produced).quantize(Decimal("0.01"))


def calculate_impairment_loss(
    carrying_value: Decimal,
    fair_value: Decimal,
) -> Decimal:
    """
    Calculate impairment loss.

    Preconditions:
        - ``carrying_value`` and ``fair_value`` are ``Decimal``.
    Postconditions:
        - Returns ``carrying_value - fair_value`` if impaired.
        - Returns ``Decimal("0")`` if fair_value >= carrying_value.
    """
    if fair_value >= carrying_value:
        return Decimal("0")
    return carrying_value - fair_value
