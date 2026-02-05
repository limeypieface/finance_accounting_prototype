"""
Serialization firewall tests.

Validates that the firewall detects non-JSON-safe values at persistence points.
"""

from __future__ import annotations

import json

import pytest

from tests.serialization.firewall import (
    SerializationFirewallError,
    assert_json_safe,
)


class TestAssertJsonSafe:
    """assert_json_safe accepts JSON-serializable values and rejects others."""

    def test_accepts_dict_list_primitive(self):
        assert_json_safe({"a": 1, "b": "x"})
        assert_json_safe([1, "y", None])
        assert_json_safe("ok")
        assert_json_safe(42)
        assert_json_safe(True)
        assert_json_safe(None)

    def test_accepts_nested_structure(self):
        assert_json_safe({"logs": [{"ts": "2026-01-01T00:00:00", "message": "x"}]})

    def test_rejects_unknown_type(self):
        class C:
            pass
        with pytest.raises(SerializationFirewallError) as exc_info:
            assert_json_safe({"bad": C()}, "decision_log")
        assert "decision_log" in str(exc_info.value)
        assert "JSON" in str(exc_info.value).lower() or "serializ" in str(exc_info.value).lower()

    def test_rejects_currency_like_object(self):
        """Domain Currency is not JSON-serializable; firewall should catch before persistence."""
        class CurrencyLike:
            code = "USD"
        with pytest.raises(SerializationFirewallError) as exc_info:
            assert_json_safe({"currency": CurrencyLike()}, "InterpretationOutcome.decision_log")
        assert "decision_log" in str(exc_info.value)


class TestRoundTrip:
    """json.dumps then json.loads preserves structure for safe values."""

    def test_roundtrip_preserves(self):
        data = [{"ts": "2026-01-01T00:00:00", "message": "hello", "level": "INFO"}]
        assert_json_safe(data)
        assert json.loads(json.dumps(data)) == data


class TestFirewallAtOutcomeRecorder:
    """Prove the serialization firewall is wired at OutcomeRecorder (persistence boundary)."""

    def test_firewall_catches_bad_decision_log_at_record_blocked(
        self, session, outcome_recorder
    ):
        """Call OutcomeRecorder.record_blocked with non-JSON-safe decision_log; firewall must raise."""
        from uuid import uuid4

        source_event_id = uuid4()
        class NotJSONSafe:
            pass

        with pytest.raises(SerializationFirewallError) as exc_info:
            outcome_recorder.record_blocked(
                source_event_id=source_event_id,
                profile_id="p",
                profile_version=1,
                reason_code="TEST",
                decision_log=[{"bad": NotJSONSafe()}],
            )
        assert "decision_log" in str(exc_info.value)
        assert exc_info.value.code == "SERIALIZATION_FIREWALL"
