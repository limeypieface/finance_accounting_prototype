"""
Serialization firewall: at persistence/export points, validate JSON-safety.

When the test suite runs with SERIALIZATION_FIREWALL=1 (or always in CI), we wrap
decision_log (and optionally other JSON columns) with assert_json_safe before
persistence. If any test stores a non-JSON-safe object, fail immediately with the field path.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from tests.serialization.firewall import assert_json_safe

_firewall_patches: list = []


def _install_firewall() -> None:
    """Wrap persistence points so decision_log is validated before write."""
    from finance_kernel.services import outcome_recorder

    for method_name in ("record_posted", "record_blocked", "record_rejected", "record_provisional", "record_non_posting"):
        if not hasattr(outcome_recorder.OutcomeRecorder, method_name):
            continue
        _orig = getattr(outcome_recorder.OutcomeRecorder, method_name)

        def _make_wrapper(original, name: str):
            def _wrapped(self, *a, **kwargs):
                if "decision_log" in kwargs and kwargs["decision_log"] is not None:
                    assert_json_safe(kwargs["decision_log"], "InterpretationOutcome.decision_log")
                return original(self, *a, **kwargs)
            return _wrapped
        _firewall_patches.append(
            patch.object(
                outcome_recorder.OutcomeRecorder,
                method_name,
                _make_wrapper(_orig, method_name),
            )
        )
    for p in _firewall_patches:
        p.start()


def pytest_configure(config):
    """Install serialization firewall when env SERIALIZATION_FIREWALL is set or always for safety."""
    if os.environ.get("SERIALIZATION_FIREWALL", "1") == "1":
        _install_firewall()
