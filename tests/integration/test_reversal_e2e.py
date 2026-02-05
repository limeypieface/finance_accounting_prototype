"""
End-to-end integration tests: Reversal flow.

Covers deferred items from reversal implementation:
- Multi-step: post via ModulePostingService → reverse → verify reversal entry and link
- Post-close-reverse: post in period 1, close period 1, reverse in current period (period 2)
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import JournalEntry
from finance_kernel.services.module_posting_service import ModulePostingStatus

# Service-tier: use test session + module_role_resolver (no get_session/get_active_config).
pytestmark = pytest.mark.service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def link_graph(session):
    """Provide LinkGraphService for integration tests."""
    from finance_kernel.services.link_graph_service import LinkGraphService
    return LinkGraphService(session)


@pytest.fixture
def journal_writer_for_reversal(session, module_role_resolver, deterministic_clock, auditor_service):
    """JournalWriter using module_role_resolver so reversal uses same accounts as posting."""
    from finance_kernel.services.journal_writer import JournalWriter
    return JournalWriter(session, module_role_resolver, deterministic_clock, auditor_service)


@pytest.fixture
def reversal_service(
    session,
    journal_writer_for_reversal,
    auditor_service,
    link_graph,
    period_service,
    deterministic_clock,
):
    """Provide ReversalService for integration tests (uses same accounts as module_posting_service)."""
    from finance_kernel.services.reversal_service import ReversalService

    return ReversalService(
        session=session,
        journal_writer=journal_writer_for_reversal,
        auditor=auditor_service,
        link_graph=link_graph,
        period_service=period_service,
        clock=deterministic_clock,
    )


# ---------------------------------------------------------------------------
# E2E: Post then reverse (same period)
# ---------------------------------------------------------------------------


class TestReversalSamePeriodE2E:
    """Post via pipeline → reverse in same period → verify reversal entry and link."""

    def test_post_then_reverse_same_period_produces_reversal_entry_and_link(
        self,
        module_posting_service,
        reversal_service,
        current_period,
        test_actor_id,
        deterministic_clock,
        session,
    ):
        """Post inventory receipt, then reverse; verify reversal entry and REVERSED_BY link."""
        from finance_kernel.models.economic_link import EconomicLinkModel
        from sqlalchemy import select

        # 1. Post via ModulePostingService
        result = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload={
                "quantity": 100,
                "unit_cost": "25.00",
                "item_code": "RAW-001",
                "po_number": "PO-2024-0100",
                "warehouse": "WH-01",
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            amount=Decimal("2500.00"),
            currency="USD",
        )
        assert result.status == ModulePostingStatus.POSTED
        assert len(result.journal_entry_ids) > 0
        original_entry_id = result.journal_entry_ids[0]
        session.flush()

        # 2. Reverse in same period
        rev_result = reversal_service.reverse_in_same_period(
            original_entry_id=original_entry_id,
            reason="E2E reversal test",
            actor_id=test_actor_id,
        )
        session.flush()

        # 3. Verify reversal entry exists and is POSTED
        reversal_entry = session.get(JournalEntry, rev_result.reversal_entry_id)
        assert reversal_entry is not None
        assert reversal_entry.is_posted
        assert reversal_entry.reversal_of_id == original_entry_id

        # 4. Verify REVERSED_BY economic link exists (stored as "reversed_by")
        links = list(
            session.execute(
                select(EconomicLinkModel).where(
                    EconomicLinkModel.link_type == "reversed_by",
                    EconomicLinkModel.child_artifact_type == "journal_entry",
                    EconomicLinkModel.child_artifact_id == rev_result.reversal_entry_id,
                )
            ).scalars().all()
        )
        assert len(links) >= 1
        row = links[0]
        link = row[0] if hasattr(row, "__len__") and len(row) == 1 else row
        assert link.parent_artifact_id == original_entry_id
        assert link.child_artifact_id == rev_result.reversal_entry_id


# ---------------------------------------------------------------------------
# E2E: Post in period 1, close period 1, reverse in current period (period 2)
# ---------------------------------------------------------------------------


class TestReversalPostCloseReverseE2E:
    """Post in period 1, close it, then reverse in current (open) period."""

    def test_post_close_then_reverse_in_current_period(
        self,
        session,
        module_posting_service,
        reversal_service,
        period_service,
        test_actor_id,
        deterministic_clock,
        create_period,
        register_modules,
        module_role_resolver,
    ):
        """Post in first period, close it, open second period, reverse in second period."""
        from datetime import date

        # Use fixed dates so we don't collide with current_period: period 1 = Jan 2024, period 2 = Feb 2024
        start1 = date(2024, 1, 1)
        end1 = date(2024, 1, 31)
        period_code_1 = "2024-01"
        create_period(period_code_1, period_code_1, start1, end1, PeriodStatus.OPEN)

        # Post in period 1
        result = module_posting_service.post_event(
            event_type="inventory.receipt",
            payload={
                "quantity": 50,
                "unit_cost": "10.00",
                "item_code": "BOLT-M8",
            },
            effective_date=start1,
            actor_id=test_actor_id,
            amount=Decimal("500.00"),
        )
        assert result.status == ModulePostingStatus.POSTED
        original_entry_id = result.journal_entry_ids[0]
        session.flush()

        # Close period 1
        period_service.close_period(period_code_1, test_actor_id)
        session.flush()

        # Period 2: next open period (Feb 2024)
        start2 = date(2024, 2, 1)
        end2 = date(2024, 2, 29)
        period_code_2 = "2024-02"
        create_period(period_code_2, period_code_2, start2, end2, PeriodStatus.OPEN)

        # Reverse in current period (period 2) with effective_date in period 2
        rev_result = reversal_service.reverse_in_current_period(
            original_entry_id=original_entry_id,
            reason="Post-close reversal E2E",
            actor_id=test_actor_id,
            effective_date=start2,
        )
        session.flush()

        # Reversal entry should be in period 2
        reversal_entry = session.get(JournalEntry, rev_result.reversal_entry_id)
        assert reversal_entry is not None
        assert reversal_entry.effective_date == start2
        assert reversal_entry.reversal_of_id == original_entry_id
