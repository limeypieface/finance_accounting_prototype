"""
Balance validation tests using the interpretation pipeline.

Verifies:
- Balanced entries post successfully via InterpretationCoordinator
- Unbalanced entries are rejected by JournalWriter
- Multi-currency entries must balance per currency
- Multiple debits / credits handled correctly
- Rounding lines handled via AccountingIntent
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilderResult,
)
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from tests.conftest import make_source_event


def _make_meaning_result(
    session,
    actor_id,
    clock,
    effective_date,
    event_type="test.balance",
    profile_id="BalanceTest",
    amount=Decimal("100.00"),
):
    """Create a successful MeaningBuilderResult for testing."""
    source_event_id = uuid4()
    # Create source Event record (FK requirement for JournalEntry)
    make_source_event(session, source_event_id, actor_id, clock, effective_date, event_type)
    econ_data = EconomicEventData(
        source_event_id=source_event_id,
        economic_type=event_type,
        effective_date=effective_date,
        profile_id=profile_id,
        profile_version=1,
        profile_hash=None,
        quantity=amount,
    )
    return MeaningBuilderResult.ok(econ_data), source_event_id


def _make_intent(source_event_id, effective_date, lines, profile_id="BalanceTest", ledger_id="GL"):
    """Create an AccountingIntent with the given lines."""
    return AccountingIntent(
        econ_event_id=uuid4(),
        source_event_id=source_event_id,
        profile_id=profile_id,
        profile_version=1,
        effective_date=effective_date,
        ledger_intents=(
            LedgerIntent(
                ledger_id=ledger_id,
                lines=tuple(lines),
            ),
        ),
        snapshot=AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
        ),
    )


class TestBalanceValidation:
    """Tests for double-entry balance validation via the interpretation pipeline."""

    def test_balanced_entry_posts(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """A balanced entry posts successfully through the pipeline."""
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success
        assert result.journal_result is not None
        assert result.journal_result.is_success
        assert result.outcome is not None

    def test_unbalanced_entry_rejected(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """An unbalanced entry is rejected by the JournalWriter."""
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("90.00"), "USD"),  # Unbalanced!
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )

        assert not result.success
        assert result.error_code is not None

    def test_multi_currency_balanced_per_currency(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Multi-currency entries must balance per currency."""
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        intent = _make_intent(
            source_id,
            today,
            lines=[
                # USD balanced
                IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                # EUR balanced
                IntentLine.debit("AccountsReceivable", Decimal("85.00"), "EUR"),
                IntentLine.credit("SalesRevenue", Decimal("85.00"), "EUR"),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success

    def test_multiple_debits_one_credit(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Entry with multiple debits and one credit posts correctly."""
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("COGS", Decimal("50.00"), "USD"),
                IntentLine.debit("InventoryAsset", Decimal("50.00"), "USD"),
                IntentLine.credit("AccountsPayable", Decimal("100.00"), "USD"),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success

    def test_one_debit_multiple_credits(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Entry with one debit and multiple credits posts correctly."""
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("80.00"), "USD"),
                IntentLine.credit("AccountsPayable", Decimal("20.00"), "USD"),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success


class TestRoundingLineHandling:
    """Tests for rounding line handling via AccountingIntent."""

    def test_rounding_line_accepted_when_balanced(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """An entry with a small rounding line that makes it balanced posts correctly."""
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        # Simulate a conversion remainder: $100.003 debit needs a $0.003 rounding credit
        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("100.003"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                IntentLine.credit(
                    "RoundingExpense",
                    Decimal("0.003"),
                    "USD",
                ),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success
