"""
B1-B2: Fault Injection and Crash Recovery Tests.

Financial systems must be resilient to crashes at any point.
These tests verify that:

1. Partial writes are prevented (atomicity)
2. Crashes don't leave orphaned records
3. System recovers to consistent state
4. Transaction isolation is maintained
"""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4
from datetime import date, datetime
from decimal import Decimal
import threading
import time

from sqlalchemy import select, event
from sqlalchemy.orm import Session

from finance_kernel.services.journal_writer import JournalWriter
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus
from finance_kernel.models.event import Event
from finance_kernel.models.audit_event import AuditEvent
from finance_kernel.domain.clock import DeterministicClock


class SimulatedCrash(Exception):
    """Exception to simulate a crash at a specific point."""
    pass


class TestAtomicityGuarantees:
    """
    B1: Partial write prevention tests.

    Verify that a crash during posting doesn't leave partial state.
    """

    def test_crash_during_journal_write_leaves_no_orphans(
        self,
        session,
        interpretation_coordinator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that a crash during journal write doesn't leave orphaned records.
        """
        from finance_kernel.domain.accounting_intent import (
            AccountingIntent, LedgerIntent, IntentLine, AccountingIntentSnapshot,
        )
        from finance_kernel.domain.meaning_builder import MeaningBuilderResult, EconomicEventData
        from tests.conftest import make_source_event

        # Count records before
        entries_before = len(session.execute(select(JournalEntry)).scalars().all())
        lines_before = len(session.execute(select(JournalLine)).scalars().all())

        source_event_id = uuid4()
        econ_event_id = uuid4()
        effective_date = deterministic_clock.now().date()

        make_source_event(session, source_event_id, test_actor_id, deterministic_clock, effective_date)

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="test.posting",
            effective_date=effective_date,
            profile_id="TestProfile",
            profile_version=1,
            profile_hash=None,
            quantity=Decimal("100.00"),
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id="TestProfile",
            profile_version=1,
            effective_date=effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                        IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        # Simulate crash by raising exception during journal write
        with patch.object(
            JournalWriter, 'write',
            side_effect=SimulatedCrash("Simulated crash during write")
        ):
            with pytest.raises(SimulatedCrash):
                interpretation_coordinator.interpret_and_post(
                    meaning_result=meaning_result,
                    accounting_intent=intent,
                    actor_id=test_actor_id,
                )

        # Rollback should have happened
        session.rollback()

        # Verify no orphaned records
        entries_after = len(session.execute(select(JournalEntry)).scalars().all())
        lines_after = len(session.execute(select(JournalLine)).scalars().all())

        assert entries_after == entries_before, (
            f"No new entries should exist after crash. Before: {entries_before}, After: {entries_after}"
        )
        assert lines_after == lines_before, (
            f"No new lines should exist after crash. Before: {lines_before}, After: {lines_after}"
        )


class TestRecoveryScenarios:
    """
    Tests for system recovery after failures.
    """

    def test_system_state_consistent_after_crash_and_restart(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
        auditor_service: AuditorService,
        interpretation_coordinator,
    ):
        """
        Verify system is in consistent state after crash and restart.
        """
        from finance_kernel.domain.accounting_intent import (
            AccountingIntent, LedgerIntent, IntentLine, AccountingIntentSnapshot,
        )
        from finance_kernel.domain.meaning_builder import MeaningBuilderResult, EconomicEventData
        from tests.conftest import make_source_event

        # Post a successful entry first
        result1 = post_via_coordinator(amount=Decimal("100.00"))
        assert result1.success
        first_entry_id = result1.journal_result.entries[0].entry_id
        session.flush()

        # Simulate crash during second post inside a SAVEPOINT
        # so the first entry is not rolled back
        source_event_id = uuid4()
        econ_event_id = uuid4()
        effective_date = deterministic_clock.now().date()

        make_source_event(session, source_event_id, test_actor_id, deterministic_clock, effective_date)

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="test.posting",
            effective_date=effective_date,
            profile_id="TestProfile",
            profile_version=1,
            profile_hash=None,
            quantity=Decimal("200.00"),
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id="TestProfile",
            profile_version=1,
            effective_date=effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("CashAsset", Decimal("200.00"), "USD"),
                        IntentLine.credit("SalesRevenue", Decimal("200.00"), "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        try:
            with session.begin_nested():
                with patch.object(
                    JournalWriter, 'write',
                    side_effect=SimulatedCrash("Crash during second post")
                ):
                    interpretation_coordinator.interpret_and_post(
                        meaning_result=meaning_result,
                        accounting_intent=intent,
                        actor_id=test_actor_id,
                    )
        except SimulatedCrash:
            pass  # Expected — SAVEPOINT automatically rolled back

        # System should be consistent - audit chain valid
        assert auditor_service.validate_chain() is True

        # First entry should still exist (SAVEPOINT protected it)
        entry = session.get(JournalEntry, first_entry_id)
        assert entry is not None
        assert entry.is_posted

        # Can post new entries
        result3 = post_via_coordinator(amount=Decimal("300.00"))
        assert result3.success

        # Chain still valid
        assert auditor_service.validate_chain() is True

    def test_no_duplicate_sequence_numbers_after_crash(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Verify no duplicate sequence numbers after crash recovery.
        """
        sequences = []

        # Post several entries
        for i in range(3):
            result = post_via_coordinator(
                amount=Decimal(str(100 + i)),
            )
            if result.success:
                entry = session.get(JournalEntry, result.journal_result.entries[0].entry_id)
                sequences.append(entry.seq)

        # Simulate crash (rollback without commit)
        session.rollback()

        # Post more entries
        for i in range(3):
            result = post_via_coordinator(
                amount=Decimal(str(200 + i)),
            )
            if result.success:
                entry = session.get(JournalEntry, result.journal_result.entries[0].entry_id)
                sequences.append(entry.seq)

        # Verify no duplicate sequences
        assert len(sequences) == len(set(sequences)), (
            f"Duplicate sequence numbers found: {sequences}"
        )


class TestGracefulDegradation:
    """
    Tests for graceful handling of failure conditions.
    """

    def test_connection_loss_during_post(
        self,
        session,
        interpretation_coordinator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify system handles connection loss gracefully.
        """
        from sqlalchemy.exc import OperationalError
        from finance_kernel.domain.accounting_intent import (
            AccountingIntent, LedgerIntent, IntentLine, AccountingIntentSnapshot,
        )
        from finance_kernel.domain.meaning_builder import MeaningBuilderResult, EconomicEventData
        from tests.conftest import make_source_event

        source_event_id = uuid4()
        econ_event_id = uuid4()
        effective_date = deterministic_clock.now().date()

        make_source_event(session, source_event_id, test_actor_id, deterministic_clock, effective_date)

        econ_data = EconomicEventData(
            source_event_id=source_event_id,
            economic_type="test.posting",
            effective_date=effective_date,
            profile_id="TestProfile",
            profile_version=1,
            profile_hash=None,
            quantity=Decimal("100.00"),
        )
        meaning_result = MeaningBuilderResult.ok(econ_data)

        intent = AccountingIntent(
            econ_event_id=econ_event_id,
            source_event_id=source_event_id,
            profile_id="TestProfile",
            profile_version=1,
            effective_date=effective_date,
            ledger_intents=(
                LedgerIntent(
                    ledger_id="GL",
                    lines=(
                        IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                        IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                    ),
                ),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )

        # Simulate connection loss
        with patch.object(
            session, 'flush',
            side_effect=OperationalError("Connection lost", None, None)
        ):
            with pytest.raises(OperationalError):
                interpretation_coordinator.interpret_and_post(
                    meaning_result=meaning_result,
                    accounting_intent=intent,
                    actor_id=test_actor_id,
                )

        # Session should still be usable after recovery
        session.rollback()
