"""
Account immutability tests based on JournalLine references.

Verifies the invariant from finance_kernel/models/account.py:
- type and normal_balance are immutable once referenced by a JournalLine
- Accounts referenced by posted lines cannot be deleted
- Non-structural fields (name, tags) can still be edited after reference

This test documents the "referenced check" behavior:
- Before first JournalLine: account is fully editable
- After first JournalLine: structural fields (account_type, normal_balance) are locked
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.exceptions import ImmutabilityViolationError


class TestAccountEditableBeforeReference:
    """Verify accounts can be fully edited before any JournalLine references them."""

    def test_account_type_can_change_before_journal_lines(
        self,
        session,
        create_account,
    ):
        """Account type can be changed when no journal lines reference the account."""
        account = create_account(
            code="TEST001",
            name="Test Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        original_id = account.id

        # Change account type - should succeed (no journal lines yet)
        account.account_type = AccountType.EXPENSE
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert refreshed.account_type == AccountType.EXPENSE

    def test_normal_balance_can_change_before_journal_lines(
        self,
        session,
        create_account,
    ):
        """Normal balance can be changed when no journal lines reference the account."""
        account = create_account(
            code="TEST002",
            name="Test Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        original_id = account.id

        # Change normal balance - should succeed (no journal lines yet)
        account.normal_balance = NormalBalance.CREDIT
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert refreshed.normal_balance == NormalBalance.CREDIT

    def test_name_can_change_before_journal_lines(
        self,
        session,
        create_account,
    ):
        """Account name can be changed when no journal lines reference the account."""
        account = create_account(
            code="TEST003",
            name="Original Name",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        original_id = account.id

        # Change name - should succeed
        account.name = "Updated Name"
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert refreshed.name == "Updated Name"

    def test_tags_can_change_before_journal_lines(
        self,
        session,
        create_account,
    ):
        """Account tags can be changed when no journal lines reference the account."""
        account = create_account(
            code="TEST004",
            name="Test Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.DIRECT.value],
        )
        original_id = account.id

        # Change tags - should succeed
        account.tags = [AccountTag.INDIRECT.value, AccountTag.BILLABLE.value]
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert AccountTag.INDIRECT.value in refreshed.tags
        assert AccountTag.BILLABLE.value in refreshed.tags

    def test_account_can_be_deleted_before_journal_lines(
        self,
        session,
        create_account,
    ):
        """Account can be deleted when no journal lines reference it."""
        account = create_account(
            code="TEST005",
            name="Deletable Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        account_id = account.id

        # Delete account - should succeed (no journal lines)
        session.delete(account)
        session.flush()

        # Verify deletion
        assert session.get(Account, account_id) is None


class TestAccountLockedAfterReference:
    """Verify structural fields are locked after JournalLine references the account."""

    @pytest.fixture
    def account_with_journal_line(
        self,
        session,
        create_account,
        create_event,
        test_actor_id,
        deterministic_clock,
        current_period,
    ):
        """Create an account that has been referenced by a posted journal entry."""
        # Create the account
        account = create_account(
            code="REF001",
            name="Referenced Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )

        # Create a second account for the balanced entry
        contra_account = create_account(
            code="REF002",
            name="Contra Account",
            account_type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT,
        )

        # Create an event (required for journal entry FK)
        event = create_event(event_type="test.account_ref")

        # Create a posted journal entry referencing the account
        entry = JournalEntry(
            source_event_id=event.event_id,
            source_event_type="test.account_ref",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.POSTED,
            idempotency_key=f"test:account_ref:{uuid4()}",
            posting_rule_version=1,
            description="Test entry for account reference",
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        # Create journal lines referencing the accounts
        debit_line = JournalLine(
            journal_entry_id=entry.id,
            account_id=account.id,
            side=LineSide.DEBIT,
            amount=Decimal("100.00"),
            currency="USD",
            line_seq=1,
            created_by_id=test_actor_id,
        )
        credit_line = JournalLine(
            journal_entry_id=entry.id,
            account_id=contra_account.id,
            side=LineSide.CREDIT,
            amount=Decimal("100.00"),
            currency="USD",
            line_seq=2,
            created_by_id=test_actor_id,
        )
        session.add(debit_line)
        session.add(credit_line)
        session.flush()

        return {
            "account": account,
            "contra_account": contra_account,
            "entry": entry,
            "debit_line": debit_line,
            "credit_line": credit_line,
        }

    def test_account_type_cannot_change_after_journal_line(
        self,
        session,
        account_with_journal_line,
    ):
        """Account type cannot be changed once referenced by a JournalLine."""
        account = account_with_journal_line["account"]

        # Attempt to change account type - should fail
        account.account_type = AccountType.EXPENSE

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "account_type" in str(exc_info.value).lower() or "Account" in str(exc_info.value)
        session.rollback()

    def test_normal_balance_cannot_change_after_journal_line(
        self,
        session,
        account_with_journal_line,
    ):
        """Normal balance cannot be changed once referenced by a JournalLine."""
        account = account_with_journal_line["account"]

        # Attempt to change normal balance - should fail
        account.normal_balance = NormalBalance.CREDIT

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "normal_balance" in str(exc_info.value).lower() or "Account" in str(exc_info.value)
        session.rollback()

    def test_name_can_still_change_after_journal_line(
        self,
        session,
        account_with_journal_line,
    ):
        """Account name CAN still be changed after JournalLine reference (non-structural)."""
        account = account_with_journal_line["account"]
        original_id = account.id

        # Change name - should succeed (non-structural field)
        account.name = "Updated Referenced Account"
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert refreshed.name == "Updated Referenced Account"

    def test_tags_can_still_change_after_journal_line(
        self,
        session,
        account_with_journal_line,
    ):
        """Account tags CAN still be changed after JournalLine reference (non-structural)."""
        account = account_with_journal_line["account"]
        original_id = account.id

        # Change tags - should succeed (non-structural field)
        account.tags = [AccountTag.BILLABLE.value]
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert AccountTag.BILLABLE.value in refreshed.tags

    def test_account_cannot_be_deleted_after_journal_line(
        self,
        session,
        account_with_journal_line,
    ):
        """Account cannot be deleted once referenced by a JournalLine."""
        account = account_with_journal_line["account"]

        # Attempt to delete account - should fail
        session.delete(account)

        # Should raise either ImmutabilityViolationError or IntegrityError (FK constraint)
        with pytest.raises(Exception) as exc_info:
            session.flush()

        # Accept either immutability violation or FK constraint violation
        error_msg = str(exc_info.value).lower()
        assert (
            "immutability" in error_msg
            or "foreign key" in error_msg
            or "violates" in error_msg
            or "constraint" in error_msg
            or "referenced" in error_msg
        )
        session.rollback()

    def test_account_code_cannot_change_after_journal_line(
        self,
        session,
        account_with_journal_line,
    ):
        """Account code cannot be changed once referenced (structural identifier)."""
        account = account_with_journal_line["account"]

        # Attempt to change account code - should fail
        account.code = "CHANGED001"

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "code" in str(exc_info.value).lower() or "Account" in str(exc_info.value)
        session.rollback()


class TestDraftEntryDoesNotLockAccount:
    """
    Verify that DRAFT journal entries do NOT lock accounts.

    Only POSTED entries should trigger the immutability constraint,
    because draft entries can still be modified or deleted.
    """

    @pytest.fixture
    def account_with_draft_entry(
        self,
        session,
        create_account,
        create_event,
        test_actor_id,
        deterministic_clock,
        current_period,
    ):
        """Create an account referenced only by a DRAFT journal entry."""
        account = create_account(
            code="DRAFT001",
            name="Draft Referenced Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )

        contra_account = create_account(
            code="DRAFT002",
            name="Draft Contra Account",
            account_type=AccountType.LIABILITY,
            normal_balance=NormalBalance.CREDIT,
        )

        event = create_event(event_type="test.draft_ref")

        # Create a DRAFT journal entry (not posted)
        entry = JournalEntry(
            source_event_id=event.event_id,
            source_event_type="test.draft_ref",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.DRAFT,  # DRAFT, not POSTED
            idempotency_key=f"test:draft_ref:{uuid4()}",
            posting_rule_version=1,
            description="Draft entry",
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        debit_line = JournalLine(
            journal_entry_id=entry.id,
            account_id=account.id,
            side=LineSide.DEBIT,
            amount=Decimal("100.00"),
            currency="USD",
            line_seq=1,
            created_by_id=test_actor_id,
        )
        credit_line = JournalLine(
            journal_entry_id=entry.id,
            account_id=contra_account.id,
            side=LineSide.CREDIT,
            amount=Decimal("100.00"),
            currency="USD",
            line_seq=2,
            created_by_id=test_actor_id,
        )
        session.add(debit_line)
        session.add(credit_line)
        session.flush()

        return {
            "account": account,
            "entry": entry,
        }

    def test_account_type_can_change_with_only_draft_reference(
        self,
        session,
        account_with_draft_entry,
    ):
        """Account type CAN be changed when only referenced by DRAFT entries."""
        account = account_with_draft_entry["account"]
        original_id = account.id

        # Change account type - should succeed (only draft reference)
        account.account_type = AccountType.EXPENSE
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert refreshed.account_type == AccountType.EXPENSE

    def test_normal_balance_can_change_with_only_draft_reference(
        self,
        session,
        account_with_draft_entry,
    ):
        """Normal balance CAN be changed when only referenced by DRAFT entries."""
        account = account_with_draft_entry["account"]
        original_id = account.id

        # Change normal balance - should succeed (only draft reference)
        account.normal_balance = NormalBalance.CREDIT
        session.flush()

        # Verify change persisted
        refreshed = session.get(Account, original_id)
        assert refreshed.normal_balance == NormalBalance.CREDIT
