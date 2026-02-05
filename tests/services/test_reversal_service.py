"""
ReversalService unit tests.

Tests cover:
- Happy path: same-period and current-period reversals
- Error paths: not posted, already reversed, closed period
- Invariant preservation: R4, R9, R10, R21, R22
- Line fidelity: sides flipped, amounts/currencies/dimensions preserved
- Canonical linkage: reversal_of_id, is_reversed, is_reversal
- Secondary linkage: REVERSED_BY economic link
- Audit trail: audit event recorded
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.exceptions import (
    ClosedPeriodError,
    CrossLedgerReversalError,
    EntryAlreadyReversedError,
    EntryNotPostedError,
)
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.models.journal import (
    JournalEntry,
    JournalEntryStatus,
    JournalLine,
    LineSide,
)
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_kernel.services.period_service import PeriodService
from finance_kernel.services.reversal_service import ReversalResult, ReversalService


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def link_graph(session):
    """Provide a LinkGraphService instance."""
    return LinkGraphService(session)


@pytest.fixture
def reversal_service(session, journal_writer, auditor_service, link_graph, period_service, deterministic_clock):
    """Provide a ReversalService instance."""
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
    """Create a posted journal entry for reversal testing.

    Posts a simple debit Cash / credit Revenue entry.
    Returns the JournalEntry ORM object.
    """
    result = post_via_coordinator(
        debit_role="CashAsset",
        credit_role="SalesRevenue",
        amount=Decimal("500.00"),
        currency="USD",
    )
    assert result.success
    session.flush()

    # Load the entry by the WrittenEntry.entry_id from JournalWriteResult
    entry_id = result.journal_result.entries[0].entry_id
    entry = session.get(JournalEntry, entry_id)
    assert entry is not None
    assert entry.is_posted
    return entry


@pytest.fixture
def multi_line_posted_entry(
    session, interpretation_coordinator, deterministic_clock, test_actor_id,
    current_period, standard_accounts,
):
    """Create a posted entry with 4 lines (2 debits, 2 credits) for testing."""
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent,
        AccountingIntentSnapshot,
        IntentLine,
        LedgerIntent,
    )
    from finance_kernel.domain.meaning_builder import (
        EconomicEventData,
        MeaningBuilderResult,
    )
    from tests.conftest import make_source_event

    source_event_id = uuid4()
    make_source_event(session, source_event_id, test_actor_id, deterministic_clock)

    econ_data = EconomicEventData(
        source_event_id=source_event_id,
        economic_type="test.multi_line",
        effective_date=deterministic_clock.now().date(),
        profile_id="TestProfile",
        profile_version=1,
        profile_hash=None,
        quantity=Decimal("1000.00"),
    )

    intent = AccountingIntent(
        econ_event_id=uuid4(),
        source_event_id=source_event_id,
        profile_id="TestProfile",
        profile_version=1,
        effective_date=deterministic_clock.now().date(),
        ledger_intents=(
            LedgerIntent(
                ledger_id="GL",
                lines=(
                    IntentLine.debit("CashAsset", Decimal("600.00"), "USD"),
                    IntentLine.debit("InventoryAsset", Decimal("400.00"), "USD"),
                    IntentLine.credit("SalesRevenue", Decimal("600.00"), "USD"),
                    IntentLine.credit("COGS", Decimal("400.00"), "USD"),
                ),
            ),
        ),
        snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
    )

    result = interpretation_coordinator.interpret_and_post(
        meaning_result=MeaningBuilderResult.ok(econ_data),
        accounting_intent=intent,
        actor_id=test_actor_id,
    )
    session.flush()

    entry = session.query(JournalEntry).filter(
        JournalEntry.source_event_id == source_event_id
    ).first()
    assert entry is not None
    assert entry.is_posted
    return entry


# =========================================================================
# Happy Path Tests
# =========================================================================


class TestReversalHappyPath:
    """Tests for successful reversal operations."""

    def test_reverse_in_same_period_creates_reversal(
        self, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversing in same period creates a new POSTED entry."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Test reversal",
            actor_id=test_actor_id,
        )

        assert isinstance(result, ReversalResult)
        assert result.original_entry_id == posted_entry.id
        assert result.reversal_entry_id != posted_entry.id
        assert result.reversal_seq is not None
        assert result.effective_date == posted_entry.effective_date

    def test_reverse_in_current_period_creates_reversal(
        self, reversal_service, posted_entry, test_actor_id, deterministic_clock,
    ):
        """Reversing into current period with explicit date succeeds."""
        result = reversal_service.reverse_in_current_period(
            original_entry_id=posted_entry.id,
            reason="Test reversal different period",
            actor_id=test_actor_id,
            effective_date=deterministic_clock.now().date(),
        )

        assert isinstance(result, ReversalResult)
        assert result.effective_date == deterministic_clock.now().date()

    def test_reversal_entry_is_posted(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal entry has POSTED status, not REVERSED."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Status check",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert reversal.status == JournalEntryStatus.POSTED
        assert reversal.is_posted

    def test_original_status_unchanged(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Original entry remains POSTED -- never mutated (R10)."""
        reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="R10 test",
            actor_id=test_actor_id,
        )

        session.refresh(posted_entry)
        assert posted_entry.status == JournalEntryStatus.POSTED
        assert posted_entry.is_posted


# =========================================================================
# Line Fidelity Tests
# =========================================================================


class TestReversalLineFidelity:
    """Tests that reversal lines are correct mechanical inversions."""

    def test_reversal_flips_debit_to_credit(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Debit lines become credit lines in the reversal."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Flip test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        original_lines = sorted(posted_entry.lines, key=lambda l: l.line_seq)
        reversal_lines = sorted(reversal.lines, key=lambda l: l.line_seq)

        assert len(original_lines) == len(reversal_lines)

        for orig, rev in zip(original_lines, reversal_lines):
            if orig.side == LineSide.DEBIT:
                assert rev.side == LineSide.CREDIT
            else:
                assert rev.side == LineSide.DEBIT

    def test_reversal_preserves_amounts(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal line amounts are identical to original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Amount test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        original_lines = sorted(posted_entry.lines, key=lambda l: l.line_seq)
        reversal_lines = sorted(reversal.lines, key=lambda l: l.line_seq)

        for orig, rev in zip(original_lines, reversal_lines):
            assert rev.amount == orig.amount

    def test_reversal_preserves_currencies(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal line currencies are identical to original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Currency test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        original_lines = sorted(posted_entry.lines, key=lambda l: l.line_seq)
        reversal_lines = sorted(reversal.lines, key=lambda l: l.line_seq)

        for orig, rev in zip(original_lines, reversal_lines):
            assert rev.currency == orig.currency

    def test_reversal_preserves_account_ids(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal lines reference the same accounts as original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Account test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        original_lines = sorted(posted_entry.lines, key=lambda l: l.line_seq)
        reversal_lines = sorted(reversal.lines, key=lambda l: l.line_seq)

        for orig, rev in zip(original_lines, reversal_lines):
            assert rev.account_id == orig.account_id

    def test_reversal_preserves_line_seq(
        self, session, reversal_service, multi_line_posted_entry, test_actor_id,
    ):
        """Reversal lines preserve line_seq ordering from original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=multi_line_posted_entry.id,
            reason="Line seq test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        original_seqs = sorted(l.line_seq for l in multi_line_posted_entry.lines)
        reversal_seqs = sorted(l.line_seq for l in reversal.lines)

        assert original_seqs == reversal_seqs

    def test_multi_line_reversal_flips_all_sides(
        self, session, reversal_service, multi_line_posted_entry, test_actor_id,
    ):
        """4-line entry reversal flips all sides correctly."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=multi_line_posted_entry.id,
            reason="Multi-line test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert len(reversal.lines) == 4

        original_lines = sorted(multi_line_posted_entry.lines, key=lambda l: l.line_seq)
        reversal_lines = sorted(reversal.lines, key=lambda l: l.line_seq)

        for orig, rev in zip(original_lines, reversal_lines):
            assert rev.side != orig.side
            assert rev.amount == orig.amount
            assert rev.account_id == orig.account_id


# =========================================================================
# Canonical Linkage Tests
# =========================================================================


class TestCanonicalLinkage:
    """Tests for the reversal_of_id canonical linkage."""

    def test_reversal_sets_reversal_of_id(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal entry has reversal_of_id pointing to original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Linkage test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert reversal.reversal_of_id == posted_entry.id
        assert reversal.is_reversal

    def test_original_is_reversed_after_reversal(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Original entry's is_reversed returns True after reversal."""
        reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Derived test",
            actor_id=test_actor_id,
        )

        session.refresh(posted_entry)
        assert posted_entry.is_reversed

    def test_reversal_creates_reversed_by_link(
        self, session, reversal_service, posted_entry, test_actor_id, link_graph,
    ):
        """REVERSED_BY economic link is created."""
        from finance_kernel.domain.economic_link import ArtifactRef

        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Link test",
            actor_id=test_actor_id,
        )

        # Check via link graph
        reversal_link = link_graph.find_reversal(
            ArtifactRef.journal_entry(posted_entry.id)
        )
        assert reversal_link is not None
        assert reversal_link.child_ref.artifact_id == result.reversal_entry_id


# =========================================================================
# Error Path Tests
# =========================================================================


class TestReversalErrors:
    """Tests for reversal error conditions."""

    def test_reverse_draft_entry_fails(
        self, session, reversal_service, test_actor_id, standard_accounts,
        current_period, create_source_event, deterministic_clock,
    ):
        """Cannot reverse a DRAFT entry."""
        # Create a draft entry manually
        source_event_id = uuid4()
        create_source_event(source_event_id)

        entry = JournalEntry(
            source_event_id=source_event_id,
            source_event_type="test.draft",
            occurred_at=deterministic_clock.now(),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            status=JournalEntryStatus.DRAFT,
            idempotency_key=f"test:draft:{uuid4()}",
            created_by_id=test_actor_id,
        )
        session.add(entry)
        session.flush()

        with pytest.raises(EntryNotPostedError):
            reversal_service.reverse_in_same_period(
                original_entry_id=entry.id,
                reason="Should fail",
                actor_id=test_actor_id,
            )

    def test_reverse_nonexistent_entry_fails(
        self, reversal_service, test_actor_id,
    ):
        """Cannot reverse an entry that doesn't exist."""
        with pytest.raises(EntryNotPostedError):
            reversal_service.reverse_in_same_period(
                original_entry_id=uuid4(),
                reason="Should fail",
                actor_id=test_actor_id,
            )

    def test_reverse_already_reversed_fails(
        self, reversal_service, posted_entry, test_actor_id,
    ):
        """Cannot reverse an entry that is already reversed."""
        # First reversal succeeds
        reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="First reversal",
            actor_id=test_actor_id,
        )

        # Second reversal fails
        with pytest.raises(EntryAlreadyReversedError):
            reversal_service.reverse_in_same_period(
                original_entry_id=posted_entry.id,
                reason="Second reversal",
                actor_id=test_actor_id,
            )


# =========================================================================
# Period Semantics Tests
# =========================================================================


class TestPeriodSemantics:
    """Tests for explicit period enforcement."""

    def test_reverse_in_closed_period_fails(
        self, session, reversal_service, posted_entry, test_actor_id, period_service,
    ):
        """reverse_in_same_period fails when original period is closed."""
        # Close the period
        from sqlalchemy import select

        period = session.execute(
            select(FiscalPeriod).where(FiscalPeriod.status == PeriodStatus.OPEN)
        ).scalar_one()
        period_service.close_period(period.period_code, test_actor_id)
        session.flush()

        with pytest.raises(ClosedPeriodError):
            reversal_service.reverse_in_same_period(
                original_entry_id=posted_entry.id,
                reason="Should fail - closed period",
                actor_id=test_actor_id,
            )

    def test_reverse_in_current_period_different_date(
        self, session, reversal_service, posted_entry, test_actor_id,
        create_period, deterministic_clock,
    ):
        """reverse_in_current_period uses the caller-provided effective_date."""
        # Create a second open period
        today = deterministic_clock.now().date()
        if today.month == 12:
            next_start = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_start = today.replace(month=today.month + 1, day=1)
        next_end = next_start + timedelta(days=28)

        create_period(
            period_code="2026-02",
            name="February 2026",
            start_date=next_start,
            end_date=next_end,
        )

        result = reversal_service.reverse_in_current_period(
            original_entry_id=posted_entry.id,
            reason="Different period reversal",
            actor_id=test_actor_id,
            effective_date=next_start,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert reversal.effective_date == next_start
        assert reversal.effective_date != posted_entry.effective_date


# =========================================================================
# Invariant Preservation Tests
# =========================================================================


class TestInvariantPreservation:
    """Tests that reversals preserve all posting invariants."""

    def test_reversal_is_balanced_r4(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal entry satisfies R4: debits == credits per currency."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="R4 test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert reversal.is_balanced

    def test_reversal_has_monotonic_seq_r9(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal entry has a seq greater than the original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="R9 test",
            actor_id=test_actor_id,
        )

        assert result.reversal_seq > posted_entry.seq

    def test_reversal_lines_have_no_rounding_r22(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """All reversal lines have is_rounding=False (R22)."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="R22 test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        for line in reversal.lines:
            assert line.is_rounding is False

    def test_reversal_has_valid_idempotency_key(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal idempotency key starts with 'reversal:'."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Idempotency test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert reversal.idempotency_key.startswith("reversal:")
        assert str(posted_entry.id) in reversal.idempotency_key

    def test_reversal_has_r21_snapshot_versions(
        self, session, reversal_service, posted_entry, test_actor_id,
    ):
        """Reversal entry copies R21 snapshot versions from original."""
        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="R21 test",
            actor_id=test_actor_id,
        )

        reversal = session.get(JournalEntry, result.reversal_entry_id)
        assert reversal.coa_version == posted_entry.coa_version
        assert reversal.dimension_schema_version == posted_entry.dimension_schema_version

    def test_reversal_creates_audit_event(
        self, session, reversal_service, posted_entry, test_actor_id, auditor_service,
    ):
        """Reversal records an audit event in the hash chain."""
        from finance_kernel.models.audit_event import AuditAction

        result = reversal_service.reverse_in_same_period(
            original_entry_id=posted_entry.id,
            reason="Audit test",
            actor_id=test_actor_id,
        )

        trace = auditor_service.get_trace("JournalEntry", result.reversal_entry_id)
        actions = [e.action for e in trace.entries]
        assert AuditAction.JOURNAL_REVERSED in actions


# =========================================================================
# Ledger Boundary Tests (Reversal Hardening #4)
# =========================================================================


class TestReversalLedgerBoundary:
    """Reversal must be in same ledger as original; cross-ledger is rejected."""

    def test_write_reversal_rejects_mismatched_expected_ledger(
        self,
        session,
        journal_writer,
        posted_entry,
        test_actor_id,
    ):
        """JournalWriter.write_reversal raises CrossLedgerReversalError when expected_ledger_id does not match original."""
        # Original entry has ledger_id from posting (default "GL" when unset)
        with pytest.raises(CrossLedgerReversalError) as exc_info:
            journal_writer.write_reversal(
                original_entry=posted_entry,
                source_event_id=uuid4(),
                actor_id=test_actor_id,
                effective_date=posted_entry.effective_date,
                reason="Cross-ledger test",
                expected_ledger_id="OTHER_LEDGER",
            )
        err = exc_info.value
        assert err.code == "CROSS_LEDGER_REVERSAL"
        assert err.journal_entry_id == str(posted_entry.id)
        assert err.requested_ledger_id == "OTHER_LEDGER"
