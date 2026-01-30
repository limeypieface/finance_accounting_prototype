"""
R16-R17: Schema Version Tests.

Events store their schema_version for future replay compatibility.
Currently only schema_version=1 is supported.

These tests verify that:
1. Events store schema_version=1 at ingestion time
2. Unsupported schema versions are rejected
3. Schema version is immutable after ingestion
4. Journal entries can trace back to source event schema
"""

import pytest
from uuid import uuid4
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.models.event import Event
from finance_kernel.models.journal import JournalEntry
from finance_kernel.domain.clock import DeterministicClock


class TestSchemaVersionStorage:
    """
    Tests for schema version storage and retrieval.
    """

    def test_event_stores_schema_version(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that events store schema_version=1 at ingestion time.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            schema_version=1,
        )

        assert result.status == PostingStatus.POSTED

        event = session.execute(
            select(Event).where(Event.event_id == result.event_id)
        ).scalar_one()

        assert event.schema_version == 1, (
            f"Event should have schema_version=1, got {event.schema_version}"
        )

    def test_default_schema_version_is_one(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that schema_version defaults to 1 when not specified.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            # schema_version not specified - should default to 1
        )

        assert result.status == PostingStatus.POSTED

        event = session.execute(
            select(Event).where(Event.event_id == result.event_id)
        ).scalar_one()

        assert event.schema_version == 1

    def test_unsupported_schema_version_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that unsupported schema versions are rejected at ingestion.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            schema_version=999,  # Unsupported version
        )

        assert result.status == PostingStatus.INGESTION_FAILED, (
            f"Unsupported schema version should be rejected, got {result.status}"
        )

        # Verify no event was created
        events = session.execute(select(Event)).scalars().all()
        assert len(events) == 0


class TestSchemaVersionImmutabilityORM:
    """
    Tests for schema version immutability at the ORM layer.
    """

    def test_schema_version_immutable_via_orm(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        GAP TEST: Verify schema_version cannot be modified via ORM.

        Currently the Event model does NOT have ORM-level immutability
        protection. This test documents the gap.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            schema_version=1,
        )
        assert result.status == PostingStatus.POSTED

        event = session.execute(
            select(Event).where(Event.event_id == result.event_id)
        ).scalar_one()

        original_version = event.schema_version
        assert original_version == 1

        # Attempt to modify via ORM
        event.schema_version = 999

        # GAP: This should raise an exception but currently doesn't
        # The Event model lacks @validates decorator or event listener
        try:
            session.flush()
            # If we get here, ORM immutability is NOT enforced
            session.rollback()
            pytest.fail(
                "GAP: Event.schema_version can be modified via ORM. "
                "Need to add @validates decorator or SQLAlchemy event listener."
            )
        except Exception as e:
            # Good - immutability is enforced
            session.rollback()
            pass

class TestSchemaVersionTraceability:
    """
    Tests for tracing journal entries back to source event schema.
    """

    def test_journal_entry_traces_to_event_schema(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that journal entries can trace back to source event's schema version.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            schema_version=1,
        )
        assert result.status == PostingStatus.POSTED

        # Get journal entry
        entry = session.get(JournalEntry, result.journal_entry_id)
        assert entry is not None

        # Trace to source event
        event = session.execute(
            select(Event).where(Event.event_id == entry.source_event_id)
        ).scalar_one()

        assert event.schema_version == 1, (
            f"Source event should have schema_version=1, got {event.schema_version}"
        )

    def test_multiple_events_all_have_schema_version(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that all events in a batch have schema_version recorded.
        """
        # Post several events
        for i in range(5):
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(100 + i), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(100 + i), "currency": "USD"},
                    ]
                },
            )
            assert result.status == PostingStatus.POSTED

        # All events should have schema_version=1
        events = session.execute(select(Event)).scalars().all()
        assert len(events) == 5

        for event in events:
            assert event.schema_version == 1, (
                f"Event {event.event_id} has schema_version={event.schema_version}, expected 1"
            )


class TestSchemaVersionEdgeCases:
    """
    Edge case tests for schema version handling.
    """

    def test_schema_version_zero_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that schema_version=0 is rejected.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            schema_version=0,
        )

        assert result.status == PostingStatus.INGESTION_FAILED

    def test_negative_schema_version_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that negative schema versions are rejected.
        """
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
            schema_version=-1,
        )

        assert result.status == PostingStatus.INGESTION_FAILED

    def test_payload_hash_consistent_for_same_payload(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that payload_hash is consistent for the same payload.

        This is critical for idempotency and replay.
        """
        payload = {
            "lines": [
                {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
            ]
        }

        result1 = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=payload,
            schema_version=1,
        )
        assert result1.status == PostingStatus.POSTED

        result2 = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload=payload,
            schema_version=1,
        )
        assert result2.status == PostingStatus.POSTED

        event1 = session.execute(
            select(Event).where(Event.event_id == result1.event_id)
        ).scalar_one()
        event2 = session.execute(
            select(Event).where(Event.event_id == result2.event_id)
        ).scalar_one()

        assert event1.payload_hash == event2.payload_hash, (
            "Same payload should produce same hash"
        )


# =============================================================================
# PostgreSQL-Specific Tests (Database Trigger Layer)
# =============================================================================
# These tests require PostgreSQL and test the database trigger layer.
# They are separated to avoid fixture conflicts with the main tests.
# =============================================================================

pytestmark_postgres = pytest.mark.postgres


@pytest.mark.postgres
class TestSchemaVersionImmutabilityDatabase:
    """
    Tests for schema version immutability at the database layer.

    These tests require PostgreSQL triggers to be installed.
    """

    def test_schema_version_immutable_via_raw_sql(
        self,
        pg_session_factory,
        postgres_engine,
    ):
        """
        Verify schema_version cannot be modified via raw SQL.

        This test requires PostgreSQL because it relies on database triggers.
        Requires: finance_kernel/db/sql/09_event_immutability.sql to be applied.
        """
        from finance_kernel.domain.clock import SystemClock
        from finance_kernel.models.account import Account, AccountType, NormalBalance
        from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus

        actor_id = uuid4()

        with pg_session_factory() as session:
            # Clean up at start
            session.execute(text("""
                TRUNCATE TABLE
                    journal_lines,
                    journal_entries,
                    audit_events,
                    events,
                    fiscal_periods,
                    accounts,
                    sequence_counters
                CASCADE
            """))
            session.commit()

            # Setup accounts
            for code, name, atype, nbal in [
                ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT),
                ("4000", "Revenue", AccountType.REVENUE, NormalBalance.CREDIT),
            ]:
                session.add(Account(
                    code=code, name=name, account_type=atype,
                    normal_balance=nbal, is_active=True, created_by_id=actor_id,
                ))

            # Setup period
            today = date.today()
            session.add(FiscalPeriod(
                period_code=today.strftime("%Y-%m"),
                name="Test Period",
                start_date=today.replace(day=1),
                end_date=today.replace(day=28),
                status=PeriodStatus.OPEN,
                created_by_id=actor_id,
            ))
            session.commit()

            # Post an event
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            result = orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=clock.now(),
                effective_date=clock.now().date(),
                actor_id=actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
                schema_version=1,
            )
            assert result.status == PostingStatus.POSTED

            # Attempt to modify via raw SQL - should be blocked by trigger
            try:
                session.execute(
                    text("UPDATE events SET schema_version = 999 WHERE event_id = :eid"),
                    {"eid": str(result.event_id)}
                )
                session.commit()
                # If we get here, database immutability is NOT enforced
                pytest.fail(
                    "GAP: Event.schema_version can be modified via raw SQL. "
                    "Apply finance_kernel/db/sql/09_event_immutability.sql to fix."
                )
            except Exception as e:
                # Good - database trigger prevented the update
                session.rollback()
                error_msg = str(e).lower()
                assert "immut" in error_msg or "r10" in error_msg or "violation" in error_msg, (
                    f"Expected immutability error, got: {e}"
                )
            finally:
                # Clean up at end to not affect other tests
                session.execute(text("""
                    TRUNCATE TABLE
                        journal_lines,
                        journal_entries,
                        audit_events,
                        events,
                        fiscal_periods,
                        accounts,
                        sequence_counters
                    CASCADE
                """))
                session.commit()
