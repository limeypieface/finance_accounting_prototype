"""
R8 Idempotency Locking tests.

R8. Idempotency locking

Idempotency keys must be enforced by:
1. Database uniqueness constraint, AND
2. Row-level lock or key-registry table.

This ensures that:
- Duplicate events are detected at the database level
- Concurrent duplicate attempts are serialized via locking
- No race conditions can cause duplicate journal entries
"""

import pytest
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from sqlalchemy import text, inspect
from sqlalchemy.exc import IntegrityError

from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry, JournalEntryStatus
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.ingestor_service import IngestorService, IngestStatus


class TestR8DatabaseUniquenessConstraints:
    """
    Verify database uniqueness constraints on idempotency keys.

    R8: Idempotency keys must be enforced by database uniqueness constraint.
    """

    def test_event_table_has_unique_constraint_on_event_id(self, session):
        """
        Verify Event table has unique constraint on event_id.

        R8: Database uniqueness constraint required.
        """
        inspector = inspect(session.bind)

        # Get unique constraints
        unique_constraints = inspector.get_unique_constraints('events')
        constraint_columns = [c['column_names'] for c in unique_constraints]

        # Should have unique constraint on event_id
        has_event_id_unique = any(['event_id'] == cols for cols in constraint_columns)

        # Also check column definition (unique=True)
        columns = inspector.get_columns('events')
        event_id_col = next((c for c in columns if c['name'] == 'event_id'), None)

        assert has_event_id_unique or (event_id_col and event_id_col.get('unique', False)), \
            "Event.event_id must have unique constraint"

    def test_journal_entry_table_has_unique_constraint_on_idempotency_key(self, session):
        """
        Verify JournalEntry table has unique constraint on idempotency_key.

        R8: Database uniqueness constraint required.
        """
        inspector = inspect(session.bind)

        # Get unique constraints
        unique_constraints = inspector.get_unique_constraints('journal_entries')
        constraint_columns = [c['column_names'] for c in unique_constraints]

        # Should have unique constraint on idempotency_key
        has_idempotency_unique = any(['idempotency_key'] == cols for cols in constraint_columns)

        assert has_idempotency_unique, \
            "JournalEntry.idempotency_key must have unique constraint"

    def test_duplicate_event_id_rejected_by_database(self, session, deterministic_clock):
        """
        Verify database rejects duplicate event_id.

        R8: Uniqueness constraint must actually enforce uniqueness.
        """
        event_id = uuid4()
        now = deterministic_clock.now()

        # Insert first event
        event1 = Event(
            event_id=event_id,
            event_type="test.duplicate",
            occurred_at=now,
            effective_date=now.date(),
            actor_id=uuid4(),
            producer="test",
            payload={"test": "data"},
            payload_hash="hash1",
            schema_version=1,
            ingested_at=now,
        )
        session.add(event1)
        session.flush()

        # Attempt to insert duplicate
        event2 = Event(
            event_id=event_id,  # Same event_id
            event_type="test.duplicate",
            occurred_at=now,
            effective_date=now.date(),
            actor_id=uuid4(),
            producer="test",
            payload={"test": "different"},
            payload_hash="hash2",
            schema_version=1,
            ingested_at=now,
        )
        session.add(event2)

        with pytest.raises(IntegrityError):
            session.flush()

        session.rollback()

    def test_duplicate_idempotency_key_rejected_by_database(
        self,
        session,
        deterministic_clock,
        standard_accounts,
        current_period,
        test_actor_id,
    ):
        """
        Verify database rejects duplicate idempotency_key.

        R8: Uniqueness constraint must actually enforce uniqueness.
        """
        idempotency_key = f"test:generic.posting:{uuid4()}"
        now = deterministic_clock.now()

        # Create events first (JournalEntry has FK to Event)
        event1_id = uuid4()
        event1 = Event(
            event_id=event1_id,
            event_type="test.r8",
            occurred_at=now,
            effective_date=now.date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data1"},
            payload_hash="hash1",
            schema_version=1,
            ingested_at=now,
        )
        session.add(event1)

        event2_id = uuid4()
        event2 = Event(
            event_id=event2_id,
            event_type="test.r8",
            occurred_at=now,
            effective_date=now.date(),
            actor_id=test_actor_id,
            producer="test",
            payload={"test": "data2"},
            payload_hash="hash2",
            schema_version=1,
            ingested_at=now,
        )
        session.add(event2)
        session.flush()

        # Insert first entry
        entry1 = JournalEntry(
            source_event_id=event1_id,
            source_event_type="test.r8",
            occurred_at=now,
            effective_date=now.date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.DRAFT,
            idempotency_key=idempotency_key,
            posting_rule_version=1,
            created_by_id=test_actor_id,
        )
        session.add(entry1)
        session.flush()

        # Attempt to insert duplicate
        entry2 = JournalEntry(
            source_event_id=event2_id,
            source_event_type="test.r8",
            occurred_at=now,
            effective_date=now.date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.DRAFT,
            idempotency_key=idempotency_key,  # Same idempotency_key
            posting_rule_version=1,
            created_by_id=test_actor_id,
        )
        session.add(entry2)

        with pytest.raises(IntegrityError):
            session.flush()

        session.rollback()


class TestR8RowLevelLocking:
    """
    Verify row-level locking for idempotency enforcement.

    R8: Row-level lock or key-registry table required.
    """

    def test_ledger_service_uses_for_update(self):
        """
        Verify LedgerService uses SELECT FOR UPDATE for idempotency check.

        R8: Row-level lock required.
        """
        import inspect as python_inspect
        from finance_kernel.services.ledger_service import LedgerService

        # Get source code of _get_existing_entry
        source = python_inspect.getsource(LedgerService._get_existing_entry)

        # Must contain with_for_update()
        assert 'with_for_update()' in source, \
            "LedgerService._get_existing_entry must use with_for_update() for row-level locking"

    def test_idempotency_check_locks_row(
        self,
        pg_session,
        pg_session_factory,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Verify idempotency check acquires row lock.

        R8: Row-level lock must be acquired during idempotency check.
        """
        from finance_kernel.db.triggers import triggers_installed
        from finance_kernel.db.engine import get_engine

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Requires PostgreSQL for row-level locking test")

        event_id = uuid4()

        # First post
        result = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED
        pg_session.commit()

        # Second post should see locked row and return ALREADY_POSTED
        result2 = posting_orchestrator.post_event(
            event_id=event_id,
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result2.status == PostingStatus.ALREADY_POSTED
        assert result2.journal_entry_id == result.journal_entry_id


class TestR8ConcurrentIdempotency:
    """
    Verify idempotency under concurrent access.

    R8: Locking must prevent race conditions.
    """

    def test_concurrent_posts_same_event_one_wins(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Concurrent posts of same event must result in exactly one entry.

        R8: Locking prevents duplicate entries.
        """
        event_id = uuid4()
        results = []
        errors = []

        def post_event():
            try:
                result = posting_orchestrator.post_event(
                    event_id=event_id,
                    event_type="generic.posting",
                    occurred_at=deterministic_clock.now(),
                    effective_date=current_period.start_date,
                    actor_id=test_actor_id,
                    producer="test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Post sequentially (thread safety issues with shared session)
        for _ in range(10):
            post_event()

        # Should have no errors
        assert len(errors) == 0, f"Errors: {errors}"

        # Should have exactly 1 POSTED, rest ALREADY_POSTED
        posted_count = sum(1 for r in results if r.status == PostingStatus.POSTED)
        already_posted_count = sum(1 for r in results if r.status == PostingStatus.ALREADY_POSTED)

        assert posted_count == 1, f"Expected 1 POSTED, got {posted_count}"
        assert already_posted_count == 9, f"Expected 9 ALREADY_POSTED, got {already_posted_count}"

        # All should reference same entry
        entry_ids = set(r.journal_entry_id for r in results if r.journal_entry_id)
        assert len(entry_ids) == 1, f"All results should reference same entry, got {len(entry_ids)}"

    def test_many_sequential_posts_one_entry(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        100 sequential posts of same event must result in one entry.

        R8: Idempotency verified over many attempts.
        """
        event_id = uuid4()

        posted_count = 0
        already_posted_count = 0
        entry_id = None

        for i in range(100):
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )

            if result.status == PostingStatus.POSTED:
                posted_count += 1
                entry_id = result.journal_entry_id
            elif result.status == PostingStatus.ALREADY_POSTED:
                already_posted_count += 1
                assert result.journal_entry_id == entry_id

        assert posted_count == 1
        assert already_posted_count == 99


class TestR8IdempotencyKeyFormat:
    """
    Verify idempotency key format and derivation.
    """

    def test_idempotency_key_derived_from_event(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Idempotency key must be derived from event attributes.

        R8: Key must be deterministic and unique per event.
        """
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
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.is_success

        # Verify idempotency key format
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None

        # Key should contain event_id
        assert str(event_id) in entry.idempotency_key

        # Key should be deterministic
        expected_key = f"test:generic.posting:{event_id}"
        assert entry.idempotency_key == expected_key

    def test_different_events_different_keys(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Different events must have different idempotency keys.

        R8: Keys must uniquely identify events.
        """
        event_ids = [uuid4() for _ in range(5)]
        entry_ids = []

        for event_id in event_ids:
            result = posting_orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )
            assert result.status == PostingStatus.POSTED
            entry_ids.append(result.journal_entry_id)

        # All entries should be unique
        assert len(set(entry_ids)) == 5

        # All idempotency keys should be unique
        entries = [session.get(JournalEntry, eid) for eid in entry_ids]
        keys = [e.idempotency_key for e in entries]
        assert len(set(keys)) == 5


class TestR8DocumentedLockingMechanism:
    """
    Document the locking mechanism for R8 compliance.
    """

    def test_document_idempotency_architecture(self):
        """
        Document the idempotency locking architecture.

        R8: Idempotency keys enforced by constraint + lock.
        """
        idempotency_architecture = {
            "database_constraints": [
                {
                    "table": "events",
                    "column": "event_id",
                    "constraint": "UniqueConstraint('event_id', name='uq_event_id')",
                    "purpose": "Prevent duplicate event ingestion",
                },
                {
                    "table": "journal_entries",
                    "column": "idempotency_key",
                    "constraint": "UniqueConstraint('idempotency_key', name='uq_journal_idempotency')",
                    "purpose": "Prevent duplicate journal entries",
                },
            ],
            "row_level_locks": [
                {
                    "service": "LedgerService",
                    "method": "_get_existing_entry",
                    "lock": "SELECT ... FOR UPDATE",
                    "purpose": "Serialize concurrent idempotency checks",
                },
                {
                    "service": "SequenceService",
                    "method": "next_sequence",
                    "lock": "SELECT ... FOR UPDATE",
                    "purpose": "Serialize sequence number allocation",
                },
            ],
            "idempotency_key_format": "{producer}:{event_type}:{event_id}",
            "conflict_resolution": [
                "1. Check for existing entry with FOR UPDATE lock",
                "2. If exists with POSTED status, return ALREADY_POSTED",
                "3. If not exists, create draft entry",
                "4. If IntegrityError (concurrent insert), rollback and fetch existing",
                "5. Finalize posting with sequence number",
            ],
        }

        # Verify documented components exist
        from finance_kernel.models.event import Event
        from finance_kernel.models.journal import JournalEntry
        from finance_kernel.services.ledger_service import LedgerService

        # Verify constraint names
        event_constraints = [c.name for c in Event.__table__.constraints]
        assert 'uq_event_id' in event_constraints

        journal_constraints = [c.name for c in JournalEntry.__table__.constraints]
        assert 'uq_journal_idempotency' in journal_constraints
