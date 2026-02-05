"""
Architecture log markers: canonical list of log signals that indicate a boundary was used.

Each marker is (id, predicate). Predicate(record) is True when the log record indicates
that boundary was crossed. LogRecord has .msg and extra keys as attributes (e.g. .trace_type).
"""

from __future__ import annotations

import logging
from typing import Callable

# (marker_id, predicate on LogRecord)
ARCHITECTURE_MARKERS: list[tuple[str, Callable[[logging.LogRecord], bool]]] = [
    ("config_load", lambda r: getattr(r, "trace_type", None) == "FINANCE_CONFIG_TRACE"),
    ("db_session", lambda r: getattr(r, "trace_type", None) == "FINANCE_DB_SESSION"),
    ("journal_write", lambda r: getattr(r, "msg", "") == "journal_write_started"),
    ("outcome_recorded", lambda r: getattr(r, "msg", "") == "outcome_recorded"),
    (
        "engine_dispatch",
        lambda r: getattr(r, "msg", "") == "engine_dispatch_started"
        or getattr(r, "trace_type", None) == "FINANCE_ENGINE_DISPATCH",
    ),
    ("policy_selection", lambda r: getattr(r, "trace_type", None) == "FINANCE_POLICY_TRACE"),
]

# For @pytest.mark.system we require these. "persistence" = journal_write OR outcome_recorded.
REQUIRED_MARKER_IDS_FOR_SYSTEM = ("config_load", "db_session", "persistence")


def markers_hit_by_record(record: logging.LogRecord) -> set[str]:
    """Return marker ids that this single log record indicates. Used to synthesize per-test analysis without storing logs."""
    hit: set[str] = set()
    for mid, pred in ARCHITECTURE_MARKERS:
        try:
            if pred(record):
                hit.add(mid)
        except Exception:
            continue
    if "journal_write" in hit or "outcome_recorded" in hit:
        hit.add("persistence")
    return hit


def markers_hit(records: list[logging.LogRecord]) -> set[str]:
    """Return set of marker ids that appear in the given log records."""
    hit: set[str] = set()
    for rec in records:
        hit |= markers_hit_by_record(rec)
    return hit


def missing_for_system(hit: set[str]) -> list[str]:
    """Return list of required marker ids missing for a system test."""
    missing = []
    for req in REQUIRED_MARKER_IDS_FOR_SYSTEM:
        if req not in hit:
            missing.append(req)
    return missing
