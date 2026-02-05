"""
Tests for AP (Accounts Payable) Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: invoice recording, payment posting, aging computation
- Engine composition: ReconciliationManager, AllocationEngine, MatchingEngine, AgingCalculator
- Payment run: create_payment_run, execute_payment_run
- Auto-match: auto_match_invoices (batch 2-way match via MatchingEngine)
- Vendor hold: hold_vendor / release_vendor_hold lifecycle
- AP models: PaymentRun, PaymentRunLine, VendorHold (frozen dataclasses)
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.matching import MatchStatus, MatchTolerance, ToleranceType
from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.ap.models import (
    HoldStatus,
    PaymentRun,
    PaymentRunLine,
    PaymentRunStatus,
    VendorHold,
)
from finance_modules.ap.service import APService
from tests.modules.conftest import TEST_VENDOR_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def ap_service(
    session,
    module_role_resolver,
    deterministic_clock,
    register_modules,
    workflow_executor,
    party_service,
    test_actor_party,
):
    """Provide APService for integration testing. workflow_executor required; party_service + test_actor_party for G14 actor validation."""
    return APService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
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
        assert "workflow_executor" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_invoice", "record_payment", "match_invoice_to_po", "calculate_aging",
            "cancel_invoice", "record_inventory_invoice", "record_accrual",
            "reverse_accrual", "record_prepayment", "apply_prepayment",
            "approve_invoice", "submit_payment", "approve_payment",
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
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
    ):
        """Record a vendor invoice through the real pipeline."""
        result = ap_service.record_invoice(
            invoice_id=uuid4(),
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_invoice_rejected_when_actor_invalid(
        self, ap_service, current_period, test_vendor_party, deterministic_clock,
    ):
        """Post with an actor_id that has no Party record; expect INVALID_ACTOR (G14). Real architecture, no mocks."""
        invalid_actor_id = uuid4()  # No Party exists for this ID
        result = ap_service.record_invoice(
            invoice_id=uuid4(),
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=invalid_actor_id,
        )

        assert not result.is_success
        assert result.status == ModulePostingStatus.INVALID_ACTOR
        assert result.message is not None and "not a valid party" in result.message
        assert result.event_id is not None

    def test_record_payment_posts(
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
    ):
        """Record a vendor payment through the real pipeline."""
        result = ap_service.record_payment(
            payment_id=uuid4(),
            invoice_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            vendor_id=TEST_VENDOR_ID,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_payment_with_discount_posts(
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
    ):
        """Record a vendor payment with early payment discount through the real pipeline."""
        result = ap_service.record_payment(
            payment_id=uuid4(),
            invoice_id=uuid4(),
            amount=Decimal("4900.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            vendor_id=TEST_VENDOR_ID,
            discount_amount=Decimal("100.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_match_over_tolerance_guard_rejected(
        self,
        ap_service,
        current_period,
        test_actor_id,
        test_vendor_party,
        deterministic_clock,
    ):
        """Match with variance over tolerance is blocked by guard (GUARD_REJECTED)."""
        result = ap_service.match_invoice_to_po(
            invoice_id=uuid4(),
            po_id=uuid4(),
            receipt_ids=[uuid4()],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            invoice_amount=Decimal("1000.00"),
            po_amount=Decimal("800.00"),  # 20% variance
            currency="USD",
            tolerance=MatchTolerance(
                amount_tolerance=Decimal("5"),
                amount_tolerance_type=ToleranceType.PERCENT,
            ),
        )
        assert result.status == ModulePostingStatus.GUARD_REJECTED
        assert not result.is_success
        assert "Guard not satisfied" in (result.message or "")

    def test_submit_payment_insufficient_funds_guard_rejected(
        self, ap_service, test_actor_id,
    ):
        """Submit payment when bank_balance < amount is blocked (GUARD_REJECTED)."""
        result = ap_service.submit_payment(
            payment_id=uuid4(),
            amount=Decimal("1000.00"),
            bank_balance=Decimal("500.00"),  # insufficient
            actor_id=test_actor_id,
            currency="USD",
        )
        assert result.status == ModulePostingStatus.GUARD_REJECTED
        assert not result.is_success
        assert "Guard not satisfied" in (result.message or "")

    def test_submit_payment_sufficient_funds_succeeds(
        self, ap_service, test_actor_id,
    ):
        """Submit payment when bank_balance >= amount passes (transition applied). Service returns TRANSITION_APPLIED, not POSTED — only kernel may assert POSTED."""
        result = ap_service.submit_payment(
            payment_id=uuid4(),
            amount=Decimal("500.00"),
            bank_balance=Decimal("1000.00"),
            actor_id=test_actor_id,
            currency="USD",
        )
        assert result.status == ModulePostingStatus.TRANSITION_APPLIED
        # No ledger post yet; is_success is True only for kernel POSTED/ALREADY_POSTED
        assert not result.is_success

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
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
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
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
    ):
        """Record a vendor inventory invoice through the real pipeline."""
        result = ap_service.record_inventory_invoice(
            invoice_id=uuid4(),
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("3000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_accrual_posts(
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
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
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
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
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
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
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
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


# =============================================================================
# AP Models (PaymentRun, PaymentRunLine, VendorHold)
# =============================================================================


class TestAPModels:
    """Verify AP models are frozen dataclasses with correct defaults."""

    def test_payment_run_creation(self):
        """PaymentRun is a frozen dataclass with correct fields."""
        run = PaymentRun(
            id=uuid4(),
            payment_date=date(2024, 1, 15),
            currency="USD",
            status=PaymentRunStatus.DRAFT,
            total_amount=Decimal("25000.00"),
            line_count=5,
            created_by=uuid4(),
        )
        assert run.status == PaymentRunStatus.DRAFT
        assert run.total_amount == Decimal("25000.00")
        assert run.line_count == 5
        assert run.executed_by is None

    def test_payment_run_defaults(self):
        """PaymentRun has sensible defaults."""
        run = PaymentRun(
            id=uuid4(),
            payment_date=date(2024, 1, 15),
            currency="USD",
        )
        assert run.status == PaymentRunStatus.DRAFT
        assert run.total_amount == Decimal("0")
        assert run.line_count == 0
        assert run.created_by is None

    def test_payment_run_line_creation(self):
        """PaymentRunLine is a frozen dataclass with correct fields."""
        line = PaymentRunLine(
            id=uuid4(),
            run_id=uuid4(),
            invoice_id=uuid4(),
            vendor_id=uuid4(),
            amount=Decimal("5000.00"),
            discount_amount=Decimal("100.00"),
        )
        assert line.amount == Decimal("5000.00")
        assert line.discount_amount == Decimal("100.00")
        assert line.payment_id is None

    def test_vendor_hold_creation(self):
        """VendorHold is a frozen dataclass with correct fields."""
        hold = VendorHold(
            id=uuid4(),
            vendor_id=uuid4(),
            reason="Dispute on invoice #1234",
            hold_date=date(2024, 1, 10),
            held_by=uuid4(),
        )
        assert hold.status == HoldStatus.ACTIVE
        assert hold.released_date is None
        assert hold.released_by is None
        assert hold.reason == "Dispute on invoice #1234"


# =============================================================================
# Payment Run
# =============================================================================


class TestCreatePaymentRun:
    """Tests for create_payment_run service method."""

    def test_create_payment_run_returns_run(
        self, ap_service, deterministic_clock, test_actor_id, test_vendor_party,
    ):
        """create_payment_run returns a PaymentRun with correct totals."""
        invoices = [
            {"invoice_id": str(uuid4()), "vendor_id": str(uuid4()), "amount": "5000.00"},
            {"invoice_id": str(uuid4()), "vendor_id": str(uuid4()), "amount": "3000.00"},
            {"invoice_id": str(uuid4()), "vendor_id": str(uuid4()), "amount": "2000.00"},
        ]

        run = ap_service.create_payment_run(
            run_id=uuid4(),
            payment_date=deterministic_clock.now().date(),
            invoices=invoices,
            actor_id=test_actor_id,
        )

        assert isinstance(run, PaymentRun)
        assert run.status == PaymentRunStatus.DRAFT
        assert run.total_amount == Decimal("10000.00")
        assert run.line_count == 3
        assert run.created_by == test_actor_id

    def test_create_payment_run_empty_invoices(
        self, ap_service, deterministic_clock, test_actor_id, test_vendor_party,
    ):
        """create_payment_run handles empty invoice list."""
        run = ap_service.create_payment_run(
            run_id=uuid4(),
            payment_date=deterministic_clock.now().date(),
            invoices=[],
            actor_id=test_actor_id,
        )

        assert run.total_amount == Decimal("0")
        assert run.line_count == 0


class TestExecutePaymentRun:
    """Tests for execute_payment_run service method."""

    def test_execute_payment_run_posts_each_line(
        self, ap_service, current_period, test_actor_id, test_vendor_party, deterministic_clock,
    ):
        """execute_payment_run posts a payment for each line."""
        run_id = uuid4()
        run = PaymentRun(
            id=run_id,
            payment_date=deterministic_clock.now().date(),
            currency="USD",
            status=PaymentRunStatus.APPROVED,
            total_amount=Decimal("8000.00"),
            line_count=2,
        )

        lines = [
            PaymentRunLine(
                id=uuid4(),
                run_id=run_id,
                invoice_id=uuid4(),
                vendor_id=TEST_VENDOR_ID,
                amount=Decimal("5000.00"),
            ),
            PaymentRunLine(
                id=uuid4(),
                run_id=run_id,
                invoice_id=uuid4(),
                vendor_id=TEST_VENDOR_ID,
                amount=Decimal("3000.00"),
            ),
        ]

        results = ap_service.execute_payment_run(
            run=run,
            lines=lines,
            actor_id=test_actor_id,
        )

        assert len(results) == 2
        assert all(r.status == ModulePostingStatus.POSTED for r in results)
        assert all(r.is_success for r in results)


# =============================================================================
# Auto-Matching
# =============================================================================


class TestAutoMatchInvoices:
    """Tests for auto_match_invoices service method."""

    def test_auto_match_returns_results(
        self, ap_service, deterministic_clock, test_actor_id,
    ):
        """auto_match_invoices returns a list of MatchResults."""
        candidates = [
            {
                "invoice_id": uuid4(),
                "po_id": uuid4(),
                "invoice_amount": "5000.00",
                "po_amount": "5000.00",
            },
        ]

        results = ap_service.auto_match_invoices(
            candidates=candidates,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert len(results) == 1
        assert results[0].status in (MatchStatus.MATCHED, MatchStatus.VARIANCE)

    def test_auto_match_empty_candidates(
        self, ap_service, deterministic_clock, test_actor_id,
    ):
        """auto_match_invoices handles empty candidate list."""
        results = ap_service.auto_match_invoices(
            candidates=[],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert results == []


# =============================================================================
# Vendor Hold / Release
# =============================================================================


class TestVendorHoldRelease:
    """Tests for hold_vendor and release_vendor_hold service methods."""

    def test_hold_vendor_returns_hold(
        self, ap_service, deterministic_clock, test_actor_id, test_vendor_party,
    ):
        """hold_vendor returns an active VendorHold."""
        vendor_id = uuid4()
        hold = ap_service.hold_vendor(
            hold_id=uuid4(),
            vendor_id=vendor_id,
            reason="Quality issue on last shipment",
            hold_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert isinstance(hold, VendorHold)
        assert hold.status == HoldStatus.ACTIVE
        assert hold.vendor_id == vendor_id
        assert hold.reason == "Quality issue on last shipment"
        assert hold.held_by == test_actor_id

    def test_release_vendor_hold_returns_released(
        self, ap_service, deterministic_clock, test_actor_id, test_vendor_party,
    ):
        """release_vendor_hold returns a released VendorHold."""
        hold = VendorHold(
            id=uuid4(),
            vendor_id=uuid4(),
            reason="Dispute resolved",
            hold_date=deterministic_clock.now().date(),
            held_by=test_actor_id,
            status=HoldStatus.ACTIVE,
        )

        released = ap_service.release_vendor_hold(
            hold=hold,
            release_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert isinstance(released, VendorHold)
        assert released.status == HoldStatus.RELEASED
        assert released.released_by == test_actor_id
        assert released.released_date == deterministic_clock.now().date()
        assert hold.status == HoldStatus.ACTIVE
