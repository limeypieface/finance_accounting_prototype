"""
Metamorphic and equivalence tests (K1-K2 Certification).

K1: Post + reverse equivalence
K2: Split/merge equivalence

K1 is now fully implemented using the ReversalService.
K2 remains TODO (deferred).

These tests verify that:
1. Post + Reverse returns ledger to baseline (K1) -- IMPLEMENTED
2. Equivalent decompositions preserve financial truth (K2) -- TODO
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    LineSide,
)
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.reversal_service import ReversalService


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def link_graph(session):
    return LinkGraphService(session)


@pytest.fixture
def reversal_service(session, journal_writer, auditor_service, link_graph, period_service, deterministic_clock):
    return ReversalService(
        session=session,
        journal_writer=journal_writer,
        auditor=auditor_service,
        link_graph=link_graph,
        period_service=period_service,
        clock=deterministic_clock,
    )


@pytest.fixture
def ledger(session):
    return LedgerSelector(session)


def _tb_hash(tb_rows) -> dict[str, Decimal]:
    """Compute a deterministic balance map from trial balance rows.

    Returns {account_code: net_balance} where net = debits - credits.
    Accounts with zero balance are excluded.
    """
    balances = {}
    for row in tb_rows:
        net = row.debit_total - row.credit_total
        if net != Decimal("0"):
            balances[row.account_code] = net
    return balances


# =========================================================================
# K1: Post + Reverse Equivalence
# =========================================================================


class TestK1PostReverseEquivalence:
    """
    K1: Post + Reverse equivalence.

    Proves that reversal is a true inverse operation:
    - Trial balance returns to baseline after post + reverse.
    - Reversal lines are exact mechanical inversions.
    - Double reversal is blocked (idempotency).
    """

    def test_post_reverse_returns_to_baseline(
        self, session, post_via_coordinator, reversal_service,
        ledger, current_period, standard_accounts, test_actor_id,
    ):
        """K1.1: Post + reverse returns ledger to baseline state.

        Metamorphic property: for any journal entry E,
            trial_balance(post(E) + reverse(E)) == trial_balance(empty)

        Steps:
        1. Record baseline trial balance (should be all zeros / empty)
        2. Post an entry (trial balance changes)
        3. Reverse the entry
        4. Verify trial balance returns to baseline
        """
        # 1. Baseline
        baseline = _tb_hash(ledger.trial_balance())

        # 2. Post an entry
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("1000.00"),
            currency="USD",
        )
        assert result.success
        session.flush()

        # Verify trial balance changed
        after_post = _tb_hash(ledger.trial_balance())
        assert after_post != baseline, "Posting should change the trial balance"

        # 3. Reverse the entry
        entry_id = result.journal_result.entries[0].entry_id
        reversal_service.reverse_in_same_period(
            original_entry_id=entry_id,
            reason="K1 metamorphic test",
            actor_id=test_actor_id,
        )
        session.flush()

        # 4. Verify baseline restored
        after_reverse = _tb_hash(ledger.trial_balance())
        assert after_reverse == baseline, (
            f"Trial balance should return to baseline after reversal.\n"
            f"Baseline: {baseline}\n"
            f"After reverse: {after_reverse}"
        )

    def test_reversed_entry_has_negated_lines(
        self, session, post_via_coordinator, reversal_service,
        current_period, standard_accounts, test_actor_id,
    ):
        """K1.2: Reversal lines are exact mechanical inversions.

        For each line in the original entry, the reversal has a
        corresponding line with:
        - Same account_id
        - Same amount
        - Opposite side (DEBIT <-> CREDIT)
        """
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("750.00"),
            currency="USD",
        )
        assert result.success
        session.flush()

        entry_id = result.journal_result.entries[0].entry_id
        original = session.get(JournalEntry, entry_id)

        rev_result = reversal_service.reverse_in_same_period(
            original_entry_id=entry_id,
            reason="K1.2 negated lines test",
            actor_id=test_actor_id,
        )
        session.flush()

        reversal = session.get(JournalEntry, rev_result.reversal_entry_id)

        # Build maps: line_seq -> (account_id, amount, side)
        orig_map = {
            l.line_seq: (l.account_id, l.amount, l.side)
            for l in original.lines
        }
        rev_map = {
            l.line_seq: (l.account_id, l.amount, l.side)
            for l in reversal.lines
        }

        assert set(orig_map.keys()) == set(rev_map.keys())

        for seq in orig_map:
            o_acct, o_amt, o_side = orig_map[seq]
            r_acct, r_amt, r_side = rev_map[seq]

            assert r_acct == o_acct, f"Line {seq}: account mismatch"
            assert r_amt == o_amt, f"Line {seq}: amount mismatch"
            assert r_side != o_side, f"Line {seq}: side should be flipped"

    def test_double_reverse_blocked(
        self, session, post_via_coordinator, reversal_service,
        current_period, standard_accounts, test_actor_id,
    ):
        """K1.3: Double reversal is blocked by unique constraint.

        Once an entry is reversed, attempting a second reversal
        raises EntryAlreadyReversedError.
        """
        from finance_kernel.exceptions import EntryAlreadyReversedError

        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("200.00"),
            currency="USD",
        )
        assert result.success
        session.flush()

        entry_id = result.journal_result.entries[0].entry_id

        # First reversal succeeds
        reversal_service.reverse_in_same_period(
            original_entry_id=entry_id,
            reason="First reversal",
            actor_id=test_actor_id,
        )
        session.flush()

        # Second reversal fails
        with pytest.raises(EntryAlreadyReversedError):
            reversal_service.reverse_in_same_period(
                original_entry_id=entry_id,
                reason="Second reversal",
                actor_id=test_actor_id,
            )

    def test_reversal_in_different_period_preserves_identity(
        self, session, post_via_coordinator, reversal_service,
        current_period, standard_accounts, test_actor_id,
        ledger, create_period, deterministic_clock, period_service,
    ):
        """K1.4: Reversal in a different period still returns net to zero.

        Post in period P1, close P1, reverse into P2.
        Per-account trial balance should net to zero across both periods.
        """
        # Post in current period
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="SalesRevenue",
            amount=Decimal("500.00"),
            currency="USD",
        )
        assert result.success
        session.flush()

        entry_id = result.journal_result.entries[0].entry_id

        # Create a future period
        today = deterministic_clock.now().date()
        if today.month == 12:
            next_start = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_start = today.replace(month=today.month + 1, day=1)
        next_end = next_start + timedelta(days=28)

        create_period(
            period_code="K1-P2",
            name="K1 Test Period 2",
            start_date=next_start,
            end_date=next_end,
        )

        # Close original period
        period_service.close_period(current_period.period_code, test_actor_id)
        session.flush()

        # Reverse into the new period
        rev_result = reversal_service.reverse_in_current_period(
            original_entry_id=entry_id,
            reason="K1.4 cross-period reversal",
            actor_id=test_actor_id,
            effective_date=next_start,
        )
        session.flush()

        # Verify reversal has different effective_date
        reversal = session.get(JournalEntry, rev_result.reversal_entry_id)
        original = session.get(JournalEntry, entry_id)
        assert reversal.effective_date != original.effective_date

        # Verify original unchanged (R10)
        assert original.status == JournalEntryStatus.POSTED

        # Verify net trial balance is zero (across both periods)
        tb = _tb_hash(ledger.trial_balance())
        assert tb == {}, (
            f"Trial balance should net to zero after cross-period reversal: {tb}"
        )


# =========================================================================
# K2: Split/Merge Equivalence (TODO)
# =========================================================================


@pytest.mark.skip(reason="TODO: K2 - Implement split/merge equivalence tests")
class TestK2SplitMergeEquivalence:
    """
    K2: Split/merge equivalence.

    Proves equivalent decompositions preserve financial truth.
    """

    def test_single_entry_equals_split_entries(self):
        """
        Verify single posting equals sum of split postings.

        TODO: Implementation steps:
        1. Post single entry: debit 1000, credit 1000
        2. Post two entries: debit 600 + debit 400, credit 600 + credit 400
        3. Compare trial balances
        4. Verify identical account totals
        """
        pass

    def test_merged_entries_preserve_audit_trail(self):
        """
        Verify splitting doesn't break traceability.

        TODO: Implementation steps:
        1. Post two related entries from same source event
        2. Verify both trace back to source event
        3. Verify audit chain includes both
        """
        pass
