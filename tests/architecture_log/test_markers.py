"""Unit tests for architecture log marker detection."""

import logging

import pytest

from tests.architecture_log.markers import (
    ARCHITECTURE_MARKERS,
    REQUIRED_MARKER_IDS_FOR_SYSTEM,
    markers_hit,
    missing_for_system,
)


def _record(msg: str, **extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord({"name": "test", "msg": msg, "args": (), "levelno": logging.INFO})
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_config_load_marker():
    records = [_record("FINANCE_CONFIG_TRACE", trace_type="FINANCE_CONFIG_TRACE")]
    assert "config_load" in markers_hit(records)


def test_db_session_marker():
    records = [_record("session_created", trace_type="FINANCE_DB_SESSION")]
    assert "db_session" in markers_hit(records)


def test_journal_write_adds_persistence():
    records = [_record("journal_write_started")]
    hit = markers_hit(records)
    assert "journal_write" in hit
    assert "persistence" in hit


def test_outcome_recorded_adds_persistence():
    records = [_record("outcome_recorded")]
    hit = markers_hit(records)
    assert "outcome_recorded" in hit
    assert "persistence" in hit


def test_engine_dispatch_marker():
    records = [_record("engine_dispatch_started")]
    assert "engine_dispatch" in markers_hit(records)
    records2 = [_record("x", trace_type="FINANCE_ENGINE_DISPATCH")]
    assert "engine_dispatch" in markers_hit(records2)


def test_missing_for_system():
    assert missing_for_system(set()) == ["config_load", "db_session", "persistence"]
    assert missing_for_system({"config_load", "db_session", "persistence"}) == []
    assert missing_for_system({"config_load", "db_session"}) == ["persistence"]


def test_required_markers_defined():
    assert "config_load" in REQUIRED_MARKER_IDS_FOR_SYSTEM
    assert "db_session" in REQUIRED_MARKER_IDS_FOR_SYSTEM
    assert "persistence" in REQUIRED_MARKER_IDS_FOR_SYSTEM
    assert len(ARCHITECTURE_MARKERS) >= 5
