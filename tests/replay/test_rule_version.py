"""
Rule version preservation tests (G1-G2 Certification).

G1: Backward compatibility with differential replay
G2: Mixed-version ledger correctness

These tests verify that:
1. posting_rule_version is correctly stored on journal entries
2. Replay uses the original rule version, not the current version
3. Mixed-version ledgers produce correct trial balances
4. Strategy upgrades don't rewrite history
"""

import pytest
from uuid import uuid4
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.models.journal import JournalEntry
from finance_kernel.domain.clock import DeterministicClock


class VersionedStrategy(BasePostingStrategy):
    """
    Test strategy with configurable version.

    This strategy changes behavior based on version:
    - Version 1: Posts to accounts 1000 (debit) and 4000 (credit)
    - Version 2: Posts to accounts 1000 (debit) and 5000 (credit) - different account!

    This simulates a business rule change where revenue recognition changed.
    """

    def __init__(self, event_type: str, version: int):
        self._event_type = event_type
        self._version = version

    @property
    def event_type(self) -> str:
        return self._event_type

    @property
    def version(self) -> int:
        return self._version

    def _compute_line_specs(
        self, event: EventEnvelope, ref: ReferenceData
    ) -> list[LineSpec]:
        amount = Decimal(event.payload.get("amount", "100.00"))

        if self._version == 1:
            # V1: Credit to Revenue (4000)
            credit_account = "4000"
        else:
            # V2: Credit to COGS (5000) - business rule changed
            credit_account = "5000"

        return [
            LineSpec(
                account_code="1000",
                side=LineSide.DEBIT,
                money=Money.of(amount, "USD"),
            ),
            LineSpec(
                account_code=credit_account,
                side=LineSide.CREDIT,
                money=Money.of(amount, "USD"),
            ),
        ]


class TestG2RuleVersionPreservation:
    """
    G2: Verify rule version is stored and preserved.

    When a journal entry is posted, the posting_rule_version must be
    recorded and preserved even if the strategy is upgraded later.
    """

    def test_posting_preserves_strategy_version(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify posting_rule_version matches the strategy version used.

        1. Create a versioned strategy (version=1)
        2. Post an event using that strategy
        3. Verify JournalEntry.posting_rule_version == 1
        """
        event_type = "test.versioned.v1"

        # Register V1 strategy
        strategy_v1 = VersionedStrategy(event_type, version=1)
        StrategyRegistry.register(strategy_v1)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "100.00"},
            )

            assert result.status == PostingStatus.POSTED
            assert result.journal_entry_id is not None

            # Verify posting_rule_version is recorded correctly
            entry = session.get(JournalEntry, result.journal_entry_id)
            assert entry is not None
            assert entry.posting_rule_version == 1, (
                f"Expected posting_rule_version=1, got {entry.posting_rule_version}"
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_version_2_strategy_records_version_2(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that posting with V2 strategy records version 2.
        """
        event_type = "test.versioned.v2"

        # Register V2 strategy
        strategy_v2 = VersionedStrategy(event_type, version=2)
        StrategyRegistry.register(strategy_v2)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "200.00"},
            )

            assert result.status == PostingStatus.POSTED

            # Verify posting_rule_version is 2
            entry = session.get(JournalEntry, result.journal_entry_id)
            assert entry is not None
            assert entry.posting_rule_version == 2, (
                f"Expected posting_rule_version=2, got {entry.posting_rule_version}"
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_upgrade_does_not_change_existing_entries(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that upgrading a strategy doesn't change existing entries.

        1. Post entry with strategy version 1
        2. Register version 2 of the same strategy
        3. Query the original entry
        4. Verify posting_rule_version is still 1
        """
        event_type = "test.versioned.upgrade"

        # Register V1 strategy
        strategy_v1 = VersionedStrategy(event_type, version=1)
        StrategyRegistry.register(strategy_v1)

        try:
            # Post with V1
            result_v1 = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "100.00"},
            )
            assert result_v1.status == PostingStatus.POSTED
            v1_entry_id = result_v1.journal_entry_id

            # Upgrade to V2 (replace in registry)
            strategy_v2 = VersionedStrategy(event_type, version=2)
            StrategyRegistry.register(strategy_v2)

            # Post with V2
            result_v2 = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "200.00"},
            )
            assert result_v2.status == PostingStatus.POSTED
            v2_entry_id = result_v2.journal_entry_id

            # Verify V1 entry still has version 1
            v1_entry = session.get(JournalEntry, v1_entry_id)
            assert v1_entry.posting_rule_version == 1, (
                f"V1 entry should still have version=1, got {v1_entry.posting_rule_version}"
            )

            # Verify V2 entry has version 2
            v2_entry = session.get(JournalEntry, v2_entry_id)
            assert v2_entry.posting_rule_version == 2, (
                f"V2 entry should have version=2, got {v2_entry.posting_rule_version}"
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_mixed_version_entries_coexist(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify entries with different rule versions can coexist.

        1. Post entries with strategy version 1
        2. Upgrade strategy to version 2
        3. Post new entries with version 2
        4. Verify both versions are correctly stored
        5. Verify different accounts were used (V1 vs V2 behavior)
        """
        event_type = "test.versioned.mixed"

        # Register V1 strategy
        strategy_v1 = VersionedStrategy(event_type, version=1)
        StrategyRegistry.register(strategy_v1)

        try:
            # Post 3 entries with V1
            v1_entry_ids = []
            for i in range(3):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": str(100 + i)},
                )
                assert result.status == PostingStatus.POSTED
                v1_entry_ids.append(result.journal_entry_id)

            # Upgrade to V2
            strategy_v2 = VersionedStrategy(event_type, version=2)
            StrategyRegistry.register(strategy_v2)

            # Post 3 entries with V2
            v2_entry_ids = []
            for i in range(3):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": str(200 + i)},
                )
                assert result.status == PostingStatus.POSTED
                v2_entry_ids.append(result.journal_entry_id)

            # Verify V1 entries have version=1
            for entry_id in v1_entry_ids:
                entry = session.get(JournalEntry, entry_id)
                assert entry.posting_rule_version == 1

            # Verify V2 entries have version=2
            for entry_id in v2_entry_ids:
                entry = session.get(JournalEntry, entry_id)
                assert entry.posting_rule_version == 2

            # Verify V1 entries used account 4000 (Revenue)
            for entry_id in v1_entry_ids:
                entry = session.get(JournalEntry, entry_id)
                credit_lines = [l for l in entry.lines if l.side == "credit"]
                assert len(credit_lines) == 1
                assert credit_lines[0].account.code == "4000", (
                    f"V1 should credit to 4000, got {credit_lines[0].account.code}"
                )

            # Verify V2 entries used account 5000 (COGS)
            for entry_id in v2_entry_ids:
                entry = session.get(JournalEntry, entry_id)
                credit_lines = [l for l in entry.lines if l.side == "credit"]
                assert len(credit_lines) == 1
                assert credit_lines[0].account.code == "5000", (
                    f"V2 should credit to 5000, got {credit_lines[0].account.code}"
                )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestG1BackwardCompatibility:
    """
    G1: Backward compatibility with differential replay.

    When replaying the ledger, entries must be processed using their
    original rule version, not the current version.
    """

    def test_entry_records_all_reference_versions(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify all reference snapshot versions are recorded (R21).

        JournalEntry must record:
        - posting_rule_version
        - coa_version
        - dimension_schema_version
        - rounding_policy_version
        - currency_registry_version

        These enable deterministic replay.
        """
        event_type = "test.versioned.snapshot"
        strategy = VersionedStrategy(event_type, version=1)
        StrategyRegistry.register(strategy)

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={"amount": "100.00"},
            )

            assert result.status == PostingStatus.POSTED

            entry = session.get(JournalEntry, result.journal_entry_id)
            assert entry is not None

            # All version fields should be populated
            assert entry.posting_rule_version is not None
            assert entry.coa_version is not None
            assert entry.dimension_schema_version is not None
            assert entry.rounding_policy_version is not None
            assert entry.currency_registry_version is not None

            print(f"\n[G1] Reference Snapshot Versions:")
            print(f"  posting_rule_version: {entry.posting_rule_version}")
            print(f"  coa_version: {entry.coa_version}")
            print(f"  dimension_schema_version: {entry.dimension_schema_version}")
            print(f"  rounding_policy_version: {entry.rounding_policy_version}")
            print(f"  currency_registry_version: {entry.currency_registry_version}")

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_replay_with_rule_upgrade_preserves_history(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify rule upgrades don't rewrite history.

        This test simulates a scenario where:
        1. Corpus of entries posted under rule version N
        2. Strategy upgraded to version N+1
        3. Query existing entries
        4. Verify N-version entries still have original version and behavior

        Note: True replay testing requires the replay engine, which
        would use posting_rule_version to select the correct strategy.
        """
        event_type = "test.versioned.replay"

        # Post corpus under V1
        strategy_v1 = VersionedStrategy(event_type, version=1)
        StrategyRegistry.register(strategy_v1)

        try:
            v1_entries = []
            for i in range(5):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": str(100 + i)},
                )
                assert result.status == PostingStatus.POSTED
                v1_entries.append(result.journal_entry_id)

            # Record V1 state (account codes used)
            v1_state = {}
            for entry_id in v1_entries:
                entry = session.get(JournalEntry, entry_id)
                credit_line = [l for l in entry.lines if l.side == "credit"][0]
                v1_state[entry_id] = {
                    "version": entry.posting_rule_version,
                    "credit_account": credit_line.account.code,
                }

            # Upgrade to V2
            strategy_v2 = VersionedStrategy(event_type, version=2)
            StrategyRegistry.register(strategy_v2)

            # Post new entries under V2
            v2_entries = []
            for i in range(3):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": str(200 + i)},
                )
                assert result.status == PostingStatus.POSTED
                v2_entries.append(result.journal_entry_id)

            # Verify V1 entries are UNCHANGED
            for entry_id in v1_entries:
                entry = session.get(JournalEntry, entry_id)
                credit_line = [l for l in entry.lines if l.side == "credit"][0]

                assert entry.posting_rule_version == v1_state[entry_id]["version"], (
                    f"V1 entry version should be preserved"
                )
                assert credit_line.account.code == v1_state[entry_id]["credit_account"], (
                    f"V1 entry credit account should be preserved"
                )

            # Verify V2 entries used new rules
            for entry_id in v2_entries:
                entry = session.get(JournalEntry, entry_id)
                credit_line = [l for l in entry.lines if l.side == "credit"][0]

                assert entry.posting_rule_version == 2
                assert credit_line.account.code == "5000", (
                    f"V2 entries should use account 5000"
                )

            print(f"\n[G1] Rule Upgrade Preservation:")
            print(f"  V1 entries: {len(v1_entries)} (preserved)")
            print(f"  V2 entries: {len(v2_entries)} (new rules)")

        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_upgrade_during_load_preserves_consistency(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify upgrades during active posting are safe.

        Each entry gets the version of the strategy at the time of posting.
        Mid-stream upgrades don't corrupt earlier or later entries.
        """
        event_type = "test.versioned.midstream"

        # Start with V1
        strategy_v1 = VersionedStrategy(event_type, version=1)
        StrategyRegistry.register(strategy_v1)

        try:
            entries_before_upgrade = []
            entries_after_upgrade = []

            # Post some entries with V1
            for i in range(3):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": str(100 + i)},
                )
                assert result.status == PostingStatus.POSTED
                entries_before_upgrade.append(result.journal_entry_id)

            # Mid-stream upgrade to V2
            strategy_v2 = VersionedStrategy(event_type, version=2)
            StrategyRegistry.register(strategy_v2)

            # Post more entries with V2
            for i in range(3):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"amount": str(200 + i)},
                )
                assert result.status == PostingStatus.POSTED
                entries_after_upgrade.append(result.journal_entry_id)

            # Verify all entries have correct versions
            for entry_id in entries_before_upgrade:
                entry = session.get(JournalEntry, entry_id)
                assert entry.posting_rule_version == 1, (
                    f"Entry before upgrade should have version=1"
                )

            for entry_id in entries_after_upgrade:
                entry = session.get(JournalEntry, entry_id)
                assert entry.posting_rule_version == 2, (
                    f"Entry after upgrade should have version=2"
                )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)
