"""
Database-level immutability trigger tests (R10 Defense-in-Depth).

These tests verify that PostgreSQL triggers block modifications even when
bypassing the ORM (bulk updates, raw SQL, cross-session attacks).

Separated from test_immutability.py because these tests use the module-scoped
``postgres_engine`` fixture (``pg_session``), which cannot safely share a
pytest module with function-scoped ``engine``/``tables`` fixtures.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from finance_kernel.db.immutability import (
    register_immutability_listeners,
    unregister_immutability_listeners,
)
from finance_kernel.exceptions import ImmutabilityViolationError
from finance_kernel.models.journal import JournalEntry, JournalEntryStatus


class TestR10DatabaseTriggers:
    """
    R10 Defense-in-Depth: Database-level trigger enforcement.

    These tests verify that PostgreSQL triggers block modifications
    even when bypassing the ORM (bulk updates, raw SQL).
    """

    @pytest.fixture
    def posted_entry_via_raw_sql(self, pg_session_factory, pg_session):
        """Create a posted journal entry using raw SQL for trigger testing."""
        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

        # Skip if triggers not installed
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

        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

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

        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

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
        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        debit_account_id = uuid4()
        credit_account_id = uuid4()

        # Create event
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

        # Create two accounts (R12 requires balanced entry with >=2 lines)
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test Debit', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": debit_account_id, "code": f"DR{uuid4().hex[:6]}", "actor_id": actor_id},
        )
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test Credit', 'revenue', 'credit', true, NOW(), :actor_id)
            """),
            {"id": credit_account_id, "code": f"CR{uuid4().hex[:6]}", "actor_id": actor_id},
        )

        # Create draft entry
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

        # Add balanced lines while draft (R12 requires >=2 lines to post)
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side,
                                          amount, currency, is_rounding, line_seq,
                                          created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD',
                       false, 1, NOW(), :actor_id)
            """),
            {"id": uuid4(), "entry_id": entry_id, "account_id": debit_account_id, "actor_id": actor_id},
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side,
                                          amount, currency, is_rounding, line_seq,
                                          created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 100.00, 'USD',
                       false, 2, NOW(), :actor_id)
            """),
            {"id": uuid4(), "entry_id": entry_id, "account_id": credit_account_id, "actor_id": actor_id},
        )
        pg_session.commit()

        # Transition from draft to posted should succeed
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


class TestR10TriggerDeleteProtection:
    """
    R10 Delete Completeness (Trigger Layer): Prove that raw SQL DELETE
    is blocked on posted journal lines by PostgreSQL triggers.
    """

    def test_raw_sql_delete_journal_lines_blocked(
        self,
        pg_session_factory,
        pg_session,
    ):
        """Raw SQL DELETE on journal_lines of a posted entry blocked by trigger."""
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        line_id = uuid4()
        debit_account_id = uuid4()
        credit_account_id = uuid4()

        # Create event
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash,
                                   schema_version, ingested_at)
                VALUES (:id, :event_id, 'test.line_del', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash', 1, NOW())
            """),
            {"id": uuid4(), "event_id": event_id, "actor_id": actor_id},
        )

        # Create two accounts (balanced entry requires debit + credit)
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test Debit', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": debit_account_id, "code": f"DR{uuid4().hex[:6]}", "actor_id": actor_id},
        )
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test Credit', 'revenue', 'credit', true, NOW(), :actor_id)
            """),
            {"id": credit_account_id, "code": f"CR{uuid4().hex[:6]}", "actor_id": actor_id},
        )

        # Create entry as DRAFT first (R12 blocks line INSERT on posted entries)
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.line_del', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :key, 1, NOW(), :actor_id)
            """),
            {
                "id": entry_id,
                "event_id": event_id,
                "actor_id": actor_id,
                "key": f"test:line_del:{entry_id}",
            },
        )

        # Create balanced lines (debit + credit) while entry is still draft
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side,
                                          amount, currency, is_rounding, line_seq,
                                          created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD',
                       false, 1, NOW(), :actor_id)
            """),
            {
                "id": line_id,
                "entry_id": entry_id,
                "account_id": debit_account_id,
                "actor_id": actor_id,
            },
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side,
                                          amount, currency, is_rounding, line_seq,
                                          created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 100.00, 'USD',
                       false, 2, NOW(), :actor_id)
            """),
            {
                "id": uuid4(),
                "entry_id": entry_id,
                "account_id": credit_account_id,
                "actor_id": actor_id,
            },
        )

        # Transition to posted (allowed: draft -> posted)
        pg_session.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": str(entry_id)},
        )
        pg_session.commit()

        # Attempt raw SQL delete on the line
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("DELETE FROM journal_lines WHERE id = :id"),
                {"id": str(line_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value) or "restrict_violation" in str(
            exc_info.value
        )
        pg_session.rollback()


class TestR10CrossSessionImmutability:
    """
    R10: Prove that immutability holds when a different database session
    attempts to modify a posted entry created by another session.

    Requires PostgreSQL for true multi-session testing via database triggers.
    """

    def test_second_session_cannot_modify_posted_entry(
        self,
        pg_session_factory,
        pg_session,
    ):
        """
        Session A posts and commits.
        Session B opens, reads the same row, attempts UPDATE -> blocked by trigger.
        """
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Requires PostgreSQL triggers for cross-session test")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        # Session A: create posted entry and commit
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash,
                                   schema_version, ingested_at)
                VALUES (:id, :event_id, 'test.cross', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash', 1, NOW())
            """),
            {"id": uuid4(), "event_id": event_id, "actor_id": actor_id},
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.cross', NOW(), CURRENT_DATE, :actor_id,
                       'posted', :key, 1, NOW(), :actor_id)
            """),
            {
                "id": entry_id,
                "event_id": event_id,
                "actor_id": actor_id,
                "key": f"test:cross:{entry_id}",
            },
        )
        pg_session.commit()

        # Session B: separate connection, attempt modification
        session_b = pg_session_factory()
        try:
            with pytest.raises((IntegrityError, ProgrammingError)):
                session_b.execute(
                    text(
                        "UPDATE journal_entries SET description = 'cross_session_attack' "
                        "WHERE id = :id"
                    ),
                    {"id": str(entry_id)},
                )
                session_b.commit()
        finally:
            session_b.rollback()
            session_b.close()

    def test_second_session_cannot_delete_posted_entry(
        self,
        pg_session_factory,
        pg_session,
    ):
        """
        Session A posts and commits.
        Session B attempts DELETE -> blocked by trigger.
        """
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Requires PostgreSQL triggers for cross-session test")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        # Session A: create posted entry
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash,
                                   schema_version, ingested_at)
                VALUES (:id, :event_id, 'test.cross_del', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash', 1, NOW())
            """),
            {"id": uuid4(), "event_id": event_id, "actor_id": actor_id},
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.cross_del', NOW(), CURRENT_DATE, :actor_id,
                       'posted', :key, 1, NOW(), :actor_id)
            """),
            {
                "id": entry_id,
                "event_id": event_id,
                "actor_id": actor_id,
                "key": f"test:cross_del:{entry_id}",
            },
        )
        pg_session.commit()

        # Session B: attempt delete
        session_b = pg_session_factory()
        try:
            with pytest.raises((IntegrityError, ProgrammingError)):
                session_b.execute(
                    text("DELETE FROM journal_entries WHERE id = :id"),
                    {"id": str(entry_id)},
                )
                session_b.commit()
        finally:
            session_b.rollback()
            session_b.close()


class TestR10TriggerSequenceIntegrity:
    """
    R10 + R21 (Trigger Layer): Prove that raw SQL seq forgery is blocked
    by PostgreSQL triggers.
    """

    def test_raw_sql_seq_update_blocked_by_trigger(
        self,
        pg_session_factory,
        pg_session,
    ):
        """Raw SQL UPDATE of seq on posted entry blocked by database trigger."""
        from sqlalchemy.exc import IntegrityError, ProgrammingError

        from finance_kernel.db.engine import get_engine
        from finance_kernel.db.triggers import triggers_installed

        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Requires PostgreSQL triggers")

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash,
                                   schema_version, ingested_at)
                VALUES (:id, :event_id, 'test.seq', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash', 1, NOW())
            """),
            {"id": uuid4(), "event_id": event_id, "actor_id": actor_id},
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            seq, created_at, created_by_id)
                VALUES (:id, :event_id, 'test.seq', NOW(), CURRENT_DATE, :actor_id,
                       'posted', :key, 1, 42, NOW(), :actor_id)
            """),
            {
                "id": entry_id,
                "event_id": event_id,
                "actor_id": actor_id,
                "key": f"test:seq:{entry_id}",
            },
        )
        pg_session.commit()

        # Attempt to forge seq via raw SQL
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET seq = 99999 WHERE id = :id"),
                {"id": str(entry_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value) or "restrict_violation" in str(
            exc_info.value
        )
        pg_session.rollback()
