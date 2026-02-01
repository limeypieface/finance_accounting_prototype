"""
Benchmark timing infrastructure.

Provides BenchTimer (context-manager based timing collector),
TimingRecord (individual measurement), and formatted console output.
"""

from __future__ import annotations

import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimingRecord:
    """A single timing measurement."""

    label: str
    iteration: int
    elapsed_ns: int

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_ns / 1_000_000


@dataclass
class TimingSummary:
    """Statistical summary over a set of TimingRecords."""

    label: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    min_ms: float
    max_ms: float


class BenchTimer:
    """Collects timing measurements via context managers.

    Usage::

        timer = BenchTimer()
        for i in range(50):
            with timer.measure("post_event", iteration=i):
                service.post_event(...)

        stats = timer.summary("post_event")
        print(f"p95 = {stats.p95_ms:.1f}ms")
    """

    def __init__(self) -> None:
        self._records: dict[str, list[TimingRecord]] = {}

    @contextmanager
    def measure(self, label: str, iteration: int = 0):
        """Context manager that records elapsed time for *label*."""
        t0 = time.perf_counter_ns()
        yield
        elapsed = time.perf_counter_ns() - t0
        rec = TimingRecord(label=label, iteration=iteration, elapsed_ns=elapsed)
        self._records.setdefault(label, []).append(rec)

    def records(self, label: str) -> list[TimingRecord]:
        """Return raw records for *label*."""
        return list(self._records.get(label, []))

    def summary(self, label: str) -> TimingSummary:
        """Compute p50/p95/p99/mean/min/max for *label*."""
        recs = self._records.get(label, [])
        if not recs:
            return TimingSummary(
                label=label, count=0,
                p50_ms=0, p95_ms=0, p99_ms=0,
                mean_ms=0, min_ms=0, max_ms=0,
            )
        durations = sorted(r.elapsed_ms for r in recs)
        n = len(durations)

        def _percentile(data: list[float], pct: float) -> float:
            """Linear-interpolation percentile (matches numpy default)."""
            k = (n - 1) * pct / 100
            lo = int(k)
            hi = min(lo + 1, n - 1)
            frac = k - lo
            return data[lo] + frac * (data[hi] - data[lo])

        return TimingSummary(
            label=label,
            count=n,
            p50_ms=_percentile(durations, 50),
            p95_ms=_percentile(durations, 95),
            p99_ms=_percentile(durations, 99),
            mean_ms=statistics.mean(durations),
            min_ms=durations[0],
            max_ms=durations[-1],
        )

    def all_labels(self) -> list[str]:
        """Return all recorded labels in insertion order."""
        return list(self._records.keys())


# ---------------------------------------------------------------------------
# Console output formatting
# ---------------------------------------------------------------------------

_SEP = "\u2550"  # ═
_THIN = "\u2500"  # ─


def print_benchmark_header(title: str) -> None:
    """Print a boxed header for a benchmark group."""
    border = _SEP * 59
    print()
    print(f"  {border}")
    print(f"    BENCHMARK RESULTS: {title}")
    print(f"  {border}")
    print()


def print_benchmark_table(
    rows: list[TimingSummary],
    *,
    regression_thresholds: dict[str, float] | None = None,
) -> None:
    """Print a formatted table of timing summaries.

    *regression_thresholds* maps label → max acceptable p95_ms.
    """
    header = (
        f"  {'Scenario':<26s}  {'N':>3s}   {'p50':>7s}  {'p95':>7s}  "
        f"{'p99':>7s}  {'mean':>7s}  {'max':>7s}"
    )
    print(header)
    sep = f"  {_THIN * 26}  {_THIN * 3}   {_THIN * 7}  {_THIN * 7}  {_THIN * 7}  {_THIN * 7}  {_THIN * 7}"
    print(sep)

    all_pass = True
    for s in rows:
        line = (
            f"  {s.label:<26s}  {s.count:>3d}   "
            f"{s.p50_ms:>6.1f}ms {s.p95_ms:>6.1f}ms "
            f"{s.p99_ms:>6.1f}ms {s.mean_ms:>6.1f}ms "
            f"{s.max_ms:>6.1f}ms"
        )
        print(line)
        if regression_thresholds and s.label in regression_thresholds:
            threshold = regression_thresholds[s.label]
            if s.p95_ms > threshold:
                print(f"    ** REGRESSION: p95 {s.p95_ms:.1f}ms > threshold {threshold:.0f}ms **")
                all_pass = False

    print()
    if regression_thresholds:
        if all_pass:
            print("  [PASS] All within regression thresholds.")
        else:
            print("  [FAIL] Some benchmarks exceeded regression thresholds.")
    print()


def print_ratio_result(
    label: str,
    ratio: float,
    threshold: float,
    *,
    unit: str = "x",
) -> None:
    """Print a single ratio metric with pass/fail."""
    status = "PASS" if ratio <= threshold else "FAIL"
    print(f"  {label}: {ratio:.2f}{unit}  (threshold: < {threshold:.1f}{unit})  [{status}]")
