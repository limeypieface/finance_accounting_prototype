"""
Reversal concurrency tests.

Covers deferred items from reversal implementation:
- test_second_reversal_after_first_raises: after one successful reversal, second
  attempt raises EntryAlreadyReversedError (deterministic idempotency check).
- True concurrent test (two threads racing reversals) would require
  pg_session_factory and committed posted entry; the unique constraint on
  reversal_of_id ensures exactly one reversal wins. This file establishes
  the sequential behavior; DB constraint enforces concurrency safety.
"""

from decimal import Decimal

import pytest

from finance_kernel.exceptions import EntryAlreadyReversedError
from finance_kernel.models.journal import JournalEntry


pytestmark = pytest.mark.slow_locks


# ---------------------------------------------------------------------------
# Fixtures (same as test_reversal_service so this file can run standalone)
# ---------------------------------------------------------------------------


@pytest.fixture
def link_graph(session):
    from finance_kernel.services.link_graph_service import LinkGraphService
    return LinkGraphService(session)


@pytest.fixture
def reversal_service(session, journal_writer, auditor_service, link_graph, period_service, deterministic_clock):
    from finance_kernel.services.reversal_service import ReversalService
    return ReversalService(
        session=session,
        journal_writer=journal_writer,
        auditor=auditor_service,
        link_graph=link_graph,
        period_service=period_service,
        clock=deterministic_clock,
    )


@pytest.fixture
def posted_entry(session, post_via_coordinator, current_period, standard_accounts):
    """Create a posted journal entry for reversal testing."""
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
# Tests
# ---------------------------------------------------------------------------


class TestReversalIdempotency:
    """Second reversal on same entry must fail (unique constraint / service check)."""

    def test_second_reversal_after_first_raises(
        self,
        reversal_service,
        posted_entry,
        test_actor_id,
    ):
        """After one successful reversal, second attempt raises EntryAlreadyReversedError."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="First reversal",
            actor_id=test_actor_id,
        )
        assert result.reversal_entry_id != posted_entry.id

        with pytest.raises(EntryAlreadyReversedError):
            reversal_service.reverse_in_same_period(
                original_entry_id=posted_entry.id,
                reason="Second reversal (should fail)",
                actor_id=test_actor_id,
            )
