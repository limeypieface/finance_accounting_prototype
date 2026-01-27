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
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import ImmutabilityViolationError


def _register_rounding_strategy(event_type: str, rounding_account_code: str) -> None:
    """Register a strategy that produces an unbalanced entry requiring rounding."""

    class RoundingStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str, rounding_code: str):
            self._event_type = evt_type
            self._rounding_code = rounding_code
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
            # Create a balanced entry that uses the rounding account
            return (
                LineSpec(
                    account_code="1000",
                    side=DomainLineSide.DEBIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
                LineSpec(
                    account_code=self._rounding_code,
                    side=DomainLineSide.CREDIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
            )

    StrategyRegistry.register(RoundingStrategy(event_type, rounding_account_code))


class TestDeleteAllRoundingAccounts:
    """
    Adversarial test: Try to delete ALL rounding accounts.

    This tests defense-in-depth against malicious or accidental deletion
    of critical system accounts.
    """

    def test_cannot_delete_rounding_account_with_posted_references(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Test that rounding accounts with posted journal lines cannot be deleted.

        This should be blocked by the existing Account immutability enforcement.
        """
        rounding_account = standard_accounts["rounding"]

        # Post a journal entry that references the rounding account
        event_type = "test.rounding.reference"
        _register_rounding_strategy(event_type, rounding_account.code)

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

            # Now try to delete the rounding account - should fail
            session.delete(rounding_account)

            with pytest.raises(Exception) as exc_info:
                session.flush()

            # Should be blocked by FK constraint or immutability
            error_msg = str(exc_info.value).lower()
            assert (
                "foreign key" in error_msg
                or "constraint" in error_msg
                or "violates" in error_msg
                or "immutability" in error_msg
            )

        finally:
            StrategyRegistry._strategies.pop(event_type, None)
            session.rollback()

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
        savepoint = session.begin_nested()
        session.delete(rounding2)
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "rounding" in str(exc_info.value).lower()
        savepoint.rollback()

        # Attempt to delete the only EUR rounding account - should also FAIL
        savepoint2 = session.begin_nested()
        session.delete(rounding3)
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        assert "rounding" in str(exc_info.value).lower()
        savepoint2.rollback()

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
        # Create exactly ONE rounding account for a specific currency
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
        session.delete(single_rounding)

        # This SHOULD raise an error if the invariant is enforced
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        # Verify the error message is appropriate
        assert "rounding" in str(exc_info.value).lower()
        assert "JPY" in str(exc_info.value) or "last" in str(exc_info.value).lower()

        # Clean up session state for teardown
        session.rollback()

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
