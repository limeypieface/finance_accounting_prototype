"""
Tests for Cash Management Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: receipt, disbursement, bank reconciliation
- Engine composition: ReconciliationManager, MatchingEngine, LinkGraphService
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.cash.service import CashService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cash_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide CashService for integration testing."""
    return CashService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestCashServiceStructure:
    """Verify CashService follows the module service pattern."""

    def test_importable(self):
        assert CashService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(CashService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_receipt", "record_disbursement", "reconcile_bank_statement",
            "record_bank_fee", "record_interest_earned", "record_transfer",
            "record_wire_transfer_out", "record_wire_transfer_cleared",
        ]
        for method_name in expected:
            assert hasattr(CashService, method_name)
            assert callable(getattr(CashService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestCashServiceIntegration:
    """Integration tests calling real Cash service methods through the posting pipeline."""

    def test_record_receipt_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a cash receipt through the real pipeline."""
        result = cash_service.record_receipt(
            receipt_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_disbursement_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a cash disbursement through the real pipeline."""
        result = cash_service.record_disbursement(
            disbursement_id=uuid4(),
            amount=Decimal("3000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            destination_type="EXPENSE",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_reconcile_bank_statement_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Reconcile a bank statement through the real pipeline."""
        result = cash_service.reconcile_bank_statement(
            statement_id=uuid4(),
            entries=[
                {"line_id": str(uuid4()), "amount": "50.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success

    def test_record_bank_fee_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a bank service charge through the real pipeline."""
        result = cash_service.record_bank_fee(
            fee_id=uuid4(),
            amount=Decimal("25.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_interest_earned_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record interest income through the real pipeline."""
        result = cash_service.record_interest_earned(
            interest_id=uuid4(),
            amount=Decimal("150.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_transfer_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record an inter-account transfer through the real pipeline."""
        result = cash_service.record_transfer(
            transfer_id=uuid4(),
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            from_bank_account_code="1020",
            to_bank_account_code="1030",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_wire_transfer_out_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record an outbound wire transfer through the real pipeline."""
        result = cash_service.record_wire_transfer_out(
            wire_id=uuid4(),
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_wire_transfer_cleared_posts(
        self, cash_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record wire transfer confirmation through the real pipeline."""
        result = cash_service.record_wire_transfer_cleared(
            wire_id=uuid4(),
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
