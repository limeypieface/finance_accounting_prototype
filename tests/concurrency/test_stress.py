"""
High-volume stress tests for PostgreSQL.

These tests push the finance kernel with large volumes of data and concurrent
operations to verify it behaves correctly under production-like load.

Test categories:
1. High-volume posting (1000+ events)
2. Extreme concurrency (200+ threads)
3. Sustained load over time
4. Large trial balance computation
5. Mixed read/write workloads
6. Sequence allocation at scale

Run with:
    pytest tests/concurrency/test_stress.py -v --timeout=300
"""

import pytest
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, wait, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Lock, Event as ThreadEvent
from uuid import uuid4
from typing import Callable
import statistics

from finance_kernel.db.engine import get_session_factory
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.sequence_service import SequenceService
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.domain.clock import SystemClock
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry


pytestmark = [pytest.mark.postgres, pytest.mark.slow_locks]


def cleanup_test_data(session):
    """Clean up all test data from the database.

    Uses TRUNCATE CASCADE to bypass immutability triggers that prevent
    deletion of posted journal entries and audit events.
    """
    from sqlalchemy import text

    tables_to_truncate = [
        "interpretation_outcomes", "economic_events",
        "journal_lines", "journal_entries", "audit_events", "events",
        "fiscal_periods", "accounts", "sequence_counters",
    ]
    for table in tables_to_truncate:
        try:
            session.execute(text(f"TRUNCATE {table} CASCADE"))
        except Exception:
            session.rollback()
    session.commit()


def setup_test_data(session, actor_id):
    """Create accounts and period for testing."""
    accounts = {}
    account_specs = [
        ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("1100", "AR", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("1200", "Inventory", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("2000", "AP", AccountType.LIABILITY, NormalBalance.CREDIT, None),
        ("3000", "Equity", AccountType.EQUITY, NormalBalance.CREDIT, None),
        ("4000", "Revenue", AccountType.REVENUE, NormalBalance.CREDIT, None),
        ("4100", "Service Revenue", AccountType.REVENUE, NormalBalance.CREDIT, None),
        ("5000", "COGS", AccountType.EXPENSE, NormalBalance.DEBIT, None),
        ("5100", "Salaries", AccountType.EXPENSE, NormalBalance.DEBIT, None),
        ("5200", "Rent", AccountType.EXPENSE, NormalBalance.DEBIT, None),
        ("9999", "Rounding", AccountType.EXPENSE, NormalBalance.DEBIT, [AccountTag.ROUNDING.value]),
    ]

    for code, name, acct_type, normal_bal, tags in account_specs:
        account = Account(
            code=code,
            name=name,
            account_type=acct_type,
            normal_balance=normal_bal,
            is_active=True,
            tags=tags,
            created_by_id=actor_id,
        )
        session.add(account)
        accounts[code] = account

    today = date.today()
    start = today.replace(day=1)
    if today.month == 12:
        end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    period = FiscalPeriod(
        period_code=today.strftime("%Y-%m"),
        name=today.strftime("%B %Y"),
        start_date=start,
        end_date=end,
        status=PeriodStatus.OPEN,
        created_by_id=actor_id,
    )
    session.add(period)
    session.commit()

    return accounts, period


class TestHighVolumePosting:
    """
    Tests for posting large volumes of journal entries.
    """

    def test_1000_sequential_events_post_successfully(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Post 1000 events sequentially to verify basic throughput.

        This establishes a baseline for posting performance.
        """
        actor_id = uuid4()
        num_events = 1000

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        posted_count = 0
        errors = []
        start_time = time.time()

        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            for i in range(num_events):
                try:
                    result = orchestrator.post_event(
                        event_id=uuid4(),
                        event_type="generic.posting",
                        occurred_at=clock.now(),
                        effective_date=effective_date,
                        actor_id=actor_id,
                        producer="volume_test",
                        payload={
                            "lines": [
                                {"account_code": "1000", "side": "debit", "amount": f"{100 + i}.00", "currency": "USD"},
                                {"account_code": "4000", "side": "credit", "amount": f"{100 + i}.00", "currency": "USD"},
                            ]
                        },
                    )
                    if result.status == PostingStatus.POSTED:
                        posted_count += 1
                except Exception as e:
                    errors.append(str(e))
        finally:
            session.close()

        elapsed = time.time() - start_time
        events_per_second = num_events / elapsed

        assert posted_count == num_events, f"Expected {num_events}, got {posted_count}. Errors: {errors[:5]}"
        print(f"\n1000 sequential posts: {elapsed:.2f}s ({events_per_second:.1f} events/sec)")

    def test_500_concurrent_distinct_events(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        500 threads posting distinct events simultaneously.

        Tests high concurrent load with no contention on the same event.
        """
        actor_id = uuid4()
        num_threads = 500

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads, timeout=60)
        start_time = time.time()

        def post_event(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="concurrent_volume",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": f"{50 + thread_id}.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": f"{50 + thread_id}.00", "currency": "USD"},
                        ]
                    },
                )
                assert result.status == PostingStatus.POSTED, (
                    f"Thread {thread_id}: expected POSTED, got {result.status}"
                )
                return result
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        elapsed = time.time() - start_time
        assert len(results) == num_threads
        print(f"\n500 concurrent posts: {elapsed:.2f}s ({num_threads/elapsed:.1f} events/sec)")


class TestExtremeConcurrency:
    """
    Tests with extreme concurrency to find race conditions.
    """

    def test_200_threads_same_event_idempotency(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        200 threads race to post the same event - exactly 1 must win.

        This is an extreme test of the idempotency constraint.
        """
        actor_id = uuid4()
        event_id = uuid4()
        num_threads = 200

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads, timeout=60)

        def post_event(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                return orchestrator.post_event(
                    event_id=event_id,
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="extreme_idempotency",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_threads)]
            results = [f.result() for f in futures]

        posted = [r for r in results if r.status == PostingStatus.POSTED]
        already_posted = [r for r in results if r.status == PostingStatus.ALREADY_POSTED]

        assert len(posted) == 1, (
            f"Expected exactly 1 POSTED, got {len(posted)}. "
            f"Already posted: {len(already_posted)}"
        )
        assert len(already_posted) == num_threads - 1

        # Verify only one journal entry exists
        with pg_session_factory() as verify_session:
            entry_count = verify_session.query(JournalEntry).count()
            assert entry_count == 1, f"Expected 1 journal entry, found {entry_count}"

    def test_1000_sequence_allocations_concurrent(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        1000 concurrent sequence allocations - all must be unique.
        """
        num_threads = 1000
        barrier = Barrier(num_threads, timeout=60)

        def allocate(thread_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                service = SequenceService(session)
                seq = service.next_value("stress_test_seq")
                session.commit()
                return seq
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(allocate, i) for i in range(num_threads)]
            sequences = [f.result() for f in futures]

        assert len(sequences) == num_threads
        assert len(sequences) == len(set(sequences)), f"Duplicate sequences! {len(sequences)} total, {len(set(sequences))} unique"
        print(f"\n1000 sequence allocations successful, range: {min(sequences)} - {max(sequences)}")


class TestSustainedLoad:
    """
    Tests with sustained load over time (not just burst).
    """

    def test_30_second_sustained_posting(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Sustained posting for 30 seconds with 20 concurrent workers.

        Simulates real-world sustained load rather than burst.
        """
        actor_id = uuid4()
        num_workers = 20
        duration_seconds = 30

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        stop_event = ThreadEvent()
        posted_counts = [0] * num_workers
        error_counts = [0] * num_workers
        error_samples = []
        error_samples_lock = Lock()
        latencies = []
        latencies_lock = Lock()

        def worker(worker_id: int):
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                while not stop_event.is_set():
                    start = time.time()
                    try:
                        result = orchestrator.post_event(
                            event_id=uuid4(),
                            event_type="generic.posting",
                            occurred_at=clock.now(),
                            effective_date=effective_date,
                            actor_id=actor_id,
                            producer="sustained_load",
                            payload={
                                "lines": [
                                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                                ]
                            },
                        )
                        if result.status == PostingStatus.POSTED:
                            posted_counts[worker_id] += 1
                            with latencies_lock:
                                latencies.append(time.time() - start)
                    except Exception as e:
                        error_counts[worker_id] += 1
                        with error_samples_lock:
                            if len(error_samples) < 10:
                                error_samples.append(str(e)[:200])
                        # Recreate session after error to reset transaction state
                        try:
                            session.rollback()
                        except Exception:
                            session.close()
                            session = pg_session_factory()
                            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)
            finally:
                session.close()

        # Start workers
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker, i) for i in range(num_workers)]

            # Let it run for duration
            time.sleep(duration_seconds)
            stop_event.set()

            wait(futures)

        total_posted = sum(posted_counts)
        total_errors = sum(error_counts)
        throughput = total_posted / duration_seconds

        # Calculate latency stats
        if latencies:
            avg_latency = statistics.mean(latencies) * 1000  # ms
            p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] * 1000
            p99_latency = sorted(latencies)[int(len(latencies) * 0.99)] * 1000
        else:
            avg_latency = p95_latency = p99_latency = 0

        error_rate = total_errors / (total_posted + total_errors) * 100 if (total_posted + total_errors) > 0 else 0

        print(f"\n30s sustained load results:")
        print(f"  Total posted: {total_posted}")
        print(f"  Throughput: {throughput:.1f} events/sec")
        print(f"  Errors: {total_errors} ({error_rate:.2f}%)")
        print(f"  Avg latency: {avg_latency:.1f}ms")
        print(f"  P95 latency: {p95_latency:.1f}ms")
        print(f"  P99 latency: {p99_latency:.1f}ms")
        if error_samples:
            print(f"  Error samples: {error_samples[:3]}")

        assert total_posted > 0, "No events posted during sustained load"
        # Allow up to 5% error rate under sustained load
        # Errors are primarily sequence collisions during rollback/retry which are
        # transient and don't corrupt data (duplicate inserts are rejected)
        assert error_rate < 5.0, f"Error rate too high: {error_rate:.2f}% ({total_errors} errors)"

        # Verify data integrity
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)
            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)
            assert total_debits == total_credits, "Trial balance not balanced after sustained load!"


class TestLargeTrialBalance:
    """
    Tests for trial balance computation with large datasets.
    """

    def test_trial_balance_with_5000_journal_entries(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Build up 5000 journal entries, then verify trial balance computes correctly.
        """
        actor_id = uuid4()
        num_entries = 5000
        batch_size = 100  # Post in batches for efficiency

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        # Post entries in parallel batches
        num_workers = 50
        entries_per_worker = num_entries // num_workers
        barrier = Barrier(num_workers, timeout=60)

        def post_batch(worker_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                worker_posted = 0
                for i in range(entries_per_worker):
                    # Vary the accounts to create realistic distribution
                    patterns = [
                        [("1000", "debit", "100.00"), ("4000", "credit", "100.00")],
                        [("5000", "debit", "60.00"), ("1200", "credit", "60.00")],
                        [("1100", "debit", "250.00"), ("4100", "credit", "250.00")],
                        [("5100", "debit", "80.00"), ("1000", "credit", "80.00")],
                    ]
                    pattern = patterns[(worker_id + i) % len(patterns)]

                    result = orchestrator.post_event(
                        event_id=uuid4(),
                        event_type="generic.posting",
                        occurred_at=clock.now(),
                        effective_date=effective_date,
                        actor_id=actor_id,
                        producer="trial_balance_stress",
                        payload={
                            "lines": [
                                {"account_code": p[0], "side": p[1], "amount": p[2], "currency": "USD"}
                                for p in pattern
                            ]
                        },
                    )
                    assert result.status == PostingStatus.POSTED, (
                        f"Worker {worker_id} event {i}: expected POSTED, got {result.status}"
                    )
                    worker_posted += 1
                return worker_posted
            finally:
                session.close()

        start_time = time.time()
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(post_batch, i) for i in range(num_workers)]
            counts = [f.result() for f in futures]
        post_time = time.time() - start_time

        total_posted = sum(counts)
        assert total_posted == num_entries

        # Now compute trial balance and time it
        start_time = time.time()
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)
        query_time = time.time() - start_time

        total_debits = sum(row.debit_total for row in trial_balance)
        total_credits = sum(row.credit_total for row in trial_balance)

        print(f"\n5000 entries trial balance:")
        print(f"  Posted: {total_posted} entries in {post_time:.2f}s")
        print(f"  Trial balance query: {query_time:.3f}s")
        print(f"  Total debits: {total_debits}")
        print(f"  Total credits: {total_credits}")
        print(f"  Accounts in TB: {len(trial_balance)}")

        assert total_debits == total_credits, "Trial balance not balanced!"
        assert len(trial_balance) > 0, "Trial balance empty"


class TestMixedWorkload:
    """
    Tests combining reads and writes simultaneously.
    """

    def test_concurrent_reads_and_writes(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Writers post events while readers query trial balance concurrently.

        Tests that reads don't block writes and vice versa.
        """
        actor_id = uuid4()
        num_writers = 20
        num_readers = 10
        duration_seconds = 15

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        stop_event = ThreadEvent()
        write_counts = [0] * num_writers
        write_errors = [0] * num_writers
        write_error_samples = []
        read_counts = [0] * num_readers
        read_errors = [0] * num_readers
        balance_always_balanced = [True] * num_readers
        thread_alive = [False] * (num_writers + num_readers)
        samples_lock = Lock()

        def writer(worker_id: int):
            session = pg_session_factory()
            try:
                thread_alive[worker_id] = True
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                while not stop_event.is_set():
                    try:
                        result = orchestrator.post_event(
                            event_id=uuid4(),
                            event_type="generic.posting",
                            occurred_at=clock.now(),
                            effective_date=effective_date,
                            actor_id=actor_id,
                            producer="mixed_write",
                            payload={
                                "lines": [
                                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                                ]
                            },
                        )
                        if result.status == PostingStatus.POSTED:
                            write_counts[worker_id] += 1
                    except Exception as e:
                        write_errors[worker_id] += 1
                        with samples_lock:
                            if len(write_error_samples) < 10:
                                write_error_samples.append(f"writer-{worker_id}: {e}")
                        try:
                            session.rollback()
                        except Exception:
                            session.close()
                            session = pg_session_factory()
                            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)
            finally:
                session.close()

        def reader(reader_id: int):
            session = pg_session_factory()
            try:
                thread_alive[num_writers + reader_id] = True
                selector = LedgerSelector(session)

                while not stop_event.is_set():
                    try:
                        trial_balance = selector.trial_balance(as_of_date=effective_date)
                        total_debits = sum(row.debit_total for row in trial_balance)
                        total_credits = sum(row.credit_total for row in trial_balance)

                        if total_debits != total_credits:
                            balance_always_balanced[reader_id] = False

                        read_counts[reader_id] += 1
                        time.sleep(0.1)  # Small delay to not overwhelm
                    except Exception:
                        read_errors[reader_id] += 1
            finally:
                session.close()

        # Start all workers
        with ThreadPoolExecutor(max_workers=num_writers + num_readers) as executor:
            writer_futures = [executor.submit(writer, i) for i in range(num_writers)]
            reader_futures = [executor.submit(reader, i) for i in range(num_readers)]

            time.sleep(duration_seconds)
            stop_event.set()

            wait(writer_futures + reader_futures)

        # Verify all threads actually ran
        assert all(thread_alive), (
            f"Not all threads started. Alive: {sum(thread_alive)}/{num_writers + num_readers}"
        )

        total_writes = sum(write_counts)
        total_reads = sum(read_counts)
        total_write_errors = sum(write_errors)
        total_read_errors = sum(read_errors)

        print(f"\nMixed workload results ({duration_seconds}s):")
        print(f"  Total writes: {total_writes} ({total_writes/duration_seconds:.1f}/sec)")
        print(f"  Total reads: {total_reads} ({total_reads/duration_seconds:.1f}/sec)")
        print(f"  Write errors: {total_write_errors}")
        print(f"  Read errors: {total_read_errors}")
        if write_error_samples:
            print(f"  Write error samples: {write_error_samples[:3]}")

        assert all(balance_always_balanced), "Trial balance was unbalanced during concurrent reads!"
        assert total_writes > 0, "No writes completed"
        assert total_reads > 0, "No reads completed"
        # Write error rate should be low — transient retries are expected, but
        # a majority of failures means something is broken.
        if total_writes + total_write_errors > 0:
            write_error_rate = total_write_errors / (total_writes + total_write_errors)
            assert write_error_rate < 0.20, (
                f"Write error rate too high: {write_error_rate:.1%}. "
                f"Samples: {write_error_samples}"
            )


class TestAuditChainStress:
    """
    Stress tests for audit chain integrity.
    """

    def test_audit_chain_integrity_after_1000_posts(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Post 1000 events then verify the entire audit chain is valid.
        """
        actor_id = uuid4()
        num_events = 1000
        num_workers = 50
        events_per_worker = num_events // num_workers

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_workers, timeout=60)

        def post_batch(worker_id: int):
            barrier.wait()
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

                worker_posted = 0
                for i in range(events_per_worker):
                    result = orchestrator.post_event(
                        event_id=uuid4(),
                        event_type="generic.posting",
                        occurred_at=clock.now(),
                        effective_date=effective_date,
                        actor_id=actor_id,
                        producer="audit_stress",
                        payload={
                            "lines": [
                                {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                                {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                            ]
                        },
                    )
                    assert result.status == PostingStatus.POSTED, (
                        f"Worker {worker_id} event {i}: expected POSTED, got {result.status}"
                    )
                    worker_posted += 1

                return worker_posted
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(post_batch, i) for i in range(num_workers)]
            counts = [f.result() for f in futures]

        total_posted = sum(counts)
        assert total_posted == num_events

        # Verify audit chain
        start_time = time.time()
        with pg_session_factory() as verify_session:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(verify_session, clock, auto_commit=False)
            chain_valid = orchestrator.validate_chain()
        verify_time = time.time() - start_time

        print(f"\nAudit chain after {total_posted} posts:")
        print(f"  Chain validation time: {verify_time:.3f}s")
        print(f"  Chain valid: {chain_valid}")

        assert chain_valid, "Audit chain corrupted after high-volume posting!"
