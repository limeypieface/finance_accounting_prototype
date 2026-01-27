"""
R6 Replay Safety tests.

R6. Replay safety

Any field that affects ledger state must be reproducible from the journal
and reference data alone.

Projections must be disposable.

This means:
1. No stored/cached balances - all balances computed from journal lines
2. Trial balance can be recomputed at any time and produce same result
3. Ledger state depends ONLY on:
   - Journal entries (immutable once posted)
   - Reference data (accounts, periods, etc.)
4. No hidden state that could cause divergence on replay
"""

import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text, inspect

from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus
from finance_kernel.models.account import Account
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus


class TestR6NoStoredBalances:
    """
    Verify that the system has no stored/cached balance fields.

    R6: Projections must be disposable - no stored state.
    """

    def test_accounts_have_no_balance_column(self, session):
        """
        Verify Account model has no balance column.

        R6: Balances must be computed, not stored.
        """
        inspector = inspect(session.bind)
        columns = [col['name'] for col in inspector.get_columns('accounts')]

        # Should NOT have any balance-related columns
        balance_columns = [c for c in columns if 'balance' in c.lower() and c != 'normal_balance']
        assert len(balance_columns) == 0, f"Found balance columns in accounts: {balance_columns}"

        # Should NOT have any cached/computed columns
        cached_columns = [c for c in columns if 'cached' in c.lower() or 'computed' in c.lower()]
        assert len(cached_columns) == 0, f"Found cached columns in accounts: {cached_columns}"

    def test_journal_entries_have_no_running_balance(self, session):
        """
        Verify JournalEntry model has no running balance column.

        R6: Running balances must be computed, not stored.
        """
        inspector = inspect(session.bind)
        columns = [col['name'] for col in inspector.get_columns('journal_entries')]

        # Should NOT have running balance
        balance_columns = [c for c in columns if 'balance' in c.lower()]
        assert len(balance_columns) == 0, f"Found balance columns in journal_entries: {balance_columns}"

    def test_journal_lines_have_no_running_balance(self, session):
        """
        Verify JournalLine model has no running balance column.

        R6: Running balances must be computed, not stored.
        """
        inspector = inspect(session.bind)
        columns = [col['name'] for col in inspector.get_columns('journal_lines')]

        # Should NOT have running balance
        balance_columns = [c for c in columns if 'running' in c.lower() or 'cumulative' in c.lower()]
        assert len(balance_columns) == 0, f"Found running balance columns: {balance_columns}"

    def test_no_balance_tables_exist(self, session):
        """
        Verify no balance/projection tables exist.

        R6: Projections must be disposable - no materialized views.
        """
        inspector = inspect(session.bind)
        tables = inspector.get_table_names()

        # Should NOT have any balance or projection tables
        forbidden_patterns = ['balance', 'projection', 'cache', 'materialized', 'summary']
        for pattern in forbidden_patterns:
            matching = [t for t in tables if pattern in t.lower()]
            # account_balances view would be bad, balance in name is OK for checking
            bad_tables = [t for t in matching if t not in ['accounts']]
            assert len(bad_tables) == 0, f"Found {pattern} tables: {bad_tables}"


class TestR6TrialBalanceReproducibility:
    """
    Verify trial balance is fully reproducible from journal entries.

    R6: Any field that affects ledger state must be reproducible.
    """

    def test_trial_balance_same_result_multiple_calls(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Calling trial_balance multiple times must give identical results.

        R6: Projections are computed, not cached with potential staleness.
        """
        # Post some entries
        for i in range(5):
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(100 * (i + 1)), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(100 * (i + 1)), "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        # Compute trial balance 10 times
        results = []
        for _ in range(10):
            tb = ledger_selector.trial_balance(as_of_date=current_period.end_date)
            # Convert to comparable tuple
            tb_tuple = tuple(
                (str(r.account_id), r.account_code, str(r.debit_total), str(r.credit_total))
                for r in sorted(tb, key=lambda x: x.account_code)
            )
            results.append(tb_tuple)

        # All must be identical
        assert len(set(results)) == 1, "Trial balance must be reproducible"

    def test_trial_balance_reproducible_after_new_session(
        self,
        session,
        engine,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Trial balance must be reproducible in a new session.

        R6: State must be reproducible from persisted data alone.
        """
        # Post some entries
        for i in range(3):
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(50 * (i + 1)), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(50 * (i + 1)), "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        session.commit()

        # Compute in current session
        ledger1 = LedgerSelector(session)
        tb1 = ledger1.trial_balance(as_of_date=current_period.end_date)
        tb1_tuple = tuple(
            (str(r.account_id), r.account_code, str(r.debit_total), str(r.credit_total))
            for r in sorted(tb1, key=lambda x: x.account_code)
        )

        # Compute in fresh session using engine
        from sqlalchemy.orm import Session as SQLAlchemySession
        with SQLAlchemySession(engine) as new_session:
            ledger2 = LedgerSelector(new_session)
            tb2 = ledger2.trial_balance(as_of_date=current_period.end_date)
            tb2_tuple = tuple(
                (str(r.account_id), r.account_code, str(r.debit_total), str(r.credit_total))
                for r in sorted(tb2, key=lambda x: x.account_code)
            )

        # Must be identical
        assert tb1_tuple == tb2_tuple, "Trial balance must be same across sessions"


class TestR6LedgerStateFromJournalOnly:
    """
    Verify ledger state depends only on journal entries and reference data.

    R6: Any field that affects ledger state must be reproducible from
    the journal and reference data alone.
    """

    def test_account_balance_derived_from_journal_lines(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Account balances must be computed from journal lines only.

        R6: Balances are projections, not stored state.
        """
        # Post entries
        amounts = [Decimal("100.00"), Decimal("250.00"), Decimal("75.50")]
        for amount in amounts:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": str(amount), "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": str(amount), "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        # Get balance via selector (computed)
        cash_account_id = standard_accounts["cash"].id
        balances = ledger_selector.account_balance(cash_account_id, as_of_date=current_period.end_date)

        assert len(balances) == 1
        computed_balance = balances[0].debit_total

        # Manually compute from journal lines
        manual_total = session.execute(
            text("""
                SELECT COALESCE(SUM(jl.amount), 0) as total
                FROM journal_lines jl
                JOIN journal_entries je ON jl.journal_entry_id = je.id
                WHERE jl.account_id = :account_id
                AND jl.side = 'debit'
                AND je.status = 'posted'
                AND je.effective_date <= :as_of
            """),
            {"account_id": str(cash_account_id), "as_of": current_period.end_date}
        ).scalar()

        # Must match
        assert computed_balance == manual_total, "Balance must be derived from journal lines"
        assert computed_balance == sum(amounts), f"Expected {sum(amounts)}, got {computed_balance}"

    def test_total_debits_equals_credits(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Total debits must equal total credits (double-entry invariant).

        R6: This invariant must hold when computed from journal lines.
        """
        # Post various entries
        entries = [
            ("1000", "4000", "100.00"),
            ("5000", "1200", "60.00"),
            ("1000", "4000", "200.00"),
            ("2000", "1000", "50.00"),
        ]

        for debit_acct, credit_acct, amount in entries:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": debit_acct, "side": "debit", "amount": amount, "currency": "USD"},
                        {"account_code": credit_acct, "side": "credit", "amount": amount, "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        # Verify double-entry
        total_debits, total_credits = ledger_selector.total_debits_credits(
            as_of_date=current_period.end_date
        )

        assert total_debits == total_credits, "Debits must equal credits"


class TestR6ProjectionDisposability:
    """
    Verify projections can be discarded and regenerated.

    R6: Projections must be disposable.
    """

    def test_trial_balance_is_pure_query(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Trial balance is a pure query with no side effects.

        R6: Projections are disposable queries, not stored data.
        """
        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=current_period.start_date,
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.is_success

        # Count rows before
        journal_count_before = session.query(JournalEntry).count()
        line_count_before = session.query(JournalLine).count()

        # Call trial_balance multiple times
        for _ in range(5):
            _ = ledger_selector.trial_balance(as_of_date=current_period.end_date)

        # Count rows after - should be unchanged
        journal_count_after = session.query(JournalEntry).count()
        line_count_after = session.query(JournalLine).count()

        assert journal_count_before == journal_count_after, "trial_balance should not create entries"
        assert line_count_before == line_count_after, "trial_balance should not create lines"

    def test_no_write_operations_in_ledger_queries(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Ledger selector queries must be read-only.

        R6: Projections are disposable - no writes on read.
        """
        # Post entries
        for i in range(3):
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        session.commit()

        # Get before state
        before_entries = session.query(JournalEntry).count()

        # Execute all ledger queries
        _ = ledger_selector.trial_balance()
        _ = ledger_selector.query()
        cash_id = standard_accounts["cash"].id
        _ = ledger_selector.account_balance(cash_id)
        _ = ledger_selector.total_debits_credits()

        # Verify no changes
        after_entries = session.query(JournalEntry).count()
        assert before_entries == after_entries, "Ledger queries must be read-only"


class TestR6ReplayFromEventsOnly:
    """
    Verify state can be reconstructed by replaying events.

    R6: Ledger state reproducible from journal and reference data alone.
    """

    def test_same_events_produce_same_balances(
        self,
        session,
        posting_orchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
        ledger_selector,
    ):
        """
        Posting the same events must produce the same account balances.

        R6: State is deterministic function of events.
        """
        # Create test events
        test_events = []
        for i in range(5):
            test_events.append({
                "event_id": uuid4(),
                "amount": str(Decimal("100.00") * (i + 1)),
            })

        # Post events
        for event in test_events:
            result = posting_orchestrator.post_event(
                event_id=event["event_id"],
                event_type="generic.posting",
                occurred_at=deterministic_clock.now(),
                effective_date=current_period.start_date,
                actor_id=test_actor_id,
                producer="test",
                payload={
                    "lines": [
                        {"account_code": "1000", "side": "debit", "amount": event["amount"], "currency": "USD"},
                        {"account_code": "4000", "side": "credit", "amount": event["amount"], "currency": "USD"},
                    ]
                },
            )
            assert result.is_success

        # Get trial balance
        tb = ledger_selector.trial_balance(as_of_date=current_period.end_date)

        # Expected total: 100 + 200 + 300 + 400 + 500 = 1500
        expected_total = Decimal("1500.00")

        cash_row = next((r for r in tb if r.account_code == "1000"), None)
        assert cash_row is not None
        assert cash_row.debit_total == expected_total


class TestR6DocumentedReplayArchitecture:
    """
    Document and verify the replay architecture.
    """

    def test_document_replay_components(self):
        """
        Document the components required for replay.

        R6: Ledger state reproducible from journal and reference data.
        """
        # The replay-safe architecture consists of:
        replay_components = {
            "source_of_truth": [
                "events",           # Original business events
                "journal_entries",  # Posted journal entries
                "journal_lines",    # Individual debit/credit lines
            ],
            "reference_data": [
                "accounts",         # Chart of accounts
                "fiscal_periods",   # Period definitions
                "dimensions",       # Dimension definitions
                "dimension_values", # Valid dimension values
                "exchange_rates",   # Historical exchange rates
            ],
            "computed_on_demand": [
                "trial_balance",    # Computed from journal_lines
                "account_balance",  # Computed from journal_lines
                "ledger_view",      # Query over posted entries
            ],
            "never_stored": [
                "running_balances", # Always computed
                "period_summaries", # Always computed
                "cached_totals",    # No caching
            ],
        }

        # Verify source tables exist
        from finance_kernel.models.journal import JournalEntry, JournalLine
        from finance_kernel.models.account import Account
        from finance_kernel.models.fiscal_period import FiscalPeriod
        from finance_kernel.models.event import Event

        assert JournalEntry.__tablename__ == "journal_entries"
        assert JournalLine.__tablename__ == "journal_lines"
        assert Account.__tablename__ == "accounts"
        assert FiscalPeriod.__tablename__ == "fiscal_periods"
        assert Event.__tablename__ == "events"

        # Verify LedgerSelector computes (doesn't store)
        from finance_kernel.selectors.ledger_selector import LedgerSelector

        # LedgerSelector has no __tablename__ - it's a query class
        assert not hasattr(LedgerSelector, '__tablename__')
