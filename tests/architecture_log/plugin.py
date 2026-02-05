"""
Synthesized analysis from logs: which parts of the system each test touched.

No full logs are stored or printed. We attach a handler to the finance_kernel logger;
for each log record we only evaluate which boundaries it indicates (config_load,
db_session, persistence, etc.) and accumulate a set per test. At session end we
print a matrix: test -> list of boundaries touched. Log-based and works on any test.
Set ARCHITECTURE_LOG_SHOW_MATRIX=1 to print the matrix.
"""

from __future__ import annotations

import logging
import os

import pytest

from tests.architecture_log.markers import markers_hit_by_record

# Per-test: only the synthesized set of boundary names (no log content stored).
_test_marker_sets: dict[str, set[str]] = {}
_current_nodeid: str | None = None

# Results for the matrix: (nodeid, hit) for every test that ran
_architecture_log_results: list[tuple[str, set[str]]] = []


class ArchitectureLogHandler(logging.Handler):
    """For each log record, only derive which boundaries it indicates and add to the current test's set."""

    def emit(self, record: logging.LogRecord) -> None:
        if _current_nodeid is None:
            return
        try:
            hit = markers_hit_by_record(record)
            if hit:
                _test_marker_sets.setdefault(_current_nodeid, set()).update(hit)
        except Exception:
            pass


_handler: ArchitectureLogHandler | None = None
_KERNEL_LOGGER_NAME = "finance_kernel"


def _kernel_logger() -> logging.Logger:
    return logging.getLogger(_KERNEL_LOGGER_NAME)


def pytest_runtest_setup(item) -> None:
    global _handler, _current_nodeid
    if _handler is None:
        _handler = ArchitectureLogHandler()
        _handler.setLevel(logging.DEBUG)
    kernel = _kernel_logger()
    if _handler not in kernel.handlers:
        kernel.addHandler(_handler)
    _current_nodeid = item.nodeid
    _test_marker_sets.pop(_current_nodeid, None)


def pytest_runtest_teardown(item) -> None:
    global _current_nodeid
    if _handler is not None and _current_nodeid is not None:
        _handler.flush()
        _kernel_logger().removeHandler(_handler)
    _current_nodeid = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Record synthesized analysis for this test (which boundaries the logs indicated)."""
    outcome = yield
    report = outcome.get_result()
    if call.when != "call":
        return
    hit = _test_marker_sets.get(item.nodeid, set())
    _architecture_log_results.append((item.nodeid, hit))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Print synthesized matrix: test -> parts of the system touched (from logs). No full logs."""
    if os.environ.get("ARCHITECTURE_LOG_SHOW_MATRIX", "") != "1":
        return
    if not _architecture_log_results:
        return
    MAX_TEST_LEN = 52
    rows = sorted(_architecture_log_results, key=lambda x: x[0])
    h1, h2 = "Test", "Parts touched"
    lines = []
    lines.append("")
    lines.append(f"  {h1:<{MAX_TEST_LEN}} │ {h2}")
    lines.append(f"  {'─' * MAX_TEST_LEN} ┼ {'─' * 40}")
    for nodeid, hit in rows:
        short = nodeid if len(nodeid) <= MAX_TEST_LEN else nodeid[: MAX_TEST_LEN - 1] + "…"
        touched = ", ".join(sorted(hit)) if hit else "(none)"
        lines.append(f"  {short:<{MAX_TEST_LEN}} │ {touched}")
    # Summary: make "wired" vs "not wired" explicit
    none_count = sum(1 for _, h in rows if not h)
    persistence_count = sum(1 for _, h in rows if "persistence" in h)
    lines.append("")
    lines.append(f"  Summary: {len(rows)} total  |  {none_count} touched nothing (not wired)  |  {persistence_count} touched persistence (wired)")
    lines.append("")
    print("\n".join(lines))
