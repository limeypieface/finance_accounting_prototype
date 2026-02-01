"""
B2: Pipeline Stage Breakdown Benchmark.

Decomposes 30 postings into measurable pipeline stages by timing
each step of _do_post_event individually via a wrapper.

Stages measured:
  1. Period validation     (DB read)
  2. Event ingestion       (DB read + write)
  3. Policy selection      (in-memory)
  4. Meaning building      (in-memory)
  5. Intent construction   (in-memory)
  6. Interpretation + journal write (DB writes)
  7. Commit                (DB flush)

This benchmark wraps the internal pipeline stages to get per-stage
timing. It posts simple_2_line events to isolate pipeline overhead
from engine computation.
"""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests.benchmarks.conftest import EFFECTIVE
from tests.benchmarks.helpers import (
    BenchTimer,
    TimingRecord,
    print_benchmark_header,
    print_benchmark_table,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

N = 30  # postings to decompose


class TestPipelineStageBreakdown:
    """B2: Where time is spent in the posting pipeline."""

    def test_pipeline_stage_breakdown(self, bench_posting_service):
        ctx = bench_posting_service
        service = ctx["service"]
        actor_id = ctx["actor_id"]

        timer = BenchTimer()

        for i in range(N):
            event_id = uuid4()
            event_type = "inventory.receipt"
            payload: dict[str, Any] = {"quantity": 500, "has_variance": False}
            amount = Decimal("25000.00")
            currency = "USD"
            producer = "inventory"
            occurred_at = service._clock.now()

            # Stage 1: Period validation
            with timer.measure("1_period_validation", iteration=i):
                from finance_kernel.exceptions import AdjustmentsNotAllowedError
                try:
                    service._period_service.validate_adjustment_allowed(
                        EFFECTIVE, is_adjustment=False,
                    )
                except (AdjustmentsNotAllowedError, Exception):
                    pass  # Validation outcome doesn't matter for timing

            # Stage 2: Event ingestion
            from finance_kernel.services.ingestor_service import IngestStatus
            with timer.measure("2_event_ingestion", iteration=i):
                ingest_result = service._ingestor.ingest(
                    event_id=event_id,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    effective_date=EFFECTIVE,
                    actor_id=actor_id,
                    producer=producer,
                    payload=payload,
                    schema_version=1,
                )
            assert ingest_result.status == IngestStatus.ACCEPTED, (
                f"Ingestion failed at iteration {i}: {ingest_result.message}"
            )

            # Stage 3: Policy selection
            from finance_kernel.domain.policy_selector import PolicySelector
            with timer.measure("3_policy_selection", iteration=i):
                profile = PolicySelector.find_for_event(
                    event_type, EFFECTIVE, payload=payload,
                )

            # Look up compiled policy (non-timed, setup for stage 6)
            compiled_policy = None
            if hasattr(service, '_compiled_pack') and service._compiled_pack:
                for cp in service._compiled_pack.policies:
                    if cp.name == profile.name and cp.version == profile.version:
                        compiled_policy = cp
                        break

            # Stage 4: Meaning building
            with timer.measure("4_meaning_building", iteration=i):
                meaning_result = service._meaning_builder.build(
                    event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    effective_date=EFFECTIVE,
                    profile=profile,
                )
            assert meaning_result.success, f"Meaning failed at {i}"

            # Stage 5: Intent construction
            from finance_kernel.domain.policy_bridge import build_accounting_intent
            with timer.measure("5_intent_construction", iteration=i):
                accounting_intent = build_accounting_intent(
                    profile_name=profile.name,
                    source_event_id=event_id,
                    effective_date=EFFECTIVE,
                    amount=amount,
                    currency=currency,
                    payload=payload,
                )

            # Stage 6: Interpretation + journal write
            with timer.measure("6_interpretation_write", iteration=i):
                interp_result = service._coordinator.interpret_and_post(
                    meaning_result=meaning_result,
                    accounting_intent=accounting_intent,
                    actor_id=actor_id,
                    compiled_policy=compiled_policy,
                    event_payload=payload,
                )
            assert interp_result.success, f"Interpretation failed at {i}"

            # Stage 7: Commit
            with timer.measure("7_commit", iteration=i):
                service._session.commit()

        # Collect summaries
        stage_labels = [
            "1_period_validation",
            "2_event_ingestion",
            "3_policy_selection",
            "4_meaning_building",
            "5_intent_construction",
            "6_interpretation_write",
            "7_commit",
        ]

        summaries = [timer.summary(label) for label in stage_labels]

        # Print results
        print_benchmark_header("B2 Pipeline Stage Breakdown")
        print_benchmark_table(summaries)

        # Sanity checks: pure-computation stages should be fast
        sel = timer.summary("3_policy_selection")
        meaning = timer.summary("4_meaning_building")
        intent = timer.summary("5_intent_construction")

        assert sel.p95_ms < 50, f"Policy selection too slow: p95={sel.p95_ms:.1f}ms"
        assert meaning.p95_ms < 50, f"Meaning building too slow: p95={meaning.p95_ms:.1f}ms"
        assert intent.p95_ms < 50, f"Intent construction too slow: p95={intent.p95_ms:.1f}ms"
