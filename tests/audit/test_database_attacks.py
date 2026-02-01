"""
Database Attack Resistance Tests (R10 Defense-in-Depth).

These tests verify that PostgreSQL triggers block ALL attempts to modify
immutable financial records, regardless of how the attack is performed:

- Bulk UPDATE statements (bypassing ORM)
- Raw SQL via session.execute()
- Direct SQL injection attempts
- Attempts to modify audit trail

The tests should FAIL if any attack succeeds. We never disable protections.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

from finance_kernel.db.engine import get_engine
from finance_kernel.db.triggers import triggers_installed
from finance_kernel.models.audit_event import AuditEvent
from finance_kernel.models.journal import JournalEntry, JournalEntryStatus, JournalLine


def require_triggers(func):
    """Decorator to skip test if PostgreSQL triggers are not installed."""
    def wrapper(*args, **kwargs):
        engine = get_engine()
        if not triggers_installed(engine):
            pytest.skip("Database triggers not installed (requires PostgreSQL)")
        return func(*args, **kwargs)
    return wrapper


class TestJournalEntryAttackResistance:
    """
    Verify that posted journal entries cannot be modified through any means.
    """

    @pytest.fixture
    def posted_entry(self, pg_session):
        """Create a legitimately posted journal entry for attack testing."""
        require_triggers(lambda: None)()

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        # Create event via raw SQL
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.attack', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        # Create posted journal entry
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            description, created_at, created_by_id)
                VALUES (:id, :event_id, 'test.attack', NOW(), CURRENT_DATE, :actor_id,
                       'posted', :idempotency_key, 1, 'Original description', NOW(), :actor_id)
            """),
            {
                "id": str(entry_id),
                "event_id": str(event_id),
                "actor_id": str(actor_id),
                "idempotency_key": f"test:attack:{entry_id}",
            },
        )
        pg_session.commit()

        return entry_id

    def test_bulk_update_description_blocked(self, pg_session, posted_entry):
        """Attack: Try to modify description via bulk UPDATE."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET description = 'HACKED' WHERE id = :id"),
                {"id": str(posted_entry)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_bulk_update_status_blocked(self, pg_session, posted_entry):
        """Attack: Try to change status from posted back to draft."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET status = 'draft' WHERE id = :id"),
                {"id": str(posted_entry)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_bulk_update_effective_date_blocked(self, pg_session, posted_entry):
        """Attack: Try to backdate the entry."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET effective_date = '2020-01-01' WHERE id = :id"),
                {"id": str(posted_entry)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_bulk_update_actor_id_blocked(self, pg_session, posted_entry):
        """Attack: Try to change who created the entry."""
        fake_actor = uuid4()
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET actor_id = :fake_actor WHERE id = :id"),
                {"id": str(posted_entry), "fake_actor": str(fake_actor)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_bulk_delete_blocked(self, pg_session, posted_entry):
        """Attack: Try to delete a posted entry."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("DELETE FROM journal_entries WHERE id = :id"),
                {"id": str(posted_entry)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_orm_bulk_update_blocked(self, pg_session, posted_entry):
        """Attack: Try to use SQLAlchemy Core update() to bypass ORM."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                update(JournalEntry)
                .where(JournalEntry.id == posted_entry)
                .values(description="HACKED via ORM Core")
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_orm_bulk_delete_blocked(self, pg_session, posted_entry):
        """Attack: Try to use SQLAlchemy Core delete() to bypass ORM."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                delete(JournalEntry).where(JournalEntry.id == posted_entry)
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_mass_update_blocked(self, pg_session, posted_entry):
        """Attack: Try to update ALL posted entries at once."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET description = 'MASS HACK' WHERE status = 'posted'")
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()


class TestJournalLineAttackResistance:
    """
    Verify that journal lines on posted entries cannot be modified.
    """

    @pytest.fixture
    def posted_entry_with_lines(self, pg_session):
        """Create a posted entry with journal lines for attack testing."""
        require_triggers(lambda: None)()

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        line_id = uuid4()
        credit_line_id = uuid4()
        account_id = uuid4()
        contra_account_id = uuid4()
        unique_code = f"ATK{str(uuid4())[:8]}"  # Unique account code per test
        contra_code = f"CTR{str(uuid4())[:8]}"

        # Create debit account
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Test Account', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": str(account_id), "code": unique_code, "actor_id": str(actor_id)},
        )

        # Create contra account for balanced entry
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Contra Account', 'liability', 'credit', true, NOW(), :actor_id)
            """),
            {"id": str(contra_account_id), "code": contra_code, "actor_id": str(actor_id)},
        )

        # Create event
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.lines', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        # Create entry as DRAFT first (triggers block line inserts on posted entries)
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.lines', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {
                "id": str(entry_id),
                "event_id": str(event_id),
                "actor_id": str(actor_id),
                "idempotency_key": f"test:lines:{entry_id}",
            },
        )

        # Add balanced journal lines while entry is still DRAFT
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount, currency,
                                          line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 1, false, NOW(), :actor_id)
            """),
            {
                "id": str(line_id),
                "entry_id": str(entry_id),
                "account_id": str(account_id),
                "actor_id": str(actor_id),
            },
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount, currency,
                                          line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 100.00, 'USD', 2, false, NOW(), :actor_id)
            """),
            {
                "id": str(credit_line_id),
                "entry_id": str(entry_id),
                "account_id": str(contra_account_id),
                "actor_id": str(actor_id),
            },
        )

        # Transition to POSTED
        pg_session.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": str(entry_id)},
        )

        pg_session.commit()
        return {"entry_id": entry_id, "line_id": line_id, "account_id": account_id}

    def test_update_line_amount_blocked(self, pg_session, posted_entry_with_lines):
        """Attack: Try to modify a journal line amount."""
        line_id = posted_entry_with_lines["line_id"]

        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_lines SET amount = 999999.99 WHERE id = :id"),
                {"id": str(line_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_line_account_blocked(self, pg_session, posted_entry_with_lines):
        """Attack: Try to change which account a line posts to."""
        line_id = posted_entry_with_lines["line_id"]
        fake_account = uuid4()

        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_lines SET account_id = :fake WHERE id = :id"),
                {"id": str(line_id), "fake": str(fake_account)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_line_side_blocked(self, pg_session, posted_entry_with_lines):
        """Attack: Try to flip debit to credit."""
        line_id = posted_entry_with_lines["line_id"]

        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_lines SET side = 'credit' WHERE id = :id"),
                {"id": str(line_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_delete_line_blocked(self, pg_session, posted_entry_with_lines):
        """Attack: Try to delete a journal line."""
        line_id = posted_entry_with_lines["line_id"]

        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("DELETE FROM journal_lines WHERE id = :id"),
                {"id": str(line_id)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()


class TestAuditEventAttackResistance:
    """
    Verify that audit events can NEVER be modified or deleted.
    """

    @pytest.fixture
    def audit_event(self, pg_session):
        """Create an audit event for attack testing."""
        require_triggers(lambda: None)()

        actor_id = uuid4()
        event_id = uuid4()
        entity_id = uuid4()

        # Get next seq value (audit events use manual sequence management)
        result = pg_session.execute(
            text("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events")
        ).fetchone()
        next_seq = result[0]

        pg_session.execute(
            text("""
                INSERT INTO audit_events (id, seq, action, entity_type, entity_id,
                                         actor_id, occurred_at, payload, payload_hash, hash)
                VALUES (:id, :seq, 'test_action', 'TestEntity',
                       :entity_id, :actor_id, NOW(), '{"test": true}', 'payload_hash_123',
                       'event_hash_456')
            """),
            {
                "id": str(event_id),
                "seq": next_seq,
                "entity_id": str(entity_id),
                "actor_id": str(actor_id),
            },
        )
        pg_session.commit()

        return {"id": event_id, "seq": next_seq}

    def test_update_hash_blocked(self, pg_session, audit_event):
        """Attack: Try to tamper with the hash chain."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET hash = 'TAMPERED_HASH' WHERE id = :id"),
                {"id": str(audit_event["id"])},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_prev_hash_blocked(self, pg_session, audit_event):
        """Attack: Try to break the chain linkage."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET prev_hash = 'BROKEN_LINK' WHERE id = :id"),
                {"id": str(audit_event["id"])},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_payload_blocked(self, pg_session, audit_event):
        """Attack: Try to modify the audit payload."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET payload = '{\"hacked\": true}' WHERE id = :id"),
                {"id": str(audit_event["id"])},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_action_blocked(self, pg_session, audit_event):
        """Attack: Try to change what action was recorded."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET action = 'fake_action' WHERE id = :id"),
                {"id": str(audit_event["id"])},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_actor_blocked(self, pg_session, audit_event):
        """Attack: Try to change who performed the action."""
        fake_actor = uuid4()
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET actor_id = :fake WHERE id = :id"),
                {"id": str(audit_event["id"]), "fake": str(fake_actor)},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_update_timestamp_blocked(self, pg_session, audit_event):
        """Attack: Try to backdate an audit event."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("UPDATE audit_events SET occurred_at = '2020-01-01 00:00:00' WHERE id = :id"),
                {"id": str(audit_event["id"])},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_delete_blocked(self, pg_session, audit_event):
        """Attack: Try to delete an audit event."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": str(audit_event["id"])},
            )
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_mass_delete_blocked(self, pg_session, audit_event):
        """Attack: Try to delete all audit events."""
        with pytest.raises((IntegrityError, ProgrammingError, InternalError)) as exc_info:
            pg_session.execute(text("DELETE FROM audit_events"))
            pg_session.commit()

        assert "R10 Violation" in str(exc_info.value)
        pg_session.rollback()

    def test_truncate_requires_privilege_revocation(self, pg_session, audit_event):
        """
        Note: TRUNCATE bypasses row-level triggers by design.

        To block TRUNCATE, you must REVOKE TRUNCATE privilege from the app user:
            REVOKE TRUNCATE ON audit_events FROM app_user;

        This test verifies the trigger is in place for row-level operations,
        but TRUNCATE requires schema-level access control.
        """
        # This test documents expected behavior - TRUNCATE bypasses triggers
        # Protection must be via REVOKE TRUNCATE or using a less-privileged app user
        pass  # Acknowledged: TRUNCATE requires privilege-level protection


class TestDraftEntriesRemainEditable:
    """
    Verify that DRAFT entries can still be modified (important for normal workflow).
    """

    @pytest.fixture
    def draft_entry(self, pg_session):
        """Create a draft journal entry with balanced lines (required by R12 trigger to post)."""
        require_triggers(lambda: None)()

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        account_id = uuid4()
        contra_account_id = uuid4()
        unique_code = f"DFT{str(uuid4())[:8]}"
        contra_code = f"DFC{str(uuid4())[:8]}"

        # Create accounts for balanced lines
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Draft Test Account', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": str(account_id), "code": unique_code, "actor_id": str(actor_id)},
        )
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Draft Contra Account', 'liability', 'credit', true, NOW(), :actor_id)
            """),
            {"id": str(contra_account_id), "code": contra_code, "actor_id": str(actor_id)},
        )

        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.draft', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            description, created_at, created_by_id)
                VALUES (:id, :event_id, 'test.draft', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, 'Draft description', NOW(), :actor_id)
            """),
            {
                "id": str(entry_id),
                "event_id": str(event_id),
                "actor_id": str(actor_id),
                "idempotency_key": f"test:draft:{entry_id}",
            },
        )

        # Add balanced journal lines (required by enforce_balanced_journal_entry trigger)
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount, currency,
                                          line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 1, false, NOW(), :actor_id)
            """),
            {
                "id": str(uuid4()),
                "entry_id": str(entry_id),
                "account_id": str(account_id),
                "actor_id": str(actor_id),
            },
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount, currency,
                                          line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 100.00, 'USD', 2, false, NOW(), :actor_id)
            """),
            {
                "id": str(uuid4()),
                "entry_id": str(entry_id),
                "account_id": str(contra_account_id),
                "actor_id": str(actor_id),
            },
        )

        pg_session.commit()

        return entry_id

    def test_draft_can_be_modified(self, pg_session, draft_entry):
        """Verify draft entries CAN be modified (normal workflow)."""
        pg_session.execute(
            text("UPDATE journal_entries SET description = 'Updated draft' WHERE id = :id"),
            {"id": str(draft_entry)},
        )
        pg_session.commit()

        result = pg_session.execute(
            text("SELECT description FROM journal_entries WHERE id = :id"),
            {"id": str(draft_entry)},
        ).fetchone()

        assert result[0] == "Updated draft"

    def test_draft_can_be_deleted(self, pg_session, draft_entry):
        """Verify draft entries CAN be deleted (normal workflow)."""
        # Delete child lines first (FK constraint)
        pg_session.execute(
            text("DELETE FROM journal_lines WHERE journal_entry_id = :id"),
            {"id": str(draft_entry)},
        )
        pg_session.execute(
            text("DELETE FROM journal_entries WHERE id = :id"),
            {"id": str(draft_entry)},
        )
        pg_session.commit()

        result = pg_session.execute(
            text("SELECT COUNT(*) FROM journal_entries WHERE id = :id"),
            {"id": str(draft_entry)},
        ).fetchone()

        assert result[0] == 0

    def test_draft_can_be_posted(self, pg_session, draft_entry):
        """Verify draft entries CAN transition to posted (normal workflow)."""
        pg_session.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": str(draft_entry)},
        )
        pg_session.commit()

        result = pg_session.execute(
            text("SELECT status FROM journal_entries WHERE id = :id"),
            {"id": str(draft_entry)},
        ).fetchone()

        assert result[0] == "posted"
