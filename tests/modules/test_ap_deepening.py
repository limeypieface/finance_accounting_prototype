"""
Tests for AP Module Deepening.

Validates:
- create_payment_run: selects invoices for batch payment (no posting)
- execute_payment_run: loops record_payment() for each line
- auto_match_invoices: batch 2-way match via MatchingEngine
- hold_vendor / release_vendor_hold: payment hold lifecycle
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_engines.matching import MatchStatus
from finance_modules.ap.models import (
    HoldStatus,
    PaymentRun,
    PaymentRunLine,
    PaymentRunStatus,
    VendorHold,
)
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
# Model Tests
# =============================================================================


class TestAPDeepeningModels:
    """Verify new AP models are frozen dataclasses with correct defaults."""

    def test_payment_run_creation(self):
        """PaymentRun is a frozen dataclass with correct fields."""
        run = PaymentRun(
            id=uuid4(),
            payment_date=__import__("datetime").date(2024, 1, 15),
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
            payment_date=__import__("datetime").date(2024, 1, 15),
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
            hold_date=__import__("datetime").date(2024, 1, 10),
            held_by=uuid4(),
        )
        assert hold.status == HoldStatus.ACTIVE
        assert hold.released_date is None
        assert hold.released_by is None
        assert hold.reason == "Dispute on invoice #1234"


# =============================================================================
# Integration Tests — Payment Run
# =============================================================================


class TestCreatePaymentRun:
    """Tests for create_payment_run service method."""

    def test_create_payment_run_returns_run(
        self, ap_service, deterministic_clock, test_actor_id,
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
        self, ap_service, deterministic_clock, test_actor_id,
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
        self, ap_service, current_period, test_actor_id, deterministic_clock,
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
                vendor_id=uuid4(),
                amount=Decimal("5000.00"),
            ),
            PaymentRunLine(
                id=uuid4(),
                run_id=run_id,
                invoice_id=uuid4(),
                vendor_id=uuid4(),
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
# Integration Tests — Auto-Matching
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
# Integration Tests — Vendor Hold / Release
# =============================================================================


class TestVendorHoldRelease:
    """Tests for hold_vendor and release_vendor_hold service methods."""

    def test_hold_vendor_returns_hold(
        self, ap_service, deterministic_clock, test_actor_id,
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
        self, ap_service, deterministic_clock, test_actor_id,
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
        # Original hold unchanged (frozen)
        assert hold.status == HoldStatus.ACTIVE
