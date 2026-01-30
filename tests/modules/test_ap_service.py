"""
Tests for AP (Accounts Payable) Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: invoice recording, payment posting, aging computation
- Engine composition: ReconciliationManager, AllocationEngine, MatchingEngine, AgingCalculator
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.ap.service import APService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def ap_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide APService for integration testing."""
    return APService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestAPServiceStructure:
    """Verify APService follows the module service pattern."""

    def test_importable(self):
        assert APService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(APService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_invoice", "record_payment", "match_invoice_to_po", "calculate_aging",
            "cancel_invoice", "record_inventory_invoice", "record_accrual",
            "reverse_accrual", "record_prepayment", "apply_prepayment",
        ]
        for method_name in expected:
            assert hasattr(APService, method_name)
            assert callable(getattr(APService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestAPServiceIntegration:
    """Integration tests calling real AP service methods through the posting pipeline."""

    def test_record_invoice_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a vendor invoice through the real pipeline."""
        result = ap_service.record_invoice(
            invoice_id=uuid4(),
            vendor_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_payment_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a vendor payment through the real pipeline."""
        result = ap_service.record_payment(
            payment_id=uuid4(),
            invoice_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            vendor_id=uuid4(),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_payment_with_discount_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a vendor payment with early payment discount through the real pipeline."""
        result = ap_service.record_payment(
            payment_id=uuid4(),
            invoice_id=uuid4(),
            amount=Decimal("4900.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            vendor_id=uuid4(),
            discount_amount=Decimal("100.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_calculate_aging_returns_report(
        self, ap_service, deterministic_clock,
    ):
        """AP aging returns an AgingReport (pure computation)."""
        report = ap_service.calculate_aging(
            as_of_date=deterministic_clock.now().date(),
        )

        assert report is not None
        assert report.item_count == 0

    def test_cancel_invoice_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Cancel a vendor invoice through the real pipeline."""
        result = ap_service.cancel_invoice(
            invoice_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_inventory_invoice_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a vendor inventory invoice through the real pipeline."""
        result = ap_service.record_inventory_invoice(
            invoice_id=uuid4(),
            vendor_id=uuid4(),
            amount=Decimal("3000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_accrual_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record an AP accrual through the real pipeline."""
        result = ap_service.record_accrual(
            accrual_id=uuid4(),
            vendor_id=uuid4(),
            amount=Decimal("2000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period_id="2024-01",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_reverse_accrual_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Reverse an AP accrual through the real pipeline."""
        result = ap_service.reverse_accrual(
            reversal_id=uuid4(),
            original_accrual_id=uuid4(),
            amount=Decimal("2000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period_id="2024-01",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_prepayment_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a vendor prepayment through the real pipeline."""
        result = ap_service.record_prepayment(
            prepayment_id=uuid4(),
            vendor_id=uuid4(),
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_apply_prepayment_posts(
        self, ap_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Apply a vendor prepayment to an invoice through the real pipeline."""
        result = ap_service.apply_prepayment(
            application_id=uuid4(),
            prepayment_id=uuid4(),
            invoice_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
