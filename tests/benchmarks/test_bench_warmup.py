"""
B6: Warm-Up vs Steady-State Benchmark.

Posts 100 sequential events and compares the mean latency of the
first 10 ("warm-up") to the remaining 90 ("steady-state").

First postings are slower because:
  - SQLAlchemy identity map is cold
  - PostgreSQL shared buffers may not have the relevant pages
  - Python import caching / JIT-like effects

Regression threshold: warm-up/steady ratio < 5.0x
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from tests.benchmarks.conftest import EFFECTIVE, make_simple_event
from tests.benchmarks.helpers import (
    BenchTimer,
    print_benchmark_header,
    print_benchmark_table,
    print_ratio_result,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

TOTAL = 100
WARMUP_COUNT = 10


class TestWarmUpVsSteadyState:
    """B6: First-10 vs remaining-90 latency comparison."""

    def test_warmup_vs_steady_state(self, bench_posting_service):
        ctx = bench_posting_service
        service = ctx["service"]
        actor_id = ctx["actor_id"]
        timer = BenchTimer()

        for i in range(TOTAL):
            evt = make_simple_event(iteration=i)
            label = "warmup" if i < WARMUP_COUNT else "steady"

            with timer.measure(label, iteration=i):
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
                f"Posting failed at iteration {i}: {result.status.value}"
            )

        warmup = timer.summary("warmup")
        steady = timer.summary("steady")

        # Print results
        print_benchmark_header("B6 Warm-Up vs Steady-State")
        print_benchmark_table([warmup, steady])

        ratio = warmup.mean_ms / steady.mean_ms if steady.mean_ms > 0 else float("inf")
        print_ratio_result(
            "Warm-up / steady-state mean latency",
            ratio,
            threshold=5.0,
        )

        # Assert regression threshold
        assert ratio < 5.0, (
            f"REGRESSION: warm-up/steady ratio={ratio:.2f}x > 5.0x "
            f"(warmup mean={warmup.mean_ms:.1f}ms, "
            f"steady mean={steady.mean_ms:.1f}ms)"
        )
