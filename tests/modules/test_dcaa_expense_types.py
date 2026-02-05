"""
Tests for DCAA Expense Domain Types.

Validates frozen dataclass invariants:
- TravelAuthorization: date ordering, non-negative amounts
- TravelCostEstimate: non-negative amounts
- GSARate: non-negative rates, first/last day computation
- GSARateTable: lookup functionality
- GSAComplianceResult / GSAViolation: value correctness
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.expense.dcaa_types import (
    GSAComplianceResult,
    GSARate,
    GSARateTable,
    GSAViolation,
    TravelAuthStatus,
    TravelAuthorization,
    TravelCostEstimate,
    TravelExpenseCategory,
)


class TestTravelCostEstimate:
    """TravelCostEstimate frozen dataclass invariants."""

    def test_valid_estimate(self):
        e = TravelCostEstimate(
            category=TravelExpenseCategory.LODGING,
            estimated_amount=Decimal("500.00"),
            nights=3,
        )
        assert e.estimated_amount == Decimal("500.00")

    def test_negative_amount_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            TravelCostEstimate(
                category=TravelExpenseCategory.LODGING,
                estimated_amount=Decimal("-100"),
            )

    def test_negative_nights_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            TravelCostEstimate(
                category=TravelExpenseCategory.LODGING,
                estimated_amount=Decimal("100"),
                nights=-1,
            )

    def test_negative_days_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            TravelCostEstimate(
                category=TravelExpenseCategory.MEALS,
                estimated_amount=Decimal("100"),
                days=-1,
            )

    def test_all_categories(self):
        for cat in TravelExpenseCategory:
            e = TravelCostEstimate(
                category=cat,
                estimated_amount=Decimal("50"),
            )
            assert e.category == cat


class TestTravelAuthorization:
    """TravelAuthorization frozen dataclass invariants."""

    def test_valid_authorization(self):
        auth = TravelAuthorization(
            authorization_id=uuid4(),
            employee_id=uuid4(),
            purpose="Client meeting",
            destination="Washington, DC",
            travel_start=date(2026, 3, 10),
            travel_end=date(2026, 3, 12),
            total_estimated=Decimal("2000.00"),
        )
        assert auth.status == TravelAuthStatus.DRAFT

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="cannot precede"):
            TravelAuthorization(
                authorization_id=uuid4(),
                employee_id=uuid4(),
                purpose="Test",
                destination="DC",
                travel_start=date(2026, 3, 12),
                travel_end=date(2026, 3, 10),
            )

    def test_negative_total_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            TravelAuthorization(
                authorization_id=uuid4(),
                employee_id=uuid4(),
                purpose="Test",
                destination="DC",
                travel_start=date(2026, 3, 10),
                travel_end=date(2026, 3, 12),
                total_estimated=Decimal("-100"),
            )

    def test_all_statuses(self):
        for status in TravelAuthStatus:
            auth = TravelAuthorization(
                authorization_id=uuid4(),
                employee_id=uuid4(),
                purpose="Test",
                destination="DC",
                travel_start=date(2026, 3, 10),
                travel_end=date(2026, 3, 12),
                status=status,
            )
            assert auth.status == status


class TestGSARate:
    """GSARate frozen dataclass invariants."""

    def test_valid_rate(self):
        rate = GSARate(
            location_code="DC-Washington",
            state="DC",
            lodging_rate=Decimal("258"),
            meals_rate=Decimal("79"),
        )
        assert rate.lodging_rate == Decimal("258")

    def test_negative_lodging_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            GSARate(
                location_code="DC",
                state="DC",
                lodging_rate=Decimal("-100"),
            )

    def test_negative_meals_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            GSARate(
                location_code="DC",
                state="DC",
                meals_rate=Decimal("-50"),
            )

    def test_first_last_day_auto_computed(self):
        rate = GSARate(
            location_code="DC-Washington",
            state="DC",
            meals_rate=Decimal("80"),
        )
        expected = (Decimal("80") * Decimal("75") / Decimal("100")).quantize(
            Decimal("0.01")
        )
        assert rate.first_last_day_meals == expected

    def test_first_last_day_explicit(self):
        rate = GSARate(
            location_code="DC",
            state="DC",
            meals_rate=Decimal("80"),
            first_last_day_meals=Decimal("55.00"),
        )
        assert rate.first_last_day_meals == Decimal("55.00")


class TestGSARateTable:
    """GSARateTable lookup functionality."""

    def test_lookup_found(self):
        dc = GSARate(
            location_code="DC-Washington",
            state="DC",
            city="Washington",
            lodging_rate=Decimal("258"),
            meals_rate=Decimal("79"),
        )
        table = GSARateTable(rates=(dc,))
        result = table.lookup("Washington", date(2026, 3, 15))
        assert result is not None
        assert result.lodging_rate == Decimal("258")

    def test_lookup_not_found(self):
        table = GSARateTable(rates=())
        result = table.lookup("SmallTown", date(2026, 3, 15))
        assert result is None

    def test_default_conus_rate(self):
        table = GSARateTable()
        default = table.get_default_rate(date(2026, 3, 15))
        assert default.lodging_rate == Decimal("107")
        assert default.meals_rate == Decimal("68")
        assert default.location_code == "CONUS-DEFAULT"

    def test_lookup_outside_date_range(self):
        dc = GSARate(
            location_code="DC-Washington",
            state="DC",
            city="Washington",
            lodging_rate=Decimal("258"),
            meals_rate=Decimal("79"),
            effective_from=date(2025, 10, 1),
            effective_to=date(2026, 9, 30),
        )
        table = GSARateTable(rates=(dc,))
        result = table.lookup("Washington", date(2024, 3, 15))
        assert result is None


class TestGSAViolation:
    """GSAViolation value correctness."""

    def test_valid_violation(self):
        v = GSAViolation(
            expense_line_id=uuid4(),
            category=TravelExpenseCategory.LODGING,
            claimed_amount=Decimal("350"),
            gsa_limit=Decimal("258"),
            excess=Decimal("92"),
            location="Washington, DC",
            expense_date=date(2026, 3, 10),
        )
        assert v.excess == Decimal("92")


class TestGSAComplianceResult:
    """GSAComplianceResult value correctness."""

    def test_compliant_result(self):
        r = GSAComplianceResult(
            is_compliant=True,
            total_claimed=Decimal("500"),
            total_allowed=Decimal("600"),
            excess_amount=Decimal("0"),
        )
        assert r.is_compliant

    def test_non_compliant_result(self):
        r = GSAComplianceResult(
            is_compliant=False,
            total_claimed=Decimal("700"),
            total_allowed=Decimal("600"),
            excess_amount=Decimal("100"),
            violations=(
                GSAViolation(
                    expense_line_id=uuid4(),
                    category=TravelExpenseCategory.LODGING,
                    claimed_amount=Decimal("350"),
                    gsa_limit=Decimal("258"),
                    excess=Decimal("92"),
                    location="DC",
                    expense_date=date(2026, 3, 10),
                ),
            ),
        )
        assert not r.is_compliant
        assert len(r.violations) == 1
