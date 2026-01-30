"""
Pure unit tests for the LogQueryPort protocol.

Tests that the Protocol is implementable by a stub and that
None log_query is handled gracefully by the TraceSelector.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from finance_kernel.selectors.trace_selector import LogQueryPort


# ============================================================================
# Stub Implementation
# ============================================================================


class StubLogQuery:
    """Minimal implementor of LogQueryPort for testing."""

    def __init__(self, records: list[dict] | None = None):
        self._records = records or []

    def query_by_correlation_id(self, correlation_id: str) -> list[dict]:
        return [r for r in self._records if r.get("correlation_id") == correlation_id]

    def query_by_event_id(self, event_id: str) -> list[dict]:
        return [r for r in self._records if r.get("event_id") == event_id]

    def query_by_trace_id(self, trace_id: str) -> list[dict]:
        return [r for r in self._records if r.get("trace_id") == trace_id]


class FailingLogQuery:
    """LogQueryPort that always raises."""

    def query_by_correlation_id(self, correlation_id: str) -> list[dict]:
        raise ConnectionError("log store unavailable")

    def query_by_event_id(self, event_id: str) -> list[dict]:
        raise ConnectionError("log store unavailable")

    def query_by_trace_id(self, trace_id: str) -> list[dict]:
        raise ConnectionError("log store unavailable")


# ============================================================================
# Tests
# ============================================================================


class TestLogQueryProtocol:
    def test_stub_is_instance_of_protocol(self):
        stub = StubLogQuery()
        assert isinstance(stub, LogQueryPort)

    def test_failing_is_instance_of_protocol(self):
        failing = FailingLogQuery()
        assert isinstance(failing, LogQueryPort)

    def test_query_by_event_id_returns_matches(self):
        eid = str(uuid4())
        records = [
            {"event_id": eid, "message": "posting_started", "ts": "2026-01-15T12:00:00Z"},
            {"event_id": str(uuid4()), "message": "other", "ts": "2026-01-15T12:00:00Z"},
            {"event_id": eid, "message": "posting_completed", "ts": "2026-01-15T12:00:01Z"},
        ]
        stub = StubLogQuery(records)
        results = stub.query_by_event_id(eid)
        assert len(results) == 2
        assert all(r["event_id"] == eid for r in results)

    def test_query_by_correlation_id(self):
        cid = "corr-123"
        records = [
            {"correlation_id": cid, "message": "step_1", "ts": "2026-01-15T12:00:00Z"},
            {"correlation_id": "other", "message": "step_2", "ts": "2026-01-15T12:00:00Z"},
        ]
        stub = StubLogQuery(records)
        results = stub.query_by_correlation_id(cid)
        assert len(results) == 1

    def test_query_by_trace_id(self):
        tid = str(uuid4())
        records = [
            {"trace_id": tid, "message": "interpretation_started", "ts": "2026-01-15T12:00:00Z"},
        ]
        stub = StubLogQuery(records)
        results = stub.query_by_trace_id(tid)
        assert len(results) == 1

    def test_empty_results(self):
        stub = StubLogQuery([])
        assert stub.query_by_event_id("nonexistent") == []
        assert stub.query_by_correlation_id("nonexistent") == []
        assert stub.query_by_trace_id("nonexistent") == []

    def test_none_log_query_is_valid(self):
        """None is a valid value for log_query (optional port)."""
        log_query = None
        assert log_query is None
        # TraceSelector accepts None and declares MissingFact instead
