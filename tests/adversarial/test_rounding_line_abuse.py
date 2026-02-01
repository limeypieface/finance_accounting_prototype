"""
Adversarial test: Abuse of is_rounding flag on JournalLines.

From the journal.py docstring:
"If multi-currency rounding produces a remainder, exactly one line
must be marked is_rounding=true"

The Attack Vectors:

1. Multiple Rounding Lines:
   Create an entry with TWO lines marked is_rounding=True.
   This violates "exactly one" and could hide manipulation.

2. Large Rounding Amount:
   Create an entry where debits and credits differ by $10,000
   but claim it's "balanced" with a $10,000 rounding line.
   Rounding should be for sub-penny differences from currency
   conversion, not material amounts.

Financial Impact:
- Multiple rounding lines could be used to inject extra amounts
- Large "rounding" lines could hide embezzlement or fraud
- Auditors expect rounding to be immaterial (< $0.01 per line typically)
- A $10,000 "rounding adjustment" is a massive red flag

The invariant should be:
- At most ONE line per entry can have is_rounding=True
- Rounding amounts should be below a configurable threshold
  (typically < 1 minor unit per line, e.g., $0.01 per line)
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    IntentLineSide,
    LedgerIntent,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilderResult,
)
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import (
    MultipleRoundingLinesError,
    RoundingAmountExceededError,
)
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from tests.conftest import make_source_event


def _make_meaning_result(session, actor_id, clock, effective_date, amount=Decimal("100.00")):
    """Create a successful MeaningBuilderResult for testing."""
    source_event_id = uuid4()
    # Create source Event record (FK requirement for JournalEntry)
    make_source_event(session, source_event_id, actor_id, clock, effective_date)
    econ_data = EconomicEventData(
        source_event_id=source_event_id,
        economic_type="test.rounding",
        effective_date=effective_date,
        profile_id="RoundingTest",
        profile_version=1,
        profile_hash=None,
        quantity=amount,
    )
    return MeaningBuilderResult.ok(econ_data), source_event_id


def _make_intent(source_event_id, effective_date, lines, profile_id="RoundingTest"):
    """Create an AccountingIntent with the given lines."""
    return AccountingIntent(
        econ_event_id=uuid4(),
        source_event_id=source_event_id,
        profile_id=profile_id,
        profile_version=1,
        effective_date=effective_date,
        ledger_intents=(
            LedgerIntent(
                ledger_id="GL",
                lines=tuple(lines),
            ),
        ),
        snapshot=AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
        ),
    )


class TestMultipleRoundingLines:
    """
    Test that an entry cannot have multiple lines marked is_rounding=True.
    """

    def test_two_rounding_lines_rejected(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Attempt to post an entry with multiple rounding lines.

        The JournalWriter enforces at most ONE rounding line per entry.
        Two rounding lines should raise MultipleRoundingLinesError.
        """
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        # Create an intent with TWO rounding lines (balanced overall)
        # Debit: $100.01 to CashAsset
        # Credit: $100.00 to SalesRevenue
        # Credit: $0.005 rounding to RoundingExpense (rounding line 1)
        # Credit: $0.005 rounding to RoundingExpense (rounding line 2)
        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("100.01"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                IntentLine(
                    account_role="RoundingExpense",
                    side=IntentLineSide.CREDIT,
                    money=Money.of(Decimal("0.005"), "USD"),
                    is_rounding=True,
                ),
                IntentLine(
                    account_role="RoundingExpense",
                    side=IntentLineSide.CREDIT,
                    money=Money.of(Decimal("0.005"), "USD"),
                    is_rounding=True,
                ),
            ],
        )

        with pytest.raises(MultipleRoundingLinesError):
            interpretation_coordinator.interpret_and_post(
                meaning_result=meaning_result,
                accounting_intent=intent,
                actor_id=test_actor_id,
            )

    def test_single_rounding_line_accepted(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that a single rounding line within threshold is accepted.

        This confirms the invariant only blocks MULTIPLE rounding lines,
        not a single legitimate rounding adjustment.
        """
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today)

        # Single rounding line within threshold
        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("100.003"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                IntentLine(
                    account_role="RoundingExpense",
                    side=IntentLineSide.CREDIT,
                    money=Money.of(Decimal("0.003"), "USD"),
                    is_rounding=True,
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

        # Verify exactly one rounding line exists
        entry_id = result.journal_result.entries[0].entry_id
        rounding_count = session.execute(
            text("""
                SELECT COUNT(*) FROM journal_lines
                WHERE journal_entry_id = :entry_id AND is_rounding = true
            """),
            {"entry_id": str(entry_id)},
        ).scalar()

        assert rounding_count == 1


class TestLargeRoundingAmount:
    """
    Test that rounding amounts must be immaterial.

    Rounding exists for sub-penny differences from currency conversion.
    A $10,000 "rounding adjustment" is not rounding - it's fraud.
    """

    def test_ten_thousand_dollar_rounding_via_direct_line_creation(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Create an entry via coordinator, then check if we can manually
        add a massive "rounding" line.

        The Attack:
        1. Post a normal $10,000 entry
        2. Add a new $10,000 line marked is_rounding=True
        3. If allowed, entry now has hidden $10,000

        Note: This tests whether the DB/ORM blocks direct line injection.
        """
        # First, post a legitimate entry
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today, amount=Decimal("10000.00"))

        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("10000.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("10000.00"), "USD"),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )
        assert result.success
        session.flush()

        # Get the posted entry
        entry_id = result.journal_result.entries[0].entry_id
        entry = session.get(JournalEntry, entry_id)
        assert entry.status == JournalEntryStatus.POSTED

        # THE ATTACK: Try to add a $10,000 "rounding" line to a posted entry
        rounding_account = standard_accounts["rounding"]

        attack_line = JournalLine(
            journal_entry_id=entry.id,
            account_id=rounding_account.id,
            side=LineSide.CREDIT,
            amount=Decimal("10000.00"),  # Massive "rounding"
            currency="USD",
            line_seq=99,
            is_rounding=True,
        )
        session.add(attack_line)

        try:
            session.flush()

            # If we get here, the attack succeeded
            pytest.fail(
                "INVARIANT BROKEN: Added a $10,000 'rounding' line to a POSTED entry. "
                "This should have been blocked by immutability rules."
            )

        except Exception as e:
            # Good - blocked (likely by immutability rules on posted entries)
            session.rollback()

    def test_intent_with_massive_rounding_rejected(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that an AccountingIntent with a massive rounding line is rejected.

        The JournalWriter enforces a rounding threshold: the rounding amount
        must be <= max(0.01, 0.01 * number_of_non_rounding_lines).

        A $10,000 rounding line vastly exceeds any reasonable threshold.
        """
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today, amount=Decimal("20000.00"))

        # Create an intent that's "balanced" only by a massive rounding line
        # Debit: $20,000 to CashAsset
        # Credit: $10,000 to SalesRevenue
        # Credit: $10,000 to RoundingExpense (is_rounding=True) -- fraud!
        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("20000.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("10000.00"), "USD"),
                IntentLine(
                    account_role="RoundingExpense",
                    side=IntentLineSide.CREDIT,
                    money=Money.of(Decimal("10000.00"), "USD"),
                    is_rounding=True,
                ),
            ],
        )

        # The JournalWriter should reject this due to rounding threshold
        with pytest.raises(RoundingAmountExceededError):
            interpretation_coordinator.interpret_and_post(
                meaning_result=meaning_result,
                accounting_intent=intent,
                actor_id=test_actor_id,
            )

    def test_unbalanced_entry_without_rounding_rejected(
        self,
        session,
        interpretation_coordinator: InterpretationCoordinator,
        standard_accounts,
        current_period,
        role_resolver,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that a massively unbalanced entry is rejected outright.

        If the posting pipeline doesn't validate balancing,
        this will create an entry with a material misstatement.
        A $10,000 imbalance should NOT be auto-corrected as 'rounding'.
        """
        today = deterministic_clock.now().date()
        meaning_result, source_id = _make_meaning_result(session, test_actor_id, deterministic_clock, today, amount=Decimal("20000.00"))

        # Intentionally unbalanced by $10,000 - no rounding line
        intent = _make_intent(
            source_id,
            today,
            lines=[
                IntentLine.debit("CashAsset", Decimal("20000.00"), "USD"),
                IntentLine.credit("SalesRevenue", Decimal("10000.00"), "USD"),
            ],
        )

        result = interpretation_coordinator.interpret_and_post(
            meaning_result=meaning_result,
            accounting_intent=intent,
            actor_id=test_actor_id,
        )

        # Should be rejected as unbalanced
        assert not result.success
        assert result.error_code is not None


class TestRoundingThresholdDocumentation:
    """
    Document expected rounding behavior and thresholds.
    """

    def test_legitimate_rounding_amount(self):
        """
        Document what legitimate rounding looks like.

        In multi-currency conversion, rounding occurs when:
        1. USD 100.00 converts to EUR at rate 0.923456
        2. Result: EUR 92.3456
        3. Rounded to: EUR 92.35 (2 decimal places)
        4. Rounding difference: EUR 0.0044

        This sub-penny difference is legitimate rounding.
        """
        # Example: $100 USD to EUR at rate 0.923456
        usd_amount = Decimal("100.00")
        rate = Decimal("0.923456")
        eur_exact = usd_amount * rate  # 92.3456
        eur_rounded = eur_exact.quantize(Decimal("0.01"))  # 92.35
        rounding_diff = abs(eur_exact - eur_rounded)  # 0.0044

        # Legitimate rounding is typically < 0.01 (one minor unit)
        assert rounding_diff < Decimal("0.01"), "Legitimate rounding should be < 1 cent"

        # Document the threshold
        print(f"\n{'='*60}")
        print("LEGITIMATE ROUNDING EXAMPLE")
        print(f"{'='*60}")
        print(f"USD Amount: ${usd_amount}")
        print(f"Exchange Rate: {rate}")
        print(f"EUR Exact: {eur_exact}")
        print(f"EUR Rounded: {eur_rounded}")
        print(f"Rounding Difference: {rounding_diff}")
        print("")
        print("Expected threshold for is_rounding=True lines:")
        print("  - Maximum: $0.01 per non-rounding line in entry")
        print("  - Or: 0.01% of total entry amount")
        print("  - Anything larger is NOT rounding, it's an error or fraud")
        print(f"{'='*60}\n")

    def test_document_rounding_invariants(self):
        """
        Document the rounding invariants that SHOULD be enforced.

        From journal.py docstring:
        "If multi-currency rounding produces a remainder, exactly one line
        must be marked is_rounding=true"

        Implied invariants:
        1. At most ONE line per entry can have is_rounding=True
        2. is_rounding lines should have amount < threshold
        3. Threshold should be: max(0.01, 0.0001 * entry_total)

        These invariants prevent:
        - Multiple hidden rounding lines
        - Large fraudulent "rounding" adjustments
        - Material misstatements disguised as immaterial rounding
        """
        expected_invariants = [
            "At most ONE line per entry can have is_rounding=True",
            "Rounding amount must be < $0.01 per non-rounding line",
            "Rounding amount must be < 0.01% of entry total",
            "Rounding is ONLY for sub-penny currency conversion remainders",
        ]

        for inv in expected_invariants:
            print(f"  EXPECTED INVARIANT: {inv}")
