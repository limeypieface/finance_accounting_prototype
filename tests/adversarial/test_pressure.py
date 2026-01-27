"""
Adversarial pressure tests for the finance kernel.

These tests are designed to find subtle bugs through:
- Edge cases in numerical precision
- Race conditions at boundaries
- State machine violations
- Attempts to break invariants

Run with:
    pytest tests/adversarial/test_pressure.py -v --timeout=300
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from threading import Barrier, Lock, Event as ThreadEvent
from uuid import uuid4
from typing import List

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from finance_kernel.db.engine import get_session_factory
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.services.sequence_service import SequenceService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.domain.clock import SystemClock, DeterministicClock
from finance_kernel.domain.currency import CurrencyRegistry
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus


pytestmark = pytest.mark.postgres


def cleanup_test_data(session):
    """Clean up all test data."""
    from finance_kernel.models.event import Event
    from finance_kernel.models.audit_event import AuditEvent
    from finance_kernel.services.sequence_service import SequenceCounter

    session.query(JournalLine).delete()
    session.query(JournalEntry).delete()
    session.query(AuditEvent).delete()
    session.query(Event).delete()
    session.query(FiscalPeriod).delete()
    session.query(Account).delete()
    session.query(SequenceCounter).delete()
    session.commit()


def setup_test_data(session, actor_id):
    """Create accounts and period for testing."""
    accounts = {}
    account_specs = [
        ("1000", "Cash", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("1100", "AR", AccountType.ASSET, NormalBalance.DEBIT, None),
        ("2000", "AP", AccountType.LIABILITY, NormalBalance.CREDIT, None),
        ("4000", "Revenue", AccountType.REVENUE, NormalBalance.CREDIT, None),
        ("5000", "COGS", AccountType.EXPENSE, NormalBalance.DEBIT, None),
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


class TestRoundingAvalanche:
    """
    Test that many small transactions don't lose pennies through rounding.

    The "salami slicing" attack: 1000 transactions of $0.001 should sum to exactly $1.00
    """

    def test_1000_sub_penny_transactions_sum_correctly(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Post 1000 transactions of $0.001 each.

        Trial balance should show exactly $1.00 in debits and credits.
        No pennies should be lost to rounding.
        """
        actor_id = uuid4()
        num_transactions = 1000
        amount_per_transaction = Decimal("0.001")
        expected_total = amount_per_transaction * num_transactions  # $1.00

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        posted_count = 0
        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            for i in range(num_transactions):
                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="rounding_avalanche",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": str(amount_per_transaction), "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": str(amount_per_transaction), "currency": "USD"},
                        ]
                    },
                )
                if result.status == PostingStatus.POSTED:
                    posted_count += 1
        finally:
            session.close()

        assert posted_count == num_transactions

        # Verify trial balance
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            # Must be exactly equal - no rounding loss
            assert total_debits == total_credits, f"Balance mismatch: {total_debits} vs {total_credits}"
            assert total_debits == expected_total, f"Expected {expected_total}, got {total_debits}"

    def test_repeating_decimal_division_no_loss(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        $100 split 3 ways should not lose or gain money.

        $100 / 3 = $33.333...
        Three shares + rounding should equal exactly $100.
        """
        actor_id = uuid4()
        total = Decimal("100.00")
        share = (total / 3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # $33.33
        remainder = total - (share * 3)  # $0.01

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            # Post three equal shares
            for i in range(3):
                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="division_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": str(share), "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": str(share), "currency": "USD"},
                        ]
                    },
                )
                assert result.status == PostingStatus.POSTED

            # Post the remainder (the penny)
            if remainder != 0:
                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="division_remainder",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": str(remainder), "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": str(remainder), "currency": "USD"},
                        ]
                    },
                )
                assert result.status == PostingStatus.POSTED
        finally:
            session.close()

        # Verify we have exactly $100
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            assert total_debits == total, f"Expected {total}, got {total_debits}"


class TestPeriodBoundaryRace:
    """
    Test race conditions at period boundaries.
    """

    def test_concurrent_post_and_close_period(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        One thread posts, another closes the period simultaneously.

        Either the post succeeds OR period closes first - never both partially.
        """
        actor_id = uuid4()
        num_posters = 20

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date
            period_id = period.id

        barrier = Barrier(num_posters + 1)  # +1 for closer
        results = {"posted": 0, "rejected": 0, "closed": False}
        results_lock = Lock()

        def post_event(thread_id: int):
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)
                barrier.wait()

                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="race_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                            {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                        ]
                    },
                )

                with results_lock:
                    if result.status == PostingStatus.POSTED:
                        results["posted"] += 1
                    else:
                        results["rejected"] += 1
            except Exception:
                with results_lock:
                    results["rejected"] += 1
            finally:
                session.close()

        def close_period():
            session = pg_session_factory()
            try:
                clock = SystemClock()
                period_service = PeriodService(session, clock)
                barrier.wait()

                # Small delay to let some posts start
                time.sleep(0.01)

                period_service.close_period(period_id, actor_id)
                session.commit()

                with results_lock:
                    results["closed"] = True
            except Exception:
                session.rollback()
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_posters + 1) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_posters)]
            futures.append(executor.submit(close_period))
            wait(futures)

        # Verify invariants
        with pg_session_factory() as verify_session:
            # Check period is closed
            period = verify_session.get(FiscalPeriod, period_id)

            # Count actual posted entries
            actual_posted = verify_session.query(JournalEntry).filter(
                JournalEntry.status == JournalEntryStatus.POSTED
            ).count()

            # Trial balance should still balance
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)
            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, "Trial balance corrupted by race!"

            print(f"\nRace results: {results['posted']} posted, {results['rejected']} rejected, period closed: {results['closed']}")
            print(f"Actual posted entries: {actual_posted}")


class TestCurrencyPrecisionTrap:
    """
    Test currency conversions don't lose money through precision differences.
    """

    def test_multi_currency_round_trip(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        USD → different precision currencies should maintain value.

        Different currencies have different decimal places:
        - USD: 2 decimals
        - JPY: 0 decimals
        - KWD: 3 decimals
        """
        actor_id = uuid4()

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        # Test with amounts that could lose precision
        test_amounts = [
            ("USD", "100.00"),
            ("USD", "100.01"),      # One cent
            ("USD", "100.005"),     # Half cent - should round
            ("USD", "33.33"),       # Repeating decimal origin
            ("USD", "0.01"),        # Minimum USD
        ]

        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            for currency, amount in test_amounts:
                result = orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="precision_test",
                    payload={
                        "lines": [
                            {"account_code": "1000", "side": "debit", "amount": amount, "currency": currency},
                            {"account_code": "4000", "side": "credit", "amount": amount, "currency": currency},
                        ]
                    },
                )
                assert result.status == PostingStatus.POSTED, f"Failed to post {amount} {currency}"
        finally:
            session.close()

        # Verify trial balance
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, "Precision loss in trial balance!"


class TestBalanceOscillation:
    """
    Test rapid back-and-forth transactions maintain consistency.
    """

    def test_rapid_credit_debit_oscillation(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Rapidly post +$100 then -$100 from multiple threads.

        Each thread posts complete pairs (+100/-100), so the trial balance
        for each thread's contribution should be self-balancing.
        """
        actor_id = uuid4()
        num_threads = 5
        oscillations_per_thread = 10

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads)
        post_counts = {"success": 0, "failed": 0}
        counts_lock = Lock()

        def oscillate(thread_id: int):
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)
                barrier.wait()

                for i in range(oscillations_per_thread):
                    # Post +$100 (debit cash, credit revenue)
                    result1 = orchestrator.post_event(
                        event_id=uuid4(),
                        event_type="generic.posting",
                        occurred_at=clock.now(),
                        effective_date=effective_date,
                        actor_id=actor_id,
                        producer="oscillation",
                        payload={
                            "lines": [
                                {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                                {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                            ]
                        },
                    )

                    # Post -$100 (credit cash, debit expense)
                    result2 = orchestrator.post_event(
                        event_id=uuid4(),
                        event_type="generic.posting",
                        occurred_at=clock.now(),
                        effective_date=effective_date,
                        actor_id=actor_id,
                        producer="oscillation",
                        payload={
                            "lines": [
                                {"account_code": "5000", "side": "debit", "amount": "100.00", "currency": "USD"},
                                {"account_code": "1000", "side": "credit", "amount": "100.00", "currency": "USD"},
                            ]
                        },
                    )

                    with counts_lock:
                        if result1.status == PostingStatus.POSTED:
                            post_counts["success"] += 1
                        else:
                            post_counts["failed"] += 1
                        if result2.status == PostingStatus.POSTED:
                            post_counts["success"] += 1
                        else:
                            post_counts["failed"] += 1

            except Exception as e:
                with counts_lock:
                    post_counts["failed"] += 1
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(oscillate, i) for i in range(num_threads)]
            wait(futures)

        expected_total = num_threads * oscillations_per_thread * 2
        print(f"\nOscillation: {post_counts['success']} succeeded, {post_counts['failed']} failed of {expected_total}")

        # Core invariant: trial balance must always be balanced
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, f"Trial balance not balanced! D={total_debits} C={total_credits}"

            # Verify entry counts match line counts
            entry_count = verify_session.query(JournalEntry).filter(
                JournalEntry.status == JournalEntryStatus.POSTED
            ).count()
            line_count = verify_session.query(JournalLine).count()

            # Each entry has exactly 2 lines in this test
            assert line_count == entry_count * 2, f"Line count mismatch: {line_count} lines for {entry_count} entries"

            # Success rate should be high (transient failures OK, but not systematic)
            success_rate = post_counts["success"] / expected_total if expected_total > 0 else 0
            assert success_rate >= 0.90, f"Too many failures: {success_rate:.1%} success rate"


class TestSequencePredictionAttack:
    """
    Test that sequence numbers can't be predicted or reused.
    """

    def test_concurrent_sequence_allocation_unique(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Multiple threads allocate sequences concurrently - all must be unique.

        This tests that our row-level locking prevents duplicate sequences.
        Note: With row-level locking, sequences are serialized so we use
        moderate concurrency to avoid test timeouts.
        """
        num_threads = 10
        sequences_per_thread = 5

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)

        all_sequences = []
        seq_lock = Lock()
        barrier = Barrier(num_threads)

        def allocate_sequences(thread_id: int):
            session = pg_session_factory()
            try:
                service = SequenceService(session)
                barrier.wait()

                thread_seqs = []
                for _ in range(sequences_per_thread):
                    seq = service.next_value("prediction_test")
                    session.commit()
                    thread_seqs.append(seq)

                with seq_lock:
                    all_sequences.extend(thread_seqs)
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(allocate_sequences, i) for i in range(num_threads)]
            wait(futures)

        # Verify all sequences are unique
        expected_count = num_threads * sequences_per_thread
        assert len(all_sequences) == expected_count, f"Expected {expected_count}, got {len(all_sequences)}"
        assert len(all_sequences) == len(set(all_sequences)), "Duplicate sequences detected!"

        # Verify sequences are monotonic (no gaps expected with our implementation)
        sorted_seqs = sorted(all_sequences)
        for i in range(1, len(sorted_seqs)):
            assert sorted_seqs[i] == sorted_seqs[i-1] + 1, f"Gap in sequence: {sorted_seqs[i-1]} -> {sorted_seqs[i]}"


class TestGhostEntry:
    """
    Test that failed validations don't leave ghost entries.
    """

    def test_validation_failure_leaves_no_trace(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Attempt to post an invalid entry - no trace should remain.
        """
        actor_id = uuid4()

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        event_id = uuid4()

        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            # Try to post unbalanced entry
            result = orchestrator.post_event(
                event_id=event_id,
                event_type="generic.posting",
                occurred_at=clock.now(),
                effective_date=effective_date,
                actor_id=actor_id,
                producer="ghost_test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "99.00", "currency": "USD"},  # Unbalanced!
                    ]
                },
            )

            assert result.status != PostingStatus.POSTED
        except Exception:
            pass  # Expected to fail
        finally:
            session.close()

        # Verify no ghost entry exists
        with pg_session_factory() as verify_session:
            # Check for any journal entry
            entries = verify_session.query(JournalEntry).all()
            assert len(entries) == 0, f"Ghost entry found: {entries}"

            # Check for any journal lines
            lines = verify_session.query(JournalLine).all()
            assert len(lines) == 0, f"Ghost lines found: {lines}"


class TestTimeTraveler:
    """
    Test handling of extreme dates.
    """

    def test_far_future_date_handling(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Posting to year 2099 - should be rejected (no period) or handled gracefully.
        """
        actor_id = uuid4()

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)

        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            # Try to post to far future
            result = orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=clock.now(),
                effective_date=date(2099, 12, 31),  # Far future
                actor_id=actor_id,
                producer="time_traveler",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )

            # Should be rejected - no period exists
            assert result.status != PostingStatus.POSTED, "Future date should not post without period"
        except Exception as e:
            # Expected - no period for this date
            assert "period" in str(e).lower() or "date" in str(e).lower()
        finally:
            session.close()


class TestOrphanHunter:
    """
    Test that orphan records can't be created.
    """

    def test_cannot_insert_orphan_journal_line(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Try to INSERT a journal line without a journal entry.

        Database should reject this via foreign key constraint.
        """
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)

        session = pg_session_factory()
        try:
            # Try to insert orphan line directly
            orphan_line = JournalLine(
                journal_entry_id=uuid4(),  # Non-existent entry!
                account_id=uuid4(),  # Doesn't matter - FK should fail first
                side="debit",
                amount=Decimal("100.00"),
                currency="USD",
            )
            session.add(orphan_line)
            session.flush()

            # Should not reach here
            pytest.fail("Orphan journal line was created!")

        except IntegrityError:
            # Expected - foreign key violation
            session.rollback()
        finally:
            session.close()


class TestAuditChainFork:
    """
    Test that parallel audit chains can't be created.
    """

    def test_concurrent_audit_events_maintain_single_chain(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Many concurrent posts should still produce a single linear audit chain.
        """
        actor_id = uuid4()
        num_threads = 20

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        barrier = Barrier(num_threads)

        def post_event(thread_id: int):
            session = pg_session_factory()
            try:
                clock = SystemClock()
                orchestrator = PostingOrchestrator(session, clock, auto_commit=True)
                barrier.wait()

                orchestrator.post_event(
                    event_id=uuid4(),
                    event_type="generic.posting",
                    occurred_at=clock.now(),
                    effective_date=effective_date,
                    actor_id=actor_id,
                    producer="chain_fork_test",
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
            wait(futures)

        # Verify audit chain is linear (no forks)
        with pg_session_factory() as verify_session:
            from finance_kernel.models.audit_event import AuditEvent

            audit_events = verify_session.query(AuditEvent).order_by(AuditEvent.seq).all()

            # Check for duplicate prev_hash (would indicate fork)
            prev_hashes = [e.prev_hash for e in audit_events if e.prev_hash is not None]
            assert len(prev_hashes) == len(set(prev_hashes)), "Audit chain forked! Duplicate prev_hash found."

            # Verify chain linkage
            for i in range(1, len(audit_events)):
                assert audit_events[i].prev_hash == audit_events[i-1].hash, \
                    f"Chain broken at seq {audit_events[i].seq}"


class TestExtremePayloads:
    """
    Test handling of extreme payloads.
    """

    def test_entry_with_100_lines(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Post an entry with 100 lines - should still balance.
        """
        actor_id = uuid4()
        num_lines = 100
        amount_per_line = Decimal("10.00")

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date

        session = pg_session_factory()
        try:
            clock = SystemClock()
            orchestrator = PostingOrchestrator(session, clock, auto_commit=True)

            # Create 50 debit lines and 50 credit lines
            lines = []
            for i in range(num_lines // 2):
                lines.append({"account_code": "1000", "side": "debit", "amount": str(amount_per_line), "currency": "USD"})
                lines.append({"account_code": "4000", "side": "credit", "amount": str(amount_per_line), "currency": "USD"})

            result = orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=clock.now(),
                effective_date=effective_date,
                actor_id=actor_id,
                producer="extreme_payload",
                payload={"lines": lines},
            )

            assert result.status == PostingStatus.POSTED
        finally:
            session.close()

        # Verify
        with pg_session_factory() as verify_session:
            entry = verify_session.query(JournalEntry).first()
            line_count = verify_session.query(JournalLine).filter(
                JournalLine.journal_entry_id == entry.id
            ).count()

            assert line_count == num_lines, f"Expected {num_lines} lines, got {line_count}"
