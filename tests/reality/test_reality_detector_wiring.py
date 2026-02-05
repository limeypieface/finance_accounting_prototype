"""
Placeholder so tests/reality/conftest.py is loaded when running the full suite (e.g. pytest tests/).

The dependency reality detector hooks and instrumentation are in conftest.py.
Tests marked @pytest.mark.system are checked to have touched real DB, config load,
and persistence write.
"""

import pytest


def test_reality_detector_module_loaded():
    """No-op: ensures this directory is collected and reality conftest is loaded."""
    from tests.reality.tracker import BoundaryTracker, get_tracker
    t = get_tracker()
    assert t is None or isinstance(t, BoundaryTracker)


@pytest.mark.system
@pytest.mark.skip(reason="Sanity: un-skip and run alone to verify detector fails (expect Missing: ...)")
def test_reality_detector_fails_when_no_boundaries_touched():
    """System test that touches no DB, config, or persistence — reality check must fail when run."""
    pass  # no session, no get_active_config, no record_posted
