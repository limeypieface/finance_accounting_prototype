"""
B8: Configuration Complexity Scaling Benchmark.

Measures posting latency across three tiers of configuration complexity:
  - SIMPLE (5 modules, ~30 policies) — startup accounting
  - MEDIUM (10 modules, ~80 policies) — mid-market
  - FULL (19 modules, ~188 policies) — enterprise

This tests whether the system scales with configuration complexity.
Policy selection is O(1) via match_index, so increased policy count
should have minimal impact on posting latency.  The primary variable
is the number of registered modules and role bindings.

Regression thresholds:
  - MEDIUM/SIMPLE mean ratio < 2.0x
  - FULL/SIMPLE mean ratio < 3.0x
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timezone
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import text

from finance_kernel.db.base import Base
from tests.benchmarks.conftest import (
    COA_UUID_NS,
    EFFECTIVE,
    FY_END,
    FY_START,
    create_accounts_from_config,
)
from tests.benchmarks.helpers import (
    BenchTimer,
    print_benchmark_header,
    print_benchmark_table,
    print_ratio_result,
)
from tests.benchmarks.tier_config import (
    TIERS,
    load_tier_config,
    make_tier_scenarios,
    register_tier_modules,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

N = 50  # postings per tier


class TestComplexityScaling:
    """B8: Posting latency across SIMPLE / MEDIUM / FULL configuration tiers."""

    @pytest.mark.parametrize("tier_name", ["simple", "medium", "full"])
    def test_complexity_tier(self, db_engine, db_tables, tier_name):
        from finance_config.bridges import build_role_resolver
        from finance_kernel.db.engine import get_session
        from finance_kernel.domain.clock import DeterministicClock
        from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
        from finance_kernel.models.party import Party, PartyStatus, PartyType
        from finance_kernel.services.module_posting_service import ModulePostingService
        from finance_services.invokers import register_standard_engines
        from finance_services.posting_orchestrator import PostingOrchestrator

        logging.disable(logging.CRITICAL)

        tier = TIERS[tier_name]

        # 1. Clean slate
        table_names = [t.name for t in reversed(Base.metadata.sorted_tables)]
        if table_names:
            with db_engine.connect() as conn:
                conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
                conn.commit()

        # 2. Load tier's dedicated config set directly from disk
        tier_config = load_tier_config(tier)

        # 3. Register only tier modules
        registered = register_tier_modules(tier)

        # 4. Wire pipeline
        session = get_session()
        clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC))
        actor_id = uuid4()

        create_accounts_from_config(session, tier_config, actor_id)

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
            party_code=f"BENCH-{tier.name}",
            party_type=PartyType.EMPLOYEE,
            name=f"Benchmark {tier.name} Actor",
            status=PartyStatus.ACTIVE,
            is_active=True,
            created_by_id=actor_id,
        )
        session.add(actor_party)
        session.commit()

        role_resolver = build_role_resolver(tier_config)
        orchestrator = PostingOrchestrator(
            session=session,
            compiled_pack=tier_config,
            role_resolver=role_resolver,
            clock=clock,
        )
        register_standard_engines(orchestrator.engine_dispatcher)

        service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=True)

        # 5. Get tier-appropriate scenarios
        scenarios = make_tier_scenarios(tier)

        # 6. Measure
        timer = BenchTimer()
        label = f"tier_{tier.name}"

        for i in range(N):
            scenario = scenarios[i % len(scenarios)]
            with timer.measure(label, iteration=i):
                result = service.post_event(
                    event_type=scenario["event_type"],
                    payload=scenario["payload"],
                    effective_date=EFFECTIVE,
                    actor_id=actor_id,
                    amount=scenario["amount"],
                    currency=scenario["currency"],
                    producer=scenario["producer"],
                    event_id=uuid4(),
                )
            assert result.is_success, (
                f"[{tier.name}] Posting failed at iteration {i}: "
                f"{result.status.value} — {result.message}"
            )

        summary = timer.summary(label)

        # 7. Print results
        print()
        print(f"  {tier.name} ({len(tier.modules)} modules, "
              f"{len(tier_config.policies)} policies, "
              f"{registered} registered): "
              f"p50={summary.p50_ms:.1f}ms  "
              f"p95={summary.p95_ms:.1f}ms  "
              f"mean={summary.mean_ms:.1f}ms")

        # 8. Cleanup — must fully release session before TRUNCATE to avoid
        # deadlocks with other module-scoped benchmark fixtures
        try:
            session.rollback()
        except Exception:
            pass
        session.close()
        # Dispose all pooled connections so TRUNCATE can acquire locks
        db_engine.dispose()
        if table_names:
            with db_engine.connect() as conn:
                conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
                conn.commit()
        logging.disable(logging.NOTSET)

        # Store summary on the test instance for cross-tier comparison
        # (pytest-parametrize runs each tier as a separate test)
        if not hasattr(TestComplexityScaling, "_tier_results"):
            TestComplexityScaling._tier_results = {}
        TestComplexityScaling._tier_results[tier.name] = summary

    def test_complexity_scaling_comparison(self, db_engine, db_tables):
        """Compare results across tiers (runs after parametrized tests).

        This test reads the stored tier results and asserts regression
        thresholds.  If any tier hasn't run yet, it is skipped.
        """
        results = getattr(TestComplexityScaling, "_tier_results", {})

        if len(results) < 3:
            pytest.skip(
                f"Only {len(results)}/3 tiers completed — "
                "run all tier tests first"
            )

        simple = results["SIMPLE"]
        medium = results["MEDIUM"]
        full = results["FULL"]

        print()
        print_benchmark_header("B8 Complexity Scaling")
        print_benchmark_table([simple, medium, full])

        # Compute ratios
        medium_ratio = medium.mean_ms / simple.mean_ms if simple.mean_ms > 0 else float("inf")
        full_ratio = full.mean_ms / simple.mean_ms if simple.mean_ms > 0 else float("inf")

        print_ratio_result("MEDIUM/SIMPLE mean latency", medium_ratio, threshold=2.0)
        print_ratio_result("FULL/SIMPLE mean latency", full_ratio, threshold=3.0)

        assert medium_ratio < 2.0, (
            f"REGRESSION: MEDIUM/SIMPLE ratio={medium_ratio:.2f}x > 2.0x "
            f"(SIMPLE={simple.mean_ms:.1f}ms, MEDIUM={medium.mean_ms:.1f}ms)"
        )
        assert full_ratio < 3.0, (
            f"REGRESSION: FULL/SIMPLE ratio={full_ratio:.2f}x > 3.0x "
            f"(SIMPLE={simple.mean_ms:.1f}ms, FULL={full.mean_ms:.1f}ms)"
        )
