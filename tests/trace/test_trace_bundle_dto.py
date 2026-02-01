"""
Pure unit tests for trace bundle DTOs.

No database required. Tests frozen enforcement, creation,
missing facts, all artifact types, and empty states.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.selectors.trace_selector import (
    ArtifactIdentifier,
    ConflictInfo,
    IntegrityInfo,
    InterpretationInfo,
    JournalEntrySnapshot,
    JournalLineSnapshot,
    LifecycleLink,
    MissingFact,
    OriginEvent,
    ReproducibilityInfo,
    TimelineEntry,
    TraceBundle,
)

# ============================================================================
# Helpers
# ============================================================================


def _now() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_origin() -> OriginEvent:
    return OriginEvent(
        event_id=uuid4(),
        event_type="inventory.receipt",
        occurred_at=_now(),
        effective_date=date(2026, 1, 15),
        actor_id=uuid4(),
        producer="inventory_module",
        payload_hash="abc123" * 10 + "abcd",
        schema_version=1,
        ingested_at=_now(),
    )


def _make_line(seq: int = 1, side: str = "debit") -> JournalLineSnapshot:
    return JournalLineSnapshot(
        line_id=uuid4(),
        account_id=uuid4(),
        account_code=f"1{seq:03d}",
        side=side,
        amount=Decimal("100.00"),
        currency="USD",
        dimensions=None,
        is_rounding=False,
        line_seq=seq,
        exchange_rate_id=None,
    )


def _make_entry(
    lines: tuple[JournalLineSnapshot, ...] | None = None,
) -> JournalEntrySnapshot:
    if lines is None:
        lines = (_make_line(1, "debit"), _make_line(2, "credit"))
    return JournalEntrySnapshot(
        entry_id=uuid4(),
        source_event_id=uuid4(),
        source_event_type="inventory.receipt",
        effective_date=date(2026, 1, 15),
        occurred_at=_now(),
        posted_at=_now(),
        status="posted",
        seq=1,
        idempotency_key="mod:inventory.receipt:evt1",
        reversal_of_id=None,
        description="Test entry",
        lines=lines,
        coa_version=1,
        dimension_schema_version=1,
        rounding_policy_version=1,
        currency_registry_version=1,
        posting_rule_version=1,
    )


def _make_bundle(**overrides) -> TraceBundle:
    defaults = dict(
        version="1.0",
        trace_id=uuid4(),
        generated_at=_now(),
        artifact=ArtifactIdentifier("event", uuid4()),
        origin=_make_origin(),
        journal_entries=(_make_entry(),),
        interpretation=None,
        reproducibility=None,
        timeline=(),
        lifecycle_links=(),
        conflicts=(),
        integrity=IntegrityInfo(
            bundle_hash="deadbeef",
            payload_hash_verified=True,
            balance_verified=True,
            audit_chain_segment_valid=True,
        ),
        missing_facts=(),
    )
    defaults.update(overrides)
    return TraceBundle(**defaults)


# ============================================================================
# Tests
# ============================================================================


class TestArtifactIdentifier:
    def test_creation(self):
        aid = ArtifactIdentifier("event", uuid4())
        assert aid.artifact_type == "event"

    def test_frozen(self):
        aid = ArtifactIdentifier("event", uuid4())
        with pytest.raises(FrozenInstanceError):
            aid.artifact_type = "journal_entry"  # type: ignore[misc]

    @pytest.mark.parametrize("art_type", [
        "event", "journal_entry", "purchase_order", "receipt",
        "invoice", "payment", "cost_lot",
    ])
    def test_all_artifact_types(self, art_type):
        aid = ArtifactIdentifier(art_type, uuid4())
        assert aid.artifact_type == art_type


class TestOriginEvent:
    def test_creation(self):
        origin = _make_origin()
        assert origin.event_type == "inventory.receipt"
        assert origin.schema_version == 1

    def test_frozen(self):
        origin = _make_origin()
        with pytest.raises(FrozenInstanceError):
            origin.event_type = "changed"  # type: ignore[misc]


class TestJournalLineSnapshot:
    def test_creation(self):
        line = _make_line(1, "debit")
        assert line.side == "debit"
        assert line.amount == Decimal("100.00")

    def test_rounding_line(self):
        line = JournalLineSnapshot(
            line_id=uuid4(),
            account_id=uuid4(),
            account_code="9999",
            side="debit",
            amount=Decimal("0.01"),
            currency="USD",
            dimensions=None,
            is_rounding=True,
            line_seq=3,
            exchange_rate_id=None,
        )
        assert line.is_rounding is True


class TestJournalEntrySnapshot:
    def test_creation_with_lines(self):
        entry = _make_entry()
        assert len(entry.lines) == 2
        assert entry.status == "posted"

    def test_r21_fields(self):
        entry = _make_entry()
        assert entry.coa_version == 1
        assert entry.posting_rule_version == 1

    def test_frozen(self):
        entry = _make_entry()
        with pytest.raises(FrozenInstanceError):
            entry.status = "reversed"  # type: ignore[misc]


class TestInterpretationInfo:
    def test_creation(self):
        info = InterpretationInfo(
            source_event_id=uuid4(),
            status="posted",
            econ_event_id=uuid4(),
            journal_entry_ids=("id1", "id2"),
            reason_code=None,
            reason_detail=None,
            profile_id="standard_receipt",
            profile_version=1,
            profile_hash="abc",
            trace_id=uuid4(),
        )
        assert info.status == "posted"
        assert len(info.journal_entry_ids) == 2

    def test_rejected_with_reason(self):
        info = InterpretationInfo(
            source_event_id=uuid4(),
            status="rejected",
            econ_event_id=None,
            journal_entry_ids=None,
            reason_code="INVALID_QUANTITY",
            reason_detail={"max": 100, "actual": -5},
            profile_id="standard_receipt",
            profile_version=1,
            profile_hash=None,
            trace_id=uuid4(),
        )
        assert info.reason_code == "INVALID_QUANTITY"


class TestReproducibilityInfo:
    def test_creation(self):
        info = ReproducibilityInfo(
            coa_version=3,
            dimension_schema_version=2,
            rounding_policy_version=1,
            currency_registry_version=5,
            fx_policy_version=2,
            posting_rule_version=4,
        )
        assert info.coa_version == 3

    def test_all_none(self):
        info = ReproducibilityInfo(
            coa_version=None,
            dimension_schema_version=None,
            rounding_policy_version=None,
            currency_registry_version=None,
            fx_policy_version=None,
            posting_rule_version=None,
        )
        assert info.coa_version is None


class TestTimelineEntry:
    def test_audit_event_entry(self):
        entry = TimelineEntry(
            timestamp=_now(),
            source="audit_event",
            action="journal_posted",
            entity_type="JournalEntry",
            entity_id=str(uuid4()),
            detail={"event_type": "inventory.receipt"},
            seq=42,
        )
        assert entry.source == "audit_event"
        assert entry.seq == 42

    def test_log_entry(self):
        entry = TimelineEntry(
            timestamp=_now(),
            source="structured_log",
            action="posting_started",
            entity_type=None,
            entity_id=None,
            detail={"duration_ms": 15.2},
            seq=None,
        )
        assert entry.source == "structured_log"
        assert entry.seq is None


class TestLifecycleLink:
    def test_creation(self):
        link = LifecycleLink(
            link_id=uuid4(),
            link_type="reversed_by",
            parent_artifact_type="journal_entry",
            parent_artifact_id=uuid4(),
            child_artifact_type="journal_entry",
            child_artifact_id=uuid4(),
            creating_event_id=uuid4(),
            created_at=_now(),
            link_metadata=None,
        )
        assert link.link_type == "reversed_by"

    def test_with_metadata(self):
        link = LifecycleLink(
            link_id=uuid4(),
            link_type="paid_by",
            parent_artifact_type="invoice",
            parent_artifact_id=uuid4(),
            child_artifact_type="payment",
            child_artifact_id=uuid4(),
            creating_event_id=uuid4(),
            created_at=_now(),
            link_metadata={"amount_applied": "500.00"},
        )
        assert link.link_metadata["amount_applied"] == "500.00"


class TestConflictInfo:
    def test_creation(self):
        conflict = ConflictInfo(
            action="protocol_violation",
            occurred_at=_now(),
            entity_type="Event",
            entity_id=str(uuid4()),
            payload={"reason": "payload_mismatch"},
        )
        assert conflict.action == "protocol_violation"


class TestIntegrityInfo:
    def test_creation(self):
        info = IntegrityInfo(
            bundle_hash="abc123",
            payload_hash_verified=True,
            balance_verified=True,
            audit_chain_segment_valid=True,
        )
        assert info.payload_hash_verified is True

    def test_unknown_integrity(self):
        info = IntegrityInfo(
            bundle_hash="abc123",
            payload_hash_verified=None,
            balance_verified=None,
            audit_chain_segment_valid=None,
        )
        assert info.payload_hash_verified is None


class TestMissingFact:
    def test_creation(self):
        mf = MissingFact(
            fact="GUARD_EVALUATION",
            expected_source="structured_logs",
            correlation_key="attempt_123",
            detail="Log retention expired",
        )
        assert mf.fact == "GUARD_EVALUATION"
        assert mf.expected_source == "structured_logs"

    def test_no_correlation_key(self):
        mf = MissingFact(
            fact="ORIGIN_EVENT",
            expected_source="events",
            correlation_key=None,
            detail="No event_id provided",
        )
        assert mf.correlation_key is None


class TestTraceBundle:
    def test_creation(self):
        bundle = _make_bundle()
        assert bundle.version == "1.0"
        assert bundle.origin is not None
        assert len(bundle.journal_entries) == 1

    def test_frozen(self):
        bundle = _make_bundle()
        with pytest.raises(FrozenInstanceError):
            bundle.version = "2.0"  # type: ignore[misc]

    def test_empty_state(self):
        bundle = _make_bundle(
            origin=None,
            journal_entries=(),
            interpretation=None,
            reproducibility=None,
            timeline=(),
            lifecycle_links=(),
            conflicts=(),
            missing_facts=(
                MissingFact("ORIGIN_EVENT", "events", None, "No event"),
                MissingFact("JOURNAL_ENTRIES", "journal_entries", None, "No entries"),
            ),
        )
        assert bundle.origin is None
        assert len(bundle.journal_entries) == 0
        assert len(bundle.missing_facts) == 2

    def test_with_all_sections(self):
        bundle = _make_bundle(
            interpretation=InterpretationInfo(
                source_event_id=uuid4(),
                status="posted",
                econ_event_id=uuid4(),
                journal_entry_ids=("id1",),
                reason_code=None,
                reason_detail=None,
                profile_id="std",
                profile_version=1,
                profile_hash="h",
                trace_id=uuid4(),
            ),
            reproducibility=ReproducibilityInfo(
                coa_version=1,
                dimension_schema_version=1,
                rounding_policy_version=1,
                currency_registry_version=1,
                fx_policy_version=1,
                posting_rule_version=1,
            ),
            timeline=(
                TimelineEntry(
                    timestamp=_now(),
                    source="audit_event",
                    action="event_ingested",
                    entity_type="Event",
                    entity_id=str(uuid4()),
                    detail=None,
                    seq=1,
                ),
            ),
            lifecycle_links=(
                LifecycleLink(
                    link_id=uuid4(),
                    link_type="reversed_by",
                    parent_artifact_type="journal_entry",
                    parent_artifact_id=uuid4(),
                    child_artifact_type="journal_entry",
                    child_artifact_id=uuid4(),
                    creating_event_id=uuid4(),
                    created_at=_now(),
                    link_metadata=None,
                ),
            ),
            conflicts=(
                ConflictInfo(
                    action="protocol_violation",
                    occurred_at=_now(),
                    entity_type="Event",
                    entity_id=str(uuid4()),
                    payload={"reason": "test"},
                ),
            ),
        )
        assert bundle.interpretation is not None
        assert bundle.reproducibility is not None
        assert len(bundle.timeline) == 1
        assert len(bundle.lifecycle_links) == 1
        assert len(bundle.conflicts) == 1
