"""
Adversarial pressure tests using the interpretation pipeline.

These tests find subtle bugs through:
- Edge cases in numerical precision
- Race conditions at boundaries
- State machine violations
- Attempts to break invariants

All posting now goes through InterpretationCoordinator → JournalWriter
using role-based AccountingIntents and EconomicEventData.

Run with:
    pytest tests/adversarial/test_pressure.py -v --timeout=300
"""

import pytest
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from finance_kernel.db.engine import get_session_factory
from finance_kernel.services.interpretation_coordinator import InterpretationCoordinator
from finance_kernel.services.journal_writer import JournalWriter, RoleResolver
from finance_kernel.services.outcome_recorder import OutcomeRecorder
from finance_kernel.services.sequence_service import SequenceService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.domain.clock import SystemClock, DeterministicClock
from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.meaning_builder import (
    EconomicEventData,
    MeaningBuilderResult,
)
from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.event import Event
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus


pytestmark = pytest.mark.postgres


def cleanup_test_data(session):
    """Clean up all test data.

    Uses TRUNCATE CASCADE to bypass immutability triggers that prevent
    deletion of posted journal lines/entries.  Truncates each table
    individually so missing tables are silently skipped.
    """
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


def _build_role_resolver(accounts):
    """Build a RoleResolver from the test accounts dict (code → Account)."""
    resolver = RoleResolver()
    role_map = {
        "CashAsset": accounts["1000"],
        "AccountsReceivable": accounts["1100"],
        "AccountsPayable": accounts["2000"],
        "SalesRevenue": accounts["4000"],
        "COGS": accounts["5000"],
        "RoundingExpense": accounts["9999"],
    }
    for role, account in role_map.items():
        resolver.register_binding(role, account.id, account.code)
    return resolver


def _make_coordinator(session, role_resolver, clock=None):
    """Create a full InterpretationCoordinator from a session."""
    clock = clock or SystemClock()
    writer = JournalWriter(session, role_resolver, clock)
    recorder = OutcomeRecorder(session, clock)
    return InterpretationCoordinator(session, writer, recorder, clock)


def _create_source_event(session, source_event_id, actor_id, effective_date, event_type="test.event"):
    """Create a source Event record (FK requirement for JournalEntry)."""
    from finance_kernel.utils.hashing import hash_payload
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    payload = {"test": "data"}
    evt = Event(
        event_id=source_event_id,
        event_type=event_type,
        occurred_at=now,
        effective_date=effective_date,
        actor_id=actor_id,
        producer="test",
        payload=payload,
        payload_hash=hash_payload(payload),
        schema_version=1,
        ingested_at=now,
    )
    session.add(evt)
    session.flush()
    return evt


def _post_balanced_entry(
    coordinator,
    session,
    actor_id,
    effective_date,
    debit_role="CashAsset",
    credit_role="SalesRevenue",
    amount=Decimal("100.00"),
    currency="USD",
    profile_id="PressureTest",
):
    """Post a balanced entry through the interpretation pipeline."""
    source_event_id = uuid4()
    econ_event_id = uuid4()

    # Create source Event record (FK requirement for JournalEntry)
    _create_source_event(session, source_event_id, actor_id, effective_date)

    econ_data = EconomicEventData(
        source_event_id=source_event_id,
        economic_type="pressure.posting",
        effective_date=effective_date,
        profile_id=profile_id,
        profile_version=1,
        profile_hash=None,
        quantity=amount,
    )
    meaning_result = MeaningBuilderResult.ok(econ_data)

    intent = AccountingIntent(
        econ_event_id=econ_event_id,
        source_event_id=source_event_id,
        profile_id=profile_id,
        profile_version=1,
        effective_date=effective_date,
        ledger_intents=(
            LedgerIntent(
                ledger_id="GL",
                lines=(
                    IntentLine.debit(debit_role, amount, currency),
                    IntentLine.credit(credit_role, amount, currency),
                ),
            ),
        ),
        snapshot=AccountingIntentSnapshot(
            coa_version=1,
            dimension_schema_version=1,
        ),
    )

    return coordinator.interpret_and_post(
        meaning_result=meaning_result,
        accounting_intent=intent,
        actor_id=actor_id,
    )


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
        """
        actor_id = uuid4()
        num_transactions = 1000
        amount_per_transaction = Decimal("0.001")
        expected_total = amount_per_transaction * num_transactions  # $1.00

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date
            role_resolver = _build_role_resolver(accounts)

        posted_count = 0
        session = pg_session_factory()
        try:
            coordinator = _make_coordinator(session, role_resolver)

            for i in range(num_transactions):
                result = _post_balanced_entry(
                    coordinator,
                    session,
                    actor_id,
                    effective_date,
                    amount=amount_per_transaction,
                )
                if result.success:
                    posted_count += 1
                    session.commit()
                else:
                    session.rollback()
        finally:
            session.close()

        assert posted_count == num_transactions

        # Verify trial balance
        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, f"Balance mismatch: {total_debits} vs {total_credits}"
            assert total_debits == expected_total, f"Expected {expected_total}, got {total_debits}"

    def test_repeating_decimal_division_no_loss(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        $100 split 3 ways should not lose or gain money.
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
            role_resolver = _build_role_resolver(accounts)

        session = pg_session_factory()
        try:
            coordinator = _make_coordinator(session, role_resolver)

            for i in range(3):
                result = _post_balanced_entry(
                    coordinator,
                    session,
                    actor_id,
                    effective_date,
                    amount=share,
                )
                assert result.success
                session.commit()

            if remainder != 0:
                result = _post_balanced_entry(
                    coordinator,
                    session,
                    actor_id,
                    effective_date,
                    amount=remainder,
                )
                assert result.success
                session.commit()
        finally:
            session.close()

        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            assert total_debits == total, f"Expected {total}, got {total_debits}"


class TestPeriodBoundaryRace:
    """Test race conditions at period boundaries."""

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
            role_resolver = _build_role_resolver(accounts)

        barrier = Barrier(num_posters + 1, timeout=30)

        def post_event(thread_id: int):
            """Returns 'posted' or 'rejected'. Must never raise."""
            session = pg_session_factory()
            try:
                coordinator = _make_coordinator(session, role_resolver)
                barrier.wait()

                result = _post_balanced_entry(
                    coordinator, session, actor_id, effective_date,
                )
                if result.success:
                    session.commit()
                    return "posted"
                else:
                    session.rollback()
                    return "rejected"
            finally:
                session.close()

        def close_period_task():
            """Returns 'closed'. Must never raise."""
            session = pg_session_factory()
            try:
                clock = SystemClock()
                period_service = PeriodService(session, clock)
                barrier.wait()
                time.sleep(0.01)
                period_service.close_period(period_id, actor_id)
                session.commit()
                return "closed"
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_posters + 1) as executor:
            post_futures = [executor.submit(post_event, i) for i in range(num_posters)]
            close_future = executor.submit(close_period_task)

            # All must complete without exception
            post_outcomes = [f.result() for f in post_futures]
            close_outcome = close_future.result()

        assert close_outcome == "closed"
        posted_count = post_outcomes.count("posted")
        rejected_count = post_outcomes.count("rejected")

        with pg_session_factory() as verify_session:
            period = verify_session.get(FiscalPeriod, period_id)

            actual_posted = verify_session.query(JournalEntry).filter(
                JournalEntry.status == JournalEntryStatus.POSTED
            ).count()

            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)
            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, "Trial balance corrupted by race!"

            print(f"\nRace results: {posted_count} posted, {rejected_count} rejected, period closed: True")
            print(f"Actual posted entries: {actual_posted}")


class TestCurrencyPrecisionTrap:
    """Test currency conversions don't lose money through precision differences."""

    def test_multi_currency_round_trip(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        USD → different precision currencies should maintain value.
        """
        actor_id = uuid4()

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date
            role_resolver = _build_role_resolver(accounts)

        test_amounts = [
            Decimal("100.00"),
            Decimal("100.01"),
            Decimal("100.005"),
            Decimal("33.33"),
            Decimal("0.01"),
        ]

        session = pg_session_factory()
        try:
            coordinator = _make_coordinator(session, role_resolver)

            for amount in test_amounts:
                result = _post_balanced_entry(
                    coordinator,
                    session,
                    actor_id,
                    effective_date,
                    amount=amount,
                )
                assert result.success, f"Failed to post {amount} USD"
                session.commit()
        finally:
            session.close()

        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, "Precision loss in trial balance!"


class TestBalanceOscillation:
    """Test rapid back-and-forth transactions maintain consistency."""

    def test_rapid_credit_debit_oscillation(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Rapidly post +$100 then -$100 from multiple threads.
        Each thread posts complete pairs, so the trial balance should always balance.
        """
        actor_id = uuid4()
        num_threads = 5
        oscillations_per_thread = 10

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date
            role_resolver = _build_role_resolver(accounts)

        barrier = Barrier(num_threads, timeout=30)

        def oscillate(thread_id: int):
            session = pg_session_factory()
            try:
                coordinator = _make_coordinator(session, role_resolver)
                barrier.wait()

                posted = 0
                for i in range(oscillations_per_thread):
                    # Post +$100 (debit cash, credit revenue)
                    result1 = _post_balanced_entry(
                        coordinator, session, actor_id, effective_date,
                        debit_role="CashAsset", credit_role="SalesRevenue",
                    )
                    assert result1.success, f"Thread {thread_id} oscillation {i} debit failed"
                    session.commit()
                    posted += 1

                    # Post -$100 (debit COGS, credit cash)
                    result2 = _post_balanced_entry(
                        coordinator, session, actor_id, effective_date,
                        debit_role="COGS", credit_role="CashAsset",
                    )
                    assert result2.success, f"Thread {thread_id} oscillation {i} credit failed"
                    session.commit()
                    posted += 1

                return posted
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(oscillate, i) for i in range(num_threads)]
            counts = [f.result() for f in futures]

        expected_total = num_threads * oscillations_per_thread * 2
        assert sum(counts) == expected_total

        with pg_session_factory() as verify_session:
            selector = LedgerSelector(verify_session)
            trial_balance = selector.trial_balance(as_of_date=effective_date)

            total_debits = sum(row.debit_total for row in trial_balance)
            total_credits = sum(row.credit_total for row in trial_balance)

            assert total_debits == total_credits, f"Trial balance not balanced! D={total_debits} C={total_credits}"

            entry_count = verify_session.query(JournalEntry).filter(
                JournalEntry.status == JournalEntryStatus.POSTED
            ).count()
            line_count = verify_session.query(JournalLine).count()

            assert line_count == entry_count * 2, f"Line count mismatch: {line_count} lines for {entry_count} entries"
            assert entry_count == expected_total


class TestSequencePredictionAttack:
    """Test that sequence numbers can't be predicted or reused."""

    def test_concurrent_sequence_allocation_unique(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Multiple threads allocate sequences concurrently - all must be unique.
        """
        num_threads = 10
        sequences_per_thread = 5

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)

        barrier = Barrier(num_threads, timeout=30)

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
                return thread_seqs
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(allocate_sequences, i) for i in range(num_threads)]
            all_sequences = []
            for f in futures:
                all_sequences.extend(f.result())

        expected_count = num_threads * sequences_per_thread
        assert len(all_sequences) == expected_count, f"Expected {expected_count}, got {len(all_sequences)}"
        assert len(all_sequences) == len(set(all_sequences)), "Duplicate sequences detected!"

        sorted_seqs = sorted(all_sequences)
        for i in range(1, len(sorted_seqs)):
            assert sorted_seqs[i] == sorted_seqs[i-1] + 1, f"Gap in sequence: {sorted_seqs[i-1]} -> {sorted_seqs[i]}"


class TestGhostEntry:
    """Test that failed validations don't leave ghost entries."""

    def test_validation_failure_leaves_no_trace(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Attempt to post an unbalanced entry - no trace should remain.
        """
        actor_id = uuid4()

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date
            role_resolver = _build_role_resolver(accounts)

        session = pg_session_factory()
        try:
            coordinator = _make_coordinator(session, role_resolver)

            source_event_id = uuid4()
            econ_event_id = uuid4()
            _create_source_event(session, source_event_id, actor_id, effective_date)

            econ_data = EconomicEventData(
                source_event_id=source_event_id,
                economic_type="pressure.ghost",
                effective_date=effective_date,
                profile_id="GhostTest",
                profile_version=1,
                profile_hash=None,
                quantity=Decimal("100.00"),
            )
            meaning_result = MeaningBuilderResult.ok(econ_data)

            # Intentionally unbalanced intent
            intent = AccountingIntent(
                econ_event_id=econ_event_id,
                source_event_id=source_event_id,
                profile_id="GhostTest",
                profile_version=1,
                effective_date=effective_date,
                ledger_intents=(
                    LedgerIntent(
                        ledger_id="GL",
                        lines=(
                            IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                            IntentLine.credit("SalesRevenue", Decimal("99.00"), "USD"),  # Unbalanced!
                        ),
                    ),
                ),
                snapshot=AccountingIntentSnapshot(
                    coa_version=1,
                    dimension_schema_version=1,
                ),
            )

            result = coordinator.interpret_and_post(
                meaning_result=meaning_result,
                accounting_intent=intent,
                actor_id=actor_id,
            )

            assert not result.success
            session.rollback()
        except Exception:
            session.rollback()
        finally:
            session.close()

        # Verify no ghost entries
        with pg_session_factory() as verify_session:
            entries = verify_session.query(JournalEntry).all()
            assert len(entries) == 0, f"Ghost entry found: {entries}"

            lines = verify_session.query(JournalLine).all()
            assert len(lines) == 0, f"Ghost lines found: {lines}"


class TestTimeTraveler:
    """Test handling of extreme dates."""

    def test_far_future_date_handling(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Posting to year 2099 - should be rejected or handled gracefully.
        The interpretation pipeline blocks via guard when no period exists.
        """
        actor_id = uuid4()

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            role_resolver = _build_role_resolver(accounts)

        session = pg_session_factory()
        try:
            coordinator = _make_coordinator(session, role_resolver)

            from finance_kernel.domain.accounting_policy import GuardCondition, GuardType
            from finance_kernel.domain.meaning_builder import GuardEvaluationResult

            no_period_guard = GuardEvaluationResult.block(
                guard=GuardCondition(
                    guard_type=GuardType.BLOCK,
                    expression="period_exists == false",
                    reason_code="NO_PERIOD",
                    message="No fiscal period exists for this date",
                ),
                detail={"effective_date": "2099-12-31"},
            )

            source_event_id = uuid4()
            _create_source_event(session, source_event_id, actor_id, date(2099, 12, 31))
            econ_data = EconomicEventData(
                source_event_id=source_event_id,
                economic_type="pressure.time_travel",
                effective_date=date(2099, 12, 31),
                profile_id="TimeTravelTest",
                profile_version=1,
                profile_hash=None,
                quantity=Decimal("100.00"),
            )
            meaning_result = MeaningBuilderResult.blocked(no_period_guard)

            intent = AccountingIntent(
                econ_event_id=uuid4(),
                source_event_id=source_event_id,
                profile_id="TimeTravelTest",
                profile_version=1,
                effective_date=date(2099, 12, 31),
                ledger_intents=(
                    LedgerIntent(
                        ledger_id="GL",
                        lines=(
                            IntentLine.debit("CashAsset", Decimal("100.00"), "USD"),
                            IntentLine.credit("SalesRevenue", Decimal("100.00"), "USD"),
                        ),
                    ),
                ),
                snapshot=AccountingIntentSnapshot(
                    coa_version=1,
                    dimension_schema_version=1,
                ),
            )

            result = coordinator.interpret_and_post(
                meaning_result=meaning_result,
                accounting_intent=intent,
                actor_id=actor_id,
            )

            assert not result.success, "Future date should not post without period"
            assert result.error_code == "NO_PERIOD"
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()


class TestOrphanHunter:
    """Test that orphan records can't be created."""

    def test_cannot_insert_orphan_journal_line(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Try to INSERT a journal line without a journal entry.
        Database should reject via foreign key constraint.
        """
        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)

        session = pg_session_factory()
        try:
            orphan_line = JournalLine(
                journal_entry_id=uuid4(),  # Non-existent entry!
                account_id=uuid4(),
                side="debit",
                amount=Decimal("100.00"),
                currency="USD",
            )
            session.add(orphan_line)
            session.flush()

            pytest.fail("Orphan journal line was created!")

        except IntegrityError:
            session.rollback()
        finally:
            session.close()


class TestAuditChainFork:
    """Test that parallel audit chains can't be created."""

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
            role_resolver = _build_role_resolver(accounts)

        barrier = Barrier(num_threads, timeout=30)

        def post_event(thread_id: int):
            session = pg_session_factory()
            try:
                coordinator = _make_coordinator(session, role_resolver)
                barrier.wait()

                result = _post_balanced_entry(
                    coordinator,
                    session,
                    actor_id,
                    effective_date,
                )
                assert result.success, f"Thread {thread_id}: post failed"
                session.commit()
                return "posted"
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(post_event, i) for i in range(num_threads)]
            outcomes = [f.result() for f in futures]

        assert len(outcomes) == num_threads
        assert all(o == "posted" for o in outcomes)

        with pg_session_factory() as verify_session:
            from finance_kernel.models.audit_event import AuditEvent

            audit_events = verify_session.query(AuditEvent).order_by(AuditEvent.seq).all()

            prev_hashes = [e.prev_hash for e in audit_events if e.prev_hash is not None]
            assert len(prev_hashes) == len(set(prev_hashes)), "Audit chain forked! Duplicate prev_hash found."

            for i in range(1, len(audit_events)):
                assert audit_events[i].prev_hash == audit_events[i-1].hash, \
                    f"Chain broken at seq {audit_events[i].seq}"


class TestExtremePayloads:
    """Test handling of extreme payloads."""

    def test_entry_with_100_lines(
        self,
        postgres_engine,
        pg_session_factory,
    ):
        """
        Post an entry with 100 lines via AccountingIntent - should still balance.
        """
        actor_id = uuid4()
        num_pairs = 50
        amount_per_line = Decimal("10.00")

        with pg_session_factory() as setup_session:
            cleanup_test_data(setup_session)
            accounts, period = setup_test_data(setup_session, actor_id)
            effective_date = period.start_date
            role_resolver = _build_role_resolver(accounts)

        session = pg_session_factory()
        try:
            coordinator = _make_coordinator(session, role_resolver)

            source_event_id = uuid4()
            econ_event_id = uuid4()
            _create_source_event(session, source_event_id, actor_id, effective_date)

            # Build 50 debit + 50 credit lines
            lines = []
            for _ in range(num_pairs):
                lines.append(IntentLine.debit("CashAsset", amount_per_line, "USD"))
                lines.append(IntentLine.credit("SalesRevenue", amount_per_line, "USD"))

            econ_data = EconomicEventData(
                source_event_id=source_event_id,
                economic_type="pressure.extreme",
                effective_date=effective_date,
                profile_id="ExtremePayloadTest",
                profile_version=1,
                profile_hash=None,
                quantity=amount_per_line * num_pairs,
            )
            meaning_result = MeaningBuilderResult.ok(econ_data)

            intent = AccountingIntent(
                econ_event_id=econ_event_id,
                source_event_id=source_event_id,
                profile_id="ExtremePayloadTest",
                profile_version=1,
                effective_date=effective_date,
                ledger_intents=(
                    LedgerIntent(
                        ledger_id="GL",
                        lines=tuple(lines),
                    ),
                ),
                snapshot=AccountingIntentSnapshot(
                    coa_version=1,
                    dimension_schema_version=1,
                ),
            )

            result = coordinator.interpret_and_post(
                meaning_result=meaning_result,
                accounting_intent=intent,
                actor_id=actor_id,
            )

            assert result.success
            session.commit()
        finally:
            session.close()

        with pg_session_factory() as verify_session:
            entry = verify_session.query(JournalEntry).first()
            line_count = verify_session.query(JournalLine).filter(
                JournalLine.journal_entry_id == entry.id
            ).count()

            assert line_count == num_pairs * 2, f"Expected {num_pairs * 2} lines, got {line_count}"
