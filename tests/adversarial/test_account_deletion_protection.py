"""
R3: Account Deletion Protection Tests.

Accounts that have been used in journal entries cannot be deleted.
This is critical for audit trail integrity - deleting an account would
make historical entries uninterpretable.

These tests verify that:
1. Unused accounts CAN be deleted
2. Used accounts CANNOT be deleted
3. Deactivated accounts with postings cannot be deleted
4. Error messages are clear and actionable
"""

import pytest
from uuid import uuid4
from decimal import Decimal

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.models.account import Account, AccountType, NormalBalance
from finance_kernel.models.journal import JournalEntry, JournalLine
from finance_kernel.exceptions import AccountReferencedError
from finance_kernel.domain.clock import DeterministicClock


class TestAccountDeletionProtection:
    """
    R3 Compliance Tests: Used accounts cannot be deleted.
    """

    def test_unused_account_can_be_deleted(
        self,
        session,
        create_account,
        test_actor_id,
    ):
        """
        Verify that unused accounts CAN be deleted.

        An account with no journal lines should be deletable.
        """
        # Create an account that won't be used
        unused_account = create_account(
            code="UNUSED-001",
            name="Unused Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
        account_id = unused_account.id
        session.flush()

        # Verify no lines reference this account
        lines = session.execute(
            select(JournalLine).where(JournalLine.account_id == account_id)
        ).scalars().all()
        assert len(lines) == 0

        # Delete should succeed
        session.delete(unused_account)
        session.flush()

        # Verify deletion
        deleted = session.get(Account, account_id)
        assert deleted is None, "Unused account should be deleted"

    def test_used_account_cannot_be_deleted(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that used accounts CANNOT be deleted.

        An account with journal lines must not be deletable.
        """
        # Post an entry using the cash account
        cash_account = standard_accounts["cash"]
        account_id = cash_account.id

        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Verify account has journal lines
        lines = session.execute(
            select(JournalLine).where(JournalLine.account_id == account_id)
        ).scalars().all()
        assert len(lines) > 0, "Account should have journal lines"

        # Attempt to delete should fail
        # Use nested transaction (SAVEPOINT) so rollback only affects the delete attempt
        with pytest.raises((IntegrityError, AccountReferencedError)):
            with session.begin_nested():
                session.delete(cash_account)
                session.flush()

        # Re-fetch the account since the nested transaction was rolled back
        # and the ORM object might be in an inconsistent state
        session.expire_all()

        # Account should still exist
        account = session.get(Account, account_id)
        assert account is not None, "Used account should NOT be deleted"

    def test_deactivated_account_with_postings_cannot_be_deleted(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that deactivated accounts with postings cannot be deleted.

        Deactivation prevents new postings but doesn't allow deletion.
        """
        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Deactivate the account
        cash_account = standard_accounts["cash"]
        cash_account.is_active = False
        session.flush()

        # Attempt to delete should still fail
        # Use nested transaction (SAVEPOINT) so rollback only affects the delete attempt
        with pytest.raises((IntegrityError, AccountReferencedError)):
            with session.begin_nested():
                session.delete(cash_account)
                session.flush()

        # Re-fetch the account since the nested transaction was rolled back
        session.expire_all()

        # Account should still exist
        account = session.get(Account, cash_account.id)
        assert account is not None

    def test_multiple_accounts_with_postings_protected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that all accounts used in an entry are protected.
        """
        # Post entry using multiple accounts
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "1100", "side": "debit", "amount": "50.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "150.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Try to delete each account - all should fail
        for account_key in ["cash", "ar", "revenue"]:
            account = standard_accounts[account_key]
            account_id = account.id

            # Use nested transaction (SAVEPOINT) so rollback only affects this delete attempt
            try:
                with session.begin_nested():
                    session.delete(account)
                    session.flush()
                pytest.fail(f"Should not be able to delete used account {account_key}")
            except (IntegrityError, AccountReferencedError):
                pass  # Expected exception, nested transaction auto-rolled back

            # Re-fetch the account
            session.expire_all()
            reloaded = session.get(Account, account_id)
            assert reloaded is not None, f"Account {account_key} should still exist"


class TestAccountDeletionViaRawSQL:
    """
    Tests for raw SQL deletion attempts (bypassing ORM).
    """

    def test_raw_sql_delete_of_used_account_blocked(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that raw SQL cannot delete used accounts.

        The database should enforce referential integrity.
        """
        from sqlalchemy import text

        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        cash_account = standard_accounts["cash"]

        # Attempt raw SQL delete
        with pytest.raises(Exception) as exc_info:
            session.execute(
                text("DELETE FROM accounts WHERE id = :id"),
                {"id": str(cash_account.id)},
            )
            session.flush()

        # Should fail due to foreign key constraint
        error_str = str(exc_info.value).lower()
        assert (
            "foreign" in error_str or
            "constraint" in error_str or
            "violates" in error_str or
            "referenced" in error_str
        ), f"Expected foreign key error, got: {exc_info.value}"

        session.rollback()


class TestAccountDeactivationVsDeletion:
    """
    Tests to verify the difference between deactivation and deletion.
    """

    def test_account_can_be_deactivated_after_use(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that accounts can be deactivated (soft-deleted) after use.

        Deactivation is the safe alternative to deletion.
        """
        # Post an entry
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1000", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )
        assert result.status == PostingStatus.POSTED

        # Deactivate should succeed
        cash_account = standard_accounts["cash"]
        assert cash_account.is_active is True

        cash_account.is_active = False
        session.flush()

        # Verify deactivation
        reloaded = session.get(Account, cash_account.id)
        assert reloaded.is_active is False

        # But deletion should still fail
        # Use nested transaction (SAVEPOINT) so rollback only affects the delete attempt
        with pytest.raises((IntegrityError, AccountReferencedError)):
            with session.begin_nested():
                session.delete(reloaded)
                session.flush()

    def test_posting_to_deactivated_account_rejected(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock: DeterministicClock,
    ):
        """
        Verify that posting to a deactivated account is rejected.
        """
        # Deactivate the AR account
        ar_account = standard_accounts["ar"]
        ar_account.is_active = False
        session.flush()

        # Try to post using deactivated account
        result = posting_orchestrator.post_event(
            event_id=uuid4(),
            event_type="generic.posting",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            producer="test",
            payload={
                "lines": [
                    {"account_code": "1100", "side": "debit", "amount": "100.00", "currency": "USD"},
                    {"account_code": "4000", "side": "credit", "amount": "100.00", "currency": "USD"},
                ]
            },
        )

        # Should be rejected with validation failure
        assert result.status == PostingStatus.VALIDATION_FAILED
        assert result.validation is not None
        assert any(
            "inactive" in str(e.code).lower() or "inactive" in str(e.message).lower()
            for e in result.validation.errors
        ), f"Expected inactive account error, got: {result.validation.errors}"
