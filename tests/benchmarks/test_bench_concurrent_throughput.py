"""
B3: Concurrent Throughput Scaling Benchmark.

Measures throughput (postings/second) at 1, 5, 10, and 20 threads.
Each thread posts 20 events through its own session + pipeline.

The sequence allocation lock (SELECT ... FOR UPDATE on the counter row)
is THE serialization bottleneck — 10 threads will NOT achieve 10x throughput.

Regression thresholds (postings/sec):
  -  1 thread:  > 5 post/sec
  -  5 threads: > 15 post/sec
  - 10 threads: > 20 post/sec
  - 20 threads: > 25 post/sec
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from finance_kernel.db.base import Base
from tests.benchmarks.conftest import (
    COA_UUID_NS,
    EFFECTIVE,
    FY_END,
    FY_START,
    create_accounts_from_config,
    make_simple_event,
)
from tests.benchmarks.helpers import print_benchmark_header

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

POSTS_PER_THREAD = 20

THREAD_COUNTS = [1, 5, 10, 20]

THROUGHPUT_THRESHOLDS = {
    1: 5.0,
    5: 15.0,
    10: 20.0,
    20: 25.0,
}


def _worker(
    thread_id: int,
    session_factory,
    config,
    actor_id,
    posts: int,
) -> tuple[int, float, int]:
    """Worker function for one thread. Returns (thread_id, elapsed_sec, success_count)."""
    from finance_config.bridges import build_role_resolver
    from finance_kernel.domain.clock import DeterministicClock
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_services.invokers import register_standard_engines
    from finance_services.posting_orchestrator import PostingOrchestrator

    session = session_factory()
    clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))

    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=session,
        compiled_pack=config,
        role_resolver=role_resolver,
        clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)
    service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=True)

    successes = 0
    t0 = time.perf_counter()

    for i in range(posts):
        evt = make_simple_event(iteration=i)
        try:
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
            if result.is_success:
                successes += 1
        except Exception:
            pass

    elapsed = time.perf_counter() - t0
    try:
        session.close()
    except Exception:
        pass

    return thread_id, elapsed, successes


class TestConcurrentThroughput:
    """B3: Throughput scaling across 1, 5, 10, 20 threads."""

    def test_concurrent_throughput(self, db_engine, db_tables, bench_session_factory):
        from finance_config import get_active_config
        from finance_kernel.db.engine import get_session
        from finance_kernel.domain.clock import DeterministicClock
        from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
        from finance_kernel.models.party import Party, PartyStatus, PartyType
        from finance_modules import register_all_modules

        logging.disable(logging.CRITICAL)

        config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)
        register_all_modules()

        print_benchmark_header("B3 Concurrent Throughput Scaling")

        results_table = []

        for n_threads in THREAD_COUNTS:
            # Clean slate for each thread count
            table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
            if table_names:
                with db_engine.connect() as conn:
                    conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
                    conn.commit()

            # Seed shared data
            session = get_session()
            clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))
            actor_id = uuid4()
            create_accounts_from_config(session, config, actor_id)

            period = FiscalPeriod(
                period_code="FY2026",
                name="Fiscal Year 2026",
                start_date=FY_START,
                end_date=FY_END,
                status=PeriodStatus.OPEN,
                created_by_id=actor_id,
            )
            session.add(period)

            actor_party = Party(
                id=actor_id,
                party_code=f"BENCH-CONC-{n_threads}",
                party_type=PartyType.EMPLOYEE,
                name="Benchmark Concurrent Actor",
                status=PartyStatus.ACTIVE,
                is_active=True,
                created_by_id=actor_id,
            )
            session.add(actor_party)
            session.commit()
            session.close()

            # Run concurrent workers
            total_posts = n_threads * POSTS_PER_THREAD
            barrier = threading.Barrier(n_threads)

            t_wall_start = time.perf_counter()

            with ThreadPoolExecutor(max_workers=n_threads) as executor:
                futures = [
                    executor.submit(
                        _worker,
                        thread_id=tid,
                        session_factory=bench_session_factory,
                        config=config,
                        actor_id=actor_id,
                        posts=POSTS_PER_THREAD,
                    )
                    for tid in range(n_threads)
                ]
                worker_results = [f.result(timeout=120) for f in futures]

            t_wall_elapsed = time.perf_counter() - t_wall_start

            total_successes = sum(r[2] for r in worker_results)
            throughput = total_successes / t_wall_elapsed if t_wall_elapsed > 0 else 0

            threshold = THROUGHPUT_THRESHOLDS[n_threads]
            status = "PASS" if throughput >= threshold else "FAIL"

            print(
                f"  {n_threads:>2d} threads: "
                f"{total_successes:>3d}/{total_posts} posted in {t_wall_elapsed:.1f}s  "
                f"→ {throughput:>6.1f} post/sec  "
                f"(threshold: > {threshold:.0f})  [{status}]"
            )

            results_table.append((n_threads, throughput, threshold))

        print()

        # Assert thresholds
        logging.disable(logging.NOTSET)
        for n_threads, throughput, threshold in results_table:
            assert throughput >= threshold, (
                f"REGRESSION at {n_threads} threads: "
                f"{throughput:.1f} post/sec < {threshold:.0f} post/sec"
            )
