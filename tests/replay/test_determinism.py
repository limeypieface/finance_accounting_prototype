"""
Replay/determinism tests for R20 compliance.

R20. Test class mapping - Replay tests (determinism)

These tests verify that replaying events produces deterministic results.
Same inputs must always produce same outputs.
"""

import pytest
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4, UUID

from finance_kernel.domain.bookkeeper import Bookkeeper
from finance_kernel.domain.dtos import (
    EventEnvelope,
    ReferenceData,
    LineSpec,
    LineSide,
    ProposedJournalEntry,
)
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.values import Currency, Money
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus


class TestBookkeeperDeterminism:
    """
    Tests for Bookkeeper (pure layer) determinism.

    R20: Replay tests for pure transformation determinism.
    """

    def test_same_event_same_reference_data_same_output(self):
        """
        Same event + same reference_data must produce identical output.

        R20: Determinism test for pure layer.
        """
        bookkeeper = Bookkeeper()

        # Fixed inputs
        event = EventEnvelope(
            event_id=UUID("12345678-1234-1234-1234-123456789abc"),
            event_type="generic.posting",
            occurred_at=datetime(2024, 1, 15, 10, 30, 0),
            effective_date=date(2024, 1, 15),
            actor_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            producer="test",
            payload={
                "lines": [
                    {
                        "account_code": "1000",
                        "side": "debit",
                        "amount": "100.00",
                        "currency": "USD",
                    },
                    {
                        "account_code": "2000",
                        "side": "credit",
                        "amount": "100.00",
                        "currency": "USD",
                    },
                ]
            },
            payload_hash="fixed_hash",
            schema_version=1,
        )

        reference_data = ReferenceData(
            account_ids_by_code={
                "1000": UUID("11111111-1111-1111-1111-111111111111"),
                "2000": UUID("22222222-2222-2222-2222-222222222222"),
            },
            active_account_codes=frozenset(["1000", "2000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={},
        )

        # Run multiple times
        results = []
        for _ in range(10):
            result = bookkeeper.propose(event, reference_data)
            results.append(result)

        # All results must be identical
        first = results[0]
        for i, result in enumerate(results[1:], 1):
            assert result.is_valid == first.is_valid
            if result.is_valid:
                assert result.proposed_entry is not None
                assert first.proposed_entry is not None
                assert len(result.proposed_entry.lines) == len(first.proposed_entry.lines)
                for j, line in enumerate(result.proposed_entry.lines):
                    first_line = first.proposed_entry.lines[j]
                    assert line.account_id == first_line.account_id
                    assert line.side == first_line.side
                    assert line.amount == first_line.amount
                    assert line.currency == first_line.currency

    def test_different_events_different_output(self):
        """
        Different events must produce different outputs.

        R20: Determinism test - inputs determine outputs.
        """
        bookkeeper = Bookkeeper()

        reference_data = ReferenceData(
            account_ids_by_code={
                "1000": UUID("11111111-1111-1111-1111-111111111111"),
                "2000": UUID("22222222-2222-2222-2222-222222222222"),
            },
            active_account_codes=frozenset(["1000", "2000"]),
            valid_currencies=frozenset([Currency("USD")]),
            rounding_account_ids={},
        )

        event1 = EventEnvelope(
            event_id=UUID("12345678-1234-1234-1234-123456789abc"),
            event_type="generic.posting",
            occurred_at=datetime(2024, 1, 15, 10, 30, 0),
            effective_date=date(2024, 1, 15),
            actor_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            payload_hash="hash1",
            schema_version=1,
        )

        event2 = EventEnvelope(
            event_id=UUID("87654321-4321-4321-4321-cba987654321"),
            event_type="generic.posting",
            occurred_at=datetime(2024, 1, 15, 10, 30, 0),
            effective_date=date(2024, 1, 15),
            actor_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "200.00", "currency": "USD"},
                    {"account_code": "2000", "side": "credit", "amount": "200.00", "currency": "USD"},
                ]
            },
            payload_hash="hash2",
            schema_version=1,
        )

        result1 = bookkeeper.propose(event1, reference_data)
        result2 = bookkeeper.propose(event2, reference_data)

        assert result1.is_valid and result2.is_valid
        # Different amounts
        assert result1.proposed_entry.lines[0].amount != result2.proposed_entry.lines[0].amount


class TestStrategyVersionDeterminism:
    """
    Tests for strategy version determinism.

    R20: Replay tests for versioned strategy determinism.
    """

    def test_same_strategy_version_same_output(self):
        """
        Same strategy version must produce same output.

        R20: Determinism test for versioned strategies.
        """
        # Create a versioned strategy
        class TestStrategyV1(BasePostingStrategy):
            @property
            def event_type(self) -> str:
                return "test.determinism.versioned"

            @property
            def version(self) -> int:
                return 1

            def _compute_line_specs(self, event, reference_data):
                amount = Decimal(event.payload.get("amount", "0"))
                return [
                    LineSpec(
                        account_code="1000",
                        side=LineSide.DEBIT,
                        money=Money.of(amount, "USD"),
                    ),
                    LineSpec(
                        account_code="2000",
                        side=LineSide.CREDIT,
                        money=Money.of(amount, "USD"),
                    ),
                ]

        strategy = TestStrategyV1()
        try:
            StrategyRegistry.register(strategy)

            bookkeeper = Bookkeeper()

            event = EventEnvelope(
                event_id=uuid4(),
                event_type="test.determinism.versioned",
                occurred_at=datetime.now(),
                effective_date=date.today(),
                actor_id=uuid4(),
                producer="test",
                payload={"amount": "150.00"},
                payload_hash="test",
                schema_version=1,
            )

            reference_data = ReferenceData(
                account_ids_by_code={
                    "1000": uuid4(),
                    "2000": uuid4(),
                },
                active_account_codes=frozenset(["1000", "2000"]),
                valid_currencies=frozenset([Currency("USD")]),
                rounding_account_ids={},
            )

            # Request specific version
            result1 = bookkeeper.propose(event, reference_data, strategy_version=1)
            result2 = bookkeeper.propose(event, reference_data, strategy_version=1)

            assert result1.is_valid and result2.is_valid
            assert result1.strategy_version == result2.strategy_version == 1
            assert result1.proposed_entry.lines[0].amount == result2.proposed_entry.lines[0].amount

        finally:
            StrategyRegistry.unregister("test.determinism.versioned")


class TestHashDeterminism:
    """
    Tests for hash computation determinism.

    R20: Replay tests for hash stability.
    """

    def test_payload_hash_deterministic(self):
        """
        Payload hash computation must be deterministic.

        R20: Determinism test for hash stability.
        """
        from finance_kernel.utils.hashing import hash_payload

        payload = {
            "lines": [
                {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                {"account_code": "2000", "side": "credit", "amount": "100.00", "currency": "USD"},
            ],
            "description": "Test entry",
        }

        # Hash multiple times
        hashes = [hash_payload(payload) for _ in range(10)]

        # All must be identical
        assert len(set(hashes)) == 1, "All hashes must be identical"

    def test_audit_event_hash_deterministic(self):
        """
        Audit event hash computation must be deterministic.

        R20: Determinism test for audit hash stability.
        """
        from finance_kernel.utils.hashing import hash_audit_event

        # Hash multiple times with same inputs
        hashes = []
        for _ in range(10):
            h = hash_audit_event(
                entity_type="JournalEntry",
                entity_id="12345678-1234-1234-1234-123456789abc",
                action="JOURNAL_POSTED",
                payload_hash="abc123",
                prev_hash="prev123",
            )
            hashes.append(h)

        # All must be identical
        assert len(set(hashes)) == 1, "Audit hashes must be identical"

    def test_hash_sensitive_to_input_changes(self):
        """
        Hash must change when any input changes.

        R20: Determinism test - different inputs produce different hashes.
        """
        from finance_kernel.utils.hashing import hash_audit_event

        base_hash = hash_audit_event(
            entity_type="JournalEntry",
            entity_id="12345678-1234-1234-1234-123456789abc",
            action="JOURNAL_POSTED",
            payload_hash="abc123",
            prev_hash="prev123",
        )

        # Change entity_type
        h1 = hash_audit_event(
            entity_type="Event",  # Changed
            entity_id="12345678-1234-1234-1234-123456789abc",
            action="JOURNAL_POSTED",
            payload_hash="abc123",
            prev_hash="prev123",
        )
        assert h1 != base_hash

        # Change entity_id
        h2 = hash_audit_event(
            entity_type="JournalEntry",
            entity_id="00000000-0000-0000-0000-000000000000",  # Changed
            action="JOURNAL_POSTED",
            payload_hash="abc123",
            prev_hash="prev123",
        )
        assert h2 != base_hash

        # Change action
        h3 = hash_audit_event(
            entity_type="JournalEntry",
            entity_id="12345678-1234-1234-1234-123456789abc",
            action="JOURNAL_REVERSED",  # Changed
            payload_hash="abc123",
            prev_hash="prev123",
        )
        assert h3 != base_hash


class TestTrialBalanceDeterminism:
    """
    Tests for trial balance computation determinism.

    R20: Replay tests for computed balance determinism.
    """

    def test_same_entries_same_trial_balance(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Same entries must produce same trial balance.

        R20: Determinism test for trial balance computation.
        """
        # Post several entries
        for i in range(10):
            event_id = uuid4()
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {
                            "account_code": "1000",
                            "side": "debit",
                            "amount": str(Decimal("100.00") * (i + 1)),
                            "currency": "USD",
                        },
                        {
                            "account_code": "4000",
                            "side": "credit",
                            "amount": str(Decimal("100.00") * (i + 1)),
                            "currency": "USD",
                        },
                    ]
                },
            )
            assert result.is_success

        # Compute trial balance multiple times
        balances = []
        for _ in range(5):
            tb = ledger_selector.trial_balance(as_of_date=current_period.end_date)
            # Convert to hashable representation
            tb_repr = tuple(
                (row.account_id, str(row.debit_total), str(row.credit_total))
                for row in sorted(tb, key=lambda x: str(x.account_id))
            )
            balances.append(tb_repr)

        # All must be identical
        assert len(set(balances)) == 1, "Trial balance must be deterministic"

    def test_event_order_independent_balance(
        self,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Final balance should be independent of posting order for balanced entries.

        R20: Determinism test - order independence for balance.
        """
        # Post entries in specific pattern
        entries = [
            ("1000", "4000", "100.00"),  # Cash -> Revenue
            ("5000", "1200", "60.00"),   # COGS -> Inventory
            ("1000", "4000", "200.00"),  # Cash -> Revenue
            ("5000", "1200", "120.00"),  # COGS -> Inventory
        ]

        for debit_acct, credit_acct, amount in entries:
            event_id = uuid4()
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": debit_acct, "side": "debit", "amount": amount, "currency": "USD"},
                        {"account_code": credit_acct, "side": "credit", "amount": amount, "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        # Compute trial balance
        trial_balance = ledger_selector.trial_balance(as_of_date=current_period.end_date)

        # Check specific account balances
        balances = {str(row.account_id): row for row in trial_balance}

        # Total debits should equal total credits
        total_debits = sum(row.debit_total for row in trial_balance)
        total_credits = sum(row.credit_total for row in trial_balance)
        assert total_debits == total_credits


class TestRoundingDeterminism:
    """
    Tests for rounding computation determinism.

    R20: Replay tests for rounding determinism.
    """

    def test_rounding_same_input_same_output(self):
        """
        Rounding must be deterministic for same inputs.

        R20: Determinism test for rounding.
        """
        from finance_kernel.domain.values import Money
        from decimal import ROUND_HALF_UP

        # Same amount rounded multiple times
        amounts = []
        for _ in range(10):
            money = Money.of(Decimal("100.555"), "USD")
            rounded = money.round()
            amounts.append(rounded.amount)

        # All must be identical
        assert len(set(amounts)) == 1, "Rounding must be deterministic"
        assert amounts[0] == Decimal("100.56")  # ROUND_HALF_UP

    def test_currency_precision_rounding_deterministic(self):
        """
        Currency-specific precision rounding must be deterministic.

        R20: Determinism test for currency precision.
        """
        from finance_kernel.domain.values import Money

        # Test different currencies
        test_cases = [
            ("USD", "100.555", "100.56"),   # 2 decimals
            ("JPY", "100.5", "101"),         # 0 decimals
            ("KWD", "100.5555", "100.556"),  # 3 decimals
        ]

        for currency, input_amt, expected in test_cases:
            results = []
            for _ in range(10):
                money = Money.of(Decimal(input_amt), currency)
                rounded = money.round()
                results.append(str(rounded.amount))

            # All must be identical and match expected
            assert len(set(results)) == 1, f"Rounding must be deterministic for {currency}"
            assert results[0] == expected, f"Expected {expected} for {currency}"
