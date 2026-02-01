"""
Tests for Expense Management Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: expense, report, allocation, reimbursement, card statement
- Engine composition: TaxCalculator, AllocationEngine
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.expense.service import ExpenseService
from tests.modules.conftest import TEST_EMPLOYEE_ID, TEST_EXPENSE_REPORT_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def expense_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide ExpenseService for integration testing."""
    return ExpenseService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestExpenseServiceStructure:
    """Verify ExpenseService follows the module service pattern."""

    def test_importable(self):
        assert ExpenseService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(ExpenseService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_expense", "record_expense_report", "allocate_expense",
            "record_reimbursement", "record_card_statement",
            "record_card_payment", "issue_advance", "clear_advance",
            "validate_against_policy", "import_card_transactions",
            "calculate_mileage", "calculate_per_diem",
            "record_policy_violation", "record_receipt_match",
        ]
        for method_name in expected:
            assert hasattr(ExpenseService, method_name)
            assert callable(getattr(ExpenseService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestExpenseServiceIntegration:
    """Integration tests calling real expense service methods through the posting pipeline."""

    def test_record_expense_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Record a single expense through the real pipeline."""
        result = expense_service.record_expense(
            expense_id=uuid4(),
            category="TRAVEL",
            amount=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_expense_report_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Record a multi-line expense report through the real pipeline."""
        result = expense_service.record_expense_report(
            report_id=uuid4(),
            lines=[
                {"category": "TRAVEL", "amount": "300.00"},
                {"category": "MEALS", "amount": "150.00"},
                {"category": "LODGING", "amount": "200.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            employee_id=TEST_EMPLOYEE_ID,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_reimbursement_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_expense_report,
    ):
        """Record employee reimbursement through the real pipeline."""
        result = expense_service.record_reimbursement(
            reimbursement_id=uuid4(),
            amount=Decimal("650.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            employee_id=TEST_EMPLOYEE_ID,
            report_id=TEST_EXPENSE_REPORT_ID,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_card_statement_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Record corporate card statement through the real pipeline."""
        result = expense_service.record_card_statement(
            statement_id=uuid4(),
            transactions=[
                {"category": "OFFICE", "amount": "99.99", "merchant": "Staples"},
                {"category": "TRAVEL", "amount": "450.00", "merchant": "Delta"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_allocate_expense_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Allocate expense across cost centers through the real pipeline."""
        from finance_engines.allocation import AllocationTarget
        from finance_kernel.domain.values import Money

        allocation_result, posting_result = expense_service.allocate_expense(
            expense_id=uuid4(),
            cost_centers=[
                AllocationTarget(
                    target_id="CC-100",
                    target_type="cost_center",
                    eligible_amount=Money.of(Decimal("600.00"), "USD"),
                ),
                AllocationTarget(
                    target_id="CC-200",
                    target_type="cost_center",
                    eligible_amount=Money.of(Decimal("400.00"), "USD"),
                ),
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("1000.00"),
        )

        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert allocation_result is not None
        assert allocation_result.allocation_count == 2

    def test_record_card_payment_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Record corporate card payment through the real pipeline."""
        result = expense_service.record_card_payment(
            payment_id=uuid4(),
            amount=Decimal("549.99"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_issue_advance_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Issue a travel advance through the real pipeline."""
        result = expense_service.issue_advance(
            advance_id=uuid4(),
            amount=Decimal("1000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_clear_advance_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Clear a travel advance against an expense report through the real pipeline."""
        result = expense_service.clear_advance(
            clearing_id=uuid4(),
            advance_id=uuid4(),
            amount=Decimal("850.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_receipt_match_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party,
    ):
        """Match an expense receipt to a card transaction through the real pipeline."""
        match_result, posting_result = expense_service.record_receipt_match(
            expense_line_id=uuid4(),
            card_transaction_id=uuid4(),
            amount=Decimal("125.50"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert len(posting_result.journal_entry_ids) > 0
        assert match_result is not None


# =============================================================================
# Non-Posting Method Tests
# =============================================================================


class TestExpenseServiceNonPosting:
    """Tests for non-posting expense service methods."""

    def test_validate_against_policy_detects_over_limit(self, expense_service):
        """Policy validation detects over-limit expenses."""
        from datetime import date

        from finance_modules.expense.models import (
            ExpenseCategory,
            ExpenseLine,
            ExpensePolicy,
            PaymentMethod,
        )

        line = ExpenseLine(
            id=uuid4(), report_id=uuid4(), line_number=1,
            expense_date=date(2026, 1, 15),
            category=ExpenseCategory.TRAVEL,
            description="Flight to NYC",
            amount=Decimal("2000.00"), currency="USD",
            payment_method=PaymentMethod.CORPORATE_CARD,
        )
        policies = {
            "travel": ExpensePolicy(
                category="travel",
                per_transaction_limit=Decimal("1000.00"),
            ),
        }

        violations = expense_service.validate_against_policy([line], policies)
        assert len(violations) == 1
        assert violations[0].violation_type == "OVER_LIMIT"

    def test_validate_against_policy_detects_missing_receipt(self, expense_service):
        """Policy validation detects missing receipts."""
        from datetime import date

        from finance_modules.expense.models import (
            ExpenseCategory,
            ExpenseLine,
            ExpensePolicy,
            PaymentMethod,
        )

        line = ExpenseLine(
            id=uuid4(), report_id=uuid4(), line_number=1,
            expense_date=date(2026, 1, 15),
            category=ExpenseCategory.MEALS,
            description="Team lunch",
            amount=Decimal("150.00"), currency="USD",
            payment_method=PaymentMethod.PERSONAL_CARD,
            receipt_attached=False,
        )
        policies = {
            "meals": ExpensePolicy(
                category="meals",
                requires_receipt_above=Decimal("50.00"),
            ),
        }

        violations = expense_service.validate_against_policy([line], policies)
        assert len(violations) == 1
        assert violations[0].violation_type == "MISSING_RECEIPT"

    def test_calculate_mileage_returns_correct_amount(self, expense_service):
        """Mileage calculation returns expected reimbursement."""
        from datetime import date

        from finance_modules.expense.models import MileageRate

        rate = MileageRate(
            effective_date=date(2026, 1, 1),
            rate_per_mile=Decimal("0.67"),
        )
        result = expense_service.calculate_mileage(Decimal("150"), rate)
        assert result == Decimal("100.50")

    def test_calculate_per_diem_returns_correct_amount(self, expense_service):
        """Per diem calculation returns expected allowance."""
        from finance_modules.expense.models import PerDiemRate

        rates = PerDiemRate(
            location="Washington, DC",
            meals_rate=Decimal("79.00"),
            lodging_rate=Decimal("258.00"),
            incidentals_rate=Decimal("20.00"),
        )
        result = expense_service.calculate_per_diem(days=3, rates=rates)
        assert result == Decimal("1071.00")  # (79+258+20) * 3

    def test_import_card_transactions(self, expense_service):
        """Card transaction import parses and validates."""
        card_id = uuid4()
        raw = [
            {
                "transaction_date": "2026-01-10",
                "posting_date": "2026-01-11",
                "merchant_name": "Delta Airlines",
                "amount": "450.00",
                "currency": "USD",
            },
            {
                "transaction_date": "2026-01-12",
                "posting_date": "2026-01-13",
                "merchant_name": "Hilton Hotels",
                "amount": "282.00",
            },
        ]

        results = expense_service.import_card_transactions(raw, card_id)
        assert len(results) == 2
        assert results[0].merchant_name == "Delta Airlines"
        assert results[0].amount == Decimal("450.00")
        assert results[0].card_id == card_id
        assert results[1].currency == "USD"

    def test_record_policy_violation(self, expense_service):
        """Recording policy violations returns the violation list."""
        from finance_modules.expense.models import PolicyViolation

        violations = [
            PolicyViolation(
                line_id=uuid4(),
                violation_type="OVER_LIMIT",
                category="travel",
                amount=Decimal("2000.00"),
                limit=Decimal("1000.00"),
                message="Over limit",
            ),
        ]
        result = expense_service.record_policy_violation(
            report_id=uuid4(),
            violations=violations,
            actor_id=uuid4(),
        )
        assert len(result) == 1
        assert result[0].violation_type == "OVER_LIMIT"
