"""
Pure unit tests for trace bundle hash determinism.

No database required. Tests hash stability, volatile field exclusion,
and canonical ordering.
"""

from dataclasses import asdict
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from finance_kernel.utils.hashing import hash_trace_bundle

# ============================================================================
# Helpers
# ============================================================================


def _fixed_uuid() -> UUID:
    return UUID("12345678-1234-5678-1234-567812345678")


def _fixed_timestamp() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_bundle_dict(**overrides) -> dict:
    defaults = {
        "version": "1.0",
        "trace_id": str(_fixed_uuid()),
        "generated_at": _fixed_timestamp().isoformat(),
        "artifact": {
            "artifact_type": "event",
            "artifact_id": str(_fixed_uuid()),
        },
        "origin": {
            "event_id": str(_fixed_uuid()),
            "event_type": "inventory.receipt",
            "occurred_at": _fixed_timestamp().isoformat(),
            "effective_date": "2026-01-15",
            "actor_id": str(_fixed_uuid()),
            "producer": "test",
            "payload_hash": "abc123",
            "schema_version": 1,
            "ingested_at": _fixed_timestamp().isoformat(),
        },
        "journal_entries": [],
        "interpretation": None,
        "reproducibility": None,
        "timeline": [],
        "lifecycle_links": [],
        "conflicts": [],
        "integrity": {
            "bundle_hash": "",
            "payload_hash_verified": True,
            "balance_verified": True,
            "audit_chain_segment_valid": True,
        },
        "missing_facts": [],
    }
    defaults.update(overrides)
    return defaults


# ============================================================================
# Tests
# ============================================================================


class TestHashDeterminism:
    def test_same_input_same_hash(self):
        bundle = _make_bundle_dict()
        hash1 = hash_trace_bundle(bundle)
        hash2 = hash_trace_bundle(bundle)
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        bundle1 = _make_bundle_dict()
        bundle2 = _make_bundle_dict(version="2.0")
        assert hash_trace_bundle(bundle1) != hash_trace_bundle(bundle2)

    def test_hash_is_sha256_hex(self):
        bundle = _make_bundle_dict()
        h = hash_trace_bundle(bundle)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestVolatileFieldExclusion:
    def test_generated_at_excluded(self):
        bundle1 = _make_bundle_dict(generated_at="2026-01-15T12:00:00Z")
        bundle2 = _make_bundle_dict(generated_at="2026-01-20T18:30:00Z")
        assert hash_trace_bundle(bundle1) == hash_trace_bundle(bundle2)

    def test_trace_id_excluded(self):
        bundle1 = _make_bundle_dict(trace_id=str(uuid4()))
        bundle2 = _make_bundle_dict(trace_id=str(uuid4()))
        assert hash_trace_bundle(bundle1) == hash_trace_bundle(bundle2)

    def test_bundle_hash_excluded(self):
        bundle1 = _make_bundle_dict()
        bundle1["integrity"]["bundle_hash"] = "hash_v1"

        bundle2 = _make_bundle_dict()
        bundle2["integrity"]["bundle_hash"] = "hash_v2"

        assert hash_trace_bundle(bundle1) == hash_trace_bundle(bundle2)

    def test_non_volatile_fields_affect_hash(self):
        bundle1 = _make_bundle_dict()
        bundle2 = _make_bundle_dict()
        bundle2["integrity"]["payload_hash_verified"] = False
        assert hash_trace_bundle(bundle1) != hash_trace_bundle(bundle2)


class TestHashStability:
    def test_empty_bundle_produces_stable_hash(self):
        bundle = _make_bundle_dict(
            origin=None,
            journal_entries=[],
            interpretation=None,
            reproducibility=None,
            timeline=[],
            lifecycle_links=[],
            conflicts=[],
            missing_facts=[],
        )
        h = hash_trace_bundle(bundle)
        # Should produce the same hash every time
        assert h == hash_trace_bundle(bundle)
        assert len(h) == 64

    def test_dict_key_order_irrelevant(self):
        bundle1 = {"version": "1.0", "artifact": {"type": "event"}}
        bundle2 = {"artifact": {"type": "event"}, "version": "1.0"}
        assert hash_trace_bundle(bundle1) == hash_trace_bundle(bundle2)

    def test_with_journal_entries(self):
        entry = {
            "entry_id": str(_fixed_uuid()),
            "source_event_id": str(_fixed_uuid()),
            "status": "posted",
            "lines": [
                {"side": "debit", "amount": "100.00", "currency": "USD"},
                {"side": "credit", "amount": "100.00", "currency": "USD"},
            ],
        }
        bundle = _make_bundle_dict(journal_entries=[entry])
        h1 = hash_trace_bundle(bundle)
        h2 = hash_trace_bundle(bundle)
        assert h1 == h2

    def test_missing_volatile_fields_graceful(self):
        """Hash works even if volatile fields are already absent."""
        bundle = {"version": "1.0", "artifact": {"type": "event"}}
        # No generated_at, trace_id, or integrity keys
        h = hash_trace_bundle(bundle)
        assert len(h) == 64

    def test_integrity_without_bundle_hash_key(self):
        """Hash works if integrity dict exists but has no bundle_hash."""
        bundle = _make_bundle_dict()
        del bundle["integrity"]["bundle_hash"]
        h = hash_trace_bundle(bundle)
        assert len(h) == 64
