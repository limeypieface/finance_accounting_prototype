"""
Tests for Expense Helper Pure Functions.

Validates calculate_mileage, calculate_per_diem, validate_expense_against_policy.
All tests are pure — no database, no session.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.expense.helpers import (
    calculate_mileage,
    calculate_per_diem,
    validate_expense_against_policy,
)
from finance_modules.expense.models import (
    ExpenseCategory,
    ExpenseLine,
    ExpensePolicy,
    MileageRate,
    PaymentMethod,
    PerDiemRate,
    PolicyViolation,
)

# =============================================================================
# Mileage Tests
# =============================================================================


class TestCalculateMileage:

    def test_basic_mileage(self):
        result = calculate_mileage(Decimal("100"), Decimal("0.67"))
        assert result == Decimal("67.00")

    def test_zero_miles(self):
        result = calculate_mileage(Decimal("0"), Decimal("0.67"))
        assert result == Decimal("0.00")

    def test_fractional_miles(self):
        result = calculate_mileage(Decimal("50.5"), Decimal("0.67"))
        assert result == Decimal("33.835")

    def test_negative_miles_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            calculate_mileage(Decimal("-10"), Decimal("0.67"))

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            calculate_mileage(Decimal("100"), Decimal("-0.50"))


# =============================================================================
# Per Diem Tests
# =============================================================================


class TestCalculatePerDiem:

    @pytest.fixture
    def rates(self):
        return PerDiemRate(
            location="New York, NY",
            meals_rate=Decimal("79.00"),
            lodging_rate=Decimal("282.00"),
            incidentals_rate=Decimal("20.00"),
        )

    def test_full_per_diem(self, rates):
        result = calculate_per_diem(days=3, rates=rates)
        expected = (Decimal("79.00") + Decimal("282.00") + Decimal("20.00")) * 3
        assert result == expected

    def test_meals_only(self, rates):
        result = calculate_per_diem(
            days=2, rates=rates,
            include_meals=True, include_lodging=False, include_incidentals=False,
        )
        assert result == Decimal("79.00") * 2

    def test_lodging_only(self, rates):
        result = calculate_per_diem(
            days=1, rates=rates,
            include_meals=False, include_lodging=True, include_incidentals=False,
        )
        assert result == Decimal("282.00")

    def test_zero_days_raises(self, rates):
        with pytest.raises(ValueError, match="positive"):
            calculate_per_diem(days=0, rates=rates)

    def test_negative_days_raises(self, rates):
        with pytest.raises(ValueError, match="positive"):
            calculate_per_diem(days=-1, rates=rates)

    def test_single_day(self, rates):
        result = calculate_per_diem(days=1, rates=rates)
        assert result == Decimal("381.00")


# =============================================================================
# Policy Validation Tests
# =============================================================================


def _make_line(
    category: ExpenseCategory = ExpenseCategory.TRAVEL,
    amount: Decimal = Decimal("100.00"),
    receipt_attached: bool = False,
    description: str = "Test expense",
) -> ExpenseLine:
    return ExpenseLine(
        id=uuid4(),
        report_id=uuid4(),
        line_number=1,
        expense_date=date(2026, 1, 15),
        category=category,
        description=description,
        amount=amount,
        currency="USD",
        payment_method=PaymentMethod.CORPORATE_CARD,
        receipt_attached=receipt_attached,
    )


class TestValidateExpenseAgainstPolicy:

    def test_no_violations_when_compliant(self):
        line = _make_line(amount=Decimal("50.00"), receipt_attached=True)
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                per_transaction_limit=Decimal("500.00"),
                requires_receipt_above=Decimal("25.00"),
            ),
        }
        violations = validate_expense_against_policy([line], policies)
        assert violations == []

    def test_detects_over_limit(self):
        line = _make_line(amount=Decimal("600.00"))
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                per_transaction_limit=Decimal("500.00"),
            ),
        }
        violations = validate_expense_against_policy([line], policies)
        assert len(violations) == 1
        assert violations[0].violation_type == "OVER_LIMIT"
        assert violations[0].amount == Decimal("600.00")
        assert violations[0].limit == Decimal("500.00")

    def test_detects_missing_receipt(self):
        line = _make_line(amount=Decimal("100.00"), receipt_attached=False)
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                requires_receipt_above=Decimal("25.00"),
            ),
        }
        violations = validate_expense_against_policy([line], policies)
        assert len(violations) == 1
        assert violations[0].violation_type == "MISSING_RECEIPT"

    def test_no_receipt_violation_when_attached(self):
        line = _make_line(amount=Decimal("100.00"), receipt_attached=True)
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                requires_receipt_above=Decimal("25.00"),
            ),
        }
        violations = validate_expense_against_policy([line], policies)
        assert violations == []

    def test_detects_missing_justification(self):
        line = _make_line(description="")
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                requires_justification=True,
            ),
        }
        violations = validate_expense_against_policy([line], policies)
        assert len(violations) == 1
        assert violations[0].violation_type == "MISSING_JUSTIFICATION"

    def test_multiple_violations_on_same_line(self):
        line = _make_line(
            amount=Decimal("1000.00"),
            receipt_attached=False,
            description="",
        )
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                per_transaction_limit=Decimal("500.00"),
                requires_receipt_above=Decimal("25.00"),
                requires_justification=True,
            ),
        }
        violations = validate_expense_against_policy([line], policies)
        types = {v.violation_type for v in violations}
        assert types == {"OVER_LIMIT", "MISSING_RECEIPT", "MISSING_JUSTIFICATION"}

    def test_no_policy_for_category_skips(self):
        line = _make_line(category=ExpenseCategory.MEALS)
        policies = {
            "travel": ExpensePolicy(category="travel", per_transaction_limit=Decimal("10.00")),
        }
        violations = validate_expense_against_policy([line], policies)
        assert violations == []

    def test_multiple_lines_multiple_categories(self):
        travel_line = _make_line(
            category=ExpenseCategory.TRAVEL,
            amount=Decimal("800.00"),
        )
        meals_line = _make_line(
            category=ExpenseCategory.MEALS,
            amount=Decimal("200.00"),
        )
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                per_transaction_limit=Decimal("500.00"),
            ),
            "meals": ExpensePolicy(
                category="meals",
                per_transaction_limit=Decimal("100.00"),
            ),
        }
        violations = validate_expense_against_policy(
            [travel_line, meals_line], policies,
        )
        assert len(violations) == 2
        categories = {v.category for v in violations}
        assert categories == {"travel", "meals"}
