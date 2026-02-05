"""
Tests for Expense DCAA Compliance Engine.

Covers:
- D6: Pre-travel authorization validation (FAR 31.205-46)
- D7: GSA per diem rate cap enforcement (JTR / FAR 31.205-46)
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.expense_compliance import (
    compute_allowable_per_diem,
    lookup_gsa_rate,
    validate_expense_within_authorization,
    validate_gsa_compliance,
    validate_lodging_against_gsa,
    validate_pre_travel_authorization,
)
from finance_modules.expense.dcaa_types import (
    GSARate,
    GSARateTable,
    TravelAuthStatus,
    TravelAuthorization,
    TravelExpenseCategory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gsa_rate(
    location_code: str = "DC-Washington",
    state: str = "DC",
    city: str = "Washington",
    lodging: str = "258.00",
    meals: str = "79.00",
) -> GSARate:
    return GSARate(
        location_code=location_code,
        state=state,
        city=city,
        fiscal_year=2026,
        lodging_rate=Decimal(lodging),
        meals_rate=Decimal(meals),
        effective_from=date(2025, 10, 1),
        effective_to=date(2026, 9, 30),
    )


def _rate_table(*rates: GSARate) -> GSARateTable:
    return GSARateTable(
        rates=rates,
        default_conus_lodging=Decimal("107"),
        default_conus_meals=Decimal("68"),
    )


def _travel_auth(
    status: TravelAuthStatus = TravelAuthStatus.APPROVED,
    total_estimated: str = "2000.00",
) -> TravelAuthorization:
    return TravelAuthorization(
        authorization_id=uuid4(),
        employee_id=uuid4(),
        purpose="Client meeting",
        destination="Washington, DC",
        travel_start=date(2026, 3, 10),
        travel_end=date(2026, 3, 12),
        total_estimated=Decimal(total_estimated),
        status=status,
    )


# ===========================================================================
# D6: Pre-Travel Authorization
# ===========================================================================


class TestPreTravelAuthorization:
    """D6: Travel expenses require pre-authorization."""

    def test_no_travel_expenses_always_valid(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=False,
            authorization=None,
            require_pre_auth=True,
        )
        assert is_valid

    def test_pre_auth_not_required_always_valid(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=True,
            authorization=None,
            require_pre_auth=False,
        )
        assert is_valid

    def test_travel_without_auth_fails(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=True,
            authorization=None,
            require_pre_auth=True,
        )
        assert not is_valid
        assert "FAR 31.205-46" in msg

    def test_travel_with_approved_auth(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=True,
            authorization=_travel_auth(TravelAuthStatus.APPROVED),
            require_pre_auth=True,
        )
        assert is_valid

    def test_travel_with_draft_auth_fails(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=True,
            authorization=_travel_auth(TravelAuthStatus.DRAFT),
            require_pre_auth=True,
        )
        assert not is_valid
        assert "must be 'approved'" in msg

    def test_travel_with_rejected_auth_fails(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=True,
            authorization=_travel_auth(TravelAuthStatus.REJECTED),
            require_pre_auth=True,
        )
        assert not is_valid

    def test_travel_with_submitted_auth_fails(self):
        is_valid, msg = validate_pre_travel_authorization(
            has_travel_expenses=True,
            authorization=_travel_auth(TravelAuthStatus.SUBMITTED),
            require_pre_auth=True,
        )
        assert not is_valid


class TestExpenseWithinAuthorization:
    """D6: Actual expenses within authorized amount."""

    def test_within_budget(self):
        auth = _travel_auth(total_estimated="2000.00")
        is_valid, msg = validate_expense_within_authorization(
            actual_total=Decimal("1800.00"),
            authorization=auth,
        )
        assert is_valid

    def test_exactly_at_budget(self):
        auth = _travel_auth(total_estimated="2000.00")
        is_valid, msg = validate_expense_within_authorization(
            actual_total=Decimal("2000.00"),
            authorization=auth,
        )
        assert is_valid

    def test_within_tolerance(self):
        auth = _travel_auth(total_estimated="2000.00")
        is_valid, msg = validate_expense_within_authorization(
            actual_total=Decimal("2100.00"),
            authorization=auth,
            overage_tolerance_pct=Decimal("10"),
        )
        assert is_valid  # 2100 <= 2200 (2000 + 10%)

    def test_exceeds_tolerance(self):
        auth = _travel_auth(total_estimated="2000.00")
        is_valid, msg = validate_expense_within_authorization(
            actual_total=Decimal("2500.00"),
            authorization=auth,
            overage_tolerance_pct=Decimal("10"),
        )
        assert not is_valid
        assert "exceed" in msg


# ===========================================================================
# D7: GSA Rate Lookup
# ===========================================================================


class TestGSARateLookup:
    """D7: GSA rate table lookup by location and date."""

    def test_location_specific_rate(self):
        dc_rate = _gsa_rate()
        table = _rate_table(dc_rate)
        result = lookup_gsa_rate(table, "Washington", date(2026, 3, 15))
        assert result.lodging_rate == Decimal("258.00")

    def test_fallback_to_conus_default(self):
        table = _rate_table()  # no location-specific rates
        result = lookup_gsa_rate(table, "SmallTown", date(2026, 3, 15))
        assert result.lodging_rate == Decimal("107")
        assert result.meals_rate == Decimal("68")

    def test_case_insensitive_lookup(self):
        dc_rate = _gsa_rate()
        table = _rate_table(dc_rate)
        result = lookup_gsa_rate(table, "washington", date(2026, 3, 15))
        assert result.lodging_rate == Decimal("258.00")


# ===========================================================================
# D7: Lodging Validation
# ===========================================================================


class TestLodgingValidation:
    """D7: Per-night lodging capped at GSA rate."""

    def test_all_nights_compliant(self):
        rate = _gsa_rate(lodging="258.00")
        nightly = (
            (date(2026, 3, 10), Decimal("200.00")),
            (date(2026, 3, 11), Decimal("258.00")),
        )
        all_ok, violations = validate_lodging_against_gsa(nightly, rate)
        assert all_ok
        assert violations == []

    def test_one_night_over(self):
        rate = _gsa_rate(lodging="258.00")
        nightly = (
            (date(2026, 3, 10), Decimal("200.00")),
            (date(2026, 3, 11), Decimal("300.00")),
        )
        all_ok, violations = validate_lodging_against_gsa(nightly, rate)
        assert not all_ok
        assert len(violations) == 1
        assert violations[0][1] == Decimal("300.00")


# ===========================================================================
# D7: Per Diem Calculation
# ===========================================================================


class TestAllowablePerDiem:
    """D7: Compute maximum allowable M&IE."""

    def test_single_day_75_percent(self):
        rate = _gsa_rate(meals="79.00")
        result = compute_allowable_per_diem(1, rate)
        assert result == rate.first_last_day_meals

    def test_two_days_both_75(self):
        rate = _gsa_rate(meals="79.00")
        result = compute_allowable_per_diem(2, rate)
        assert result == rate.first_last_day_meals * 2

    def test_three_days_first_last_reduced(self):
        rate = _gsa_rate(meals="79.00")
        result = compute_allowable_per_diem(3, rate)
        expected = rate.first_last_day_meals * 2 + rate.meals_rate * 1
        assert result == expected

    def test_zero_days(self):
        rate = _gsa_rate(meals="79.00")
        result = compute_allowable_per_diem(0, rate)
        assert result == Decimal("0")

    def test_no_first_last_reduction(self):
        rate = _gsa_rate(meals="79.00")
        result = compute_allowable_per_diem(
            3, rate, include_first_last_day_reduction=False,
        )
        assert result == Decimal("79.00") * 3


# ===========================================================================
# D7: Full GSA Compliance Check
# ===========================================================================


class TestGSACompliance:
    """D7: Validate all travel expenses against GSA limits."""

    def test_fully_compliant(self):
        table = _rate_table(_gsa_rate(lodging="258.00", meals="79.00"))
        lines = (
            (uuid4(), TravelExpenseCategory.LODGING, Decimal("200.00"), date(2026, 3, 10)),
            (uuid4(), TravelExpenseCategory.MEALS, Decimal("50.00"), date(2026, 3, 10)),
        )
        result = validate_gsa_compliance(
            expense_lines=lines,
            gsa_rate_table=table,
            travel_location="Washington",
            travel_start=date(2026, 3, 10),
            travel_end=date(2026, 3, 10),
        )
        assert result.is_compliant

    def test_lodging_violation(self):
        table = _rate_table(_gsa_rate(lodging="258.00", meals="79.00"))
        lines = (
            (uuid4(), TravelExpenseCategory.LODGING, Decimal("350.00"), date(2026, 3, 10)),
        )
        result = validate_gsa_compliance(
            expense_lines=lines,
            gsa_rate_table=table,
            travel_location="Washington",
            travel_start=date(2026, 3, 10),
            travel_end=date(2026, 3, 10),
        )
        assert not result.is_compliant
        assert len(result.violations) >= 1
        assert result.violations[0].category == TravelExpenseCategory.LODGING

    def test_meals_total_violation(self):
        table = _rate_table(_gsa_rate(lodging="258.00", meals="79.00"))
        # Single-day trip: 75% of 79 = 59.25 is max
        lines = (
            (uuid4(), TravelExpenseCategory.MEALS, Decimal("100.00"), date(2026, 3, 10)),
        )
        result = validate_gsa_compliance(
            expense_lines=lines,
            gsa_rate_table=table,
            travel_location="Washington",
            travel_start=date(2026, 3, 10),
            travel_end=date(2026, 3, 10),
        )
        assert not result.is_compliant
        assert any(
            v.category == TravelExpenseCategory.MEALS
            for v in result.violations
        )

    def test_non_travel_categories_pass_through(self):
        table = _rate_table(_gsa_rate(lodging="258.00", meals="79.00"))
        lines = (
            (uuid4(), TravelExpenseCategory.AIRFARE, Decimal("500.00"), date(2026, 3, 10)),
            (uuid4(), TravelExpenseCategory.PARKING, Decimal("25.00"), date(2026, 3, 10)),
        )
        result = validate_gsa_compliance(
            expense_lines=lines,
            gsa_rate_table=table,
            travel_location="Washington",
            travel_start=date(2026, 3, 10),
            travel_end=date(2026, 3, 10),
        )
        # Airfare and parking are not capped by GSA
        assert result.is_compliant

    def test_conus_default_used_for_unknown_location(self):
        table = _rate_table()  # no specific rates
        lines = (
            (uuid4(), TravelExpenseCategory.LODGING, Decimal("120.00"), date(2026, 3, 10)),
        )
        result = validate_gsa_compliance(
            expense_lines=lines,
            gsa_rate_table=table,
            travel_location="SmallTown",
            travel_start=date(2026, 3, 10),
            travel_end=date(2026, 3, 10),
        )
        # 120 > CONUS default 107, so violation
        assert not result.is_compliant
