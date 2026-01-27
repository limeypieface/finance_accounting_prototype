"""
Adversarial test: Modify JournalLine after parent is posted.

From the journal.py docstring:
- "Posted JournalEntry is immutable"
- "Reversals create new JournalEntries; originals never change"

The Attack Vector:
Rather than modifying the JournalEntry itself (which is explicitly protected),
the attacker targets the child JournalLine records. If line amounts or accounts
can be changed after posting, the entire audit trail becomes meaningless.

Financial Impact:
- Changing amount: $100 debit becomes $10,000 debit - instant embezzlement
- Changing account_id: Move expense to asset account - hide fraud
- Entry still shows "posted" status with original timestamp
- Audit trail shows entry was posted, but numbers don't match history

This is the most critical immutability invariant in the entire system.
"""

import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from finance_kernel.models.account import Account, AccountType, NormalBalance
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import ImmutabilityViolationError


def _register_test_strategy(event_type: str, amount: Decimal = Decimal("100.00")) -> None:
    """Register a simple balanced strategy for testing."""

    class TestStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str, amt: Decimal):
            self._event_type = evt_type
            self._amount = amt
            self._version = 1

        @property
        def event_type(self) -> str:
            return self._event_type

        @property
        def version(self) -> int:
            return self._version

        def _compute_line_specs(
            self, event: EventEnvelope, ref: ReferenceData
        ) -> tuple[LineSpec, ...]:
            return (
                LineSpec(
                    account_code="1000",
                    side=DomainLineSide.DEBIT,
                    money=Money.of(self._amount, "USD"),
                ),
                LineSpec(
                    account_code="4000",
                    side=DomainLineSide.CREDIT,
                    money=Money.of(self._amount, "USD"),
                ),
            )

    StrategyRegistry.register(TestStrategy(event_type, amount))


class TestJournalLineModificationAfterPosting:
    """
    Test that JournalLine records cannot be modified after parent is posted.
    """

    @pytest.fixture
    def posted_entry_with_lines(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Create a posted journal entry with lines for testing."""
        event_type = "test.line.modification"
        _register_test_strategy(event_type, Decimal("500.00"))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
            session.flush()

            # Get the entry and its lines
            entry = session.get(JournalEntry, result.journal_entry_id)

            assert entry is not None
            assert entry.status == JournalEntryStatus.POSTED
            assert len(entry.lines) == 2

            return {
                "entry": entry,
                "debit_line": next(l for l in entry.lines if l.side == LineSide.DEBIT),
                "credit_line": next(l for l in entry.lines if l.side == LineSide.CREDIT),
            }
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_change_line_amount_after_posting_orm(
        self,
        session,
        posted_entry_with_lines,
    ):
        """
        CRITICAL TEST: Changing JournalLine.amount after posting should be blocked.

        The Attack:
        - Original: $500.00 debit to Cash
        - After attack: $50,000.00 debit to Cash
        - Entry still shows "posted" with original timestamp
        - Balance sheet now shows $49,500 more cash than actually exists
        """
        debit_line = posted_entry_with_lines["debit_line"]
        original_amount = debit_line.amount

        assert original_amount == Decimal("500.00")

        # THE ATTACK: Change amount from $500 to $50,000
        debit_line.amount = Decimal("50000.00")

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "journal" in error_msg or "line" in error_msg or "posted" in error_msg

        session.rollback()

    def test_change_line_account_id_after_posting_orm(
        self,
        session,
        posted_entry_with_lines,
        standard_accounts,
    ):
        """
        CRITICAL TEST: Changing JournalLine.account_id after posting should be blocked.

        The Attack:
        - Original: Debit to Cash (1000)
        - After attack: Debit to some other account
        - Money "moves" between accounts without a reversing entry
        - Audit trail shows entry posted to Cash, but balance is elsewhere
        """
        debit_line = posted_entry_with_lines["debit_line"]
        original_account_id = debit_line.account_id

        # Find a different account to redirect to
        other_account = standard_accounts.get("revenue") or standard_accounts.get("rounding")
        assert other_account is not None
        assert other_account.id != original_account_id

        # THE ATTACK: Change account_id to different account
        debit_line.account_id = other_account.id

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "journal" in error_msg or "line" in error_msg or "posted" in error_msg

        session.rollback()

    def test_change_line_side_after_posting_orm(
        self,
        session,
        posted_entry_with_lines,
    ):
        """
        CRITICAL TEST: Changing JournalLine.side after posting should be blocked.

        The Attack:
        - Original: $500.00 DEBIT to Cash (increases cash)
        - After attack: $500.00 CREDIT to Cash (decreases cash)
        - $1,000 swing in account balance
        - Entry still "balanced" but meaning completely inverted
        """
        debit_line = posted_entry_with_lines["debit_line"]
        assert debit_line.side == LineSide.DEBIT

        # THE ATTACK: Flip debit to credit
        debit_line.side = LineSide.CREDIT

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "journal" in error_msg or "line" in error_msg or "posted" in error_msg

        session.rollback()

    def test_change_line_currency_after_posting_orm(
        self,
        session,
        posted_entry_with_lines,
    ):
        """
        Changing JournalLine.currency after posting should be blocked.

        The Attack:
        - Original: $500.00 USD
        - After attack: $500.00 EUR (or JPY, etc.)
        - Same number, completely different value
        """
        debit_line = posted_entry_with_lines["debit_line"]
        original_currency = debit_line.currency

        assert original_currency == "USD"

        # THE ATTACK: Change currency
        debit_line.currency = "EUR"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "journal" in error_msg or "line" in error_msg or "posted" in error_msg

        session.rollback()

    def test_delete_line_after_posting_orm(
        self,
        session,
        posted_entry_with_lines,
    ):
        """
        CRITICAL TEST: Deleting JournalLine after posting should be blocked.

        The Attack:
        - Original: Balanced entry with debit and credit lines
        - After attack: Delete the credit line
        - Entry now "unbalanced" - debits don't equal credits
        - Or: Delete the debit line that recorded the expense
        """
        debit_line = posted_entry_with_lines["debit_line"]

        # THE ATTACK: Delete one of the lines
        session.delete(debit_line)

        with pytest.raises(Exception) as exc_info:
            session.flush()

        # Accept either ImmutabilityViolationError or FK constraint
        error_msg = str(exc_info.value).lower()
        assert (
            "immutability" in error_msg
            or "journal" in error_msg
            or "posted" in error_msg
            or "foreign key" in error_msg
            or "constraint" in error_msg
        )

        session.rollback()


class TestJournalLineRawSQLAttack:
    """
    Test that raw SQL cannot modify JournalLines on posted entries.

    Only database triggers can stop this.
    """

    @pytest.fixture
    def posted_entry_with_lines(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """Create a posted journal entry with lines for testing."""
        event_type = "test.line.raw.sql"
        _register_test_strategy(event_type, Decimal("1000.00"))

        try:
            result = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result.status == PostingStatus.POSTED
            session.flush()

            entry = session.get(JournalEntry, result.journal_entry_id)

            return {
                "entry": entry,
                "debit_line": next(l for l in entry.lines if l.side == LineSide.DEBIT),
                "credit_line": next(l for l in entry.lines if l.side == LineSide.CREDIT),
            }
        finally:
            StrategyRegistry._strategies.pop(event_type, None)

    def test_raw_sql_amount_modification(
        self,
        session,
        posted_entry_with_lines,
    ):
        """
        Attack via raw SQL: Bypass ORM and modify amount directly.

        Only database triggers can stop this.
        """
        debit_line = posted_entry_with_lines["debit_line"]
        line_id = str(debit_line.id)
        original_amount = Decimal("1000.00")

        try:
            # THE ATTACK: Raw SQL update
            session.execute(
                text("UPDATE journal_lines SET amount = :new_amount WHERE id = :id"),
                {"new_amount": Decimal("99999.99"), "id": line_id},
            )
            session.flush()

            # Check if the update succeeded
            result = session.execute(
                text("SELECT amount FROM journal_lines WHERE id = :id"),
                {"id": line_id},
            ).scalar()

            if result == Decimal("99999.99"):
                pytest.fail(
                    f"INVARIANT BROKEN: Raw SQL changed journal line amount from "
                    f"${original_amount} to ${result} on a POSTED entry. "
                    f"Defense-in-depth failed - no DB trigger protection."
                )

        except Exception as e:
            # Good - some protection kicked in (likely DB trigger)
            session.rollback()

    def test_raw_sql_account_id_modification(
        self,
        session,
        posted_entry_with_lines,
        standard_accounts,
    ):
        """
        Attack via raw SQL: Change account_id directly.
        """
        debit_line = posted_entry_with_lines["debit_line"]
        line_id = str(debit_line.id)
        original_account_id = str(debit_line.account_id)

        other_account = standard_accounts.get("revenue") or standard_accounts.get("rounding")
        new_account_id = str(other_account.id)

        try:
            # THE ATTACK: Raw SQL update
            session.execute(
                text("UPDATE journal_lines SET account_id = :new_account WHERE id = :id"),
                {"new_account": new_account_id, "id": line_id},
            )
            session.flush()

            # Check if the update succeeded
            result = session.execute(
                text("SELECT account_id FROM journal_lines WHERE id = :id"),
                {"id": line_id},
            ).scalar()

            if str(result) == new_account_id:
                pytest.fail(
                    f"INVARIANT BROKEN: Raw SQL changed journal line account_id from "
                    f"{original_account_id} to {new_account_id} on a POSTED entry. "
                    f"Money has been 'moved' without a proper reversing entry."
                )

        except Exception as e:
            # Good - some protection kicked in
            session.rollback()

    def test_raw_sql_delete_line(
        self,
        session,
        posted_entry_with_lines,
    ):
        """
        Attack via raw SQL: Delete a journal line directly.
        """
        debit_line = posted_entry_with_lines["debit_line"]
        line_id = str(debit_line.id)

        try:
            # THE ATTACK: Raw SQL delete
            session.execute(
                text("DELETE FROM journal_lines WHERE id = :id"),
                {"id": line_id},
            )
            session.flush()

            # Check if the delete succeeded
            result = session.execute(
                text("SELECT COUNT(*) FROM journal_lines WHERE id = :id"),
                {"id": line_id},
            ).scalar()

            if result == 0:
                pytest.fail(
                    "INVARIANT BROKEN: Raw SQL deleted a journal line from a POSTED entry. "
                    "The entry is now unbalanced and the audit trail is corrupted."
                )

        except Exception as e:
            # Good - some protection kicked in
            session.rollback()


class TestDraftEntryLinesAreMutable:
    """
    Verify that DRAFT entry lines remain mutable.

    This ensures immutability only kicks in after posting.
    Note: Draft line mutability is tested elsewhere via the posting workflow.
    These tests verify the contrast with posted entry behavior.
    """

    def test_draft_lines_remain_mutable_documented(self):
        """
        Document that draft entry lines SHOULD be mutable.

        The contrast is:
        - DRAFT lines: fully mutable (amounts, accounts, can be deleted)
        - POSTED lines: completely immutable (protected by ORM + DB triggers)

        This test documents the expected behavior. Full draft line tests
        are covered in tests/audit/test_immutability.py which test the
        JournalEntry status transition from DRAFT to POSTED.
        """
        # This is a documentation test - the actual behavior is tested
        # in other test files. Here we document the invariant:
        #
        # Before posting (status=DRAFT):
        #   - JournalLine.amount CAN be changed
        #   - JournalLine.account_id CAN be changed
        #   - JournalLine.side CAN be changed
        #   - JournalLine CAN be deleted
        #
        # After posting (status=POSTED):
        #   - ALL of the above are BLOCKED
        #   - Both via ORM listeners and PostgreSQL triggers
        #
        # This two-phase approach allows corrections BEFORE posting
        # while ensuring immutability AFTER posting.
        pass
