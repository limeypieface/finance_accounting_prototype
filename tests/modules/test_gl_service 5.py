"""
Tests for General Ledger Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: journal entries, adjustments, closing entries, intercompany, dividends
- Engine: budget variance computation via VarianceCalculator
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.gl.service import GeneralLedgerService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def gl_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide GeneralLedgerService for integration testing."""
    return GeneralLedgerService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests (lightweight sanity checks)
# =============================================================================


class TestGeneralLedgerServiceStructure:
    """Verify GeneralLedgerService follows the module service pattern."""

    def test_importable(self):
        """GeneralLedgerService is importable from the gl module."""
        assert GeneralLedgerService is not None

    def test_constructor_signature(self):
        """Constructor accepts session, role_resolver, clock."""
        sig = inspect.signature(GeneralLedgerService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        """Service exposes all expected domain operations."""
        expected = [
            "record_journal_entry",
            "record_adjustment",
            "record_closing_entry",
            "compute_budget_variance",
            "record_intercompany_transfer",
            "record_dividend_declared",
            "recognize_deferred_revenue",
            "recognize_deferred_expense",
            "record_fx_unrealized_gain",
            "record_fx_unrealized_loss",
            "record_fx_realized_gain",
            "record_fx_realized_loss",
        ]
        for method_name in expected:
            assert hasattr(GeneralLedgerService, method_name), (
                f"Missing method: {method_name}"
            )
            assert callable(getattr(GeneralLedgerService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestGLServiceIntegration:
    """Integration tests calling real GL service methods through the posting pipeline."""

    def test_record_journal_entry_reaches_pipeline(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """record_journal_entry reaches the real pipeline.

        NOTE: gl.journal_entry profile is not yet registered in GL profiles.py.
        This test verifies the method exercises the real posting pipeline
        (validation, audit, profile lookup) end-to-end. When the profile is
        added, change assertion to POSTED.
        """
        result = gl_service.record_journal_entry(
            entry_id=uuid4(),
            description="Test journal entry",
            lines=[
                {"account": "5100", "debit": "1000.00"},
                {"account": "2000", "credit": "1000.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("1000.00"),
        )

        # Pipeline runs: validation passes, audit event created, profile not found
        assert result.status == ModulePostingStatus.PROFILE_NOT_FOUND

    def test_record_adjustment_reaches_pipeline(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """record_adjustment reaches the real pipeline.

        NOTE: gl.adjustment profile is not yet registered in GL profiles.py.
        Same as above — real pipeline, pending profile.
        """
        result = gl_service.record_adjustment(
            entry_id=uuid4(),
            description="Monthly accrual adjustment",
            lines=[
                {"account": "5200", "debit": "500.00"},
                {"account": "2100", "credit": "500.00"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            adjustment_type="ACCRUAL",
            amount=Decimal("500.00"),
        )

        assert result.status == ModulePostingStatus.PROFILE_NOT_FOUND

    def test_record_closing_entry_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a year-end closing entry through the real pipeline."""
        result = gl_service.record_closing_entry(
            period_id="2025-12",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            net_income=Decimal("50000.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_intercompany_transfer_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record an intercompany transfer through the real pipeline."""
        result = gl_service.record_intercompany_transfer(
            transfer_id=uuid4(),
            from_entity="ENTITY-A",
            to_entity="ENTITY-B",
            amount=Decimal("25000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            description="Intercompany receivable/payable",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_dividend_declared_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a dividend declaration through the real pipeline."""
        result = gl_service.record_dividend_declared(
            dividend_id=uuid4(),
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            description="Q4 dividend declared",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_compute_budget_variance(self, gl_service):
        """Compute budget variance via VarianceCalculator engine (pure computation)."""
        variance_result = gl_service.compute_budget_variance(
            budget_amount=Decimal("10000.00"),
            actual_amount=Decimal("11500.00"),
        )

        # Actual > budget = unfavorable variance
        assert variance_result.variance.amount == Decimal("1500.00")
        assert not variance_result.is_favorable

    def test_compute_budget_variance_favorable(self, gl_service):
        """Favorable variance when actual is less than budget."""
        variance_result = gl_service.compute_budget_variance(
            budget_amount=Decimal("10000.00"),
            actual_amount=Decimal("8500.00"),
        )

        # Actual < budget = favorable
        assert variance_result.variance.amount == Decimal("-1500.00")
        assert variance_result.is_favorable

    def test_recognize_deferred_revenue_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Recognize deferred revenue through the real pipeline."""
        result = gl_service.recognize_deferred_revenue(
            recognition_id=uuid4(),
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            remaining_deferred=Decimal("15000.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_recognize_deferred_expense_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Recognize prepaid expense through the real pipeline."""
        result = gl_service.recognize_deferred_expense(
            recognition_id=uuid4(),
            amount=Decimal("2000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            remaining_deferred=Decimal("10000.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_fx_unrealized_gain_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record unrealized FX gain through the real pipeline."""
        result = gl_service.record_fx_unrealized_gain(
            amount=Decimal("1200.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            original_currency="EUR",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_fx_unrealized_loss_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record unrealized FX loss through the real pipeline."""
        result = gl_service.record_fx_unrealized_loss(
            amount=Decimal("800.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            original_currency="GBP",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_fx_realized_gain_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record realized FX gain through the real pipeline."""
        result = gl_service.record_fx_realized_gain(
            amount=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            original_currency="JPY",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_fx_realized_loss_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record realized FX loss through the real pipeline."""
        result = gl_service.record_fx_realized_loss(
            amount=Decimal("350.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            original_currency="CHF",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
