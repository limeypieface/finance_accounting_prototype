"""
Tests for AR Module Deepening.

Validates:
- generate_dunning_letters: collection letters by aging (no posting)
- auto_apply_payment: FIFO payment application via existing apply_payment
- check_credit_limit / update_credit_limit: credit management (no posting)
- auto_write_off_small_balances: batch write-off via existing record_write_off
- record_finance_charge: late payment interest posting
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.ar.models import (
    AutoApplyRule,
    CreditDecision,
    DunningHistory,
    DunningLevel,
)
from finance_modules.ar.service import ARService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def ar_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide ARService for integration testing."""
    return ARService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestARDeepeningModels:
    """Verify new AR models are frozen dataclasses with correct defaults."""

    def test_dunning_history_creation(self):
        """DunningHistory is a frozen dataclass with correct fields."""
        record = DunningHistory(
            id=uuid4(),
            customer_id=uuid4(),
            level=DunningLevel.FIRST_NOTICE,
            sent_date=__import__("datetime").date(2024, 1, 15),
            as_of_date=__import__("datetime").date(2024, 1, 15),
            total_overdue=Decimal("10000.00"),
            invoice_count=3,
        )
        assert record.level == DunningLevel.FIRST_NOTICE
        assert record.total_overdue == Decimal("10000.00")
        assert record.invoice_count == 3
        assert record.currency == "USD"
        assert record.notes is None

    def test_credit_decision_creation(self):
        """CreditDecision is a frozen dataclass with correct fields."""
        decision = CreditDecision(
            id=uuid4(),
            customer_id=uuid4(),
            decision_date=__import__("datetime").date(2024, 1, 10),
            previous_limit=Decimal("50000.00"),
            new_limit=Decimal("75000.00"),
            approved=True,
            reason="Strong payment history",
            decided_by=uuid4(),
        )
        assert decision.approved is True
        assert decision.previous_limit == Decimal("50000.00")
        assert decision.new_limit == Decimal("75000.00")

    def test_credit_decision_defaults(self):
        """CreditDecision has sensible defaults."""
        decision = CreditDecision(
            id=uuid4(),
            customer_id=uuid4(),
            decision_date=__import__("datetime").date(2024, 1, 10),
            previous_limit=None,
            new_limit=None,
        )
        assert decision.approved is True
        assert decision.order_amount is None
        assert decision.reason is None

    def test_auto_apply_rule_creation(self):
        """AutoApplyRule is a frozen dataclass with correct fields."""
        rule = AutoApplyRule(
            id=uuid4(),
            name="Match by invoice number",
            priority=1,
            match_field="invoice_number",
            tolerance=Decimal("0.01"),
        )
        assert rule.is_active is True
        assert rule.tolerance == Decimal("0.01")
        assert rule.match_field == "invoice_number"


# =============================================================================
# Integration Tests — Dunning
# =============================================================================


class TestGenerateDunningLetters:
    """Tests for generate_dunning_letters service method."""

    def test_dunning_letters_generated(
        self, ar_service, deterministic_clock, test_actor_id, test_customer_party,
    ):
        """Dunning letters are generated for overdue customers."""
        overdue_customers = [
            {"customer_id": uuid4(), "total_overdue": "5000.00", "days_overdue": 15, "invoice_count": 2},
            {"customer_id": uuid4(), "total_overdue": "12000.00", "days_overdue": 45, "invoice_count": 4},
            {"customer_id": uuid4(), "total_overdue": "3000.00", "days_overdue": 75, "invoice_count": 1},
        ]

        results = ar_service.generate_dunning_letters(
            as_of_date=deterministic_clock.now().date(),
            overdue_customers=overdue_customers,
            actor_id=test_actor_id,
        )

        assert len(results) == 3
        assert all(isinstance(r, DunningHistory) for r in results)
        assert results[0].level == DunningLevel.REMINDER
        assert results[1].level == DunningLevel.FIRST_NOTICE
        assert results[2].level == DunningLevel.SECOND_NOTICE

    def test_dunning_skips_current_customers(
        self, ar_service, deterministic_clock, test_actor_id, test_customer_party,
    ):
        """Customers with 0 days overdue are skipped."""
        overdue_customers = [
            {"customer_id": uuid4(), "total_overdue": "100.00", "days_overdue": 0, "invoice_count": 1},
        ]

        results = ar_service.generate_dunning_letters(
            as_of_date=deterministic_clock.now().date(),
            overdue_customers=overdue_customers,
            actor_id=test_actor_id,
        )

        assert results == []

    def test_dunning_empty_list(
        self, ar_service, deterministic_clock, test_actor_id, test_customer_party,
    ):
        """Empty overdue list returns empty results."""
        results = ar_service.generate_dunning_letters(
            as_of_date=deterministic_clock.now().date(),
            overdue_customers=[],
            actor_id=test_actor_id,
        )

        assert results == []


# =============================================================================
# Integration Tests — Auto Cash Application
# =============================================================================


class TestAutoApplyPayment:
    """Tests for auto_apply_payment service method."""

    def test_auto_apply_payment_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Auto-apply posts via existing apply_payment (FIFO)."""
        open_invoices = [
            {"invoice_id": uuid4(), "due_date": "2023-11-15", "amount": "3000.00"},
            {"invoice_id": uuid4(), "due_date": "2023-12-01", "amount": "2000.00"},
        ]

        result = ar_service.auto_apply_payment(
            payment_id=uuid4(),
            customer_id=uuid4(),
            payment_amount=Decimal("5000.00"),
            open_invoices=open_invoices,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success


# =============================================================================
# Integration Tests — Credit Management
# =============================================================================


class TestCreditManagement:
    """Tests for check_credit_limit and update_credit_limit."""

    def test_check_credit_limit_approved(
        self, ar_service, test_actor_id, test_customer_party,
    ):
        """Order within credit limit is approved."""
        decision = ar_service.check_credit_limit(
            customer_id=uuid4(),
            order_amount=Decimal("5000.00"),
            current_balance=Decimal("10000.00"),
            credit_limit=Decimal("50000.00"),
            actor_id=test_actor_id,
        )

        assert isinstance(decision, CreditDecision)
        assert decision.approved is True
        assert "Within limit" in decision.reason

    def test_check_credit_limit_rejected(
        self, ar_service, test_actor_id, test_customer_party,
    ):
        """Order exceeding credit limit is rejected."""
        decision = ar_service.check_credit_limit(
            customer_id=uuid4(),
            order_amount=Decimal("45000.00"),
            current_balance=Decimal("10000.00"),
            credit_limit=Decimal("50000.00"),
            actor_id=test_actor_id,
        )

        assert isinstance(decision, CreditDecision)
        assert decision.approved is False
        assert "exceeds limit" in decision.reason

    def test_update_credit_limit_returns_decision(
        self, ar_service, test_actor_id, test_customer_party,
    ):
        """update_credit_limit returns a CreditDecision."""
        decision = ar_service.update_credit_limit(
            customer_id=uuid4(),
            previous_limit=Decimal("50000.00"),
            new_limit=Decimal("75000.00"),
            actor_id=test_actor_id,
            reason="Strong payment history",
        )

        assert isinstance(decision, CreditDecision)
        assert decision.approved is True
        assert decision.previous_limit == Decimal("50000.00")
        assert decision.new_limit == Decimal("75000.00")
        assert decision.reason == "Strong payment history"


# =============================================================================
# Integration Tests — Small Balance Write-Off
# =============================================================================


class TestAutoWriteOffSmallBalances:
    """Tests for auto_write_off_small_balances service method."""

    def test_auto_write_off_posts_qualifying_invoices(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Invoices below threshold are written off."""
        small_invoices = [
            {"invoice_id": uuid4(), "customer_id": uuid4(), "balance": "5.00"},
            {"invoice_id": uuid4(), "customer_id": uuid4(), "balance": "3.50"},
        ]

        results = ar_service.auto_write_off_small_balances(
            threshold=Decimal("10.00"),
            small_balance_invoices=small_invoices,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert len(results) == 2
        assert all(r.status == ModulePostingStatus.POSTED for r in results)

    def test_auto_write_off_skips_above_threshold(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Invoices above threshold are not written off."""
        invoices = [
            {"invoice_id": uuid4(), "customer_id": uuid4(), "balance": "500.00"},
        ]

        results = ar_service.auto_write_off_small_balances(
            threshold=Decimal("10.00"),
            small_balance_invoices=invoices,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert results == []

    def test_auto_write_off_empty_list(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Empty invoice list returns empty results."""
        results = ar_service.auto_write_off_small_balances(
            threshold=Decimal("10.00"),
            small_balance_invoices=[],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert results == []


# =============================================================================
# Integration Tests — Finance Charges
# =============================================================================


class TestRecordFinanceCharge:
    """Tests for record_finance_charge service method."""

    def test_record_finance_charge_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Finance charge posts via ar.finance_charge profile."""
        result = ar_service.record_finance_charge(
            charge_id=uuid4(),
            customer_id=uuid4(),
            amount=Decimal("150.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period="2024-01",
            annual_rate=Decimal("0.18"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
