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
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_reimbursement_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record employee reimbursement through the real pipeline."""
        result = expense_service.record_reimbursement(
            reimbursement_id=uuid4(),
            amount=Decimal("650.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_card_statement_posts(
        self, expense_service, current_period, test_actor_id, deterministic_clock,
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
