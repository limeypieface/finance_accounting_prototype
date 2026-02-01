"""
Tests for Multi-Currency Completion (Phase 1D).

Validates:
- translate_balances: pure calculation of currency translation + CTA
- record_cta: posts cumulative translation adjustment to equity
- run_period_end_revaluation: batch FX unrealized gain/loss posting
- multi_currency_trial_balance: trial balance across multiple currencies
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.gl.models import (
    RevaluationResult,
    TranslationMethod,
    TranslationResult,
)
from finance_modules.gl.service import GeneralLedgerService
from finance_modules.reporting.models import MultiCurrencyTrialBalance
from finance_modules.reporting.service import ReportingService

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


@pytest.fixture
def reporting_service(session, deterministic_clock):
    """Provide ReportingService for reporting tests."""
    return ReportingService(
        session=session,
        clock=deterministic_clock,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestMultiCurrencyModels:
    """Verify multi-currency models are frozen dataclasses."""

    def test_translation_result_creation(self):
        """TranslationResult is a frozen dataclass with correct fields."""
        result = TranslationResult(
            id=uuid4(),
            entity_id="ENTITY-001",
            period="2024-12",
            source_currency="EUR",
            target_currency="USD",
            method=TranslationMethod.CURRENT_RATE,
            translated_amount=Decimal("1200.00"),
            cta_amount=Decimal("200.00"),
            exchange_rate=Decimal("1.20"),
        )
        assert result.source_currency == "EUR"
        assert result.target_currency == "USD"
        assert result.method == TranslationMethod.CURRENT_RATE
        assert result.translated_amount == Decimal("1200.00")
        assert result.cta_amount == Decimal("200.00")

    def test_translation_result_is_frozen(self):
        """TranslationResult is immutable."""
        result = TranslationResult(
            id=uuid4(),
            entity_id="ENTITY-001",
            period="2024-12",
            source_currency="EUR",
            target_currency="USD",
            method=TranslationMethod.CURRENT_RATE,
            translated_amount=Decimal("1200.00"),
            cta_amount=Decimal("200.00"),
            exchange_rate=Decimal("1.20"),
        )
        with pytest.raises(AttributeError):
            result.cta_amount = Decimal("300.00")  # type: ignore[misc]

    def test_revaluation_result_creation(self):
        """RevaluationResult is a frozen dataclass with correct defaults."""
        result = RevaluationResult(
            id=uuid4(),
            period="2024-12",
            revaluation_date=date(2024, 12, 31),
        )
        assert result.currencies_processed == 0
        assert result.total_gain == Decimal("0")
        assert result.total_loss == Decimal("0")
        assert result.entries_posted == 0

    def test_revaluation_result_with_values(self):
        """RevaluationResult with explicit values."""
        result = RevaluationResult(
            id=uuid4(),
            period="2024-12",
            revaluation_date=date(2024, 12, 31),
            currencies_processed=3,
            total_gain=Decimal("5000.00"),
            total_loss=Decimal("2000.00"),
            entries_posted=3,
        )
        assert result.currencies_processed == 3
        assert result.total_gain == Decimal("5000.00")
        assert result.total_loss == Decimal("2000.00")
        assert result.entries_posted == 3

    def test_translation_method_values(self):
        """TranslationMethod enum has expected values."""
        assert TranslationMethod.CURRENT_RATE.value == "current_rate"
        assert TranslationMethod.TEMPORAL.value == "temporal"

    def test_multi_currency_trial_balance_model(self):
        """MultiCurrencyTrialBalance is a frozen dataclass."""
        mctb = MultiCurrencyTrialBalance(
            metadata=None,  # type: ignore[arg-type]
            currency_reports=(),
            currencies=("USD", "EUR"),
            total_debits_by_currency=(("USD", Decimal("0")), ("EUR", Decimal("0"))),
            total_credits_by_currency=(("USD", Decimal("0")), ("EUR", Decimal("0"))),
            all_balanced=True,
        )
        assert mctb.currencies == ("USD", "EUR")
        assert mctb.all_balanced is True


# =============================================================================
# Integration Tests — translate_balances
# =============================================================================


class TestTranslateBalances:
    """Tests for translate_balances pure calculation method."""

    def test_translate_balances_current_rate(self, gl_service):
        """Current rate translation multiplies balance by rate."""
        result = gl_service.translate_balances(
            entity_id="ENTITY-001",
            period="2024-12",
            source_currency="EUR",
            target_currency="USD",
            balance_amount=Decimal("1000.00"),
            exchange_rate=Decimal("1.10"),
            method=TranslationMethod.CURRENT_RATE,
        )

        assert isinstance(result, TranslationResult)
        assert result.translated_amount == Decimal("1100.00")
        assert result.cta_amount == Decimal("100.00")
        assert result.source_currency == "EUR"
        assert result.target_currency == "USD"
        assert result.method == TranslationMethod.CURRENT_RATE

    def test_translate_balances_temporal(self, gl_service):
        """Temporal translation also applies exchange rate."""
        result = gl_service.translate_balances(
            entity_id="ENTITY-002",
            period="2024-12",
            source_currency="GBP",
            target_currency="USD",
            balance_amount=Decimal("500.00"),
            exchange_rate=Decimal("1.25"),
            method=TranslationMethod.TEMPORAL,
        )

        assert result.translated_amount == Decimal("625.00")
        assert result.cta_amount == Decimal("125.00")
        assert result.method == TranslationMethod.TEMPORAL

    def test_translate_balances_negative_cta(self, gl_service):
        """CTA can be negative when rate < 1."""
        result = gl_service.translate_balances(
            entity_id="ENTITY-003",
            period="2024-12",
            source_currency="USD",
            target_currency="JPY",
            balance_amount=Decimal("1000.00"),
            exchange_rate=Decimal("0.90"),
        )

        assert result.translated_amount == Decimal("900.00")
        assert result.cta_amount == Decimal("-100.00")


# =============================================================================
# Integration Tests — record_cta
# =============================================================================


class TestRecordCTA:
    """Tests for record_cta posting method."""

    def test_record_cta_posts(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """CTA posting succeeds via fx.translation_adjustment profile."""
        result = gl_service.record_cta(
            entity_id="ENTITY-001",
            period="2024-12",
            cta_amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            source_currency="EUR",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_cta_negative_uses_abs(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Negative CTA amount is posted using absolute value."""
        result = gl_service.record_cta(
            entity_id="ENTITY-002",
            period="2024-12",
            cta_amount=Decimal("-3000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            source_currency="GBP",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success


# =============================================================================
# Integration Tests — run_period_end_revaluation
# =============================================================================


class TestRunPeriodEndRevaluation:
    """Tests for run_period_end_revaluation batch method."""

    def test_revaluation_single_gain(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Single gain entry posts and returns correct summary."""
        entries = [
            {"amount": "2000.00", "original_currency": "EUR", "is_gain": True},
        ]

        result = gl_service.run_period_end_revaluation(
            revaluation_entries=entries,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period="2024-12",
        )

        assert isinstance(result, RevaluationResult)
        assert result.total_gain == Decimal("2000.00")
        assert result.total_loss == Decimal("0")
        assert result.entries_posted == 1
        assert result.currencies_processed == 1

    def test_revaluation_mixed_gain_loss(
        self, gl_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Mixed gain/loss entries are summarized correctly."""
        entries = [
            {"amount": "3000.00", "original_currency": "EUR", "is_gain": True},
            {"amount": "1500.00", "original_currency": "GBP", "is_gain": False},
        ]

        result = gl_service.run_period_end_revaluation(
            revaluation_entries=entries,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period="2024-12",
        )

        assert result.total_gain == Decimal("3000.00")
        assert result.total_loss == Decimal("1500.00")
        assert result.entries_posted == 2
        assert result.currencies_processed == 2

    def test_revaluation_empty_entries(
        self, gl_service, test_actor_id, deterministic_clock,
    ):
        """Empty entries list returns zero-valued result."""
        result = gl_service.run_period_end_revaluation(
            revaluation_entries=[],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            period="2024-12",
        )

        assert result.total_gain == Decimal("0")
        assert result.total_loss == Decimal("0")
        assert result.entries_posted == 0
        assert result.currencies_processed == 0


# =============================================================================
# Integration Tests — multi_currency_trial_balance
# =============================================================================


class TestMultiCurrencyTrialBalance:
    """Tests for multi_currency_trial_balance reporting method."""

    def test_multi_currency_tb_basic(
        self, reporting_service, module_accounts, current_period, deterministic_clock,
    ):
        """Multi-currency TB generates reports for each currency."""
        as_of = deterministic_clock.now().date()
        result = reporting_service.multi_currency_trial_balance(
            as_of_date=as_of,
            currencies=["USD", "EUR"],
        )

        assert isinstance(result, MultiCurrencyTrialBalance)
        assert result.currencies == ("USD", "EUR")
        assert len(result.currency_reports) == 2
        currencies_in_debits = [c for c, _ in result.total_debits_by_currency]
        assert "USD" in currencies_in_debits
        assert "EUR" in currencies_in_debits

    def test_multi_currency_tb_single_currency(
        self, reporting_service, module_accounts, current_period, deterministic_clock,
    ):
        """Single-currency TB works correctly."""
        as_of = deterministic_clock.now().date()
        result = reporting_service.multi_currency_trial_balance(
            as_of_date=as_of,
            currencies=["USD"],
        )

        assert result.currencies == ("USD",)
        assert len(result.currency_reports) == 1
        assert result.all_balanced is True

    def test_multi_currency_tb_all_balanced(
        self, reporting_service, module_accounts, current_period, deterministic_clock,
    ):
        """All currency TBs should be balanced (debits = credits)."""
        as_of = deterministic_clock.now().date()
        result = reporting_service.multi_currency_trial_balance(
            as_of_date=as_of,
            currencies=["USD", "EUR", "GBP"],
        )

        assert result.all_balanced is True
        for report in result.currency_reports:
            assert report.is_balanced
