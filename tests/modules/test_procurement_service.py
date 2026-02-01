"""
Tests for Procurement Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: PO creation, goods receipt, price variance
- Engine composition: VarianceCalculator, MatchingEngine, LinkGraphService
- Requisition lifecycle: create, convert to PO
- PO amendments and encumbrance adjustments
- 3-way match: receipt to PO with AP subledger
- Supplier evaluation (pure calculation)
- Quantity variance posting
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.procurement.service import ProcurementService
from tests.modules.conftest import TEST_EMPLOYEE_ID

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
            "create_requisition", "convert_requisition_to_po",
            "amend_purchase_order", "match_receipt_to_po",
            "evaluate_supplier", "record_quantity_variance",
        ]
        for method_name in expected:
            assert hasattr(ProcurementService, method_name)
            assert callable(getattr(ProcurementService, method_name))


# =============================================================================
# Integration Tests — Real Posting (existing)
# =============================================================================


class TestProcurementServiceIntegration:
    """Integration tests calling real procurement service methods through the posting pipeline."""

    def test_create_purchase_order_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
        test_vendor_party,
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
        test_vendor_party,
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
        test_vendor_party,
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
        test_vendor_party,
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
        test_vendor_party,
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


# =============================================================================
# New Integration Tests — Requisitions, Amendments, Matching
# =============================================================================


class TestRequisition:
    """Requisition creation posts commitment memo entry."""

    def test_create_requisition_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
        test_vendor_party, test_employee_party,
    ):
        result = procurement_service.create_requisition(
            requisition_id=uuid4(),
            requester_id=TEST_EMPLOYEE_ID,
            items=[
                {"description": "Laptops", "quantity": "10", "estimated_unit_cost": "1200.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0


class TestRequisitionConversion:
    """Converting a requisition to PO relieves commitment and creates encumbrance."""

    def test_convert_requisition_to_po_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
        test_vendor_party,
    ):
        result = procurement_service.convert_requisition_to_po(
            requisition_id=uuid4(),
            po_id=uuid4(),
            vendor_id="V-010",
            amount=Decimal("12000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0


class TestPOAmendment:
    """PO amendment adjusts encumbrance by the delta amount."""

    def test_amend_purchase_order_adjusts_encumbrance(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
        test_vendor_party,
    ):
        po_version, result = procurement_service.amend_purchase_order(
            po_id=uuid4(),
            delta_amount=Decimal("5000.00"),
            amendment_reason="Scope increase",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            version=2,
            changes=("unit_price", "quantity"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
        assert po_version.version == 2
        assert po_version.amendment_reason == "Scope increase"
        assert po_version.changes == ("unit_price", "quantity")


class TestReceiptMatch:
    """3-way match: receipt matched to PO posts encumbrance relief + AP subledger."""

    def test_match_receipt_to_po_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
        test_vendor_party,
    ):
        match_result, posting_result = procurement_service.match_receipt_to_po(
            receipt_id=uuid4(),
            po_id=uuid4(),
            po_line_id=uuid4(),
            matched_quantity=Decimal("100"),
            matched_amount=Decimal("2500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            vendor_id="V-001",
        )

        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert len(posting_result.journal_entry_ids) > 0
        assert match_result is not None


class TestSupplierEvaluation:
    """Supplier evaluation: pure calculation, no posting."""

    def test_evaluate_supplier_returns_score(
        self, procurement_service, deterministic_clock,
    ):
        score = procurement_service.evaluate_supplier(
            vendor_id=uuid4(),
            period="2026-Q1",
            delivery_score=Decimal("85.00"),
            quality_score=Decimal("92.00"),
            price_score=Decimal("78.00"),
            evaluation_date=deterministic_clock.now().date(),
        )

        assert score.period == "2026-Q1"
        assert score.delivery_score == Decimal("85.00")
        assert score.quality_score == Decimal("92.00")
        assert score.price_score == Decimal("78.00")
        # Overall = (85 + 92 + 78) / 3 = 85.00
        assert score.overall_score == Decimal("85.00")


class TestQuantityVariance:
    """Quantity variance between PO and receipt posts to GL."""

    def test_record_quantity_variance_posts(
        self, procurement_service, current_period, test_actor_id, deterministic_clock,
        test_vendor_party,
    ):
        result = procurement_service.record_quantity_variance(
            receipt_id=uuid4(),
            po_id=uuid4(),
            variance_quantity=Decimal("-5"),
            variance_amount=Decimal("125.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
