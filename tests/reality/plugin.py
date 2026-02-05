"""
Reality detector plugin (instrumentation only; no enforcement).

Previously enforced that tests marked @pytest.mark.system touched full stack.
Tracking of which parts of the system are used by which tests is now log-based only
(see tests.architecture_log.plugin). This plugin is kept for optional instrumentation
but does not fail any test or change exit status.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tests.reality.tracker import (
    boundary_tracker,
    get_tracker,
    reset_tracker,
)

# Patches applied at session start and left active.
_reality_patches: list = []


def _install_instrumentation() -> None:
    """Patch boundaries to record when they are used."""
    from tests.reality.tracker import get_tracker

    # 1. Engine.connect (real DB)
    try:
        from finance_kernel.db import engine as db_engine

        def _wrap_engine_connect(e):
            _orig = e.connect
            def _record_connect(*a, **k):
                t = get_tracker()
                if t:
                    t.touch_db_connection()
                return _orig(*a, **k)
            e.connect = _record_connect
            return e

        _orig_init = db_engine.init_engine_from_url
        def _wrapped_init(*a, **k):
            return _wrap_engine_connect(_orig_init(*a, **k))
        _reality_patches.append(patch.object(db_engine, "init_engine_from_url", _wrapped_init))

        _orig_get_engine = db_engine.get_engine
        _get_engine_wrapped = []

        def _wrapped_get_engine():
            e = _orig_get_engine()
            if not _get_engine_wrapped:
                _get_engine_wrapped.append(True)
                _wrap_engine_connect(e)
            return e
        _reality_patches.append(patch.object(db_engine, "get_engine", _wrapped_get_engine))
    except Exception:
        pass

    # 2. get_active_config (config load)
    try:
        import finance_config
        _orig_get_active = finance_config.get_active_config

        def _wrapped_get_active(*a, **k):
            t = get_tracker()
            if t:
                t.touch_config_load()
            return _orig_get_active(*a, **k)

        _reality_patches.append(
            patch.object(finance_config, "get_active_config", _wrapped_get_active)
        )
    except Exception:
        pass

    # 3. SequenceService.next_value
    try:
        from unittest.mock import patch
        from finance_kernel.services import sequence_service
        _orig_next = sequence_service.SequenceService.next_value

        def _wrapped_next(self, name):
            t = get_tracker()
            if t:
                t.touch_sequence_allocator()
            return _orig_next(self, name)

        _reality_patches.append(
            patch.object(sequence_service.SequenceService, "next_value", _wrapped_next)
        )
    except Exception:
        pass

    # 4. OutcomeRecorder persistence
    try:
        from finance_kernel.services import outcome_recorder
        for method_name in ("record_posted", "record_blocked", "record_rejected", "record_provisional", "record_non_posting"):
            if not hasattr(outcome_recorder.OutcomeRecorder, method_name):
                continue
            _orig = getattr(outcome_recorder.OutcomeRecorder, method_name)

            def _make_wrapper(original):
                def _wrapped(self, *a, **k):
                    t = get_tracker()
                    if t:
                        t.touch_persistence_write()
                    return original(self, *a, **k)
                return _wrapped
            _reality_patches.append(
                patch.object(outcome_recorder.OutcomeRecorder, method_name, _make_wrapper(_orig))
            )
    except Exception:
        pass

    for p in _reality_patches:
        p.start()


def _has_system_mark(item) -> bool:
    return item.get_closest_marker("system") is not None


def pytest_configure(config):
    _install_instrumentation()


def pytest_runtest_setup(item):
    if _has_system_mark(item):
        reset_tracker()
        boundary_tracker()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """No enforcement: tracking is log-based only (architecture_log plugin)."""
    outcome = yield
    # Do not fail any test based on markers or boundaries.
    return


def pytest_sessionfinish(session, exitstatus):
    """No report or exit status change."""
    return
