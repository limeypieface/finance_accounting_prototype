"""
B1: Single-Posting Latency Benchmark.

Posts 50 events per scenario through ModulePostingService.post_event()
and measures end-to-end latency.

Scenarios:
  - simple_2_line:       Inventory receipt, 2 lines, no engine
  - complex_multi_line:  Payroll accrual, 6+ lines
  - engine_requiring:    Inventory receipt with PPV variance engine

Regression thresholds (generous — catches broken code, not slow machines):
  - simple_2_line:       p95 < 200ms
  - complex_multi_line:  p95 < 300ms
  - engine_requiring:    p95 < 400ms
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from tests.benchmarks.conftest import EFFECTIVE, SCENARIO_FACTORIES
from tests.benchmarks.helpers import (
    BenchTimer,
    print_benchmark_header,
    print_benchmark_table,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

N = 50  # postings per scenario

REGRESSION_THRESHOLDS = {
    "simple_2_line": 200.0,
    "complex_multi_line": 300.0,
    "engine_requiring": 400.0,
}


class TestSinglePostingLatency:
    """B1: End-to-end posting latency by event complexity."""

    def test_single_posting_latency(self, bench_posting_service):
        ctx = bench_posting_service
        service = ctx["service"]
        actor_id = ctx["actor_id"]
        timer = BenchTimer()

        summaries = []

        for scenario_name, factory in SCENARIO_FACTORIES.items():
            for i in range(N):
                evt = factory(iteration=i)
                with timer.measure(scenario_name, iteration=i):
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
                assert result.is_success, (
                    f"Posting failed at iteration {i} for {scenario_name}: "
                    f"{result.status.value} — {result.message}"
                )

            summaries.append(timer.summary(scenario_name))

        # Print results
        print_benchmark_header("B1 Single-Posting Latency")
        print_benchmark_table(summaries, regression_thresholds=REGRESSION_THRESHOLDS)

        # Assert regression thresholds
        for s in summaries:
            threshold = REGRESSION_THRESHOLDS.get(s.label)
            if threshold is not None:
                assert s.p95_ms <= threshold, (
                    f"REGRESSION: {s.label} p95={s.p95_ms:.1f}ms > {threshold:.0f}ms"
                )
