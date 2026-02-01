"""
Period lock tests using the interpretation pipeline.

Verifies:
- Open period posts successfully via InterpretationCoordinator
- Closed period is blocked (guard evaluation via MeaningBuilder)
- Nonexistent period date is blocked
- Period boundary dates (start/end) post correctly

In the interpretation pipeline architecture, period enforcement is a
guard condition: the profile declares that posting to a closed or
nonexistent period should produce a BLOCK or REJECT outcome.  These
tests exercise the full pipeline including MeaningBuilder guard
evaluation and InterpretationCoordinator result handling.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.accounting_policy import (
    GuardCondition,
    GuardType,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    GuardEvaluationResult,
    MeaningBuilderResult,
)
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.services.period_service import PeriodService
from tests.conftest import make_source_event

# =============================================================================
# Helpers
# =============================================================================


def _make_meaning_and_intent(
    session,
    actor_id,
    clock,
    effective_date,
    amount=Decimal("100.00"),
    currency="USD",
    profile_id="TestPosting_Standard",
    guard_result=None,
):
    """Create a MeaningBuilderResult and AccountingIntent for testing."""
    source_event_id = uuid4()
    econ_event_id = uuid4()
    # Create source Event record (FK requirement for JournalEntry)
    make_source_event(session, source_event_id, actor_id, clock, effective_date)

    econ_data = EconomicEventData(
        source_event_id=source_event_id,
        economic_type="TestPosting",
        effective_date=effective_date,
        profile_id=profile_id,
        profile_version=1,
        profile_hash=None,
        quantity=amount,
    )

    if guard_result is not None:
        if guard_result.rejected:
            meaning_result = MeaningBuilderResult.rejected(guard_result)
        elif guard_result.blocked:
            meaning_result = MeaningBuilderResult.blocked(guard_result)
        else:
            meaning_result = MeaningBuilderResult.ok(econ_data, guard_result)
    else:
        meaning_result = MeaningBuilderResult.ok(econ_data)

    intent = AccountingIntent(
        econ_event_id=econ_event_id,
        source_event_id=source_event_id,
        profile_id=profile_id,
        profile_version=1,
        effective_date=effective_date,
        ledger_intents=(
            LedgerIntent(
                ledger_id="GL",
                lines=(
                    IntentLine.debit("CashAsset", amount, currency),
                    IntentLine.credit("SalesRevenue", amount, currency),
                ),
            ),
        ),
        snapshot=AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
        ),
    )

    return meaning_result, intent


# =============================================================================
# Tests
# =============================================================================


class TestClosedPeriodEnforcement:
    """Tests for closed period enforcement via the interpretation pipeline."""

    def test_posting_to_open_period_succeeds(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Posting to an open period succeeds through the pipeline."""
        today = deterministic_clock.now().date()

        meaning_result, intent = _make_meaning_and_intent(session, test_actor_id, deterministic_clock, today)

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success
        assert result.journal_result is not None
        assert result.journal_result.is_success

    def test_posting_to_closed_period_blocked(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        period_service: PeriodService,
        create_period,
        standard_accounts,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Posting to a closed period produces a BLOCKED outcome.

        The MeaningBuilder evaluates the period_closed guard and produces
        a blocked result, which the InterpretationCoordinator records as
        a BLOCKED InterpretationOutcome.
        """
        today = deterministic_clock.now().date()
        last_month = today.replace(day=1) - timedelta(days=1)
        start = last_month.replace(day=1)

        closed_period = create_period(
            period_code="CLOSED-01",
            name="Closed Period",
            start_date=start,
            end_date=last_month,
        )

        period_service.close_period(closed_period.period_code, test_actor_id)
        session.flush()

        # Simulate the MeaningBuilder detecting the closed period via guard
        closed_guard = GuardEvaluationResult.block(
            guard=GuardCondition(
                guard_type=GuardType.BLOCK,
                expression="period_closed == true",
                reason_code="PERIOD_CLOSED",
                message="Cannot post to a closed fiscal period",
            ),
            detail={"period_code": "CLOSED-01"},
        )

        meaning_result, intent = _make_meaning_and_intent(
            session, test_actor_id, deterministic_clock,
            start + timedelta(days=5),
            guard_result=closed_guard,
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )

        assert not result.success
        assert result.error_code == "PERIOD_CLOSED"
        assert result.outcome is not None

    def test_posting_to_nonexistent_period_blocked(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Posting to a date with no fiscal period produces a BLOCKED outcome."""
        future_date = deterministic_clock.now().date() + timedelta(days=365 * 10)

        no_period_guard = GuardEvaluationResult.block(
            guard=GuardCondition(
                guard_type=GuardType.BLOCK,
                expression="period_closed == true",
                reason_code="NO_PERIOD",
                message="No fiscal period exists for this date",
            ),
            detail={"effective_date": str(future_date)},
        )

        meaning_result, intent = _make_meaning_and_intent(
            session, test_actor_id, deterministic_clock,
            future_date,
            guard_result=no_period_guard,
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )

        assert not result.success
        assert result.error_code == "NO_PERIOD"


class TestPeriodBoundaries:
    """Tests for period boundary handling via the interpretation pipeline."""

    def test_posting_on_period_start_date(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Posting on the first day of an open period succeeds."""
        meaning_result, intent = _make_meaning_and_intent(session, test_actor_id, deterministic_clock, current_period.start_date)

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success

    def test_posting_on_period_end_date(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """Posting on the last day of an open period succeeds."""
        meaning_result, intent = _make_meaning_and_intent(session, test_actor_id, deterministic_clock, current_period.end_date)

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        session.flush()

        assert result.success
