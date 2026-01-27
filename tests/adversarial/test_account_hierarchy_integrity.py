"""
Adversarial test: Account hierarchy integrity.

The "why" behind the normal_balance immutability invariant:
- Changing normal_balance on a parent account corrupts the financial meaning
  of ALL balances in that subtree
- A $10,000 debit balance in an ASSET account (normal_balance=DEBIT) means
  the company OWNS $10,000
- If you change normal_balance to CREDIT, that same $10,000 debit balance
  now means the company OWES $10,000 - a $20,000 swing in reported position

This test creates an account tree with balances and attempts to change
the root's normal_balance. If this succeeds, financial reports are corrupted.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from finance_kernel.models.account import Account, AccountType, NormalBalance, AccountTag
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import ImmutabilityViolationError


def _register_strategy(event_type: str, debit_code: str, credit_code: str, amount: Decimal) -> None:
    """Register a strategy that posts to specific accounts."""

    class TestStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str, dr_code: str, cr_code: str, amt: Decimal):
            self._event_type = evt_type
            self._debit_code = dr_code
            self._credit_code = cr_code
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
                    account_code=self._debit_code,
                    side=DomainLineSide.DEBIT,
                    money=Money.of(self._amount, "USD"),
                ),
                LineSpec(
                    account_code=self._credit_code,
                    side=DomainLineSide.CREDIT,
                    money=Money.of(self._amount, "USD"),
                ),
            )

    StrategyRegistry.register(TestStrategy(event_type, debit_code, credit_code, amount))


class TestAccountHierarchyIntegrity:
    """
    Test that changing normal_balance on a parent account is blocked
    when children have balances, even if the parent has no direct journal lines.

    The financial integrity at stake:
    - Parent: "Fixed Assets" (normal_balance=DEBIT, meaning positive = owned)
    - Child: "Equipment" with $50,000 debit balance
    - Child: "Vehicles" with $30,000 debit balance
    - Total: $80,000 in assets OWNED by the company

    If parent's normal_balance changes to CREDIT:
    - Same $80,000 debit balance now means $80,000 OWED
    - Financial statements swing by $160,000 (from +$80k to -$80k)
    - This is a material misstatement that would fail any audit
    """

    @pytest.fixture
    def account_tree_with_balances(
        self,
        session,
        posting_orchestrator: PostingOrchestrator,
        current_period,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Create an account tree with posted balances.

        Structure:
            Fixed Assets (1100) - PARENT, no direct journal lines
            ├── Equipment (1110) - $50,000 debit balance
            └── Vehicles (1120) - $30,000 debit balance

        Revenue account for balanced entries:
            Revenue (4000) - credit side of entries
        """
        # Create parent account (no direct journal lines)
        parent = Account(
            code="1100",
            name="Fixed Assets",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(parent)
        session.flush()

        # Create child accounts
        equipment = Account(
            code="1110",
            name="Equipment",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            parent_id=parent.id,
            currency="USD",
            created_by_id=test_actor_id,
        )
        vehicles = Account(
            code="1120",
            name="Vehicles",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            parent_id=parent.id,
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(equipment)
        session.add(vehicles)
        session.flush()

        # Create revenue account for balanced entries
        revenue = Account(
            code="4000",
            name="Revenue",
            account_type=AccountType.REVENUE,
            normal_balance=NormalBalance.CREDIT,
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(revenue)
        session.flush()

        # Post $50,000 to Equipment
        event_type1 = "test.equipment.purchase"
        _register_strategy(event_type1, "1110", "4000", Decimal("50000.00"))

        try:
            result1 = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type1,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result1.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type1, None)

        # Post $30,000 to Vehicles
        event_type2 = "test.vehicle.purchase"
        _register_strategy(event_type2, "1120", "4000", Decimal("30000.00"))

        try:
            result2 = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type2,
                occurred_at=deterministic_clock.now(),
                effective_date=deterministic_clock.now().date(),
                actor_id=test_actor_id,
                producer="test",
                payload={},
            )
            assert result2.status == PostingStatus.POSTED
        finally:
            StrategyRegistry._strategies.pop(event_type2, None)

        session.flush()

        return {
            "parent": parent,
            "equipment": equipment,
            "vehicles": vehicles,
            "revenue": revenue,
        }

    def test_change_parent_normal_balance_corrupts_financial_reports(
        self,
        session,
        account_tree_with_balances,
    ):
        """
        CRITICAL TEST: Changing parent's normal_balance corrupts all child balances.

        Before change:
        - Equipment: $50,000 DR (normal=DEBIT) → Asset of $50,000
        - Vehicles: $30,000 DR (normal=DEBIT) → Asset of $30,000
        - Total Fixed Assets: $80,000 OWNED

        If parent normal_balance changes to CREDIT:
        - Same balances now mean LIABILITIES
        - Equipment: $50,000 DR (normal=CREDIT) → Liability of $50,000
        - Vehicles: $30,000 DR (normal=CREDIT) → Liability of $30,000
        - Total: $80,000 OWED (not owned!)

        This is a $160,000 swing in reported financial position.
        """
        parent = account_tree_with_balances["parent"]

        # Verify parent has no direct journal lines
        from sqlalchemy import text
        direct_lines = session.execute(
            text("SELECT COUNT(*) FROM journal_lines WHERE account_id = :id"),
            {"id": str(parent.id)},
        ).scalar()
        assert direct_lines == 0, "Parent should have no direct journal lines"

        # Verify children have balances
        equipment_balance = session.execute(
            text("""
                SELECT COALESCE(SUM(CASE WHEN side = 'debit' THEN amount ELSE -amount END), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON jl.journal_entry_id = je.id
                WHERE jl.account_id = :id AND je.status = 'posted'
            """),
            {"id": str(account_tree_with_balances["equipment"].id)},
        ).scalar()
        assert equipment_balance == Decimal("50000.00"), "Equipment should have $50,000 balance"

        # NOW THE ATTACK: Try to change parent's normal_balance
        # This should be BLOCKED because it would corrupt the meaning of child balances
        parent.normal_balance = NormalBalance.CREDIT

        # If this flush succeeds, financial reports are corrupted
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "normal_balance" in error_msg or "structural" in error_msg or "account" in error_msg

        # Clean up
        session.rollback()

    def test_change_parent_account_type_corrupts_financial_classification(
        self,
        session,
        account_tree_with_balances,
    ):
        """
        Changing parent's account_type also corrupts financial reports.

        Before: account_type=ASSET → appears on Balance Sheet as Asset
        After: account_type=EXPENSE → appears on Income Statement as Expense

        $80,000 of equipment would suddenly appear as $80,000 expense,
        destroying both the Balance Sheet AND Income Statement accuracy.
        """
        parent = account_tree_with_balances["parent"]

        # Try to change parent's account_type
        parent.account_type = AccountType.EXPENSE

        # If this succeeds, financial classification is corrupted
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "account_type" in error_msg or "structural" in error_msg or "account" in error_msg

        # Clean up
        session.rollback()

    def test_direct_child_reference_blocks_parent_change(
        self,
        session,
        account_tree_with_balances,
    ):
        """
        Even though parent has no direct lines, children's references
        should protect the parent's structural integrity.

        The invariant should be:
        "Structural fields are immutable if ANY descendant has posted journal lines"

        Not just:
        "Structural fields are immutable if THIS account has posted journal lines"
        """
        parent = account_tree_with_balances["parent"]
        equipment = account_tree_with_balances["equipment"]

        # Verify equipment has journal lines
        from sqlalchemy import text
        equipment_lines = session.execute(
            text("""
                SELECT COUNT(*) FROM journal_lines jl
                JOIN journal_entries je ON jl.journal_entry_id = je.id
                WHERE jl.account_id = :id AND je.status = 'posted'
            """),
            {"id": str(equipment.id)},
        ).scalar()
        assert equipment_lines > 0, "Equipment should have posted journal lines"

        # Parent has no direct lines
        parent_lines = session.execute(
            text("""
                SELECT COUNT(*) FROM journal_lines jl
                JOIN journal_entries je ON jl.journal_entry_id = je.id
                WHERE jl.account_id = :id AND je.status = 'posted'
            """),
            {"id": str(parent.id)},
        ).scalar()
        assert parent_lines == 0, "Parent should have no direct journal lines"

        # The attack: change parent's structural field
        parent.normal_balance = NormalBalance.CREDIT

        # THIS IS THE KEY TEST:
        # If this succeeds, we're only checking direct references, not hierarchy
        try:
            session.flush()
            # If we get here, the invariant is broken
            pytest.fail(
                "INVARIANT BROKEN: Parent's normal_balance was changed even though "
                "children have posted balances. This corrupts $80,000 of financial data. "
                "The check only looks at direct journal lines, not descendant accounts."
            )
        except ImmutabilityViolationError:
            # Good - properly protected
            session.rollback()


class TestFinancialReportCorruption:
    """
    Demonstrate the actual financial damage from changing normal_balance.
    """

    def test_quantify_financial_impact_of_normal_balance_change(
        self,
        session,
        test_actor_id,
    ):
        """
        Quantify the financial statement impact of a normal_balance change.

        This test documents WHY the invariant matters in dollar terms.
        """
        # Setup: Create a simple account with balance
        account = Account(
            code="DEMO-ASSET",
            name="Demo Asset Account",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(account)
        session.flush()

        # Simulate a $100,000 debit balance
        balance_amount = Decimal("100000.00")
        balance_side = "debit"

        # Calculate reported value BEFORE change
        # For DEBIT normal_balance: debit balance = positive asset
        if account.normal_balance == NormalBalance.DEBIT:
            reported_before = balance_amount if balance_side == "debit" else -balance_amount
        else:
            reported_before = -balance_amount if balance_side == "debit" else balance_amount

        # What would happen if normal_balance changed to CREDIT?
        # For CREDIT normal_balance: debit balance = NEGATIVE (liability)
        hypothetical_normal = NormalBalance.CREDIT
        if hypothetical_normal == NormalBalance.DEBIT:
            reported_after = balance_amount if balance_side == "debit" else -balance_amount
        else:
            reported_after = -balance_amount if balance_side == "debit" else balance_amount

        # Calculate the swing
        swing = reported_before - reported_after

        # Document the impact
        print(f"\n{'='*60}")
        print("FINANCIAL IMPACT OF NORMAL_BALANCE CHANGE")
        print(f"{'='*60}")
        print(f"Account: {account.name}")
        print(f"Balance: ${balance_amount:,.2f} {balance_side.upper()}")
        print(f"")
        print(f"BEFORE (normal_balance=DEBIT):")
        print(f"  Reported as: ${reported_before:,.2f} ASSET")
        print(f"")
        print(f"AFTER (normal_balance=CREDIT):")
        print(f"  Reported as: ${reported_after:,.2f} (LIABILITY)")
        print(f"")
        print(f"TOTAL SWING: ${abs(swing):,.2f}")
        print(f"{'='*60}")

        # Assert the swing is material
        assert swing == Decimal("200000.00"), (
            f"Normal balance change creates ${swing:,.2f} swing in reported position"
        )

        # This is why the invariant exists
        assert swing > 0, "Changing normal_balance ALWAYS corrupts financial reports"
