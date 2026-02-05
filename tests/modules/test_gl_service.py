"""
Tests for General Ledger Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: journal entries, adjustments, closing entries, intercompany, dividends
- Engine: budget variance computation via VarianceCalculator
- Multi-currency: translate_balances, record_cta, run_period_end_revaluation, multi_currency_trial_balance
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.gl.models import (
    AccountReconciliation,
    CloseTaskStatus,
    PeriodCloseTask,
    RecurringEntry,
    ReconciliationStatus,
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
def gl_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide GeneralLedgerService for integration testing. workflow_executor required; party_service + test_actor_party for G14."""
    return GeneralLedgerService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


@pytest.fixture
def reporting_service(session, deterministic_clock):
    """Provide ReportingService for reporting / multi-currency tests."""
    return ReportingService(
        session=session,
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
        """Constructor accepts session, role_resolver, workflow_executor, clock."""
        sig = inspect.signature(GeneralLedgerService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "workflow_executor" in params
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


# =============================================================================
# GL models (AccountReconciliation, RecurringEntry, etc.)
# =============================================================================


class TestGLModels:
    """Verify GL models are frozen dataclasses with correct defaults."""

    def test_account_reconciliation_creation(self):
        """AccountReconciliation is a frozen dataclass with correct fields."""
        recon = AccountReconciliation(
            id=uuid4(),
            account_id=uuid4(),
            period="2025-12",
            reconciled_date=date(2025, 12, 31),
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
            reconciled_date=date(2025, 12, 31),
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
            completed_date=date(2025, 12, 31),
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
            start_date=date(2025, 1, 1),
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
            start_date=date(2025, 1, 1),
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
            start_date=date(2022, 1, 1),
            end_date=date(2023, 12, 31),
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


# =============================================================================
# Multi-Currency (translate_balances, CTA, revaluation, multi_currency_trial_balance)
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
