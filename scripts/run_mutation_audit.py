#!/usr/bin/env python3
"""
Mutation kill-rate audit runner.

Runs the test suite once per mutation (MUTATION_NAME). For each run, if all tests
pass, pytest exits with 1 (see tests/conftest.py pytest_sessionfinish). This script
fails the build if any mutation run returns 0 (which would mean no tests failed
under that mutation — i.e. tests are not constraining that seam).

By default, captures each run and prints a summary table + insight buckets at the end.
Use --live to stream pytest output (no table).

Usage:
  python scripts/run_mutation_audit.py
  python scripts/run_mutation_audit.py --live
  python scripts/run_mutation_audit.py tests/
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass

# Allow importing tests.mutation when run as script (no PYTHONPATH required).
_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
if _root not in sys.path:
    sys.path.insert(0, _root)

DEFAULT_PATHS = [
    "tests/adversarial",
    "tests/posting",
    "tests/integration",
    "tests/architecture",
    "tests/services",
]

MUTATIONS_ENV = "MUTATION_NAME"

SUMMARY_RE = re.compile(
    r"=+\s+(\d+)\s+failed,\s+(\d+)\s+passed(?:\s*,\s*(\d+)\s+errors?)?\s+in\s+([\d.]+)s\s+=+"
)


@dataclass
class RunStats:
    mutation: str
    exit_code: int
    failed: int
    passed: int
    errors: int
    time_s: float

    @property
    def total(self) -> int:
        return self.failed + self.passed + self.errors

    @property
    def killed(self) -> bool:
        """True if this mutation caused at least one failure or error (audit expects this)."""
        return self.exit_code != 0


def _parse_summary(stdout: str) -> tuple[int, int, int, float] | None:
    """Return (failed, passed, errors, time_s) or None."""
    for line in reversed((stdout or "").splitlines()):
        m = SUMMARY_RE.search(line)
        if m:
            return (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3) or "0"),
                float(m.group(4)),
            )
    return None


def _print_summary_table(runs: list[RunStats], stream=None) -> None:
    stream = stream or sys.stdout
    if not runs:
        return
    col_mutation = max(len(r.mutation) for r in runs)
    col_mutation = max(col_mutation, len("Mutation"))
    header = (
        f"  {'Mutation':<{col_mutation}}  Exit   Failed  Passed  Errors   Time"
    )
    sep = "  " + "-" * (col_mutation + 2 + 6 + 7 + 7 + 7 + 8)
    stream.write("\n")
    stream.write("  Mutation audit — per-run stats\n")
    stream.write(sep + "\n")
    stream.write(header + "\n")
    stream.write(sep + "\n")
    for r in runs:
        stream.write(
            f"  {r.mutation:<{col_mutation}}  {r.exit_code:>4}   {r.failed:>5}  {r.passed:>6}  {r.errors:>6}  {r.time_s:>5.1f}s\n"
        )
    stream.write(sep + "\n")
    stream.flush()


def _print_buckets(runs: list[RunStats], killed_count: int, stream=None) -> None:
    stream = stream or sys.stdout
    total_runs = len(runs)
    all_killed = killed_count == total_runs and total_runs > 0
    stream.write("\n  Buckets / insight\n")
    stream.write("  " + "-" * 50 + "\n")
    stream.write(f"  Audit result:    {'PASSED (all mutations killed)' if all_killed else 'REGRESSION (one or more mutations did not kill any test)'}\n")
    stream.write(f"  Mutations killed: {killed_count}/{total_runs}\n")
    stream.write(f"  Total test runs:  {total_runs} (same test set per mutation)\n")
    if runs:
        total_tests = max(r.total for r in runs) or runs[0].total
        stream.write(f"  Tests per run:    ~{total_tests}\n")
        most_failures = max(runs, key=lambda r: r.failed)
        most_errors = max(runs, key=lambda r: r.errors)
        slowest = max(runs, key=lambda r: r.time_s)
        stream.write(f"  Most failures:    {most_failures.mutation} ({most_failures.failed})\n")
        stream.write(f"  Most errors:     {most_errors.mutation} ({most_errors.errors})\n")
        stream.write(f"  Slowest run:     {slowest.mutation} ({slowest.time_s:.1f}s)\n")
    stream.write("  " + "-" * 50 + "\n")
    stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run mutation kill-rate audit")
    parser.add_argument(
        "--mutations",
        default=None,
        help="Comma-separated mutation names (default: all from MUTATION_NAMES)",
    )
    parser.add_argument(
        "--pytest-args",
        default="",
        help="Extra args for pytest (e.g. '-x --tb=short')",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Stream pytest output (no summary table or buckets at end).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=DEFAULT_PATHS,
        help="Test paths (default: adversarial, posting, integration, architecture, services)",
    )
    args = parser.parse_args()

    if args.mutations:
        mutations = [m.strip() for m in args.mutations.split(",")]
    else:
        from tests.mutation.mutations import MUTATION_NAMES
        mutations = list(MUTATION_NAMES)

    pytest_args = list(args.paths)
    if args.pytest_args:
        pytest_args.extend(args.pytest_args.split())

    failed_runs: list[str] = []
    run_stats: list[RunStats] = []
    use_capture = not args.live

    for mutation in mutations:
        env = os.environ.copy()
        env[MUTATIONS_ENV] = mutation
        if use_capture:
            result = subprocess.run(
                [sys.executable, "-m", "pytest"] + pytest_args,
                env=env,
                capture_output=True,
                text=True,
            )
            parsed = _parse_summary(result.stdout or "")
            if parsed:
                failed_n, passed_n, errors_n, time_s = parsed
                run_stats.append(
                    RunStats(
                        mutation=mutation,
                        exit_code=result.returncode,
                        failed=failed_n,
                        passed=passed_n,
                        errors=errors_n,
                        time_s=time_s,
                    )
                )
            else:
                run_stats.append(
                    RunStats(
                        mutation=mutation,
                        exit_code=result.returncode,
                        failed=0,
                        passed=0,
                        errors=0,
                        time_s=0.0,
                    )
                )
            print(
                f"  [{mutation}] exit={result.returncode}  failed={run_stats[-1].failed}, passed={run_stats[-1].passed}, errors={run_stats[-1].errors}, time={run_stats[-1].time_s:.1f}s",
                flush=True,
            )
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        else:
            print(f"\n--- Mutation: {mutation} ---", flush=True)
            result = subprocess.run(
                [sys.executable, "-m", "pytest"] + pytest_args,
                env=env,
            )

        if result.returncode == 0:
            failed_runs.append(mutation)

    if use_capture and run_stats:
        failed_runs = [r.mutation for r in run_stats if r.exit_code == 0]

    if use_capture and run_stats:
        _print_summary_table(run_stats)
        killed = sum(1 for r in run_stats if r.killed)
        _print_buckets(run_stats, killed)

    if failed_runs:
        print(
            "\nREGRESSION: The following mutation(s) did not cause any test failure:",
            file=sys.stderr,
        )
        for m in failed_runs:
            print(f"  - {m}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
