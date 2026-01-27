"""
Adversarial test: Fiscal period boundary manipulation attack.

The Attack Vector:
A closed period covers a date range (e.g., Jan 1-31). By shrinking the end_date
to Jan 30, the attacker "reopens" Jan 31 without changing the status flag.

Why This Matters:
- Period status = CLOSED is the ONLY check many systems perform
- If end_date can be modified, closed periods can be "shrunk" to exclude dates
- Those excluded dates are now "open" for posting
- This bypasses all status-based audit controls

Financial Impact:
- Attacker could post fraudulent entries to dates that SHOULD be closed
- Month-end close becomes meaningless if boundaries can shift
- Audit trail shows period was "closed" but entries appear in that period
- SOX/regulatory compliance violations
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry, JournalLine, JournalEntryStatus, LineSide
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.posting_orchestrator import PostingOrchestrator, PostingStatus
from finance_kernel.domain.strategy import BasePostingStrategy
from finance_kernel.domain.strategy_registry import StrategyRegistry
from finance_kernel.domain.dtos import EventEnvelope, LineSpec, LineSide as DomainLineSide, ReferenceData
from finance_kernel.domain.values import Money
from finance_kernel.exceptions import ImmutabilityViolationError


def _register_simple_strategy(event_type: str) -> None:
    """Register a simple balanced strategy for testing."""

    class SimpleStrategy(BasePostingStrategy):
        def __init__(self, evt_type: str):
            self._event_type = evt_type
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
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
                LineSpec(
                    account_code="4000",
                    side=DomainLineSide.CREDIT,
                    money=Money.of(Decimal("100.00"), "USD"),
                ),
            )

    StrategyRegistry.register(SimpleStrategy(event_type))


class TestFiscalPeriodBoundaryAttack:
    """
    Test that closed period date boundaries cannot be modified.

    Attack scenario:
    1. Period "January 2024" covers Jan 1-31, status=CLOSED
    2. Attacker changes end_date from Jan 31 to Jan 30
    3. Jan 31 is now NOT covered by ANY closed period
    4. Attacker posts fraudulent entry dated Jan 31
    5. Entry appears to be in "January" but bypassed period close
    """

    @pytest.fixture
    def closed_january_period(self, session, period_service, test_actor_id):
        """Create a closed January period for boundary attack testing."""
        period_service.create_period(
            period_code="2024-01-BOUNDARY",
            name="January 2024 (Boundary Test)",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        period_service.close_period("2024-01-BOUNDARY", test_actor_id)
        session.flush()

        period = session.query(FiscalPeriod).filter_by(period_code="2024-01-BOUNDARY").one()
        return period

    def test_shrink_end_date_to_reopen_days_orm(
        self,
        session,
        closed_january_period,
    ):
        """
        CRITICAL TEST: Shrinking end_date on a closed period should be blocked.

        The Attack:
        - Original: Jan 1-31 CLOSED
        - After attack: Jan 1-30 CLOSED
        - Result: Jan 31 is "open" for posting

        If this commit succeeds, the attacker can post to Jan 31.
        """
        original_end = closed_january_period.end_date
        assert original_end == date(2024, 1, 31)

        # THE ATTACK: Shrink end_date by one day
        closed_january_period.end_date = date(2024, 1, 30)

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "fiscalperiod" in error_msg or "end_date" in error_msg or "closed" in error_msg

        session.rollback()

    def test_shrink_end_date_to_yesterday_orm(
        self,
        session,
        period_service,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Attack variant: Shrink end_date to yesterday.

        This is the exact attack the user described: change end_date to yesterday
        to "reopen" today for posting in a supposedly closed period.
        """
        today = deterministic_clock.now().date()
        yesterday = today - timedelta(days=1)

        # Create a period covering "this month" and close it
        month_start = today.replace(day=1)
        if today.month == 12:
            month_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        period_service.create_period(
            period_code="CUR-BOUND-ATTACK",
            name="Current Month (Boundary Attack)",
            start_date=month_start,
            end_date=month_end,
            actor_id=test_actor_id,
        )
        session.flush()

        period_service.close_period("CUR-BOUND-ATTACK", test_actor_id)
        session.flush()

        period = session.query(FiscalPeriod).filter_by(period_code="CUR-BOUND-ATTACK").one()

        # THE ATTACK: Change end_date to yesterday
        period.end_date = yesterday

        # If this succeeds, "today" through "month_end" are now open for posting
        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "fiscalperiod" in error_msg or "end_date" in error_msg or "closed" in error_msg

        session.rollback()

    def test_expand_start_date_to_exclude_beginning_orm(
        self,
        session,
        closed_january_period,
    ):
        """
        Attack variant: Expand start_date to exclude early days.

        - Original: Jan 1-31 CLOSED
        - After attack: Jan 5-31 CLOSED
        - Result: Jan 1-4 are "open" for posting

        This is the mirror of the end_date attack.
        """
        original_start = closed_january_period.start_date
        assert original_start == date(2024, 1, 1)

        # THE ATTACK: Move start_date forward
        closed_january_period.start_date = date(2024, 1, 5)

        with pytest.raises(ImmutabilityViolationError) as exc_info:
            session.flush()

        error_msg = str(exc_info.value).lower()
        assert "fiscalperiod" in error_msg or "start_date" in error_msg or "closed" in error_msg

        session.rollback()

    def test_raw_sql_end_date_attack(
        self,
        session,
        closed_january_period,
    ):
        """
        Attack via raw SQL: Bypass ORM entirely.

        Only database triggers can stop this.
        """
        period_id = str(closed_january_period.id)

        # THE ATTACK: Raw SQL update
        try:
            session.execute(
                text("UPDATE fiscal_periods SET end_date = :new_end WHERE id = :id"),
                {"new_end": date(2024, 1, 30), "id": period_id},
            )
            session.flush()

            # Check if the update succeeded
            result = session.execute(
                text("SELECT end_date FROM fiscal_periods WHERE id = :id"),
                {"id": period_id},
            ).scalar()

            if result == date(2024, 1, 30):
                pytest.fail(
                    "INVARIANT BROKEN: Raw SQL changed end_date on closed period from Jan 31 to Jan 30. "
                    "Jan 31 is now 'open' for posting despite period being marked CLOSED. "
                    "Defense-in-depth failed - no DB trigger protection for period boundaries."
                )

        except Exception as e:
            # Good - some protection kicked in (likely DB trigger)
            session.rollback()


class TestBoundaryAttackWithPostingExploit:
    """
    Full exploit chain: Shrink period boundary, then post to "reopened" date.

    This demonstrates the actual financial impact of the boundary attack.
    """

    def test_full_exploit_chain(
        self,
        session,
        period_service,
        posting_orchestrator: PostingOrchestrator,
        standard_accounts,
        test_actor_id,
        deterministic_clock,
    ):
        """
        Full attack demonstration:
        1. Create and close January period (Jan 1-31)
        2. Attempt to shrink end_date to Jan 30
        3. If successful, post a fraudulent entry dated Jan 31
        4. Entry appears in "January" but bypassed period close

        If step 2 succeeds, the exploit is complete.
        """
        # Step 1: Create and close January period
        period_service.create_period(
            period_code="2024-01-EXPLOIT",
            name="January 2024 (Exploit Test)",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        period_service.close_period("2024-01-EXPLOIT", test_actor_id)
        session.flush()

        period = session.query(FiscalPeriod).filter_by(period_code="2024-01-EXPLOIT").one()

        # Verify posting to Jan 31 is blocked (period is closed)
        event_type = "test.exploit.before"
        _register_simple_strategy(event_type)

        try:
            result_before = posting_orchestrator.post_event(
                event_id=uuid4(),
                event_type=event_type,
                occurred_at=deterministic_clock.now(),
                effective_date=date(2024, 1, 31),  # Last day of closed period
                actor_id=test_actor_id,
                producer="test",
                payload={"before_exploit": True},
            )

            # Should be rejected - period is closed
            assert result_before.status == PostingStatus.PERIOD_CLOSED, (
                "Pre-exploit check failed: posting to closed period should be rejected"
            )

            # Step 2: THE ATTACK - shrink end_date
            savepoint = session.begin_nested()
            try:
                period.end_date = date(2024, 1, 30)
                session.flush()

                # If we get here, the attack succeeded - try to exploit it
                # Re-fetch period to confirm change
                session.refresh(period)
                if period.end_date == date(2024, 1, 30):
                    # Step 3: Post fraudulent entry to the "reopened" Jan 31
                    exploit_event_type = "test.exploit.after"
                    _register_simple_strategy(exploit_event_type)

                    try:
                        result_after = posting_orchestrator.post_event(
                            event_id=uuid4(),
                            event_type=exploit_event_type,
                            occurred_at=deterministic_clock.now(),
                            effective_date=date(2024, 1, 31),  # Now "open"!
                            actor_id=test_actor_id,
                            producer="test",
                            payload={"fraudulent_entry": True},
                        )

                        if result_after.status == PostingStatus.POSTED:
                            pytest.fail(
                                "FULL EXPLOIT SUCCESSFUL:\n"
                                "1. January period was closed (Jan 1-31)\n"
                                "2. Attacker changed end_date to Jan 30\n"
                                "3. Jan 31 posting SUCCEEDED despite month being 'closed'\n"
                                "4. Financial audit trail is now compromised\n"
                                "5. Month-end close is meaningless if boundaries can shift"
                            )
                    finally:
                        StrategyRegistry._strategies.pop(exploit_event_type, None)

            except ImmutabilityViolationError:
                # Good - attack was blocked
                savepoint.rollback()

        finally:
            StrategyRegistry._strategies.pop(event_type, None)


class TestBoundaryAttackAuditImplications:
    """
    Document the audit implications of boundary manipulation.
    """

    def test_audit_trail_corruption_scenario(
        self,
        session,
        period_service,
        test_actor_id,
    ):
        """
        Document what WOULD happen if boundary attacks succeeded.

        Audit Trail Before Attack:
        - Period "2024-01" status=CLOSED, dates Jan 1-31
        - Audit shows period was closed on Feb 5, 2024

        After Boundary Attack:
        - Period "2024-01" status=CLOSED, dates Jan 1-30 (changed!)
        - Status still shows CLOSED
        - closed_at still shows Feb 5, 2024
        - Auditor sees "closed period" but Jan 31 is actually open

        The status flag becomes a LIE because the boundaries shifted.
        """
        # Create a period to demonstrate the concept
        period_service.create_period(
            period_code="AUDIT-SCENARIO",
            name="Audit Scenario Period",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            actor_id=test_actor_id,
        )
        session.flush()

        period_service.close_period("AUDIT-SCENARIO", test_actor_id)
        session.flush()

        period = session.query(FiscalPeriod).filter_by(period_code="AUDIT-SCENARIO").one()

        # Document the audit record
        status_val = period.status.value if hasattr(period.status, 'value') else str(period.status)
        audit_record = {
            "period_code": period.period_code,
            "status": status_val,
            "start_date": str(period.start_date),
            "end_date": str(period.end_date),
            "closed_at": str(period.closed_at) if period.closed_at else None,
            "closed_by_id": str(period.closed_by_id) if period.closed_by_id else None,
        }

        print(f"\n{'='*60}")
        print("AUDIT TRAIL - PERIOD CLOSE RECORD")
        print(f"{'='*60}")
        for key, value in audit_record.items():
            print(f"  {key}: {value}")
        print(f"{'='*60}")
        print("\nIf end_date could be changed to Jan 30:")
        print("  - Status would still show 'closed'")
        print("  - closed_at would still show the original timestamp")
        print("  - BUT Jan 31 would be open for posting")
        print("  - Auditor would see 'closed period' but entries could appear")
        print("  - This is a material misrepresentation of controls")
        print(f"{'='*60}\n")

        # The actual assertion: boundaries must be immutable
        period.end_date = date(2024, 1, 30)

        with pytest.raises(ImmutabilityViolationError):
            session.flush()

        session.rollback()
