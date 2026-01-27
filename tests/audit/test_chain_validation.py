"""
Audit chain validation tests.

Verifies:
- Full chain walk from any entry back to genesis
- Tamper detection via hash chain validation
- Every posted fact has an audit event
- Hash chain integrity across all audit events
"""

import pytest
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, date

from sqlalchemy import select, update

from finance_kernel.models.audit_event import AuditEvent, AuditAction
from finance_kernel.models.journal import JournalEntry, JournalEntryStatus
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.db.engine import get_engine, is_postgres


@contextmanager
def disabled_immutability():
    """
    Context manager that disables both ORM and database-level immutability enforcement.

    Use this for tests that need to simulate tampering with audit data.
    """
    from finance_kernel.db.immutability import (
        register_immutability_listeners,
        unregister_immutability_listeners,
    )
    from finance_kernel.db.triggers import (
        install_immutability_triggers,
        uninstall_immutability_triggers,
    )

    engine = get_engine()

    # Disable ORM listeners
    unregister_immutability_listeners()

    # Disable database triggers (PostgreSQL only)
    if is_postgres():
        uninstall_immutability_triggers(engine)

    try:
        yield
    finally:
        # Re-enable ORM listeners
        register_immutability_listeners()

        # Re-enable database triggers (PostgreSQL only)
        if is_postgres():
            install_immutability_triggers(engine)


@dataclass
class ChainValidationResult:
    """Result of audit chain validation."""

    total_events: int
    valid_events: int
    invalid_events: int
    chain_intact: bool
    first_invalid_seq: int | None
    tamper_detected: bool
    orphan_facts: int  # Posted facts without audit events


class AuditChainValidator:
    """Validates the integrity of the audit chain."""

    def __init__(self, session):
        self.session = session

    def validate_full_chain(self) -> ChainValidationResult:
        """
        Walk the entire audit chain and validate each link.
        """
        events = self.session.execute(
            select(AuditEvent).order_by(AuditEvent.seq)
        ).scalars().all()

        if not events:
            return ChainValidationResult(
                total_events=0,
                valid_events=0,
                invalid_events=0,
                chain_intact=True,
                first_invalid_seq=None,
                tamper_detected=False,
                orphan_facts=0,
            )

        valid_count = 0
        invalid_count = 0
        first_invalid = None
        tamper_detected = False

        prev_hash = None

        for i, event in enumerate(events):
            # Validate this event's hash
            expected_hash = self._compute_event_hash(event, prev_hash)

            if event.hash == expected_hash:
                valid_count += 1
            else:
                invalid_count += 1
                tamper_detected = True
                if first_invalid is None:
                    first_invalid = i

            # Also check prev_hash linkage
            if event.prev_hash != prev_hash:
                invalid_count += 1
                tamper_detected = True
                if first_invalid is None:
                    first_invalid = i

            prev_hash = event.hash

        # Count orphan facts (posted entries without audit events)
        posted_entries = self.session.execute(
            select(JournalEntry).where(JournalEntry.status == JournalEntryStatus.POSTED)
        ).scalars().all()

        audited_entry_ids = set()
        for event in events:
            if event.action == AuditAction.JOURNAL_POSTED:
                audited_entry_ids.add(event.entity_id)

        orphan_count = sum(
            1 for entry in posted_entries
            if entry.id not in audited_entry_ids
        )

        return ChainValidationResult(
            total_events=len(events),
            valid_events=valid_count,
            invalid_events=invalid_count,
            chain_intact=(invalid_count == 0),
            first_invalid_seq=first_invalid,
            tamper_detected=tamper_detected,
            orphan_facts=orphan_count,
        )

    def _compute_event_hash(self, event: AuditEvent, prev_hash: str | None) -> str:
        """Recompute the expected hash for an audit event."""
        # Match the hashing logic in AuditService/hash_audit_event
        # Handle both enum and string for action
        action_value = event.action.value if hasattr(event.action, 'value') else event.action
        # Use | as separator and "GENESIS" for None prev_hash (matching hash_audit_event)
        components = [
            event.entity_type,
            str(event.entity_id),
            action_value,
            event.payload_hash,
            prev_hash or "GENESIS",
        ]
        hash_input = "|".join(components)
        return hashlib.sha256(hash_input.encode()).hexdigest()

    def trace_entry_to_genesis(self, entry_id) -> list[AuditEvent]:
        """
        Walk back from a specific entry to the first audit event.
        """
        # Find the audit event for this entry
        entry_event = self.session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == entry_id,
                AuditEvent.action == AuditAction.JOURNAL_POSTED,
            )
        ).scalar_one_or_none()

        if not entry_event:
            return []

        # Walk backwards through prev_hash chain
        chain = [entry_event]
        current_hash = entry_event.prev_hash

        while current_hash:
            prev_event = self.session.execute(
                select(AuditEvent).where(AuditEvent.hash == current_hash)
            ).scalar_one_or_none()

            if prev_event:
                chain.append(prev_event)
                current_hash = prev_event.prev_hash
            else:
                break

        return list(reversed(chain))  # Return in chronological order


def _register_simple_strategy(event_type: str) -> None:
    """Register a simple balanced strategy for testing."""

    class SimpleStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str):
            self._event_type = evt_type
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
            amount = Decimal(event.payload.get("amount", "100.00"))
            return (
                LineSpec(
                    account_code="1000",
                    side=LineSide.DEBIT,
                    money=Money.of(amount, "USD"),
                ),
                LineSpec(
                    account_code="4000",
                    side=LineSide.CREDIT,
                    money=Money.of(amount, "USD"),
                ),
            )

    StrategyRegistry.register(SimpleStrategy(event_type))


class TestAuditChainIntegrity:
    """Tests for audit chain integrity."""

    def test_chain_valid_after_postings(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Post multiple events and verify chain integrity.
        """
        event_type = "test.audit"
        _register_simple_strategy(event_type)

        try:
            # Post several events
            for i in range(10):
                posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"index": i, "amount": f"{100 + i}.00"},
                )

            session.flush()

            # Validate chain
            validator = AuditChainValidator(session)
            result = validator.validate_full_chain()

            assert result.chain_intact, "Chain should be intact after normal postings"
            assert not result.tamper_detected, "No tampering should be detected"
            assert result.orphan_facts == 0, "All posted facts should have audit events"
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_every_posted_entry_has_audit_event(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify every posted JournalEntry has a corresponding audit event.
        """
        event_type = "test.audit_entry"
        _register_simple_strategy(event_type)

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
            session.flush()

            # Find audit event for this entry
            audit_event = session.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_id == result.journal_entry_id,
                    AuditEvent.action == AuditAction.JOURNAL_POSTED,
                )
            ).scalar_one_or_none()

            assert audit_event is not None, "Posted entry must have audit event"
            assert audit_event.entity_type == "JournalEntry"
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestTamperDetection:
    """Tests for tamper detection via hash chain."""

    def test_modified_payload_detected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Modify an audit event's payload and verify detection.
        """
        event_type = "test.tamper_payload"
        _register_simple_strategy(event_type)

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
            session.flush()

            # Get the audit event
            audit_event = session.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_id == result.journal_entry_id,
                )
            ).scalar_one()

            # Tamper with the payload hash (disable immutability enforcement first)
            original_payload_hash = audit_event.payload_hash
            tampered_hash = "tampered_" + original_payload_hash[:50]

            with disabled_immutability():
                session.execute(
                    update(AuditEvent)
                    .where(AuditEvent.id == audit_event.id)
                    .values(payload_hash=tampered_hash)
                )
                session.flush()

            # Validate chain - should detect tampering
            validator = AuditChainValidator(session)
            validation_result = validator.validate_full_chain()

            assert validation_result.tamper_detected, "Tampering should be detected"
            assert not validation_result.chain_intact, "Chain should not be intact"
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_broken_prev_hash_link_detected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Break a prev_hash link and verify detection.
        """
        event_type = "test.chain_break"
        _register_simple_strategy(event_type)

        try:
            # Create two postings to have a chain
            for i in range(2):
                posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"index": i, "amount": "100.00"},
                )

            session.flush()

            # Get the second audit event and break its prev_hash
            events = session.execute(
                select(AuditEvent).order_by(AuditEvent.seq)
            ).scalars().all()

            if len(events) >= 2:
                second_event = events[1]

                # Disable immutability to simulate tampering
                with disabled_immutability():
                    session.execute(
                        update(AuditEvent)
                        .where(AuditEvent.id == second_event.id)
                        .values(prev_hash="broken_link")
                    )
                    session.flush()

                # Validate
                validator = AuditChainValidator(session)
                validation_result = validator.validate_full_chain()

                assert validation_result.tamper_detected, "Broken link should be detected"
        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestTraceWalk:
    """Tests for walking audit traces."""

    def test_trace_entry_to_genesis(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Trace a journal entry back through the entire audit chain.
        """
        event_type = "test.trace"
        _register_simple_strategy(event_type)

        try:
            # Create several postings
            entry_ids = []
            for i in range(5):
                result = posting_orchestrator.post_event(
                    event_id=uuid4(),
                    event_type=event_type,
                    occurred_at=deterministic_clock.now(),
                    effective_date=deterministic_clock.now().date(),
                    actor_id=test_actor_id,
                    producer="test",
                    payload={"index": i, "amount": "100.00"},
                )
                entry_ids.append(result.journal_entry_id)

            session.flush()

            # Trace the last entry back to genesis
            validator = AuditChainValidator(session)
            trace = validator.trace_entry_to_genesis(entry_ids[-1])

            # Should have at least one event (the posting of this entry)
            assert len(trace) >= 1

            # First event in trace should have no prev_hash (or be the genesis)
            if trace:
                # Verify chain continuity
                for i in range(1, len(trace)):
                    assert trace[i].prev_hash == trace[i - 1].hash
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_trace_includes_all_related_events(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify trace includes the posting audit event.
        """
        event_type = "test.trace_related"
        _register_simple_strategy(event_type)

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
            session.flush()

            validator = AuditChainValidator(session)
            trace = validator.trace_entry_to_genesis(result.journal_entry_id)

            # Find the JOURNAL_POSTED event in the trace
            posted_events = [e for e in trace if e.action == AuditAction.JOURNAL_POSTED]
            assert len(posted_events) >= 1, "Trace should include JOURNAL_POSTED event"
        finally:
            StrategyRegistry._strategies.pop(event_type, None)
