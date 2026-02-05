"""
Unit test: reality detector rule (tracker with no boundaries touched fails satisfies_reality_check).

Proves the rule is correct without needing the full teardown hook (which would require
running a system-marked test that touches nothing).
"""

import pytest


def test_reality_check_fails_when_nothing_touched():
    """Tracker with no boundaries touched must fail satisfies_reality_check."""
    from tests.reality.tracker import boundary_tracker, reset_tracker

    reset_tracker()
    t = boundary_tracker()
    # Don't touch DB, config, or persistence
    ok, missing = t.satisfies_reality_check()
    assert ok is False
    assert "real DB connection" in missing
    assert "at least one config load" in missing
    assert "at least one persistence write" in missing
