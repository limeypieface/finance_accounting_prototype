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

import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money


class TestMultipleRoundingLines:
    """
    Test that an entry cannot have multiple lines marked is_rounding=True.
    """

    def test_two_rounding_lines_via_posting_orchestrator(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Attempt to post an entry that results in multiple rounding lines.

        The posting workflow should ensure at most ONE rounding line.
        """
        # Use a strategy that produces an unbalanced result requiring rounding
        event_type = "test.multi.rounding.check"

        class MultiRoundingStrategy(BasePostingStrategy):
            def __init__(self):
                self._event_type = event_type
                self._version = 1

            @property
            def event_type(self) -> str:
                return self._event_type

            @property
            def version(self) -> int:
                return self._version

            def _compute_line_specs(
                self, event: EventEnvelope, ref: ReferenceData
            ) -> tuple[LineSpec, ...]:
                # Return lines that need rounding
                return (
                    LineSpec(
                        account_code="1000",
                        side=DomainLineSide.DEBIT,
                        money=Money.of(Decimal("100.001"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                )

        StrategyRegistry.register(MultiRoundingStrategy())

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )

            if result.status == PostingStatus.POSTED:
                # Check how many rounding lines exist
                rounding_count = session.execute(
                    text("""
                        SELECT COUNT(*) FROM journal_lines
                        WHERE journal_entry_id = :entry_id AND is_rounding = true
                    """),
                    {"entry_id": str(result.journal_entry_id)},
                ).scalar()

                # Document the result
                assert rounding_count <= 1, (
                    f"INVARIANT CHECK: Entry has {rounding_count} rounding lines. "
                    f"Expected at most 1."
                )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestLargeRoundingAmount:
    """
    Test that rounding amounts must be immaterial.

    Rounding exists for sub-penny differences from currency conversion.
    A $10,000 "rounding adjustment" is not rounding - it's fraud.
    """

    def test_ten_thousand_dollar_rounding_via_direct_line_creation(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Create an entry via orchestrator, then check if we can manually
        add a massive "rounding" line.

        The Attack:
        1. Post a normal $10,000 entry
        2. Add a new $10,000 line marked is_rounding=True
        3. If allowed, entry now has hidden $10,000

        Note: This tests whether the DB/ORM blocks direct line injection.
        """
        # First, post a legitimate entry
        event_type = "test.large.rounding"

        class SimpleStrategy(BasePostingStrategy):
            def __init__(self):
                self._event_type = event_type
                self._version = 1

            @property
            def event_type(self) -> str:
                return self._event_type

            @property
            def version(self) -> int:
                return self._version

            def _compute_line_specs(
                self, event: EventEnvelope, ref: ReferenceData
            ) -> tuple[LineSpec, ...]:
                return (
                    LineSpec(
                        account_code="1000",
                        side=DomainLineSide.DEBIT,
                        money=Money.of(Decimal("10000.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("10000.00"), "USD"),
                    ),
                )

        StrategyRegistry.register(SimpleStrategy())

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )

            assert result.status == PostingStatus.POSTED

            # Get the posted entry
            entry = session.get(JournalEntry, result.journal_entry_id)
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

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_strategy_producing_large_rounding(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test a strategy that produces a massive imbalance requiring "rounding".

        If the posting orchestrator doesn't validate rounding thresholds,
        this will create an entry with material misstatement.
        """
        event_type = "test.massive.imbalance"

        class MassiveImbalanceStrategy(BasePostingStrategy):
            """Strategy that produces $10,000 imbalance."""

            def __init__(self):
                self._event_type = event_type
                self._version = 1

            @property
            def event_type(self) -> str:
                return self._event_type

            @property
            def version(self) -> int:
                return self._version

            def _compute_line_specs(
                self, event: EventEnvelope, ref: ReferenceData
            ) -> tuple[LineSpec, ...]:
                # Intentionally unbalanced by $10,000
                return (
                    LineSpec(
                        account_code="1000",
                        side=DomainLineSide.DEBIT,
                        money=Money.of(Decimal("20000.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("10000.00"), "USD"),
                    ),
                )

        StrategyRegistry.register(MassiveImbalanceStrategy())

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )

            if result.status == PostingStatus.POSTED:
                # Check if a $10,000 rounding line was created
                rounding_lines = session.execute(
                    text("""
                        SELECT amount FROM journal_lines
                        WHERE journal_entry_id = :entry_id AND is_rounding = true
                    """),
                    {"entry_id": str(result.journal_entry_id)},
                ).fetchall()

                if rounding_lines:
                    for (amount,) in rounding_lines:
                        if amount >= Decimal("1.00"):
                            pytest.fail(
                                f"INVARIANT BROKEN: Entry posted with ${amount} 'rounding' line.\n"
                                f"A $10,000 imbalance should NOT be auto-corrected as 'rounding'.\n"
                                f"Rounding is for sub-penny currency conversion differences only.\n"
                                f"This should have been rejected as UNBALANCED_ENTRY."
                            )

            elif result.status == PostingStatus.VALIDATION_FAILED:
                # Good - the imbalance was rejected
                pass
            else:
                # Document the result
                print(f"Posting result: {result.status}, message: {result.message}")

        finally:
            StrategyRegistry._strategies.pop(event_type, None)


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
        print(f"")
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

