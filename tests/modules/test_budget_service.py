"""
Tests for Budgeting Module.

Validates:
- post_budget_entry: memo posting
- transfer_budget: posts
- lock_budget: no posting, returns lock
- record_encumbrance: posts
- relieve_encumbrance: posts, updates status
- cancel_encumbrance: posts, cancels
- get_budget_vs_actual: pure calculation
- get_encumbrance_balance: pure calculation
- get_available_budget: pure calculation
- update_forecast: memo posting
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from tests.modules.conftest import TEST_BUDGET_VERSION_ID
from finance_modules.budget.models import (
    BudgetEntry,
    BudgetLock,
    BudgetVariance,
    BudgetVersion,
    BudgetStatus,
    Encumbrance,
    EncumbranceStatus,
    ForecastEntry,
)
from finance_modules.budget.service import BudgetService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def budget_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide BudgetService for integration testing."""
    return BudgetService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestBudgetModels:
    """Verify budget models are frozen dataclasses."""

    def test_budget_version_creation(self):
        bv = BudgetVersion(id=uuid4(), name="FY2025 Original", fiscal_year=2025)
        assert bv.status == BudgetStatus.DRAFT

    def test_budget_entry_creation(self):
        entry = BudgetEntry(
            id=uuid4(), version_id=uuid4(),
            account_code="6000", period="2025-01",
            amount=Decimal("50000.00"),
        )
        assert entry.currency == "USD"

    def test_encumbrance_defaults(self):
        enc = Encumbrance(
            id=uuid4(), po_id=uuid4(),
            account_code="6000", amount=Decimal("10000"),
            period="2025-01",
        )
        assert enc.status == EncumbranceStatus.OPEN
        assert enc.relieved_amount == Decimal("0")

    def test_budget_variance_creation(self):
        bv = BudgetVariance(
            account_code="6000", period="2025-01",
            budget_amount=Decimal("50000"),
            actual_amount=Decimal("45000"),
            variance_amount=Decimal("5000"),
            variance_percentage=Decimal("10"),
            is_favorable=True,
        )
        assert bv.is_favorable is True

    def test_forecast_entry_creation(self):
        fe = ForecastEntry(
            id=uuid4(), version_id=uuid4(),
            account_code="6000", period="2025-06",
            forecast_amount=Decimal("55000.00"),
        )
        assert fe.basis == "trend"


# =============================================================================
# Integration Tests — Budget Entries
# =============================================================================


class TestBudgetEntry:
    """Tests for post_budget_entry."""

    def test_budget_entry_posts(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Budget memo entry posts successfully."""
        entry, result = budget_service.post_budget_entry(
            version_id=TEST_BUDGET_VERSION_ID,
            account_code="6000",
            period="2025-01",
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(entry, BudgetEntry)
        assert entry.amount == Decimal("50000.00")


class TestBudgetTransfer:
    """Tests for transfer_budget."""

    def test_transfer_posts(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Budget transfer posts successfully."""
        result = budget_service.transfer_budget(
            from_account="6000",
            to_account="6100",
            amount=Decimal("10000.00"),
            period="2025-01",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            version_id=TEST_BUDGET_VERSION_ID,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests — Budget Lock
# =============================================================================


class TestBudgetLock:
    """Tests for lock_budget."""

    def test_lock_returns_lock(self, budget_service, test_actor_id):
        """Lock returns BudgetLock without posting."""
        lock = budget_service.lock_budget(
            version_id=uuid4(),
            period_range_start="2025-01",
            period_range_end="2025-12",
            actor_id=test_actor_id,
        )
        assert isinstance(lock, BudgetLock)
        assert lock.locked_by == test_actor_id


# =============================================================================
# Integration Tests — Encumbrances
# =============================================================================


class TestEncumbranceLifecycle:
    """Tests for encumbrance commit, relieve, cancel."""

    def test_encumbrance_commit_posts(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Encumbrance commitment posts."""
        enc, result = budget_service.record_encumbrance(
            po_id=uuid4(),
            amount=Decimal("15000.00"),
            account_code="6000",
            period="2025-01",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert enc.status == EncumbranceStatus.OPEN

    def test_encumbrance_relieve_partial(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Partial relief updates status."""
        enc = Encumbrance(
            id=uuid4(), po_id=uuid4(),
            account_code="6000", amount=Decimal("10000"),
            period="2025-01",
        )
        updated, result = budget_service.relieve_encumbrance(
            encumbrance=enc,
            relief_amount=Decimal("4000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert updated.status == EncumbranceStatus.PARTIALLY_RELIEVED
        assert updated.relieved_amount == Decimal("4000.00")

    def test_encumbrance_relieve_full(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Full relief sets status to RELIEVED."""
        enc = Encumbrance(
            id=uuid4(), po_id=uuid4(),
            account_code="6000", amount=Decimal("10000"),
            period="2025-01",
        )
        updated, result = budget_service.relieve_encumbrance(
            encumbrance=enc,
            relief_amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert updated.status == EncumbranceStatus.RELIEVED

    def test_encumbrance_cancel_posts(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Encumbrance cancellation posts."""
        enc = Encumbrance(
            id=uuid4(), po_id=uuid4(),
            account_code="6000", amount=Decimal("8000"),
            period="2025-01",
        )
        updated, result = budget_service.cancel_encumbrance(
            encumbrance=enc,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert updated.status == EncumbranceStatus.CANCELLED


# =============================================================================
# Query Tests
# =============================================================================


class TestBudgetQueries:
    """Tests for budget query methods."""

    def test_budget_vs_actual_favorable(self, budget_service):
        """Favorable variance when actual < budget."""
        variance = budget_service.get_budget_vs_actual(
            budget_amount=Decimal("50000"),
            actual_amount=Decimal("45000"),
            account_code="6000",
            period="2025-01",
        )
        assert variance.variance_amount == Decimal("5000")
        assert variance.is_favorable is True
        assert variance.variance_percentage == Decimal("10")

    def test_budget_vs_actual_unfavorable(self, budget_service):
        """Unfavorable variance when actual > budget."""
        variance = budget_service.get_budget_vs_actual(
            budget_amount=Decimal("50000"),
            actual_amount=Decimal("55000"),
            account_code="6000",
            period="2025-01",
        )
        assert variance.variance_amount == Decimal("-5000")
        assert variance.is_favorable is False

    def test_encumbrance_balance(self, budget_service):
        """Outstanding encumbrance balance."""
        encumbrances = [
            Encumbrance(id=uuid4(), po_id=uuid4(), account_code="6000",
                        amount=Decimal("10000"), period="2025-01",
                        status=EncumbranceStatus.OPEN),
            Encumbrance(id=uuid4(), po_id=uuid4(), account_code="6000",
                        amount=Decimal("8000"), period="2025-01",
                        status=EncumbranceStatus.PARTIALLY_RELIEVED,
                        relieved_amount=Decimal("3000")),
            Encumbrance(id=uuid4(), po_id=uuid4(), account_code="6000",
                        amount=Decimal("5000"), period="2025-01",
                        status=EncumbranceStatus.RELIEVED,
                        relieved_amount=Decimal("5000")),
        ]
        balance = budget_service.get_encumbrance_balance(encumbrances)
        # 10000 + (8000 - 3000) = 15000 (relieved excluded)
        assert balance == Decimal("15000")

    def test_available_budget(self, budget_service):
        """Available = budget - actual - encumbrances."""
        available = budget_service.get_available_budget(
            budget_amount=Decimal("100000"),
            actual_amount=Decimal("60000"),
            encumbrance_balance=Decimal("15000"),
        )
        assert available == Decimal("25000")


# =============================================================================
# Integration Tests — Forecast
# =============================================================================


class TestForecast:
    """Tests for update_forecast."""

    def test_forecast_update_posts(
        self, budget_service, current_period, test_actor_id, deterministic_clock,
        test_budget_version,
    ):
        """Forecast update memo posting."""
        entry, result = budget_service.update_forecast(
            version_id=uuid4(),
            account_code="6000",
            period="2025-06",
            forecast_amount=Decimal("55000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            basis="trend",
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(entry, ForecastEntry)
        assert entry.forecast_amount == Decimal("55000.00")
