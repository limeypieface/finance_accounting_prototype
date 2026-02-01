"""
B4: Decision Journal Overhead Benchmark.

Measures the overhead of LogCapture (structured log capture + JSON
serialization) by comparing posting latency with and without the
capture handler installed.

LogCapture is instantiated and installed automatically by
InterpretationCoordinator.interpret_and_post(). To measure the
"without" baseline, we temporarily patch interpret_and_post to
skip LogCapture installation.

Regression threshold: overhead < 30% of total posting time
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from tests.benchmarks.conftest import EFFECTIVE, make_simple_event
from tests.benchmarks.helpers import (
    BenchTimer,
    print_benchmark_header,
    print_benchmark_table,
    print_ratio_result,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

N = 50  # postings per variant


class TestDecisionJournalOverhead:
    """B4: LogCapture + JSON serialization overhead."""

    def test_decision_journal_overhead(self, bench_posting_service):
        ctx = bench_posting_service
        service = ctx["service"]
        actor_id = ctx["actor_id"]
        coordinator = service._coordinator
        timer = BenchTimer()

        # --- Variant A: Normal posting (with LogCapture) ---
        for i in range(N):
            evt = make_simple_event(iteration=i)
            with timer.measure("with_capture", iteration=i):
                result = service.post_event(
                    event_type=evt["event_type"],
                    payload=evt["payload"],
                    effective_date=EFFECTIVE,
                    actor_id=actor_id,
                    amount=evt["amount"],
                    currency=evt["currency"],
                    producer=evt["producer"],
                    event_id=uuid4(),
                )
            assert result.is_success, f"With-capture failed at {i}"

        # --- Variant B: Posting with LogCapture disabled ---
        # Patch interpret_and_post to use a no-op LogCapture
        from finance_kernel.services.log_capture import LogCapture

        class NoOpCapture(LogCapture):
            """LogCapture that does not install itself."""
            def install(self):
                return self  # No-op: don't add to logger

            def uninstall(self):
                pass  # No-op

        original_interpret = coordinator.interpret_and_post.__func__

        def patched_interpret(self_coord, **kwargs):
            """interpret_and_post with NoOpCapture."""
            # The LogCapture is created inline in interpret_and_post.
            # We patch LogCapture at the module level.
            with patch(
                "finance_kernel.services.interpretation_coordinator.LogCapture",
                NoOpCapture,
            ):
                return original_interpret(self_coord, **kwargs)

        with patch.object(type(coordinator), "interpret_and_post", patched_interpret):
            for i in range(N):
                evt = make_simple_event(iteration=i)
                with timer.measure("without_capture", iteration=i):
                    result = service.post_event(
                        event_type=evt["event_type"],
                        payload=evt["payload"],
                        effective_date=EFFECTIVE,
                        actor_id=actor_id,
                        amount=evt["amount"],
                        currency=evt["currency"],
                        producer=evt["producer"],
                        event_id=uuid4(),
                    )
                assert result.is_success, f"Without-capture failed at {i}"

        # Compute overhead
        with_summary = timer.summary("with_capture")
        without_summary = timer.summary("without_capture")

        overhead_ms = with_summary.mean_ms - without_summary.mean_ms
        overhead_pct = (overhead_ms / with_summary.mean_ms * 100) if with_summary.mean_ms > 0 else 0

        # Print results
        print_benchmark_header("B4 Decision Journal Overhead")
        print_benchmark_table([with_summary, without_summary])

        print(f"  Overhead: {overhead_ms:.1f}ms ({overhead_pct:.1f}% of total)")
        print()

        threshold_pct = 30.0
        status = "PASS" if overhead_pct <= threshold_pct else "FAIL"
        print(f"  [{status}] Overhead {overhead_pct:.1f}% (threshold: < {threshold_pct:.0f}%)")
        print()

        assert overhead_pct <= threshold_pct, (
            f"REGRESSION: LogCapture overhead {overhead_pct:.1f}% > {threshold_pct:.0f}%"
        )
