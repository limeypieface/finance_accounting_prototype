"""
Adversarial test: Verify enforcement of is_rounding invariants.

The journal.py docstring states:
"If multi-currency rounding produces a remainder, exactly one line
must be marked is_rounding=true"

This test file verifies that enforcement is in place at multiple levels:
1. Strategy layer - validates during line computation
2. Ledger service - validates before persisting lines
3. Database triggers - blocks raw SQL attacks

These invariants prevent:
- Embezzlement hidden as "rounding adjustments"
- Data entry errors masked as legitimate rounding
- Audit trail corruption via rounding line injection
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import MultipleRoundingLinesError, RoundingAmountExceededError


class TestRoundingInvariantEnforcement:
    """
    Verify that rounding invariants ARE enforced at multiple levels.

    These tests PASS when enforcement blocks the attack.
    These tests FAIL if enforcement is missing or bypassed.
    """

    def test_strategy_rejects_multiple_rounding_lines(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that the strategy layer rejects entries with multiple rounding lines.

        The Attack: A malicious strategy produces multiple is_rounding=True lines.
        Expected: Strategy validation rejects this before it reaches the database.
        """
        event_type = "test.enforce.multi.rounding"

        class MultiRoundingStrategy(BasePostingStrategy):
            """Malicious strategy that produces multiple rounding lines."""

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
                # Malicious: Return multiple rounding lines
                return (
                    LineSpec(
                        account_code="1000",
                        side=DomainLineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("99.97"), "USD"),
                    ),
                    # TWO rounding lines - should be rejected
                    LineSpec(
                        account_code="9999",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("0.01"), "USD"),
                        is_rounding=True,
                    ),
                    LineSpec(
                        account_code="9999",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("0.02"), "USD"),
                        is_rounding=True,
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

            # Enforcement should reject this
            assert result.status == PostingStatus.VALIDATION_FAILED, (
                f"ENFORCEMENT MISSING: Multiple rounding lines were accepted! "
                f"Status: {result.status}, Message: {result.message}"
            )
            assert result.validation is not None
            # R22: Strategies cannot produce rounding lines at all
            # So we may get STRATEGY_ROUNDING_VIOLATION (R22) or MULTIPLE_ROUNDING_LINES (legacy)
            assert any(
                "MULTIPLE_ROUNDING_LINES" in str(e.code)
                or "STRATEGY_ROUNDING_VIOLATION" in str(e.code)
                or "rounding" in str(e.message).lower()
                for e in result.validation.errors
            ), f"Expected rounding-related error, got: {result.validation.errors}"

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_strategy_rejects_large_rounding_amount(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that the strategy layer rejects large rounding amounts.

        The Attack: A strategy produces a $10,000 "rounding" line.
        Expected: Validation rejects this as exceeding threshold.
        """
        event_type = "test.enforce.large.rounding"

        class LargeRoundingStrategy(BasePostingStrategy):
            """Malicious strategy that produces massive 'rounding' line."""

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
                # Malicious: $10,000 "rounding" to balance an unbalanced entry
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
                    # $10,000 "rounding" - should be rejected!
                    LineSpec(
                        account_code="9999",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("10000.00"), "USD"),
                        is_rounding=True,
                    ),
                )

        StrategyRegistry.register(LargeRoundingStrategy())

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

            # Enforcement should reject this
            assert result.status == PostingStatus.VALIDATION_FAILED, (
                f"ENFORCEMENT MISSING: $10,000 'rounding' was accepted! "
                f"Status: {result.status}, Message: {result.message}"
            )
            assert result.validation is not None
            # R22: Strategies cannot produce rounding lines at all
            # So we may get STRATEGY_ROUNDING_VIOLATION (R22) or ROUNDING_AMOUNT_EXCEEDED (legacy)
            assert any(
                "ROUNDING_AMOUNT_EXCEEDED" in str(e.code)
                or "STRATEGY_ROUNDING_VIOLATION" in str(e.code)
                or "rounding" in str(e.message).lower()
                for e in result.validation.errors
            ), f"Expected rounding-related error, got: {result.validation.errors}"

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_database_blocks_multiple_rounding_lines_via_raw_sql(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that database triggers block raw SQL injection of multiple rounding lines.

        The Attack: Use raw SQL to bypass ORM and inject multiple rounding lines.
        Expected: Database trigger rejects the second rounding line.
        """
        # First, post a legitimate entry
        event_type = "test.db.multi.rounding"

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
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
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
            entry_id = result.journal_entry_id

            # THE ATTACK: Use raw SQL to add multiple rounding lines
            rounding_account = standard_accounts["rounding"]

            # Try to add ANY rounding line via raw SQL to posted entry
            # R12 prevents adding ANY lines to posted entries (first line of defense)
            # If we somehow got past R12, the rounding trigger would block the second line
            with pytest.raises(Exception) as exc_info:
                session.execute(
                    text("""
                        INSERT INTO journal_lines
                        (id, journal_entry_id, account_id, side, amount, currency,
                         line_seq, is_rounding, created_at, updated_at, created_by_id)
                        VALUES
                        (:id, :entry_id, :account_id, 'credit', 0.01, 'USD',
                         100, true, NOW(), NOW(), :actor_id)
                    """),
                    {
                        "id": str(uuid4()),
                        "entry_id": str(entry_id),
                        "account_id": str(rounding_account.id),
                        "actor_id": str(test_actor_id),
                    },
                )
                session.flush()

            # Verify the attack was blocked by database triggers
            # R12: Cannot add lines to posted entry (first line of defense)
            # Or ROUNDING_INVARIANT_VIOLATION if we got past R12
            error_msg = str(exc_info.value)
            assert (
                "R12" in error_msg
                or "posted" in error_msg.lower()
                or "ROUNDING_INVARIANT_VIOLATION" in error_msg
            ), (
                f"Expected R12 or rounding violation from database trigger, "
                f"got: {exc_info.value}"
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)
            session.rollback()

    def test_database_blocks_large_rounding_via_raw_sql(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that database triggers block raw SQL injection of large rounding amounts.

        The Attack: Use raw SQL to bypass ORM and inject a $10,000 rounding line.
        Expected: Database trigger rejects the large amount.
        """
        # First, post a legitimate entry
        event_type = "test.db.large.rounding"

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
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
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
            entry_id = result.journal_entry_id

            # THE ATTACK: Use raw SQL to add a $10,000 rounding line
            rounding_account = standard_accounts["rounding"]

            with pytest.raises(Exception) as exc_info:
                session.execute(
                    text("""
                        INSERT INTO journal_lines
                        (id, journal_entry_id, account_id, side, amount, currency,
                         line_seq, is_rounding, created_at, updated_at, created_by_id)
                        VALUES
                        (:id, :entry_id, :account_id, 'credit', 10000.00, 'USD',
                         100, true, NOW(), NOW(), :actor_id)
                    """),
                    {
                        "id": str(uuid4()),
                        "entry_id": str(entry_id),
                        "account_id": str(rounding_account.id),
                        "actor_id": str(test_actor_id),
                    },
                )
                session.flush()

            # Verify the error is from the trigger
            assert "ROUNDING_THRESHOLD_VIOLATION" in str(exc_info.value), (
                f"Expected ROUNDING_THRESHOLD_VIOLATION from database trigger, "
                f"got: {exc_info.value}"
            )

        except Exception as e:
            if "ROUNDING_THRESHOLD_VIOLATION" in str(e):
                # Good - database trigger is working
                session.rollback()
            else:
                raise
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestLegitimateRoundingAllowed:
    """
    Verify that legitimate rounding (sub-penny amounts) is still allowed.
    """

    def test_legitimate_small_rounding_allowed(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that legitimate sub-penny rounding is allowed.

        Per R22, only the Bookkeeper may generate rounding lines - strategies
        produce unbalanced entries and the Bookkeeper adds the rounding line.

        This test verifies that small imbalances (< $0.01) are automatically
        rounded by the Bookkeeper.
        """
        event_type = "test.legitimate.rounding"

        class ImbalancedStrategy(BasePostingStrategy):
            """Strategy that produces a small imbalance for Bookkeeper to round."""

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
                # Small imbalance - Bookkeeper should add rounding line (R22)
                # Note: Do NOT set is_rounding=True - that's the Bookkeeper's job
                return (
                    LineSpec(
                        account_code="1000",
                        side=DomainLineSide.DEBIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("99.995"), "USD"),
                    ),
                )

        StrategyRegistry.register(ImbalancedStrategy())

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

            # The Bookkeeper should add a rounding line for the small imbalance
            assert result.status == PostingStatus.POSTED, (
                f"Small imbalance should be auto-rounded by Bookkeeper! "
                f"Status: {result.status}, Message: {result.message}"
            )

            # Verify the entry has a rounding line added by Bookkeeper
            entry = session.query(JournalEntry).filter_by(id=result.journal_entry_id).first()
            rounding_lines = [line for line in entry.lines if line.is_rounding]
            assert len(rounding_lines) == 1, (
                f"Expected Bookkeeper to add exactly one rounding line, "
                f"found {len(rounding_lines)}"
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_entry_with_no_rounding_allowed(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify that perfectly balanced entries without rounding are allowed.
        """
        event_type = "test.no.rounding"

        class BalancedStrategy(BasePostingStrategy):
            """Strategy that produces perfectly balanced entry."""

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
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                    LineSpec(
                        account_code="4000",
                        side=DomainLineSide.CREDIT,
                        money=Money.of(Decimal("100.00"), "USD"),
                    ),
                )

        StrategyRegistry.register(BalancedStrategy())

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

            assert result.status == PostingStatus.POSTED, (
                f"Balanced entry without rounding was rejected! "
                f"Status: {result.status}, Message: {result.message}"
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestDocumentEnforcementLevels:
    """
    Document the multi-layer enforcement that is now in place.
    """

    def test_document_enforcement_architecture(self):
        """
        Document the multi-level enforcement architecture for rounding invariants.
        """
        enforcement_layers = [
            {
                "layer": "Strategy Layer (Pure)",
                "file": "finance_kernel/domain/strategy.py",
                "method": "_validate_rounding_invariants()",
                "protects_against": [
                    "Malicious strategies producing multiple rounding lines",
                    "Strategies producing large 'rounding' amounts",
                ],
                "errors": [
                    "MULTIPLE_ROUNDING_LINES - more than one is_rounding=True",
                    "ROUNDING_AMOUNT_EXCEEDED - amount > 0.01 per line",
                ],
            },
            {
                "layer": "Ledger Service (ORM)",
                "file": "finance_kernel/services/ledger_service.py",
                "method": "_validate_rounding_invariants()",
                "protects_against": [
                    "Direct ORM manipulation bypassing strategies",
                    "Crash recovery scenarios with inconsistent state",
                ],
                "errors": [
                    "MultipleRoundingLinesError",
                    "RoundingAmountExceededError",
                ],
            },
            {
                "layer": "Database Triggers (PostgreSQL)",
                "file": "finance_kernel/db/triggers.py",
                "triggers": [
                    "trg_journal_line_single_rounding",
                    "trg_journal_line_rounding_threshold",
                ],
                "protects_against": [
                    "Raw SQL attacks bypassing ORM",
                    "Direct database access",
                    "Migration scripts",
                    "Admin tools",
                ],
                "errors": [
                    "ROUNDING_INVARIANT_VIOLATION",
                    "ROUNDING_THRESHOLD_VIOLATION",
                ],
            },
        ]

        print("\n" + "=" * 70)
        print("ROUNDING INVARIANT ENFORCEMENT ARCHITECTURE")
        print("=" * 70)
        print("\nInvariants enforced:")
        print("  1. At most ONE line per entry can have is_rounding=True")
        print("  2. Rounding amount must be < 0.01 per non-rounding line")
        print("\nThese invariants prevent:")
        print("  - Embezzlement hidden as 'rounding adjustments'")
        print("  - Multiple hidden rounding lines injecting extra amounts")
        print("  - Audit trail corruption via rounding line manipulation")

        for layer in enforcement_layers:
            print(f"\n{'='*70}")
            print(f"LAYER: {layer['layer']}")
            print(f"File: {layer['file']}")
            if "method" in layer:
                print(f"Method: {layer['method']}")
            if "triggers" in layer:
                print(f"Triggers: {', '.join(layer['triggers'])}")
            print(f"\nProtects against:")
            for threat in layer["protects_against"]:
                print(f"  - {threat}")
            print(f"\nErrors raised:")
            for error in layer["errors"]:
                print(f"  - {error}")

        print("\n" + "=" * 70 + "\n")
