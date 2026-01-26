"""
Append-only persistence tests (R10) and Hash-chain enforcement tests (R11).

R10 Verifies:
- JournalEntry is immutable after posting
- JournalLine is immutable when parent entry is posted
- AuditEvent is always immutable
- Updates and deletes are forbidden

R11 Verifies:
- All audit-relevant actions emit AuditEvent in same transaction
- Modifications to historical records break the hash chain
"""

import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.models.audit_event import AuditEvent, AuditAction
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import ImmutabilityViolationError, AuditChainBrokenError
from finance_kernel.db.immutability import unregister_immutability_listeners, register_immutability_listeners


def _register_test_strategy(event_type: str) -> None:
    """Register a simple balanced strategy for testing."""

    class TestStrategy(BasePostingStrategy):
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

    StrategyRegistry.register(TestStrategy(event_type))


class TestR10JournalEntryImmutability:
    """R10: Posted JournalEntry records are immutable."""

    def test_posted_entry_cannot_be_modified(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that modifying a posted journal entry raises ImmutabilityViolationError."""
        event_type = "test.r10.entry_modify"
        _register_test_strategy(event_type)

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        assert result.status == PostingStatus.POSTED
        session.flush()

        # Try to modify the posted entry
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None
        assert entry.status == JournalEntryStatus.POSTED

        # Attempt to modify description
        entry.description = "Modified description"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "JournalEntry" in str(exc_info.value)
        assert "Cannot modify" in str(exc_info.value)

        # Rollback to clean up
        session.rollback()

    def test_posted_entry_cannot_be_deleted(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that deleting a posted journal entry raises ImmutabilityViolationError."""
        event_type = "test.r10.entry_delete"
        _register_test_strategy(event_type)

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        assert result.status == PostingStatus.POSTED
        session.flush()

        # Try to delete the posted entry
        entry = session.get(JournalEntry, result.journal_entry_id)
        session.delete(entry)

        # When deleting entry with cascade, lines are deleted first
        # Either JournalEntry or JournalLine violation is acceptable
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value)
        assert "Journal" in error_msg  # JournalEntry or JournalLine
        assert "deleted" in error_msg.lower() or "cannot" in error_msg.lower()

        session.rollback()

    def test_draft_entry_can_be_modified(
        self,
        session,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        create_event,
    ):
        """Test that draft journal entries CAN still be modified."""
        # Create an event first (for FK constraint)
        event = create_event(event_type="test.draft")

        # Create a draft entry directly
        entry = JournalEntry(
            source_event_id=event.event_id,
            source_event_type="test.draft",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.DRAFT,
            idempotency_key=f"test:test.draft:{uuid4()}",
            posting_rule_version=1,
            description="Original description",
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        # Modify the draft entry - should work
        entry.description = "Modified description"
        session.flush()

        # Verify modification succeeded
        refreshed = session.get(JournalEntry, entry.id)
        assert refreshed.description == "Modified description"


class TestR10JournalLineImmutability:
    """R10: JournalLine records are immutable when parent entry is posted."""

    def test_line_cannot_be_modified_after_posting(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that modifying a journal line after posting raises ImmutabilityViolationError."""
        event_type = "test.r10.line_modify"
        _register_test_strategy(event_type)

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        assert result.status == PostingStatus.POSTED
        session.flush()

        # Try to modify a line
        entry = session.get(JournalEntry, result.journal_entry_id)
        line = entry.lines[0]

        line.line_memo = "Modified memo"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "JournalLine" in str(exc_info.value)

        session.rollback()

    def test_line_cannot_be_deleted_after_posting(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that deleting a journal line after posting raises ImmutabilityViolationError."""
        event_type = "test.r10.line_delete"
        _register_test_strategy(event_type)

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        assert result.status == PostingStatus.POSTED
        session.flush()

        # Try to delete a line
        entry = session.get(JournalEntry, result.journal_entry_id)
        line = entry.lines[0]
        session.delete(line)

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "JournalLine" in str(exc_info.value)

        session.rollback()


class TestR10AuditEventImmutability:
    """R10: AuditEvent records are always immutable."""

    def test_audit_event_cannot_be_modified(
        self,
        session,
        auditor_service: AuditorService,
        test_actor_id,
    ):
        """Test that modifying an audit event raises ImmutabilityViolationError."""
        # Create an audit event
        audit_event = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.audit",
            producer="test",
            actor_id=test_actor_id,
        )
        session.flush()

        # Try to modify it
        audit_event.payload = {"modified": True}

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "AuditEvent" in str(exc_info.value)
        assert "immutable" in str(exc_info.value).lower()

        session.rollback()

    def test_audit_event_cannot_be_deleted(
        self,
        session,
        auditor_service: AuditorService,
        test_actor_id,
    ):
        """Test that deleting an audit event raises ImmutabilityViolationError."""
        # Create an audit event
        audit_event = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.audit",
            producer="test",
            actor_id=test_actor_id,
        )
        session.flush()

        # Try to delete it
        session.delete(audit_event)

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "AuditEvent" in str(exc_info.value)
        assert "deleted" in str(exc_info.value).lower()

        session.rollback()


class TestR11HashChainEnforcement:
    """R11: Hash chain enforcement for audit events."""

    def test_audit_events_form_valid_chain(
        self,
        session,
        auditor_service: AuditorService,
        test_actor_id,
    ):
        """Test that audit events form a valid hash chain."""
        # Create multiple audit events
        event1 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.chain.1",
            producer="test",
            actor_id=test_actor_id,
        )

        event2 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.chain.2",
            producer="test",
            actor_id=test_actor_id,
        )

        event3 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.chain.3",
            producer="test",
            actor_id=test_actor_id,
        )

        session.flush()

        # Validate the chain
        assert auditor_service.validate_chain() is True

        # Verify chain linkage
        assert event1.prev_hash is None  # First event has no prev_hash
        assert event2.prev_hash == event1.hash
        assert event3.prev_hash == event2.hash

    def test_posting_creates_audit_event_in_same_transaction(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        auditor_service: AuditorService,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting creates an audit event in the same transaction."""
        event_type = "test.r11.same_transaction"
        _register_test_strategy(event_type)

        event_id = uuid4()

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        assert result.status == PostingStatus.POSTED

        # Before commit, audit events should be visible in the same transaction
        trace = auditor_service.get_trace("JournalEntry", result.journal_entry_id)
        assert not trace.is_empty
        assert trace.last_action == AuditAction.JOURNAL_POSTED

    def test_tampered_hash_breaks_chain_validation(
        self,
        session,
        auditor_service: AuditorService,
        test_actor_id,
    ):
        """Test that modifying a hash breaks chain validation."""
        # Create audit events
        event1 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.tamper.1",
            producer="test",
            actor_id=test_actor_id,
        )

        event2 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.tamper.2",
            producer="test",
            actor_id=test_actor_id,
        )

        session.flush()

        # Validate chain is initially valid
        assert auditor_service.validate_chain() is True

        # Temporarily disable immutability to simulate tampering
        unregister_immutability_listeners()

        try:
            # Tamper with the hash using raw SQL
            session.execute(
                text("UPDATE audit_events SET hash = 'tampered_hash' WHERE id = :id"),
                {"id": str(event1.id)},
            )
            session.flush()

            # Expire all objects to force reload from DB
            session.expire_all()

            # Chain validation should now fail
            with pytest.raises(AuditChainBrokenError):
                auditor_service.validate_chain()

        finally:
            # Re-enable immutability and rollback
            session.rollback()
            register_immutability_listeners()

    def test_tampered_prev_hash_breaks_chain_validation(
        self,
        session,
        auditor_service: AuditorService,
        test_actor_id,
    ):
        """Test that modifying prev_hash breaks chain validation."""
        # Create audit events
        event1 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.tamper_prev.1",
            producer="test",
            actor_id=test_actor_id,
        )

        event2 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.tamper_prev.2",
            producer="test",
            actor_id=test_actor_id,
        )

        session.flush()

        # Validate chain is initially valid
        assert auditor_service.validate_chain() is True

        # Temporarily disable immutability to simulate tampering
        unregister_immutability_listeners()

        try:
            # Tamper with the prev_hash using raw SQL
            session.execute(
                text("UPDATE audit_events SET prev_hash = 'wrong_prev_hash' WHERE id = :id"),
                {"id": str(event2.id)},
            )
            session.flush()

            # Expire all objects to force reload from DB
            session.expire_all()

            # Chain validation should now fail
            with pytest.raises(AuditChainBrokenError):
                auditor_service.validate_chain()

        finally:
            # Re-enable immutability and rollback
            session.rollback()
            register_immutability_listeners()

    def test_first_event_must_have_null_prev_hash(
        self,
        session,
        auditor_service: AuditorService,
        test_actor_id,
    ):
        """Test that the first audit event must have null prev_hash."""
        # Create first audit event
        event1 = auditor_service.record_event_ingested(
            event_id=uuid4(),
            event_type="test.genesis",
            producer="test",
            actor_id=test_actor_id,
        )

        session.flush()

        # First event should have null prev_hash
        assert event1.prev_hash is None
        assert event1.is_genesis is True

        # Chain should be valid
        assert auditor_service.validate_chain() is True

        # Temporarily disable immutability to simulate tampering
        unregister_immutability_listeners()

        try:
            # Tamper: add a prev_hash to the first event
            session.execute(
                text("UPDATE audit_events SET prev_hash = 'fake_prev' WHERE id = :id"),
                {"id": str(event1.id)},
            )
            session.flush()

            # Expire all objects to force reload from DB
            session.expire_all()

            # Chain validation should fail because first event has non-null prev_hash
            with pytest.raises(AuditChainBrokenError):
                auditor_service.validate_chain()

        finally:
            session.rollback()
            register_immutability_listeners()


class TestR11AuditCoverage:
    """R11: All audit-relevant actions must emit AuditEvent."""

    def test_event_ingestion_creates_audit_event(
        self,
        session,
        ingestor_service,
        auditor_service: AuditorService,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that event ingestion creates an audit event."""
        event_id = uuid4()

        result = ingestor_service.ingest(
            event_id=event_id,
            event_type="test.ingest",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        session.flush()

        # Verify audit event was created
        trace = auditor_service.get_trace("Event", event_id)
        assert not trace.is_empty
        assert trace.first_action == AuditAction.EVENT_INGESTED

    def test_posting_creates_audit_event(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        auditor_service: AuditorService,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Test that posting creates an audit event."""
        event_type = "test.r11.posting_audit"
        _register_test_strategy(event_type)

        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data"},
        )

        assert result.status == PostingStatus.POSTED
        session.flush()

        # Verify audit event was created
        trace = auditor_service.get_trace("JournalEntry", result.journal_entry_id)
        assert not trace.is_empty
        assert trace.last_action == AuditAction.JOURNAL_POSTED
