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
from finance_kernel.db.engine import get_engine, is_postgres
from contextlib import contextmanager


@contextmanager
def disabled_immutability():
    """
    Context manager that disables both ORM and database-level immutability enforcement.

    Use this for tests that need to simulate tampering with audit data.
    """
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

    def test_posted_entry_cannot_be_modified_via_merge(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that detaching, modifying, and merging a posted entry is blocked.

        This tests a potential attack vector where someone:
        1. Fetches a posted JournalEntry
        2. Detaches it from the session (session.expunge)
        3. Modifies it while detached
        4. Uses session.merge() to save changes

        The ORM immutability listeners should catch this on merge.
        """
        event_type = "test.r10.merge_attack"
        _register_test_strategy(event_type)

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type=event_type,
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "merge_attack"},
        )

        assert result.status == PostingStatus.POSTED

        # Commit to persist the entry before testing the merge attack
        session.commit()

        # Fetch the posted entry
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None
        assert entry.status == JournalEntryStatus.POSTED
        original_description = entry.description
        entry_id = entry.id

        # Detach from session
        session.expunge(entry)

        # Modify while detached (no ORM listener fires yet)
        entry.description = "Tampered via merge attack"

        # Attempt to merge back - should be blocked
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            merged = session.merge(entry)
            session.flush()

        assert "JournalEntry" in str(exc_info.value)
        assert "Cannot modify" in str(exc_info.value)

        # Rollback and verify original data is intact
        session.rollback()

        # Re-fetch to confirm no changes persisted
        fresh_entry = session.get(JournalEntry, entry_id)
        assert fresh_entry is not None
        assert fresh_entry.description == original_description



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

        try:
            # Disable both ORM and DB-level immutability to simulate tampering
            with disabled_immutability():
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
            session.rollback()

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

        try:
            # Disable both ORM and DB-level immutability to simulate tampering
            with disabled_immutability():
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
            session.rollback()

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

        try:
            # Disable both ORM and DB-level immutability to simulate tampering
            with disabled_immutability():
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


class TestR10DatabaseTriggers:
    """
    R10 Defense-in-Depth: Database-level trigger enforcement.

    These tests verify that PostgreSQL triggers block modifications
    even when bypassing the ORM (bulk updates, raw SQL).
    """

    @pytest.fixture
    def posted_entry_via_raw_sql(self, pg_session_factory, pg_session):
        """Create a posted journal entry using raw SQL for trigger testing."""
        from finance_kernel.db.triggers import triggers_installed
        from finance_kernel.db.engine import get_engine

        # Skip if triggers not installed (e.g., SQLite)
        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        # Create minimal test data via raw SQL
        # Event model extends Base (no created_at columns)
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.trigger', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": uuid4(), "event_id": event_id, "actor_id": actor_id},
        )

        # JournalEntry extends TrackedBase (has created_at, created_by_id)
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.trigger', NOW(), CURRENT_DATE, :actor_id,
                       'posted', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {
                "id": entry_id,
                "event_id": event_id,
                "actor_id": actor_id,
                "idempotency_key": f"test:trigger:{entry_id}",
            },
        )

        pg_session.commit()
        return entry_id

    def test_bulk_update_blocked_by_trigger(
        self, pg_session_factory, pg_session, posted_entry_via_raw_sql
    ):
        """Test that bulk UPDATE statements are blocked by database triggers."""
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        entry_id = posted_entry_via_raw_sql

        # Attempt bulk update via Core (bypasses ORM listeners)
        # Cast UUID to string for raw SQL since id column uses UUIDString type
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET description = 'hacked' WHERE id = :id"),
                {"id": str(entry_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value) or "restrict_violation" in str(exc_info.value)
        pg_session.rollback()

    def test_raw_sql_delete_blocked_by_trigger(
        self, pg_session_factory, pg_session, posted_entry_via_raw_sql
    ):
        """Test that raw SQL DELETE is blocked by database triggers."""
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        entry_id = posted_entry_via_raw_sql

        # Attempt raw SQL delete
        # Cast UUID to string for raw SQL since id column uses UUIDString type
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("DELETE FROM journal_entries WHERE id = :id"),
                {"id": str(entry_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value) or "restrict_violation" in str(exc_info.value)
        pg_session.rollback()

    def test_audit_event_update_blocked_by_trigger(self, pg_session_factory, pg_session):
        """Test that audit events cannot be modified via raw SQL."""
        from sqlalchemy.exc import IntegrityError, ProgrammingError
        from finance_kernel.db.triggers import triggers_installed
        from finance_kernel.db.engine import get_engine

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")

        actor_id = uuid4()

        # Create an audit event via raw SQL
        # AuditEvent extends Base (no created_at columns)
        pg_session.execute(
            text("""
                INSERT INTO audit_events (id, seq, action, entity_type, entity_id,
                                         actor_id, occurred_at, payload, payload_hash,
                                         hash)
                VALUES (:id, 1, 'test_action', 'Test', :entity_id, :actor_id,
                       NOW(), '{}', 'payload_hash', 'original_hash')
            """),
            {"id": uuid4(), "entity_id": uuid4(), "actor_id": actor_id},
        )
        pg_session.commit()

        # Attempt to update the audit event
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET hash = 'tampered' WHERE seq = 1")
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value) or "restrict_violation" in str(exc_info.value)
        pg_session.rollback()

    def test_audit_event_delete_blocked_by_trigger(self, pg_session_factory, pg_session):
        """Test that audit events cannot be deleted via raw SQL."""
        from sqlalchemy.exc import IntegrityError, ProgrammingError
        from finance_kernel.db.triggers import triggers_installed
        from finance_kernel.db.engine import get_engine

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")

        actor_id = uuid4()

        # Create an audit event via raw SQL
        # AuditEvent extends Base (no created_at columns)
        pg_session.execute(
            text("""
                INSERT INTO audit_events (id, seq, action, entity_type, entity_id,
                                         actor_id, occurred_at, payload, payload_hash,
                                         hash)
                VALUES (:id, 999, 'test_delete', 'Test', :entity_id, :actor_id,
                       NOW(), '{}', 'payload_hash', 'hash_to_delete')
            """),
            {"id": uuid4(), "entity_id": uuid4(), "actor_id": actor_id},
        )
        pg_session.commit()

        # Attempt to delete the audit event
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(text("DELETE FROM audit_events WHERE seq = 999"))
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value) or "restrict_violation" in str(exc_info.value)
        pg_session.rollback()

    def test_posting_transition_allowed_by_trigger(self, pg_session_factory, pg_session):
        """Test that the initial posting transition (draft -> posted) IS allowed."""
        from finance_kernel.db.triggers import triggers_installed
        from finance_kernel.db.engine import get_engine

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        # Create event and draft entry
        # Event extends Base (no created_at columns)
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.post', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": uuid4(), "event_id": event_id, "actor_id": actor_id},
        )

        # JournalEntry extends TrackedBase (has created_at, created_by_id)
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.post', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {
                "id": entry_id,
                "event_id": event_id,
                "actor_id": actor_id,
                "idempotency_key": f"test:post:{entry_id}",
            },
        )
        pg_session.commit()

        # Transition from draft to posted should succeed
        # Cast UUID to string for raw SQL since id column uses UUIDString type
        pg_session.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": str(entry_id)},
        )
        pg_session.commit()

        # Verify status changed
        result = pg_session.execute(
            text("SELECT status FROM journal_entries WHERE id = :id"),
            {"id": str(entry_id)},
        ).fetchone()

        assert result[0] == "posted"

    def test_merge_attack_blocked_by_trigger(
        self,
        pg_session_factory,
        pg_session,
        posted_entry_via_raw_sql,
    ):
        """
        Test that database triggers block merge attacks even if ORM listeners fail.

        This tests a potential attack vector where someone:
        1. Fetches a posted JournalEntry
        2. Detaches it from the session (session.expunge)
        3. Modifies it while detached
        4. Uses session.merge() to save changes

        Defense-in-depth: Even if ORM listeners were bypassed, PostgreSQL
        triggers should still block the UPDATE statement.
        """
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        entry_id = posted_entry_via_raw_sql

        # Fetch the posted entry
        entry = pg_session.get(JournalEntry, entry_id)
        assert entry is not None
        assert entry.status == JournalEntryStatus.POSTED
        original_description = entry.description

        # Detach from session
        pg_session.expunge(entry)

        # Modify while detached
        entry.description = "Tampered via merge - trigger should block"

        # Temporarily disable ORM listeners to test trigger-only protection
        unregister_immutability_listeners()
        try:
            with pytest.raises((IntegrityError, ProgrammingError, ImmutabilityViolationError)) as exc_info:
                merged = pg_session.merge(entry)
                pg_session.flush()

            error_msg = str(exc_info.value)
            # Should be blocked by database trigger
            assert "R10 Violation" in error_msg or "restrict_violation" in error_msg or "Cannot modify" in error_msg
        finally:
            # Re-enable ORM listeners
            register_immutability_listeners()

        pg_session.rollback()

        # Verify original data intact
        fresh_entry = pg_session.get(JournalEntry, entry_id)
        assert fresh_entry is not None
        assert fresh_entry.description == original_description
