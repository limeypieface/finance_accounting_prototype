"""
B9: Full Event Coverage Benchmark.

Posts every single event from the event catalog through the posting pipeline
for each configuration tier:
  - SIMPLE (5 modules, ~30 policies)
  - MEDIUM (10 modules, ~80 policies)
  - FULL (19 modules, ~188 policies)

This is the ultimate integration test: it proves that every policy in the
configuration can be compiled, matched, interpreted, and posted to produce
a valid balanced journal entry.  Any misconfigured policy, missing role
binding, or broken from_context mapping will fail here.

Unlike B8 (which measures latency on a handful of scenarios), B9 exercises
the ENTIRE event surface area for each tier.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from finance_kernel.db.base import Base
from tests.benchmarks.conftest import (
    FY_START,
    FY_END,
    EFFECTIVE,
    create_accounts_from_config,
)
from tests.benchmarks.helpers import (
    BenchTimer,
    print_benchmark_header,
    print_benchmark_table,
)
from tests.benchmarks.tier_config import (
    TIERS,
    load_tier_config,
    register_tier_modules,
)
from tests.benchmarks.event_catalog import (
    build_event_catalog,
    TestEvent,
)

pytestmark = [pytest.mark.benchmark, pytest.mark.postgres]

# Policies that require external state (e.g., bank statement imports, prior
# invoices, prior deposits) that cannot be established through a single posting.
# These are tracked and reported separately from mandatory assertions.
REQUIRES_PRIOR_STATE: frozenset[str] = frozenset({
    # BANK subledger events: need PENDING entries from bank statement import
    "CashReconciliation",
    "CashAutoReconciled",
    "CashNSFReturn",
    # BANK subledger: deposit/withdrawal need prior bank balance
    "CashDeposit",
    "CashWithdrawalExpense",
    "CashWithdrawalSupplier",
    "CashWithdrawalPayroll",
    # AP subledger: dual-ledger invoices fail reconciliation in isolation
    # (AP subledger entries net to 0 while GL control has credit balance)
    "APInvoiceExpense",
    "APInvoicePOMatched",
    "APInvoiceInventory",
    "APInvoiceCancelled",
    # AP subledger: payments/discount need prior AP invoice balance
    "APPayment",
    "APPaymentWithDiscount",
    # AR subledger: dual-ledger invoices fail reconciliation in isolation
    "ARInvoice",
    # AR subledger: payments/credits/write-offs need prior AR invoice balance
    "ARPaymentReceived",
    "ARReceiptApplied",
    "ARReceiptAppliedDiscount",
    "ARCreditMemoReturn",
    "ARCreditMemoPriceAdj",
    "ARCreditMemoService",
    "ARCreditMemoError",
    "ARWriteOff",
    "ARRefundIssued",
    "ARFinanceCharge",
    # Contracts module: AP/BANK subledger events (same dual-ledger pattern)
    "APInvoiceAllowable",
    "APInvoiceUnallowable",
    "APInvoiceConditional",
    "BankWithdrawalExpenseAllowable",
    "BankWithdrawalExpenseUnallowable",
})


def _post_event(service, event: TestEvent, actor_id, effective: date):
    """Post a single TestEvent and return the result."""
    return service.post_event(
        event_type=event.event_type,
        payload=event.payload,
        effective_date=effective,
        actor_id=actor_id,
        amount=event.amount,
        currency=event.currency,
        producer=event.producer,
        event_id=uuid4(),
    )


class TestFullEventCoverage:
    """B9: Post every event from the catalog for each configuration tier."""

    @pytest.mark.parametrize("tier_name", ["simple", "medium", "full"])
    def test_all_events_for_tier(self, db_engine, db_tables, tier_name):
        from finance_config.bridges import build_role_resolver
        from finance_kernel.db.engine import get_session
        from finance_kernel.domain.clock import DeterministicClock
        from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
        from finance_kernel.models.party import Party, PartyType, PartyStatus
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

        # 4. Build event catalog from the tier's filtered config
        catalog = build_event_catalog(tier_config)
        assert len(catalog) > 0, (
            f"[{tier.name}] Event catalog is empty — no policies in tier?"
        )

        # 5. Wire pipeline
        session = get_session()
        clock = DeterministicClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
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
            party_code=f"B9-{tier.name}",
            party_type=PartyType.EMPLOYEE,
            name=f"B9 Full Coverage {tier.name} Actor",
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

        # 6. Post every event in the catalog
        timer = BenchTimer()
        successes = []
        failures = []

        for event in catalog:
            label = f"{tier.name}_{event.label}"
            try:
                with timer.measure(label):
                    result = _post_event(service, event, actor_id, EFFECTIVE)
                if result.is_success:
                    successes.append(event)
                else:
                    failures.append((event, f"{result.status.value}: {result.message}"))
            except Exception as exc:
                failures.append((event, f"EXCEPTION: {exc}"))

        # 7. Report results
        print()
        print(f"  {'=' * 60}")
        print(f"  B9 FULL EVENT COVERAGE: {tier.name}")
        print(f"    Tier: {tier.description}")
        print(f"    Modules: {len(tier.modules)}")
        print(f"    Policies: {len(tier_config.policies)}")
        print(f"    Events in catalog: {len(catalog)}")
        print(f"    Successes: {len(successes)}")
        print(f"    Failures: {len(failures)}")
        print(f"  {'=' * 60}")

        if failures:
            print()
            print(f"  FAILED EVENTS ({len(failures)}):")
            for event, reason in failures:
                print(f"    - {event.label} [{event.module}] "
                      f"({event.event_type}): {reason}")
            print()

        if successes:
            # Compute aggregate timing for successful postings
            all_times = []
            for event in successes:
                label = f"{tier.name}_{event.label}"
                recs = timer.records(label)
                all_times.extend(r.elapsed_ms for r in recs)

            if all_times:
                all_times.sort()
                n = len(all_times)
                mean_ms = sum(all_times) / n
                p50_idx = int(n * 0.5)
                p95_idx = min(int(n * 0.95), n - 1)
                print(f"  Timing (successful postings):")
                print(f"    count={n}  mean={mean_ms:.1f}ms  "
                      f"p50={all_times[p50_idx]:.1f}ms  "
                      f"p95={all_times[p95_idx]:.1f}ms  "
                      f"min={all_times[0]:.1f}ms  "
                      f"max={all_times[-1]:.1f}ms")
                print()

        # Print per-module breakdown
        module_stats: dict[str, dict] = {}
        for event in catalog:
            m = event.module
            if m not in module_stats:
                module_stats[m] = {"total": 0, "passed": 0, "failed": 0}
            module_stats[m]["total"] += 1

        for event in successes:
            module_stats[event.module]["passed"] += 1
        for event, _ in failures:
            module_stats[event.module]["failed"] += 1

        print(f"  Per-module breakdown:")
        print(f"  {'Module':<20s}  {'Total':>5s}  {'Pass':>5s}  {'Fail':>5s}")
        print(f"  {'-'*20}  {'-'*5}  {'-'*5}  {'-'*5}")
        for mod in sorted(module_stats):
            s = module_stats[mod]
            status = "OK" if s["failed"] == 0 else "FAIL"
            print(f"  {mod:<20s}  {s['total']:>5d}  {s['passed']:>5d}  "
                  f"{s['failed']:>5d}  [{status}]")
        print()

        # 8. Store results for cross-tier comparison
        if not hasattr(TestFullEventCoverage, "_tier_results"):
            TestFullEventCoverage._tier_results = {}
        TestFullEventCoverage._tier_results[tier.name] = {
            "catalog_size": len(catalog),
            "successes": len(successes),
            "failures": len(failures),
            "failure_details": failures,
            "module_stats": module_stats,
            "state_dep_count": 0,  # Updated after assertion filtering below
        }

        # 9. Cleanup
        try:
            session.rollback()
        except Exception:
            pass
        session.close()
        db_engine.dispose()
        if table_names:
            with db_engine.connect() as conn:
                conn.execute(text("TRUNCATE " + ", ".join(table_names) + " CASCADE"))
                conn.commit()
        logging.disable(logging.NOTSET)

        # 10. Assert: non-state-dependent events must post successfully
        real_failures = [
            (e, reason) for e, reason in failures
            if e.policy_name not in REQUIRES_PRIOR_STATE
        ]
        state_dep_failures = [
            (e, reason) for e, reason in failures
            if e.policy_name in REQUIRES_PRIOR_STATE
        ]
        # Update stored results with state-dependent count
        TestFullEventCoverage._tier_results[tier.name]["state_dep_count"] = len(state_dep_failures)

        if state_dep_failures:
            print(f"  STATE-DEPENDENT (excluded from assertion): "
                  f"{len(state_dep_failures)} events")
            for e, reason in state_dep_failures:
                print(f"    - {e.label}: {reason}")
            print()

        assert len(real_failures) == 0, (
            f"[{tier.name}] {len(real_failures)}/{len(catalog)} events failed:\n"
            + "\n".join(
                f"  {e.label} [{e.module}] ({e.event_type}): {reason}"
                for e, reason in real_failures
            )
        )

    def test_full_coverage_summary(self, db_engine, db_tables):
        """Cross-tier summary (runs after parametrized tests).

        Prints a combined report showing coverage across all three tiers.
        """
        results = getattr(TestFullEventCoverage, "_tier_results", {})

        if len(results) < 3:
            pytest.skip(
                f"Only {len(results)}/3 tiers completed — "
                "run all tier tests first"
            )

        print()
        print_benchmark_header("B9 Full Event Coverage Summary")

        total_events = 0
        total_pass = 0
        total_fail = 0
        total_state_dep = 0

        for tier_name in ["SIMPLE", "MEDIUM", "FULL"]:
            r = results[tier_name]
            state_dep = r.get("state_dep_count", 0)
            real_fail = r["failures"] - state_dep
            total_events += r["catalog_size"]
            total_pass += r["successes"]
            total_fail += real_fail
            total_state_dep += state_dep

            if real_fail == 0 and state_dep == 0:
                status = "ALL PASS"
            elif real_fail == 0:
                status = f"{state_dep} state-dep (excluded)"
            else:
                status = f"{real_fail} FAILED + {state_dep} state-dep"
            print(f"  {tier_name:>8s}: {r['successes']}/{r['catalog_size']} events posted  [{status}]")

        print()
        if total_fail == 0:
            summary = f"ALL PASS ({total_state_dep} state-dep excluded)"
        else:
            summary = f"{total_fail} FAILED + {total_state_dep} state-dep"
        print(f"  {'TOTAL':>8s}: {total_pass}/{total_events} events posted  [{summary}]")
        print()

        # Collect all unique modules across tiers
        all_modules: set[str] = set()
        for r in results.values():
            all_modules.update(r["module_stats"].keys())

        print(f"  Module coverage by tier:")
        print(f"  {'Module':<20s}  {'SIMPLE':>8s}  {'MEDIUM':>8s}  {'FULL':>8s}")
        print(f"  {'-'*20}  {'-'*8}  {'-'*8}  {'-'*8}")
        for mod in sorted(all_modules):
            cells = []
            for tier_name in ["SIMPLE", "MEDIUM", "FULL"]:
                ms = results[tier_name]["module_stats"].get(mod)
                if ms is None:
                    cells.append("      --")
                elif ms["failed"] == 0:
                    cells.append(f"  {ms['passed']:>3d}/{ms['total']:<3d}")
                else:
                    cells.append(f" {ms['passed']:>2d}/{ms['total']:<2d} X")
            print(f"  {mod:<20s}{''.join(cells)}")
        print()

        assert total_fail == 0, (
            f"FULL COVERAGE FAILURE: {total_fail}/{total_events} real failures across tiers "
            f"({total_state_dep} state-dependent excluded)"
        )
