"""
Tests for Tax Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: tax obligation, tax payment, tax calculation
- Engine composition: TaxCalculator
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.tax.service import TaxService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tax_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide TaxService for integration testing."""
    return TaxService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestTaxServiceStructure:
    """Verify TaxService follows the module service pattern."""

    def test_importable(self):
        assert TaxService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(TaxService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_tax_obligation", "record_tax_payment",
            "record_vat_settlement", "calculate_tax",
        ]
        for method_name in expected:
            assert hasattr(TaxService, method_name)
            assert callable(getattr(TaxService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestTaxServiceIntegration:
    """Integration tests calling real tax service methods through the posting pipeline."""

    def test_record_tax_obligation_posts(
        self, tax_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record sales tax obligation through the real pipeline."""
        result = tax_service.record_tax_obligation(
            obligation_id=uuid4(),
            tax_type="sales_tax_collected",
            amount=Decimal("600.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction="CA",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_tax_payment_posts(
        self, tax_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record tax payment through the real pipeline."""
        result = tax_service.record_tax_payment(
            payment_id=uuid4(),
            tax_type="sales_tax",
            amount=Decimal("600.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction="CA",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_calculate_tax_pure(self, tax_service):
        """Calculate tax amounts via TaxCalculator engine (pure computation)."""
        from finance_engines.tax import TaxRate

        result = tax_service.calculate_tax(
            amount=Decimal("10000.00"),
            tax_codes=["CA_SALES"],
            rates={
                "CA_SALES": TaxRate(
                    tax_code="CA_SALES",
                    tax_name="California Sales Tax",
                    rate=Decimal("0.0725"),
                ),
            },
        )

        assert result.tax_total.amount == Decimal("725.00")
        assert result.gross_amount.amount == Decimal("10725.00")
        assert result.net_amount.amount == Decimal("10000.00")

    def test_record_use_tax_obligation_posts(
        self, tax_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record use tax obligation through the real pipeline."""
        result = tax_service.record_tax_obligation(
            obligation_id=uuid4(),
            tax_type="use_tax_accrued",
            amount=Decimal("150.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction="TX",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_vat_input_posts(
        self, tax_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record VAT input credit through the real pipeline."""
        result = tax_service.record_tax_obligation(
            obligation_id=uuid4(),
            tax_type="vat_input",
            amount=Decimal("200.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction="DE",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_vat_settlement_posts(
        self, tax_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record VAT settlement (output - input = net payment) through the real pipeline."""
        result = tax_service.record_vat_settlement(
            settlement_id=uuid4(),
            output_vat=Decimal("1000.00"),
            input_vat=Decimal("600.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction="DE",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_tax_refund_posts(
        self, tax_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record tax refund received through the real pipeline."""
        result = tax_service.record_tax_obligation(
            obligation_id=uuid4(),
            tax_type="refund_received",
            amount=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction="CA",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
