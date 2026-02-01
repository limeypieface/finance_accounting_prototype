"""
Tests for AR (Accounts Receivable) Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: invoice, payment, payment application, aging
- Engine composition: ReconciliationManager, AllocationEngine, AgingCalculator
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.ar.service import ARService
from tests.modules.conftest import TEST_CUSTOMER_ID

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
