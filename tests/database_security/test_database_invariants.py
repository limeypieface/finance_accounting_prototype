"""
Database-Level Invariant and Security Tests.

These tests verify that critical financial invariants are enforced at the
DATABASE level, not just the application level. This provides defense-in-depth
against:

1. Transaction Isolation Attacks - dirty reads, phantom reads, lost updates
2. Constraint Bypass Attempts - raw SQL that bypasses ORM validation
3. Rollback Side Effects - orphaned records, sequence gaps
4. Audit Atomicity - audit events under high concurrency
5. Concurrent Trigger Races - timing attacks on immutability triggers

These tests require PostgreSQL with appropriate isolation levels configured.
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from uuid import uuid4
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from finance_kernel.db.engine import get_engine, get_session_factory
from finance_kernel.db.triggers import triggers_installed


def require_postgres_check(engine):
    """Check if running on PostgreSQL."""
    if engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL")


def require_triggers_check(engine):
    """Check if database triggers are installed."""
    if not triggers_installed(engine):
        pytest.skip("Database triggers not installed")


# =============================================================================
# 1. TRANSACTION ISOLATION ATTACKS
# =============================================================================


class TestTransactionIsolation:
    """
    Tests for transaction isolation level enforcement.

    Financial systems MUST prevent:
    - Dirty reads (seeing uncommitted data)
    - Non-repeatable reads (data changing between reads)
    - Phantom reads (new rows appearing in repeated queries)
    - Lost updates (concurrent modifications overwriting each other)
    """

    @pytest.fixture
    def isolation_test_account(self, pg_session, postgres_engine):
        """Create an account for isolation testing."""
        require_postgres_check(postgres_engine)

        actor_id = uuid4()
        account_id = uuid4()
        code = f"ISO{uuid4().hex[:8]}"

        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Isolation Test', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": str(account_id), "code": code, "actor_id": str(actor_id)},
        )
        pg_session.commit()
        return {"id": account_id, "code": code, "actor_id": actor_id}

    def test_no_dirty_reads_on_account_balance(self, postgres_engine, isolation_test_account):
        """
        CRITICAL: Verify Thread B cannot read Thread A's uncommitted changes.

        Scenario:
        1. Thread A starts transaction, updates account name (uncommitted)
        2. Thread B reads account name in separate transaction
        3. Thread B should see OLD value, not Thread A's uncommitted change
        4. Thread A rolls back
        """
        session_factory = get_session_factory()
        account_id = str(isolation_test_account["id"])
        original_name = "Isolation Test"
        uncommitted_name = "DIRTY_READ_TEST"

        results = {"thread_a_started": False, "thread_b_read": None, "thread_a_rolled_back": False}
        barrier = threading.Barrier(2, timeout=10)

        def thread_a_uncommitted_write():
            """Write but don't commit, then rollback."""
            session = session_factory()
            try:
                session.execute(
                    text("UPDATE accounts SET name = :name WHERE id = :id"),
                    {"name": uncommitted_name, "id": account_id}
                )
                results["thread_a_started"] = True
                barrier.wait()  # Signal Thread B to read
                time.sleep(0.5)  # Hold transaction open
                session.rollback()
                results["thread_a_rolled_back"] = True
            finally:
                session.close()

        def thread_b_read():
            """Read while Thread A has uncommitted changes."""
            session = session_factory()
            try:
                barrier.wait()  # Wait for Thread A to make uncommitted change
                time.sleep(0.1)  # Small delay to ensure Thread A's write is in progress
                result = session.execute(
                    text("SELECT name FROM accounts WHERE id = :id"),
                    {"id": account_id}
                ).fetchone()
                results["thread_b_read"] = result[0] if result else None
                session.commit()
            finally:
                session.close()

        threads = [
            threading.Thread(target=thread_a_uncommitted_write),
            threading.Thread(target=thread_b_read),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # Thread B should have read the ORIGINAL value, not the uncommitted change
        assert results["thread_b_read"] == original_name, (
            f"DIRTY READ DETECTED! Thread B read '{results['thread_b_read']}' "
            f"but should have read '{original_name}'"
        )

    def test_lost_update_prevention(self, postgres_engine, isolation_test_account):
        """
        Verify concurrent updates don't lose changes (lost update anomaly).

        Scenario:
        1. Thread A reads account, prepares update
        2. Thread B reads same account, prepares update
        3. Thread A commits update
        4. Thread B commits update - should see conflict or serialize properly
        """
        session_factory = get_session_factory()
        account_id = str(isolation_test_account["id"])

        results = {"thread_a_committed": False, "thread_b_result": None}
        barrier = threading.Barrier(2, timeout=10)

        def thread_a_update():
            """First updater."""
            session = session_factory()
            try:
                session.execute(
                    text("SELECT name FROM accounts WHERE id = :id FOR UPDATE"),
                    {"id": account_id}
                )
                barrier.wait()
                time.sleep(0.1)
                session.execute(
                    text("UPDATE accounts SET name = 'Thread_A_Update' WHERE id = :id"),
                    {"id": account_id}
                )
                session.commit()
                results["thread_a_committed"] = True
            except Exception as e:
                session.rollback()
                results["thread_a_committed"] = f"ERROR: {e}"
            finally:
                session.close()

        def thread_b_update():
            """Second updater - should wait or fail."""
            session = session_factory()
            try:
                barrier.wait()
                session.execute(
                    text("SELECT name FROM accounts WHERE id = :id FOR UPDATE"),
                    {"id": account_id}
                )
                session.execute(
                    text("UPDATE accounts SET name = 'Thread_B_Update' WHERE id = :id"),
                    {"id": account_id}
                )
                session.commit()
                results["thread_b_result"] = "committed"
            except OperationalError as e:
                session.rollback()
                results["thread_b_result"] = f"blocked: {e}"
            finally:
                session.close()

        threads = [
            threading.Thread(target=thread_a_update),
            threading.Thread(target=thread_b_update),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # Verify one thread won - no lost updates
        session = session_factory()
        try:
            result = session.execute(
                text("SELECT name FROM accounts WHERE id = :id"),
                {"id": account_id}
            ).fetchone()
            final_name = result[0]
            session.commit()
        finally:
            session.close()

        assert final_name in ("Thread_A_Update", "Thread_B_Update"), (
            f"Lost update! Final value is '{final_name}' but should be one of the thread updates"
        )


# =============================================================================
# 2. CONSTRAINT BYPASS ATTEMPTS
# =============================================================================


class TestConstraintBypass:
    """
    Tests that database-level constraints cannot be bypassed via raw SQL.
    """

    def test_unbalanced_journal_entry_via_raw_sql(self, pg_session, postgres_engine):
        """
        CRITICAL: Attempt to create an unbalanced journal entry via raw SQL.

        An entry with 100 debit and 99 credit MUST be REJECTED by the database.
        The trg_journal_entry_balance_check trigger enforces R12 compliance.
        """
        require_postgres_check(postgres_engine)
        require_triggers_check(postgres_engine)

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        account_id_1 = uuid4()
        account_id_2 = uuid4()

        # Create test accounts
        for acc_id, code in [(account_id_1, f"BAL1{uuid4().hex[:6]}"), (account_id_2, f"BAL2{uuid4().hex[:6]}")]:
            pg_session.execute(
                text("""
                    INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                         is_active, created_at, created_by_id)
                    VALUES (:id, :code, 'Balance Test', 'asset', 'debit', true, NOW(), :actor_id)
                """),
                {"id": str(acc_id), "code": code, "actor_id": str(actor_id)},
            )

        # Create source event
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.unbalanced', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        # Create journal entry (draft status)
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.unbalanced', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {
                "id": str(entry_id),
                "event_id": str(event_id),
                "actor_id": str(actor_id),
                "idempotency_key": f"test:unbalanced:{entry_id}",
            },
        )

        # Create UNBALANCED lines: 100 debit, 99 credit (difference of 1.00)
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 1, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_1), "actor_id": str(actor_id)},
        )

        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 99.00, 'USD', 2, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_2), "actor_id": str(actor_id)},
        )

        # Try to POST this unbalanced entry - MUST be blocked by trigger
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
                {"id": str(entry_id)}
            )
            pg_session.commit()

        pg_session.rollback()

        # Verify the error is the R12 balance violation
        error_msg = str(exc_info.value).lower()
        assert "r12" in error_msg or "unbalanced" in error_msg or "balance" in error_msg, (
            f"Expected R12/balance error, got: {exc_info.value}"
        )

    def test_entry_with_single_line_rejected(self, pg_session, postgres_engine):
        """
        Verify entries with fewer than 2 lines cannot be posted.

        R12 requires at least a debit and credit line.
        """
        require_postgres_check(postgres_engine)
        require_triggers_check(postgres_engine)

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        account_id = uuid4()

        # Create account
        pg_session.execute(
            text("""
                INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                     is_active, created_at, created_by_id)
                VALUES (:id, :code, 'Single Line Test', 'asset', 'debit', true, NOW(), :actor_id)
            """),
            {"id": str(account_id), "code": f"SGL{uuid4().hex[:6]}", "actor_id": str(actor_id)},
        )

        # Create event
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.single', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        # Create entry
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.single', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {"id": str(entry_id), "event_id": str(event_id), "actor_id": str(actor_id), "idempotency_key": f"test:single:{entry_id}"},
        )

        # Create only ONE line
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 1, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id), "actor_id": str(actor_id)},
        )

        # Try to post - should fail with minimum line count error
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
                {"id": str(entry_id)}
            )
            pg_session.commit()

        pg_session.rollback()
        error_msg = str(exc_info.value).lower()
        assert "r12" in error_msg or "fewer than 2" in error_msg or "line" in error_msg

    def test_balanced_entry_posts_successfully(self, pg_session, postgres_engine):
        """
        Verify a properly balanced entry CAN be posted.

        This ensures the trigger allows valid entries through.
        """
        require_postgres_check(postgres_engine)
        require_triggers_check(postgres_engine)

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        account_id_1 = uuid4()
        account_id_2 = uuid4()

        # Create accounts
        for acc_id, code in [(account_id_1, f"BAL1{uuid4().hex[:6]}"), (account_id_2, f"BAL2{uuid4().hex[:6]}")]:
            pg_session.execute(
                text("""
                    INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                         is_active, created_at, created_by_id)
                    VALUES (:id, :code, 'Balance Test', 'asset', 'debit', true, NOW(), :actor_id)
                """),
                {"id": str(acc_id), "code": code, "actor_id": str(actor_id)},
            )

        # Create event
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.balanced', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        # Create entry
        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.balanced', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {"id": str(entry_id), "event_id": str(event_id), "actor_id": str(actor_id), "idempotency_key": f"test:balanced:{entry_id}"},
        )

        # Create BALANCED lines: 100 debit, 100 credit
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 1, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_1), "actor_id": str(actor_id)},
        )

        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 100.00, 'USD', 2, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_2), "actor_id": str(actor_id)},
        )

        # Post should succeed
        pg_session.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": str(entry_id)}
        )
        pg_session.commit()

        # Verify it was posted
        result = pg_session.execute(
            text("SELECT status FROM journal_entries WHERE id = :id"),
            {"id": str(entry_id)}
        ).fetchone()
        assert result[0] == "posted", "Balanced entry should be posted successfully"

    def test_cannot_add_line_to_posted_entry(self, pg_session, postgres_engine):
        """
        Verify lines cannot be added to already-posted entries.

        This prevents post-hoc manipulation of posted entries.
        """
        require_postgres_check(postgres_engine)
        require_triggers_check(postgres_engine)

        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()
        account_id_1 = uuid4()
        account_id_2 = uuid4()

        # Create accounts
        for acc_id, code in [(account_id_1, f"ADD1{uuid4().hex[:6]}"), (account_id_2, f"ADD2{uuid4().hex[:6]}")]:
            pg_session.execute(
                text("""
                    INSERT INTO accounts (id, code, name, account_type, normal_balance,
                                         is_active, created_at, created_by_id)
                    VALUES (:id, :code, 'Add Line Test', 'asset', 'debit', true, NOW(), :actor_id)
                """),
                {"id": str(acc_id), "code": code, "actor_id": str(actor_id)},
            )

        # Create and post a balanced entry
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.addline', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash123', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
        )

        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.addline', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {"id": str(entry_id), "event_id": str(event_id), "actor_id": str(actor_id), "idempotency_key": f"test:addline:{entry_id}"},
        )

        # Add balanced lines and post
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'debit', 50.00, 'USD', 1, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_1), "actor_id": str(actor_id)},
        )
        pg_session.execute(
            text("""
                INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                          currency, line_seq, is_rounding, created_at, created_by_id)
                VALUES (:id, :entry_id, :account_id, 'credit', 50.00, 'USD', 2, false, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_2), "actor_id": str(actor_id)},
        )
        pg_session.execute(
            text("UPDATE journal_entries SET status = 'posted' WHERE id = :id"),
            {"id": str(entry_id)}
        )
        pg_session.commit()

        # Now try to add another line - should fail
        with pytest.raises((IntegrityError, ProgrammingError)) as exc_info:
            pg_session.execute(
                text("""
                    INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                              currency, line_seq, is_rounding, created_at, created_by_id)
                    VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 3, false, NOW(), :actor_id)
                """),
                {"id": str(uuid4()), "entry_id": str(entry_id), "account_id": str(account_id_1), "actor_id": str(actor_id)},
            )
            pg_session.commit()

        pg_session.rollback()
        error_msg = str(exc_info.value).lower()
        assert "r12" in error_msg or "posted" in error_msg or "cannot add" in error_msg

    def test_duplicate_idempotency_key_rejected(self, pg_session, postgres_engine):
        """Verify duplicate idempotency keys are rejected at database level."""
        require_postgres_check(postgres_engine)

        actor_id = uuid4()
        event_id_1 = uuid4()
        event_id_2 = uuid4()
        idempotency_key = f"test:duplicate:{uuid4()}"

        # Create first event and entry
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.dup', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash1', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id_1), "actor_id": str(actor_id)},
        )

        pg_session.execute(
            text("""
                INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                            occurred_at, effective_date, actor_id,
                                            status, idempotency_key, posting_rule_version,
                                            created_at, created_by_id)
                VALUES (:id, :event_id, 'test.dup', NOW(), CURRENT_DATE, :actor_id,
                       'draft', :idempotency_key, 1, NOW(), :actor_id)
            """),
            {"id": str(uuid4()), "event_id": str(event_id_1), "actor_id": str(actor_id), "idempotency_key": idempotency_key},
        )
        pg_session.commit()

        # Try duplicate
        pg_session.execute(
            text("""
                INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                   actor_id, producer, payload, payload_hash, schema_version,
                                   ingested_at)
                VALUES (:id, :event_id, 'test.dup2', NOW(), CURRENT_DATE,
                       :actor_id, 'test', '{}', 'hash2', 1, NOW())
            """),
            {"id": str(uuid4()), "event_id": str(event_id_2), "actor_id": str(actor_id)},
        )

        with pytest.raises(IntegrityError) as exc_info:
            pg_session.execute(
                text("""
                    INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                                occurred_at, effective_date, actor_id,
                                                status, idempotency_key, posting_rule_version,
                                                created_at, created_by_id)
                    VALUES (:id, :event_id, 'test.dup2', NOW(), CURRENT_DATE, :actor_id,
                           'draft', :idempotency_key, 1, NOW(), :actor_id)
                """),
                {"id": str(uuid4()), "event_id": str(event_id_2), "actor_id": str(actor_id), "idempotency_key": idempotency_key},
            )
            pg_session.commit()

        pg_session.rollback()


# =============================================================================
# 3. ROLLBACK SAFETY TESTS
# =============================================================================


class TestRollbackSafety:
    """Tests that rollbacks don't leave orphaned or inconsistent data."""

    def test_no_orphaned_lines_after_entry_failure(self, pg_session, postgres_engine):
        """Verify journal lines are rolled back if entry creation fails."""
        require_postgres_check(postgres_engine)

        actor_id = uuid4()
        entry_id = uuid4()
        line_id = uuid4()

        result = pg_session.execute(text("SELECT COUNT(*) FROM journal_lines")).fetchone()
        lines_before = result[0]

        try:
            pg_session.execute(
                text("""
                    INSERT INTO journal_lines (id, journal_entry_id, account_id, side, amount,
                                              currency, line_seq, is_rounding, created_at, created_by_id)
                    VALUES (:id, :entry_id, :account_id, 'debit', 100.00, 'USD', 1, false, NOW(), :actor_id)
                """),
                {"id": str(line_id), "entry_id": str(entry_id), "account_id": str(uuid4()), "actor_id": str(actor_id)},
            )
            pg_session.commit()
            pytest.fail("Expected FK violation")
        except IntegrityError:
            pg_session.rollback()

        result = pg_session.execute(text("SELECT COUNT(*) FROM journal_lines")).fetchone()
        lines_after = result[0]

        assert lines_after == lines_before, f"Orphaned line! Lines before: {lines_before}, after: {lines_after}"


# =============================================================================
# 4. AUDIT ATOMICITY UNDER CONCURRENCY
# =============================================================================


class TestAuditAtomicity:
    """Tests that audit events are created atomically with their source operations."""

    def test_audit_seq_unique_under_concurrent_inserts(self, postgres_engine):
        """Verify audit event sequence numbers remain unique under high concurrency."""
        require_postgres_check(postgres_engine)

        session_factory = get_session_factory()
        num_threads = 20  # Reduced for faster tests
        results = {"seqs": [], "errors": []}
        lock = threading.Lock()
        barrier = threading.Barrier(num_threads, timeout=30)

        def create_audit_event(thread_id):
            session = session_factory()
            try:
                barrier.wait()

                result = session.execute(
                    text("SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events FOR UPDATE")
                ).fetchone()
                next_seq = result[0]

                event_id = uuid4()
                session.execute(
                    text("""
                        INSERT INTO audit_events (id, seq, action, entity_type, entity_id,
                                                 actor_id, occurred_at, payload, payload_hash, hash)
                        VALUES (:id, :seq, 'concurrent_test', 'Test', :entity_id,
                               :actor_id, NOW(), '{}', 'hash', 'hash')
                    """),
                    {"id": str(event_id), "seq": next_seq, "entity_id": str(uuid4()), "actor_id": str(uuid4())},
                )
                session.commit()

                with lock:
                    results["seqs"].append(next_seq)

            except Exception as e:
                session.rollback()
                with lock:
                    results["errors"].append(f"Thread {thread_id}: {e}")
            finally:
                session.close()

        threads = [threading.Thread(target=create_audit_event, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        unique_seqs = set(results["seqs"])
        if len(unique_seqs) != len(results["seqs"]):
            duplicates = [s for s in results["seqs"] if results["seqs"].count(s) > 1]
            pytest.fail(f"DUPLICATE SEQ NUMBERS! Duplicates: {set(duplicates)}")


# =============================================================================
# 5. CONCURRENT TRIGGER RACE CONDITIONS
# =============================================================================


class TestConcurrentTriggerRaces:
    """Tests for race conditions in database trigger execution."""

    def test_concurrent_immutability_check_race(self, postgres_engine):
        """Verify immutability triggers work correctly under concurrent access."""
        require_postgres_check(postgres_engine)
        require_triggers_check(postgres_engine)

        session_factory = get_session_factory()
        actor_id = uuid4()
        event_id = uuid4()
        entry_id = uuid4()

        # Create posted entry
        session = session_factory()
        try:
            session.execute(
                text("""
                    INSERT INTO events (id, event_id, event_type, occurred_at, effective_date,
                                       actor_id, producer, payload, payload_hash, schema_version,
                                       ingested_at)
                    VALUES (:id, :event_id, 'test.race', NOW(), CURRENT_DATE,
                           :actor_id, 'test', '{}', 'hash123', 1, NOW())
                """),
                {"id": str(uuid4()), "event_id": str(event_id), "actor_id": str(actor_id)},
            )

            session.execute(
                text("""
                    INSERT INTO journal_entries (id, source_event_id, source_event_type,
                                                occurred_at, effective_date, actor_id,
                                                status, idempotency_key, posting_rule_version,
                                                description, created_at, created_by_id)
                    VALUES (:id, :event_id, 'test.race', NOW(), CURRENT_DATE, :actor_id,
                           'posted', :idempotency_key, 1, 'Original', NOW(), :actor_id)
                """),
                {"id": str(entry_id), "event_id": str(event_id), "actor_id": str(actor_id), "idempotency_key": f"test:race:{entry_id}"},
            )
            session.commit()
        finally:
            session.close()

        results = {"thread_a": None, "thread_b": None}
        barrier = threading.Barrier(2, timeout=10)

        def attempt_modification(thread_name, new_description):
            sess = session_factory()
            try:
                barrier.wait()
                sess.execute(
                    text("UPDATE journal_entries SET description = :desc WHERE id = :id"),
                    {"desc": new_description, "id": str(entry_id)}
                )
                sess.commit()
                results[thread_name] = "MODIFIED"
            except Exception as e:
                sess.rollback()
                results[thread_name] = f"BLOCKED: {type(e).__name__}"
            finally:
                sess.close()

        threads = [
            threading.Thread(target=attempt_modification, args=("thread_a", "Hacked by A")),
            threading.Thread(target=attempt_modification, args=("thread_b", "Hacked by B")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert "BLOCKED" in results["thread_a"], f"Thread A not blocked: {results['thread_a']}"
        assert "BLOCKED" in results["thread_b"], f"Thread B not blocked: {results['thread_b']}"

        # Verify entry unchanged
        session = session_factory()
        try:
            result = session.execute(
                text("SELECT description FROM journal_entries WHERE id = :id"),
                {"id": str(entry_id)}
            ).fetchone()
            assert result[0] == "Original", f"Entry was modified to: {result[0]}"
        finally:
            session.close()
