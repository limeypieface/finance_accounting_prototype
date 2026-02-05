"""
Tests for AR (Accounts Receivable) Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: invoice, payment, payment application, aging
- Engine composition: ReconciliationManager, AllocationEngine, AgingCalculator
- Dunning: generate_dunning_letters; auto_apply_payment (FIFO); credit check/update
- Auto write-off: auto_write_off_small_balances; record_finance_charge
- AR models: DunningHistory, CreditDecision, AutoApplyRule (frozen dataclasses)
"""

from __future__ import annotations

import inspect
from datetime import date
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
from tests.modules.conftest import TEST_CUSTOMER_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def ar_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide ARService for integration testing. workflow_executor required; party_service + test_actor_party for G14."""
    return ARService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestARServiceStructure:
    """Verify ARService follows the module service pattern."""

    def test_importable(self):
        assert ARService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(ARService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "workflow_executor" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_invoice", "record_payment", "apply_payment", "calculate_aging",
            "record_receipt", "record_credit_memo", "record_write_off",
            "record_bad_debt_provision", "record_deferred_revenue",
            "recognize_deferred_revenue", "record_refund",
        ]
        for method_name in expected:
            assert hasattr(ARService, method_name)
            assert callable(getattr(ARService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestARServiceIntegration:
    """Integration tests calling real AR service methods through the posting pipeline."""

    def test_record_invoice_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record a customer invoice through the real pipeline."""
        result = ar_service.record_invoice(
            invoice_id=uuid4(),
            customer_id=TEST_CUSTOMER_ID,
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_payment_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record a customer payment through the real pipeline."""
        result = ar_service.record_payment(
            payment_id=uuid4(),
            customer_id=TEST_CUSTOMER_ID,
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_apply_payment_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Apply payment to invoices through the real pipeline."""
        result = ar_service.apply_payment(
            payment_id=uuid4(),
            invoice_ids=[uuid4()],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            payment_amount=Decimal("5000.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_calculate_aging_returns_report(
        self, ar_service, deterministic_clock,
    ):
        """AR aging returns an AgingReport (pure computation)."""
        report = ar_service.calculate_aging(
            as_of_date=deterministic_clock.now().date(),
        )

        assert report is not None
        assert report.item_count == 0

    def test_record_receipt_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record a customer receipt (unapplied cash) through the real pipeline."""
        result = ar_service.record_receipt(
            receipt_id=uuid4(),
            customer_id=TEST_CUSTOMER_ID,
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_credit_memo_return_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record a customer return credit memo through the real pipeline."""
        result = ar_service.record_credit_memo(
            memo_id=uuid4(),
            customer_id=TEST_CUSTOMER_ID,
            amount=Decimal("1500.00"),
            reason_code="RETURN",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_write_off_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Write off an uncollectible receivable through the real pipeline."""
        result = ar_service.record_write_off(
            write_off_id=uuid4(),
            invoice_id=uuid4(),
            customer_id=uuid4(),
            amount=Decimal("800.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_bad_debt_provision_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record a bad debt provision through the real pipeline."""
        result = ar_service.record_bad_debt_provision(
            provision_id=uuid4(),
            amount=Decimal("2000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period_id="2024-01",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_deferred_revenue_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record deferred revenue through the real pipeline."""
        result = ar_service.record_deferred_revenue(
            deferred_id=uuid4(),
            customer_id=uuid4(),
            amount=Decimal("12000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_recognize_deferred_revenue_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Recognize deferred revenue through the real pipeline."""
        result = ar_service.recognize_deferred_revenue(
            recognition_id=uuid4(),
            original_deferred_id=uuid4(),
            amount=Decimal("1000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period_id="2024-01",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_refund_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
        """Record a customer refund through the real pipeline."""
        result = ar_service.record_refund(
            refund_id=uuid4(),
            customer_id=uuid4(),
            amount=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0


# =============================================================================
# AR Models (DunningHistory, CreditDecision, AutoApplyRule)
# =============================================================================


class TestARModels:
    """Verify AR models are frozen dataclasses with correct defaults."""

    def test_dunning_history_creation(self):
        record = DunningHistory(
            id=uuid4(),
            customer_id=uuid4(),
            level=DunningLevel.FIRST_NOTICE,
            sent_date=date(2024, 1, 15),
            as_of_date=date(2024, 1, 15),
            total_overdue=Decimal("10000.00"),
            invoice_count=3,
        )
        assert record.level == DunningLevel.FIRST_NOTICE
        assert record.total_overdue == Decimal("10000.00")
        assert record.invoice_count == 3
        assert record.currency == "USD"
        assert record.notes is None

    def test_credit_decision_creation(self):
        decision = CreditDecision(
            id=uuid4(),
            customer_id=uuid4(),
            decision_date=date(2024, 1, 10),
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
        decision = CreditDecision(
            id=uuid4(),
            customer_id=uuid4(),
            decision_date=date(2024, 1, 10),
            previous_limit=None,
            new_limit=None,
        )
        assert decision.approved is True
        assert decision.order_amount is None
        assert decision.reason is None

    def test_auto_apply_rule_creation(self):
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
# Dunning
# =============================================================================


class TestGenerateDunningLetters:
    def test_dunning_letters_generated(
        self, ar_service, deterministic_clock, test_actor_id, test_customer_party,
    ):
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
        results = ar_service.generate_dunning_letters(
            as_of_date=deterministic_clock.now().date(),
            overdue_customers=[],
            actor_id=test_actor_id,
        )
        assert results == []


class TestAutoApplyPayment:
    def test_auto_apply_payment_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
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


class TestCreditManagement:
    def test_check_credit_limit_approved(
        self, ar_service, test_actor_id, test_customer_party,
    ):
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


class TestAutoWriteOffSmallBalances:
    def test_auto_write_off_posts_qualifying_invoices(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
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
        invoices = [{"invoice_id": uuid4(), "customer_id": uuid4(), "balance": "500.00"}]
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
        results = ar_service.auto_write_off_small_balances(
            threshold=Decimal("10.00"),
            small_balance_invoices=[],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert results == []


class TestRecordFinanceCharge:
    def test_record_finance_charge_posts(
        self, ar_service, current_period, test_actor_id, test_customer_party, deterministic_clock,
    ):
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
