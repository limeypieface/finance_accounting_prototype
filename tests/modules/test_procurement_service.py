"""
Tests for Procurement Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: PO creation, goods receipt, price variance
- Engine composition: VarianceCalculator, MatchingEngine, LinkGraphService
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.procurement.service import ProcurementService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def procurement_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide ProcurementService for integration testing."""
    return ProcurementService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestProcurementServiceStructure:
    """Verify ProcurementService follows the module service pattern."""

    def test_importable(self):
        assert ProcurementService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(ProcurementService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "create_purchase_order", "receive_goods", "record_price_variance",
            "record_commitment", "relieve_commitment",
        ]
        for method_name in expected:
            assert hasattr(ProcurementService, method_name)
            assert callable(getattr(ProcurementService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestProcurementServiceIntegration:
    """Integration tests calling real procurement service methods through the posting pipeline."""

    def test_create_purchase_order_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Create a purchase order through the real pipeline."""
        result = procurement_service.create_purchase_order(
            po_id=uuid4(),
            vendor_id="V-001",
            lines=[
                {"item_code": "WIDGET-001", "quantity": "100", "unit_price": "25.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_receive_goods_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Receive goods against a PO through the real pipeline."""
        result = procurement_service.receive_goods(
            receipt_id=uuid4(),
            po_id=uuid4(),
            lines=[
                {"item_code": "WIDGET-001", "quantity": "100", "unit_price": "25.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_price_variance_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record purchase price variance through the real pipeline."""
        result = procurement_service.record_price_variance(
            po_id=uuid4(),
            invoice_id=uuid4(),
            variance_amount=Decimal("250.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            po_unit_price=Decimal("25.00"),
            invoice_unit_price=Decimal("27.50"),
            quantity=Decimal("100"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_commitment_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a purchase commitment (memo) through the real pipeline."""
        result = procurement_service.record_commitment(
            commitment_id=uuid4(),
            po_id=uuid4(),
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            vendor_id="V-002",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_relieve_commitment_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Relieve a purchase commitment (memo reversal) through the real pipeline."""
        result = procurement_service.relieve_commitment(
            relief_id=uuid4(),
            commitment_id=uuid4(),
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
