"""
Adversarial test: Attempt to delete all ROUNDING accounts.

The Account model docstring states:
"At least one rounding account must exist per currency or ledger"

This test verifies that:
1. Rounding accounts with posted journal lines cannot be deleted (existing enforcement)
2. The LAST rounding account cannot be deleted even without references (invariant)

If the last db.delete() succeeds, the invariant is broken.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.exceptions import ImmutabilityViolationError, AccountReferencedError
from sqlalchemy.exc import IntegrityError


class TestDeleteAllRoundingAccounts:
    """
    Adversarial test: Try to delete ALL rounding accounts.

    This tests defense-in-depth against malicious or accidental deletion
    of critical system accounts.
    """

    def test_cannot_delete_rounding_account_with_posted_references(
        self,
        session,
        post_via_coordinator,
        standard_accounts,
        current_period,
    ):
        """
        Test that rounding accounts with posted journal lines cannot be deleted.

        This should be blocked by the existing Account immutability enforcement.
        Posts using the RoundingExpense role which maps to the
        rounding account (code 9999).
        """
        rounding_account = standard_accounts["rounding"]

        # Post a journal entry that uses the rounding account
        result = post_via_coordinator(
            debit_role="CashAsset",
            credit_role="RoundingExpense",
            amount=Decimal("100.00"),
        )
        assert result.success
        session.flush()

        # Now try to delete the rounding account - should fail
        # Use nested transaction so failure doesn't break the session
        with pytest.raises((IntegrityError, ImmutabilityViolationError, AccountReferencedError)) as exc_info:
            with session.begin_nested():
                session.delete(rounding_account)
                session.flush()

        # Should be blocked by FK constraint, immutability, or referenced check
        error_msg = str(exc_info.value).lower()
        assert (
            "foreign key" in error_msg
            or "constraint" in error_msg
            or "violates" in error_msg
            or "immutability" in error_msg
            or "referenced" in error_msg
            or "cannot be deleted" in error_msg
            or "rounding" in error_msg
        ), f"Expected deletion protection error, got: {exc_info.value}"

    def test_attempt_delete_all_rounding_accounts_bulk(
        self,
        session,
        create_account,
        test_actor_id,
    ):
        """
        Adversarial: Attempt to delete ALL accounts tagged as ROUNDING via bulk query.

        Expected behavior with enforcement:
        - Can delete rounding accounts if another exists for same currency
        - Cannot delete the LAST rounding account for any currency
        """
        from sqlalchemy import text

        # Create multiple rounding accounts for different currencies
        rounding1 = create_account(
            code="ROUND-USD-1",
            name="USD Rounding 1",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
            currency="USD",
        )
        rounding2 = create_account(
            code="ROUND-USD-2",
            name="USD Rounding 2",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
            currency="USD",
        )
        rounding3 = create_account(
            code="ROUND-EUR",
            name="EUR Rounding",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
            currency="EUR",
        )
        session.flush()

        # Count rounding accounts by currency before
        usd_before = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%' AND currency = 'USD'")
        ).scalar()
        eur_before = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%' AND currency = 'EUR'")
        ).scalar()

        assert usd_before == 2, "Should have 2 USD rounding accounts"
        assert eur_before == 1, "Should have 1 EUR rounding account"

        # Attempt to delete the first USD rounding account - should SUCCEED
        # (because rounding2 still exists for USD)
        session.delete(rounding1)
        session.flush()

        usd_after_first = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%' AND currency = 'USD'")
        ).scalar()
        assert usd_after_first == 1, "Should have 1 USD rounding account after first deletion"

        # Attempt to delete the second (now last) USD rounding account - should FAIL
        # Use a savepoint so we can continue testing
        with pytest.raises((ImmutabilityViolationError, IntegrityError)) as exc_info:
            with session.begin_nested():
                session.delete(rounding2)
                session.flush()

        assert "rounding" in str(exc_info.value).lower(), f"Expected rounding error, got: {exc_info.value}"

        # Re-fetch rounding2 since it might have been expunged
        session.expire_all()

        # Attempt to delete the only EUR rounding account - should also FAIL
        with pytest.raises((ImmutabilityViolationError, IntegrityError)) as exc_info:
            with session.begin_nested():
                session.delete(rounding3)
                session.flush()

        assert "rounding" in str(exc_info.value).lower(), f"Expected rounding error, got: {exc_info.value}"

        # Verify at least one rounding account remains per currency
        usd_remaining = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%' AND currency = 'USD'")
        ).scalar()
        eur_remaining = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%' AND currency = 'EUR'")
        ).scalar()

        assert usd_remaining >= 1, "At least 1 USD rounding account must remain"
        assert eur_remaining >= 1, "At least 1 EUR rounding account must remain"

    def test_last_rounding_account_cannot_be_deleted(
        self,
        session,
        test_actor_id,
    ):
        """
        Explicit test: The LAST rounding account for a currency cannot be deleted.

        This tests the specific invariant that at least one must exist.
        """
        from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
        from sqlalchemy import text

        single_rounding = Account(
            code="ONLY-ROUNDING-JPY",
            name="Only JPY Rounding Account",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
            currency="JPY",
            created_by_id=test_actor_id,
        )
        session.add(single_rounding)
        session.flush()

        # Verify it's the only JPY rounding account
        jpy_rounding_count = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%' AND currency = 'JPY'")
        ).scalar()
        assert jpy_rounding_count == 1, "Should have exactly 1 JPY rounding account"

        # Attempt to delete the last rounding account for JPY
        with pytest.raises((ImmutabilityViolationError, IntegrityError)) as exc_info:
            with session.begin_nested():
                session.delete(single_rounding)
                session.flush()

        # Verify the error message is appropriate
        error_str = str(exc_info.value).lower()
        assert "rounding" in error_str, f"Expected rounding error, got: {exc_info.value}"
        assert (
            "jpy" in error_str or "last" in error_str or "currency" in error_str
        ), f"Expected currency or 'last' in error, got: {exc_info.value}"

    def test_raw_sql_delete_all_rounding_accounts(
        self,
        session,
        create_account,
        test_actor_id,
    ):
        """
        Adversarial: Attempt to delete ALL rounding accounts via raw SQL.

        This bypasses ORM listeners - only DB triggers can stop this.
        """
        from sqlalchemy import text

        # Create a rounding account
        rounding = create_account(
            code="ROUND-RAW-TEST",
            name="Raw SQL Test Rounding",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            tags=[AccountTag.ROUNDING.value],
        )
        session.flush()

        # Count rounding accounts before
        before_count = session.execute(
            text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%'")
        ).scalar()

        assert before_count >= 1, "Should have at least 1 rounding account"

        # Attempt raw SQL deletion of ALL rounding accounts
        try:
            session.execute(
                text("DELETE FROM accounts WHERE tags::text LIKE '%rounding%'")
            )
            session.flush()

            # Check how many remain
            after_count = session.execute(
                text("SELECT COUNT(*) FROM accounts WHERE tags::text LIKE '%rounding%'")
            ).scalar()

            if after_count == 0:
                pytest.fail(
                    f"INVARIANT BROKEN: Raw SQL deleted all {before_count} rounding accounts! "
                    "Defense-in-depth failed - no DB trigger protection for rounding accounts."
                )

        except Exception as e:
            # Good - some protection kicked in
            session.rollback()
