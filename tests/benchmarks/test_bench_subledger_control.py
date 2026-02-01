"""
B5: Subledger G9 Reconciliation Overhead Benchmark.

Compares posting latency for events that trigger subledger control
validation (G9 reconciliation queries) vs events that only post
to the GL (no subledger).

Subledger control validation queries account balances via aggregation
queries against journal_lines. This benchmark measures whether that
extra work is within acceptable bounds.

Regression threshold: subledger added latency < 50ms p95
"""

from __future__ import annotations

import logging
from decimal import Decimal
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


def _make_gl_only_event(*, iteration: int = 0) -> dict:
    """Payroll accrual — GL-only, no subledger posting or G9 validation."""
    return {
        "event_type": "payroll.accrual",
        "amount": Decimal("125000.00"),
        "currency": "USD",
        "payload": {
            "gross_pay": "125000.00",
            "federal_tax_amount": "25000.00",
            "state_tax_amount": "8750.00",
            "fica_amount": "9562.50",
            "net_pay_amount": "81687.50",
        },
        "producer": "payroll",
    }


def _make_subledger_event(*, iteration: int = 0) -> dict:
    """Inventory receipt — triggers subledger posting + G9 control validation."""
    return {
        "event_type": "inventory.receipt",
        "amount": Decimal("25000.00"),
        "currency": "USD",
        "payload": {"quantity": 500, "has_variance": False},
        "producer": "inventory",
    }


class TestSubledgerControlOverhead:
    """B5: G9 reconciliation query cost."""

    def test_subledger_control_overhead(self, bench_posting_service):
        ctx = bench_posting_service
        service = ctx["service"]
        actor_id = ctx["actor_id"]
        timer = BenchTimer()

        # --- GL-only postings ---
        for i in range(N):
            evt = _make_gl_only_event(iteration=i)
            with timer.measure("gl_only", iteration=i):
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
                f"GL-only failed at {i}: {result.status.value} — {result.message}"
            )

        # --- Subledger postings ---
        for i in range(N):
            evt = _make_subledger_event(iteration=i)
            with timer.measure("with_subledger", iteration=i):
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
                f"Subledger failed at {i}: {result.status.value} — {result.message}"
            )

        gl_summary = timer.summary("gl_only")
        sl_summary = timer.summary("with_subledger")

        added_latency_p95 = sl_summary.p95_ms - gl_summary.p95_ms
        added_latency_mean = sl_summary.mean_ms - gl_summary.mean_ms

        # Print results
        print_benchmark_header("B5 Subledger G9 Control Overhead")
        print_benchmark_table([gl_summary, sl_summary])

        print(f"  Added latency (mean): {added_latency_mean:.1f}ms")
        print(f"  Added latency (p95):  {added_latency_p95:.1f}ms")
        print()

        threshold = 50.0
        # Only assert if subledger is actually slower (it might not be
        # if the events exercise different code paths with different costs)
        if added_latency_p95 > 0:
            status = "PASS" if added_latency_p95 <= threshold else "FAIL"
            print(f"  [{status}] Added p95 latency: {added_latency_p95:.1f}ms (threshold: < {threshold:.0f}ms)")
        else:
            print(f"  [PASS] Subledger not slower than GL-only (delta: {added_latency_p95:.1f}ms)")
        print()

        # Only fail if subledger is significantly slower
        if added_latency_p95 > 0:
            assert added_latency_p95 <= threshold, (
                f"REGRESSION: Subledger added p95 latency={added_latency_p95:.1f}ms > {threshold:.0f}ms"
            )
