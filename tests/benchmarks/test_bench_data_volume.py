"""
B7: Data Volume Scaling Benchmark.

Measures posting latency at different existing journal entry volumes:
  - 0 entries (empty database)
  - 1,000 entries
  - 10,000 entries
  - 50,000 entries

This tests whether query performance degrades as tables grow.
Well-indexed queries should show minimal degradation.

Regression threshold: 50K/empty latency ratio < 3.0x
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from finance_kernel.db.base import Base
from tests.benchmarks.conftest import (
    EFFECTIVE,
    FY_START,
    FY_END,
    create_accounts_from_config,
    make_simple_event,
)
from tests.benchmarks.helpers import (
    BenchTimer,
    print_benchmark_header,
    print_benchmark_table,
    print_ratio_result,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

MEASURE_N = 30  # postings to measure at each volume level
VOLUME_LEVELS = [0, 1_000, 5_000, 20_000]


def _seed_entries(service, actor_id, count: int) -> int:
    """Seed *count* journal entries into the database.

    Periodically expires the session identity map to prevent memory
    accumulation during large seeding runs.
    """
    successes = 0
    session = service._session
    for i in range(count):
        evt = make_simple_event(iteration=i)
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
        # Expire identity map every 500 postings to prevent OOM
        if (i + 1) % 500 == 0:
            session.expire_all()
    return successes


class TestDataVolumeScaling:
    """B7: Latency at 0, 1K, 10K, 50K existing journal entries."""

    def test_data_volume_scaling(self, db_engine, db_tables):
        from datetime import datetime, timezone
        from finance_config import get_active_config
        from finance_config.bridges import build_role_resolver
        from finance_kernel.db.engine import get_session
        from finance_kernel.domain.clock import DeterministicClock
        from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
        from finance_kernel.models.party import Party, PartyType, PartyStatus
        from finance_kernel.services.module_posting_service import ModulePostingService
        from finance_modules import register_all_modules
        from finance_services.invokers import register_standard_engines
        from finance_services.posting_orchestrator import PostingOrchestrator

        logging.disable(logging.CRITICAL)

        config = get_active_config(legal_entity="*", as_of_date=EFFECTIVE)
        register_all_modules()

        # Clean slate
        table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
        if table_names:
            with db_engine.connect() as conn:
                conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
                conn.commit()

        # Seed shared data
        session = get_session()
        clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
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
            party_code="BENCH-VOLUME",
            party_type=PartyType.EMPLOYEE,
            name="Benchmark Volume Actor",
            status=PartyStatus.ACTIVE,
            is_active=True,
            created_by_id=actor_id,
        )
        session.add(actor_party)
        session.commit()

        role_resolver = build_role_resolver(config)
        orchestrator = PostingOrchestrator(
            session=session,
            compiled_pack=config,
            role_resolver=role_resolver,
            clock=clock,
        )
        register_standard_engines(orchestrator.engine_dispatcher)
        service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=True)

        timer = BenchTimer()
        summaries = []
        cumulative = 0

        print_benchmark_header("B7 Data Volume Scaling")

        for target_volume in VOLUME_LEVELS:
            # Seed up to target volume
            seed_count = target_volume - cumulative
            if seed_count > 0:
                print(f"  Seeding {seed_count} entries to reach {target_volume}...")
                _seed_entries(service, actor_id, seed_count)
                cumulative += seed_count

            # Measure
            label = f"volume_{target_volume}"
            for i in range(MEASURE_N):
                evt = make_simple_event(iteration=i)
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
                    f"Posting failed at volume {target_volume}, iteration {i}"
                )
            cumulative += MEASURE_N

            s = timer.summary(label)
            summaries.append(s)
            print(f"  {label}: p50={s.p50_ms:.1f}ms  p95={s.p95_ms:.1f}ms  mean={s.mean_ms:.1f}ms")

        print()
        print_benchmark_table(summaries)

        # Compute degradation ratio
        empty_mean = summaries[0].mean_ms
        max_mean = summaries[-1].mean_ms
        ratio = max_mean / empty_mean if empty_mean > 0 else float("inf")

        print_ratio_result(
            f"volume_{VOLUME_LEVELS[-1]}/volume_0 mean latency",
            ratio,
            threshold=3.0,
        )

        # Cleanup
        session.close()
        if table_names:
            with db_engine.connect() as conn:
                conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
                conn.commit()
        logging.disable(logging.NOTSET)

        assert ratio < 3.0, (
            f"REGRESSION: {VOLUME_LEVELS[-1]}/empty ratio={ratio:.2f}x > 3.0x "
            f"(empty={empty_mean:.1f}ms, {VOLUME_LEVELS[-1]}={max_mean:.1f}ms)"
        )
