"""
Integration tests for TraceSelector.

Requires PostgreSQL. Tests trace bundle assembly against real data
from existing ledger tables, events, audit events, and economic links.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.models.economic_link import EconomicLinkModel
from finance_kernel.models.event import Event
from finance_kernel.models.interpretation_outcome import (
    InterpretationOutcome,
    OutcomeStatus,
)
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.selectors.trace_selector import (
    LogQueryPort,
    MissingFact,
    TraceBundle,
    TraceSelector,
)
from finance_kernel.utils.hashing import hash_payload


# ============================================================================
# Stub LogQueryPort
# ============================================================================


class StubLogQuery:
    """Test implementation of LogQueryPort."""

    def __init__(self, records: list[dict] | None = None):
        self._records = records or []

    def query_by_correlation_id(self, correlation_id: str) -> list[dict]:
        return [r for r in self._records if r.get("correlation_id") == correlation_id]

    def query_by_event_id(self, event_id: str) -> list[dict]:
        return [r for r in self._records if r.get("event_id") == event_id]

    def query_by_trace_id(self, trace_id: str) -> list[dict]:
        return [r for r in self._records if r.get("trace_id") == trace_id]


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def trace_selector(session, deterministic_clock):
    """TraceSelector without LogQueryPort."""
    return TraceSelector(session, clock=deterministic_clock)


@pytest.fixture
def trace_selector_with_logs(session, deterministic_clock):
    """Factory for TraceSelector with a StubLogQuery."""

    def _make(records: list[dict]):
        return TraceSelector(
            session,
            clock=deterministic_clock,
            log_query=StubLogQuery(records),
        )

    return _make


# ============================================================================
# Helpers
# ============================================================================


def _create_event(session, clock, actor_id, event_type="test.event", payload=None):
    """Create a test Event record."""
    event_id = uuid4()
    payload = payload or {"test": "data", "amount": "100.00"}
    evt = Event(
        event_id=event_id,
        event_type=event_type,
        occurred_at=clock.now(),
        effective_date=clock.now().date(),
        actor_id=actor_id,
        producer="test",
        payload=payload,
        payload_hash=hash_payload(payload),
        schema_version=1,
        ingested_at=clock.now(),
    )
    session.add(evt)
    session.flush()
    return evt


def _create_posted_entry(
    session, clock, actor_id, event, accounts, seq=1,
    amount=Decimal("100.00"), debit_key="cash", credit_key="revenue",
):
    """Create a posted JournalEntry with balanced lines.

    Follows the DRAFT → lines → POSTED workflow required by
    the immutability listeners and balanced-entry trigger.
    """
    entry = JournalEntry(
        source_event_id=event.event_id,
        source_event_type=event.event_type,
        occurred_at=event.occurred_at,
        effective_date=event.effective_date,
        actor_id=actor_id,
        status=JournalEntryStatus.DRAFT,
        idempotency_key=f"test:{event.event_type}:{event.event_id}",
        posting_rule_version=1,
        coa_version=1,
        dimension_schema_version=1,
        rounding_policy_version=1,
        currency_registry_version=1,
        description="Test entry",
        created_by_id=actor_id,
    )
    session.add(entry)
    session.flush()

    debit = JournalLine(
        journal_entry_id=entry.id,
        account_id=accounts[debit_key].id,
        side=LineSide.DEBIT,
        amount=amount,
        currency="USD",
        is_rounding=False,
        line_seq=1,
        created_by_id=actor_id,
    )
    credit = JournalLine(
        journal_entry_id=entry.id,
        account_id=accounts[credit_key].id,
        side=LineSide.CREDIT,
        amount=amount,
        currency="USD",
        is_rounding=False,
        line_seq=2,
        created_by_id=actor_id,
    )
    session.add_all([debit, credit])
    session.flush()

    # Transition DRAFT → POSTED (allowed by immutability listeners)
    entry.status = JournalEntryStatus.POSTED
    entry.posted_at = clock.now()
    entry.seq = seq
    session.flush()

    return entry


def _create_audit_event(
    session, clock, actor_id, entity_type, entity_id, action, seq, payload=None,
):
    """Create an AuditEvent record with a synthetic hash chain."""
    p = payload or {}
    p_hash = hash_payload(p)
    ae = AuditEvent(
        seq=seq,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_id=actor_id,
        occurred_at=clock.now(),
        payload=p,
        payload_hash=p_hash,
        prev_hash=None,
        hash=hash_payload({"seq": seq, "action": action.value, "p_hash": p_hash}),
    )
    session.add(ae)
    session.flush()
    return ae


def _create_economic_link(
    session, clock, event_id,
    parent_type, parent_id,
    child_type, child_id,
    link_type="FULFILLED_BY",
    metadata=None,
):
    """Create an EconomicLinkModel record."""
    link = EconomicLinkModel(
        link_type=link_type,
        parent_artifact_type=parent_type,
        parent_artifact_id=parent_id,
        child_artifact_type=child_type,
        child_artifact_id=child_id,
        creating_event_id=event_id,
        created_at=clock.now(),
        link_metadata=metadata,
    )
    session.add(link)
    session.flush()
    return link


def _create_interpretation_outcome(
    session, clock, event_id,
    status=OutcomeStatus.POSTED,
    econ_event_id=None,
    journal_entry_ids=None,
    reason_code=None,
    reason_detail=None,
    profile_id="test_profile",
    profile_version=1,
):
    """Create an InterpretationOutcome record."""
    outcome = InterpretationOutcome(
        source_event_id=event_id,
        status=status,
        econ_event_id=econ_event_id,
        journal_entry_ids=journal_entry_ids,
        reason_code=reason_code,
        reason_detail=reason_detail,
        profile_id=profile_id,
        profile_version=profile_version,
        profile_hash=None,
        trace_id=uuid4(),
        created_at=clock.now(),
    )
    session.add(outcome)
    session.flush()
    return outcome


# ============================================================================
# Tests: trace_by_event_id
# ============================================================================


class TestTraceByEventId:
    def test_happy_path(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Trace by event_id returns a complete bundle."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        entry = _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert isinstance(bundle, TraceBundle)
        assert bundle.version == "1.0"
        assert bundle.artifact.artifact_type == "event"
        assert bundle.artifact.artifact_id == event.event_id
        assert bundle.origin is not None
        assert bundle.origin.event_id == event.event_id
        assert bundle.origin.event_type == "test.event"
        assert len(bundle.journal_entries) == 1
        assert bundle.journal_entries[0].entry_id == entry.id

    def test_includes_lines_with_account_codes(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Journal entry snapshots include line details with account codes."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        snap = bundle.journal_entries[0]
        assert len(snap.lines) == 2
        codes = {line.account_code for line in snap.lines}
        assert "1000" in codes  # cash
        assert "4000" in codes  # revenue
        sides = {line.side for line in snap.lines}
        assert sides == {"debit", "credit"}
        assert all(line.amount == Decimal("100.00") for line in snap.lines)
        assert all(line.currency == "USD" for line in snap.lines)

    def test_includes_audit_trail(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Audit events appear in the timeline."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        _create_audit_event(
            session, deterministic_clock, test_actor_id,
            "Event", event.event_id,
            AuditAction.EVENT_INGESTED, seq=100,
            payload={"event_type": "test.event"},
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        audit_entries = [t for t in bundle.timeline if t.source == "audit_event"]
        assert len(audit_entries) >= 1
        assert any(t.action == "event_ingested" for t in audit_entries)

    def test_includes_lifecycle_links(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Economic links appear as lifecycle links."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        entry = _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        receipt_id = uuid4()
        _create_economic_link(
            session, deterministic_clock, event.event_id,
            parent_type="receipt", parent_id=receipt_id,
            child_type="journal_entry", child_id=entry.id,
            link_type="FULFILLED_BY",
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert len(bundle.lifecycle_links) >= 1
        link = bundle.lifecycle_links[0]
        assert link.link_type == "FULFILLED_BY"
        assert link.child_artifact_id == entry.id

    def test_unknown_event_returns_missing_facts(self, trace_selector):
        """Unknown event_id produces missing facts."""
        unknown_id = uuid4()
        bundle = trace_selector.trace_by_event_id(unknown_id)

        assert bundle.origin is None
        assert len(bundle.journal_entries) == 0
        facts = {mf.fact for mf in bundle.missing_facts}
        assert "ORIGIN_EVENT" in facts
        assert "JOURNAL_ENTRIES" in facts


# ============================================================================
# Tests: trace_by_journal_entry_id
# ============================================================================


class TestTraceByJournalEntryId:
    def test_resolves_event_from_entry(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Tracing by entry ID resolves the source event."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        entry = _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_journal_entry_id(entry.id)

        assert bundle.artifact.artifact_type == "journal_entry"
        assert bundle.artifact.artifact_id == entry.id
        assert bundle.origin is not None
        assert bundle.origin.event_id == event.event_id
        assert len(bundle.journal_entries) >= 1
        assert any(e.entry_id == entry.id for e in bundle.journal_entries)


# ============================================================================
# Tests: trace_by_artifact_ref
# ============================================================================


class TestTraceByArtifactRef:
    def test_event_type_delegates(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """artifact_type='event' delegates to trace_by_event_id."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_artifact_ref("event", event.event_id)

        assert bundle.artifact.artifact_type == "event"
        assert bundle.origin is not None
        assert bundle.origin.event_id == event.event_id

    def test_journal_entry_type_delegates(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """artifact_type='journal_entry' delegates to trace_by_journal_entry_id."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        entry = _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_artifact_ref("journal_entry", entry.id)

        assert bundle.artifact.artifact_type == "journal_entry"
        assert bundle.origin is not None

    def test_custom_type_resolves_via_links(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Custom artifact type resolves journal entries via economic links."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        entry = _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        po_id = uuid4()
        _create_economic_link(
            session, deterministic_clock, event.event_id,
            parent_type="purchase_order", parent_id=po_id,
            child_type="journal_entry", child_id=entry.id,
            link_type="FULFILLED_BY",
        )

        bundle = trace_selector.trace_by_artifact_ref("purchase_order", po_id)

        assert bundle.artifact.artifact_type == "purchase_order"
        assert bundle.artifact.artifact_id == po_id
        assert len(bundle.journal_entries) >= 1


# ============================================================================
# Tests: Interpretation
# ============================================================================


class TestInterpretation:
    def test_posted_outcome_included(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """InterpretationOutcome with POSTED status appears in bundle."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        entry = _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        _create_interpretation_outcome(
            session, deterministic_clock, event.event_id,
            status=OutcomeStatus.POSTED,
            econ_event_id=uuid4(),
            journal_entry_ids=[str(entry.id)],
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.interpretation is not None
        assert bundle.interpretation.status == "posted"
        assert bundle.interpretation.source_event_id == event.event_id
        assert bundle.interpretation.profile_id == "test_profile"

    def test_rejected_outcome_included(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Rejected outcome includes reason code."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_interpretation_outcome(
            session, deterministic_clock, event.event_id,
            status=OutcomeStatus.REJECTED,
            econ_event_id=None,
            journal_entry_ids=None,
            reason_code="INVALID_QUANTITY",
            reason_detail={"max": 100, "actual": -5},
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.interpretation is not None
        assert bundle.interpretation.status == "rejected"
        assert bundle.interpretation.reason_code == "INVALID_QUANTITY"

    def test_no_outcome_is_not_missing_fact(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Pipeline A events without outcomes don't produce a MissingFact."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.interpretation is None
        interp_facts = [mf for mf in bundle.missing_facts if "INTERPRETATION" in mf.fact]
        assert len(interp_facts) == 0


# ============================================================================
# Tests: R21 Reproducibility
# ============================================================================


class TestReproducibility:
    def test_r21_fields_from_journal_entry(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """R21 snapshot populated from journal entry version fields."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.reproducibility is not None
        assert bundle.reproducibility.coa_version == 1
        assert bundle.reproducibility.dimension_schema_version == 1
        assert bundle.reproducibility.rounding_policy_version == 1
        assert bundle.reproducibility.currency_registry_version == 1
        assert bundle.reproducibility.posting_rule_version == 1

    def test_no_entries_no_reproducibility(self, trace_selector):
        """Empty trace has no reproducibility info."""
        bundle = trace_selector.trace_by_event_id(uuid4())
        assert bundle.reproducibility is None


# ============================================================================
# Tests: Integrity
# ============================================================================


class TestIntegrity:
    def test_payload_hash_verified(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Bundle verifies event payload hash matches stored hash."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.integrity.payload_hash_verified is True

    def test_balance_verified(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Bundle verifies all entries are balanced (debits == credits)."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.integrity.balance_verified is True

    def test_audit_chain_segment_valid(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Audit segment with monotonic sequences validates."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        _create_audit_event(
            session, deterministic_clock, test_actor_id,
            "Event", event.event_id,
            AuditAction.EVENT_INGESTED, seq=200,
        )
        _create_audit_event(
            session, deterministic_clock, test_actor_id,
            "Event", event.event_id,
            AuditAction.JOURNAL_POSTED, seq=201,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert bundle.integrity.audit_chain_segment_valid is True

    def test_bundle_hash_deterministic(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Same event traced twice produces the same bundle hash."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle1 = trace_selector.trace_by_event_id(event.event_id)
        bundle2 = trace_selector.trace_by_event_id(event.event_id)

        assert bundle1.integrity.bundle_hash == bundle2.integrity.bundle_hash
        assert len(bundle1.integrity.bundle_hash) == 64

    def test_bundle_hash_is_sha256(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Bundle hash is a 64-char lowercase hex string (SHA-256)."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        h = bundle.integrity.bundle_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_unknown_event_integrity_is_none(self, trace_selector):
        """Unknown event produces None integrity checks (nothing to verify)."""
        bundle = trace_selector.trace_by_event_id(uuid4())
        assert bundle.integrity.payload_hash_verified is None
        assert bundle.integrity.balance_verified is None


# ============================================================================
# Tests: LogQueryPort
# ============================================================================


class TestLogQueryPort:
    def test_no_log_query_declares_missing_fact(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Without LogQueryPort, STRUCTURED_LOGS missing fact declared."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        log_facts = [mf for mf in bundle.missing_facts if mf.fact == "STRUCTURED_LOGS"]
        assert len(log_facts) == 1
        assert log_facts[0].expected_source == "interpretation_outcome.decision_log"

    def test_with_log_query_includes_log_entries(
        self, session, trace_selector_with_logs, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """With LogQueryPort, log entries appear in timeline."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        log_records = [
            {
                "event_id": str(event.event_id),
                "message": "posting_started",
                "ts": "2026-01-15T12:00:00+00:00",
                "duration_ms": 5.2,
            },
            {
                "event_id": str(event.event_id),
                "message": "posting_completed",
                "ts": "2026-01-15T12:00:01+00:00",
                "duration_ms": 10.1,
            },
        ]
        selector = trace_selector_with_logs(log_records)
        bundle = selector.trace_by_event_id(event.event_id)

        log_entries = [t for t in bundle.timeline if t.source == "structured_log"]
        assert len(log_entries) == 2
        assert any(t.action == "posting_started" for t in log_entries)
        assert any(t.action == "posting_completed" for t in log_entries)

        # No missing fact for logs when port is provided
        log_facts = [mf for mf in bundle.missing_facts if mf.fact == "STRUCTURED_LOGS"]
        assert len(log_facts) == 0


# ============================================================================
# Tests: Protocol Violations
# ============================================================================


class TestConflicts:
    def test_protocol_violation_appears_as_conflict(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Protocol violations from audit trail appear in conflicts."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        _create_audit_event(
            session, deterministic_clock, test_actor_id,
            "Event", event.event_id,
            AuditAction.PROTOCOL_VIOLATION, seq=300,
            payload={"reason": "payload_mismatch"},
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert len(bundle.conflicts) >= 1
        conflict = bundle.conflicts[0]
        assert conflict.action == "protocol_violation"
        assert conflict.entity_type == "Event"

    def test_no_violations_empty_conflicts(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """Clean event has no conflicts."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )

        bundle = trace_selector.trace_by_event_id(event.event_id)

        assert len(bundle.conflicts) == 0


# ============================================================================
# Tests: Read-Only Safety
# ============================================================================


class TestReadOnlySafety:
    def test_no_writes_during_assembly(
        self, session, trace_selector, standard_accounts,
        test_actor_id, deterministic_clock,
    ):
        """TraceSelector assembly performs no INSERT, UPDATE, or DELETE."""
        event = _create_event(session, deterministic_clock, test_actor_id)
        _create_posted_entry(
            session, deterministic_clock, test_actor_id, event, standard_accounts,
        )
        session.flush()

        # Count rows before trace
        event_count_before = session.execute(
            select(func.count()).select_from(Event)
        ).scalar()
        entry_count_before = session.execute(
            select(func.count()).select_from(JournalEntry)
        ).scalar()
        audit_count_before = session.execute(
            select(func.count()).select_from(AuditEvent)
        ).scalar()

        # Perform trace
        bundle = trace_selector.trace_by_event_id(event.event_id)
        session.flush()

        # Count rows after trace
        event_count_after = session.execute(
            select(func.count()).select_from(Event)
        ).scalar()
        entry_count_after = session.execute(
            select(func.count()).select_from(JournalEntry)
        ).scalar()
        audit_count_after = session.execute(
            select(func.count()).select_from(AuditEvent)
        ).scalar()

        assert event_count_before == event_count_after
        assert entry_count_before == entry_count_after
        assert audit_count_before == audit_count_after
        assert bundle is not None


# ============================================================================
# Tests: Full Pipeline Integration
# ============================================================================


class TestPipelineIntegration:
    def test_trace_event_posted_via_coordinator(
        self,
        session,
        standard_accounts,
        current_period,
        post_via_coordinator,
        deterministic_clock,
    ):
        """Trace an event posted through the full interpretation pipeline."""
        source_event_id = uuid4()
        result = post_via_coordinator(
            source_event_id=source_event_id,
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("250.00"),
        )
        assert result.success

        selector = TraceSelector(session, clock=deterministic_clock)
        bundle = selector.trace_by_event_id(source_event_id)

        # Origin resolved
        assert bundle.origin is not None
        assert bundle.origin.event_id == source_event_id

        # Journal entries found
        assert len(bundle.journal_entries) >= 1
        snap = bundle.journal_entries[0]
        assert snap.status == "posted"
        assert len(snap.lines) >= 2

        # Interpretation outcome found
        assert bundle.interpretation is not None
        assert bundle.interpretation.status == "posted"

        # Integrity checks pass
        assert bundle.integrity.payload_hash_verified is True
        assert bundle.integrity.balance_verified is True
        assert bundle.integrity.bundle_hash != ""
        assert len(bundle.integrity.bundle_hash) == 64
