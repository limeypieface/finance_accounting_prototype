"""
Tests for GL Module Deepening.

Validates:
- generate_recurring_entry: posts from RecurringEntry template
- record_retained_earnings_roll: year-end P&L → RE transfer
- reconcile_account: period sign-off (no posting)
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.gl.models import (
    AccountReconciliation,
    CloseTaskStatus,
    PeriodCloseTask,
    ReconciliationStatus,
    RecurringEntry,
)
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
# Model Tests
# =============================================================================


class TestGLDeepeningModels:
    """Verify new GL models are frozen dataclasses with correct defaults."""

    def test_account_reconciliation_creation(self):
        """AccountReconciliation is a frozen dataclass with correct fields."""
        recon = AccountReconciliation(
            id=uuid4(),
            account_id=uuid4(),
            period="2025-12",
            reconciled_date=__import__("datetime").date(2025, 12, 31),
            reconciled_by=uuid4(),
            status=ReconciliationStatus.RECONCILED,
            notes="Confirmed against bank statement",
            balance_confirmed=Decimal("50000.00"),
        )
        assert recon.status == ReconciliationStatus.RECONCILED
        assert recon.balance_confirmed == Decimal("50000.00")
        assert recon.notes == "Confirmed against bank statement"

    def test_account_reconciliation_defaults(self):
        """AccountReconciliation has sensible defaults."""
        recon = AccountReconciliation(
            id=uuid4(),
            account_id=uuid4(),
            period="2025-12",
            reconciled_date=__import__("datetime").date(2025, 12, 31),
            reconciled_by=uuid4(),
        )
        assert recon.status == ReconciliationStatus.PENDING
        assert recon.notes is None
        assert recon.balance_confirmed == Decimal("0")

    def test_period_close_task_creation(self):
        """PeriodCloseTask is a frozen dataclass with correct fields."""
        task = PeriodCloseTask(
            id=uuid4(),
            period="2025-12",
            task_name="reconcile_bank",
            module="gl",
            status=CloseTaskStatus.COMPLETED,
            completed_by=uuid4(),
            completed_date=__import__("datetime").date(2025, 12, 31),
        )
        assert task.status == CloseTaskStatus.COMPLETED
        assert task.module == "gl"
        assert task.task_name == "reconcile_bank"

    def test_period_close_task_defaults(self):
        """PeriodCloseTask defaults to PENDING with no completion info."""
        task = PeriodCloseTask(
            id=uuid4(),
            period="2025-12",
            task_name="post_depreciation",
            module="assets",
        )
        assert task.status == CloseTaskStatus.PENDING
        assert task.completed_by is None
        assert task.completed_date is None


# =============================================================================
# Integration Tests — Recurring Entry
# =============================================================================


class TestGenerateRecurringEntry:
    """Tests for generate_recurring_entry service method."""

    def test_generate_recurring_entry_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Generate a recurring entry from an active template."""
        template = RecurringEntry(
            id=uuid4(),
            name="Monthly Rent",
            description="Office rent accrual",
            frequency="monthly",
            start_date=__import__("datetime").date(2025, 1, 1),
        )

        result = gl_service.generate_recurring_entry(
            template=template,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("5000.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_generate_recurring_entry_inactive_rejected(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Inactive template is rejected without posting."""
        template = RecurringEntry(
            id=uuid4(),
            name="Cancelled Subscription",
            description="No longer active",
            frequency="monthly",
            start_date=__import__("datetime").date(2025, 1, 1),
            is_active=False,
        )

        result = gl_service.generate_recurring_entry(
            template=template,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("1000.00"),
        )

        assert result.status == ModulePostingStatus.GUARD_REJECTED
        assert "inactive" in result.message.lower()

    def test_generate_recurring_entry_past_end_date_rejected(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Template past its end_date is rejected."""
        template = RecurringEntry(
            id=uuid4(),
            name="Expired Lease",
            description="Lease ended",
            frequency="monthly",
            start_date=__import__("datetime").date(2022, 1, 1),
            end_date=__import__("datetime").date(2023, 12, 31),
        )

        result = gl_service.generate_recurring_entry(
            template=template,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("3000.00"),
        )

        assert result.status == ModulePostingStatus.GUARD_REJECTED
        assert "end date" in result.message.lower()


# =============================================================================
# Integration Tests — Retained Earnings Roll
# =============================================================================


class TestRetainedEarningsRoll:
    """Tests for record_retained_earnings_roll service method."""

    def test_retained_earnings_roll_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Year-end retained earnings roll posts successfully."""
        result = gl_service.record_retained_earnings_roll(
            fiscal_year=2025,
            net_income=Decimal("150000.00"),
            actor_id=test_actor_id,
            effective_date=deterministic_clock.now().date(),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0


# =============================================================================
# Integration Tests — Account Reconciliation
# =============================================================================


class TestReconcileAccount:
    """Tests for reconcile_account service method."""

    def test_reconcile_account_returns_reconciliation(
        self, gl_service, test_actor_id, deterministic_clock,
    ):
        """reconcile_account creates an AccountReconciliation record."""
        account_id = uuid4()
        recon = gl_service.reconcile_account(
            reconciliation_id=uuid4(),
            account_id=account_id,
            period="2025-12",
            balance_confirmed=Decimal("75000.00"),
            actor_id=test_actor_id,
            reconciled_date=deterministic_clock.now().date(),
            notes="Matched to bank statement",
        )

        assert isinstance(recon, AccountReconciliation)
        assert recon.status == ReconciliationStatus.RECONCILED
        assert recon.account_id == account_id
        assert recon.balance_confirmed == Decimal("75000.00")
        assert recon.notes == "Matched to bank statement"
        assert recon.period == "2025-12"
