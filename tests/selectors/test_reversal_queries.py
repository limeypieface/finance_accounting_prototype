"""
Reversal selector and query consistency tests.

Verifies:
- is_reversed derivation: original entry shows is_reversed after reversal exists.
- is_reversal derivation: reversal entry has is_reversal True.
- Trial balance correctness with reversals: after reversing an entry, trial
  balance nets to zero for the affected accounts (R6: computed from journal).
"""

from decimal import Decimal

import pytest

from finance_kernel.models.journal import JournalEntry
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.reversal_service import ReversalService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def link_graph(session):
    """Provide LinkGraphService for reversal linkage."""
    return LinkGraphService(session)


@pytest.fixture
def reversal_service(
    session,
    journal_writer,
    auditor_service,
    link_graph,
    period_service,
    deterministic_clock,
):
    """Provide ReversalService (uses same journal_writer/accounts as post_via_coordinator)."""
    return ReversalService(
        session=session,
        journal_writer=journal_writer,
        auditor=auditor_service,
        link_graph=link_graph,
        period_service=period_service,
        clock=deterministic_clock,
    )


@pytest.fixture
def posted_entry_for_selector(
    session,
    post_via_coordinator,
    current_period,
    standard_accounts,
):
    """Create a posted journal entry (Cash debit / Revenue credit) for selector tests."""
    result = post_via_coordinator(
        debit_role="CashAsset",
        credit_role="SalesRevenue",
        amount=Decimal("500.00"),
        currency="USD",
    )
    assert result.success
    session.flush()
    entry_id = result.journal_result.entries[0].entry_id
    entry = session.get(JournalEntry, entry_id)
    assert entry is not None
    assert entry.is_posted
    return entry


# ---------------------------------------------------------------------------
# is_reversed / is_reversal derivation
# ---------------------------------------------------------------------------


class TestReversalDerivation:
    """Verify JournalEntry is_reversed and is_reversal reflect reversal linkage."""

    def test_original_entry_is_reversed_after_reversal(
        self,
        session,
        reversal_service,
        posted_entry_for_selector,
        test_actor_id,
    ):
        """After a reversal exists, the original entry reports is_reversed True."""
        original = posted_entry_for_selector
        assert original.is_reversed is False
        assert original.is_reversal is False

        reversal_service.reverse_in_same_period(
            original_entry_id=original.id,
            reason="Selector test",
            actor_id=test_actor_id,
        )
        session.flush()

        # Reload to populate reversed_by (lazy relationship)
        session.refresh(original)
        assert original.is_reversed is True
        assert original.is_reversal is False

    def test_reversal_entry_has_is_reversal_true(
        self,
        session,
        reversal_service,
        posted_entry_for_selector,
        test_actor_id,
    ):
        """The reversal entry has is_reversal True and reversal_of_id set."""
        original = posted_entry_for_selector
        rev_result = reversal_service.reverse_in_same_period(
            original_entry_id=original.id,
            reason="Selector test",
            actor_id=test_actor_id,
        )
        session.flush()

        reversal_entry = session.get(JournalEntry, rev_result.reversal_entry_id)
        assert reversal_entry is not None
        assert reversal_entry.is_reversal is True
        assert reversal_entry.reversal_of_id == original.id
        assert reversal_entry.is_reversed is False


# ---------------------------------------------------------------------------
# Trial balance with reversals
# ---------------------------------------------------------------------------


class TestTrialBalanceWithReversals:
    """Verify trial balance (R6) correctly includes reversal lines and nets."""

    def test_trial_balance_nets_to_zero_after_reversal(
        self,
        session,
        ledger_selector: LedgerSelector,
        reversal_service,
        posted_entry_for_selector,
        test_actor_id,
        deterministic_clock,
    ):
        """After posting then reversing, trial balance for affected accounts nets to zero."""
        effective_date = deterministic_clock.now().date()
        original = posted_entry_for_selector

        # Trial balance after single entry: Cash 500 debit, Revenue 500 credit
        tb_before = ledger_selector.trial_balance(as_of_date=effective_date)
        cash_rows = [r for r in tb_before if r.account_code == "1000"]
        revenue_rows = [r for r in tb_before if r.account_code == "4000"]
        assert len(cash_rows) == 1
        assert len(revenue_rows) == 1
        assert cash_rows[0].debit_total == Decimal("500.00")
        assert cash_rows[0].credit_total == Decimal("0")
        assert revenue_rows[0].credit_total == Decimal("500.00")
        assert revenue_rows[0].debit_total == Decimal("0")

        # Reverse
        reversal_service.reverse_in_same_period(
            original_entry_id=original.id,
            reason="TB test",
            actor_id=test_actor_id,
        )
        session.flush()

        # Trial balance after reversal: same accounts, debits == credits (net zero)
        tb_after = ledger_selector.trial_balance(as_of_date=effective_date)
        cash_after = [r for r in tb_after if r.account_code == "1000"]
        revenue_after = [r for r in tb_after if r.account_code == "4000"]
        assert len(cash_after) == 1
        assert len(revenue_after) == 1
        assert cash_after[0].debit_total == cash_after[0].credit_total
        assert revenue_after[0].debit_total == revenue_after[0].credit_total
