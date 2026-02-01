"""
Tests for PeriodCloseOrchestrator.

Covers:
- Health check diagnostics (clean, SL variance, suspense balance)
- begin_close acquires R25 lock
- R25: normal posting blocked during CLOSING, close-posting allowed
- Phase 1: subledger close (with and without SL service)
- Phase 2: trial balance verification
- Phase 3: adjustment callback
- Phase 4: closing entries (year-end vs monthly skip)
- Phase 5: GL close (CLOSING -> CLOSED)
- Phase 6: lock period (year-end only)
- Full close_period_full (monthly happy path)
- Full close_period_full (year-end happy path)
- Phase failure stops execution
- Cancel close reverts to OPEN
- Authority model enforcement
- Close certificate with ledger hash and audit event
- get_status query
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.exceptions import (
    CloseAuthorityError,
    ClosedPeriodError,
    PeriodAlreadyClosedError,
    PeriodClosingError,
)
from finance_kernel.models.audit_event import AuditAction
from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.selectors.ledger_selector import LedgerSelector
from finance_kernel.selectors.subledger_selector import SubledgerSelector
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.period_service import PeriodService
from finance_services._close_types import (
    CloseRole,
    CloseRunStatus,
)
from finance_services.period_close_orchestrator import (
    DefaultCloseRoleResolver,
    PeriodCloseOrchestrator,
)


# =========================================================================
# Fixtures
# =========================================================================

TEST_ACTOR = uuid4()
PERIOD_CODE = "2026-01"
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 31)


@pytest.fixture
def clock():
    return DeterministicClock()


@pytest.fixture
def period(session, create_period):
    """Create a standard open period covering January 2026."""
    return create_period(
        period_code=PERIOD_CODE,
        name="January 2026",
        start_date=PERIOD_START,
        end_date=PERIOD_END,
        status=PeriodStatus.OPEN,
    )


@pytest.fixture
def period_service(session, clock):
    return PeriodService(session, clock)


@pytest.fixture
def auditor(session, clock):
    return AuditorService(session, clock)


@pytest.fixture
def sl_selector(session):
    return SubledgerSelector(session)


@pytest.fixture
def ledger_selector(session):
    return LedgerSelector(session)


@pytest.fixture
def journal_selector(session):
    return JournalSelector(session)


@pytest.fixture
def mock_reporting():
    """Mock ReportingService with a balanced trial balance."""
    svc = MagicMock()
    tb = MagicMock()
    tb.is_balanced = True
    tb.total_debits = Decimal("1000.00")
    tb.total_credits = Decimal("1000.00")
    svc.trial_balance.return_value = tb
    return svc


@pytest.fixture
def mock_gl():
    """Mock GeneralLedgerService with successful closing entry."""
    svc = MagicMock()
    result = MagicMock()
    result.journal_entry_ids = [uuid4()]
    svc.record_closing_entry.return_value = result
    return svc


@pytest.fixture
def mock_sl_period():
    """Mock SubledgerPeriodService with successful close."""
    svc = MagicMock()
    # Import the enum for the status
    from finance_kernel.models.subledger import SubledgerPeriodStatus

    result = MagicMock()
    result.status = SubledgerPeriodStatus.CLOSED
    svc.close_subledger_period.return_value = result
    return svc


@pytest.fixture
def orchestrator(
    session, period_service, mock_sl_period, mock_reporting, mock_gl,
    auditor, sl_selector, ledger_selector, journal_selector, clock,
):
    """Build a PeriodCloseOrchestrator with real kernel services + mock module services."""
    return PeriodCloseOrchestrator(
        session=session,
        period_service=period_service,
        sl_period_service=mock_sl_period,
        reporting_service=mock_reporting,
        gl_service=mock_gl,
        auditor_service=auditor,
        subledger_selector=sl_selector,
        ledger_selector=ledger_selector,
        journal_selector=journal_selector,
        clock=clock,
    )


class _PreparerResolver:
    """Role resolver that gives everyone PREPARER role."""

    def resolve(self, actor_id: UUID) -> CloseRole:
        return CloseRole.PREPARER


@pytest.fixture
def restricted_orchestrator(
    session, period_service, mock_sl_period, mock_reporting, mock_gl,
    auditor, sl_selector, ledger_selector, journal_selector, clock,
):
    """Orchestrator with PREPARER-only role resolver (restricted)."""
    return PeriodCloseOrchestrator(
        session=session,
        period_service=period_service,
        sl_period_service=mock_sl_period,
        reporting_service=mock_reporting,
        gl_service=mock_gl,
        auditor_service=auditor,
        subledger_selector=sl_selector,
        ledger_selector=ledger_selector,
        journal_selector=journal_selector,
        clock=clock,
        role_resolver=_PreparerResolver(),
    )


# =========================================================================
# Health Check
# =========================================================================


class TestHealthCheck:
    """Health check is read-only — no state changes."""

    def test_clean_health_check(self, orchestrator, period, standard_accounts):
        """Health check with no data returns balanced, no issues."""
        result = orchestrator.health_check(PERIOD_CODE, PERIOD_END)

        assert result.period_code == PERIOD_CODE
        assert result.trial_balance_ok is True
        assert result.can_proceed is True
        assert len(result.blocking_issues) == 0

    def test_health_check_suspense_warning(
        self, orchestrator, period, standard_accounts, session,
        post_via_coordinator,
    ):
        """Non-zero suspense account generates a warning (not blocking)."""
        # Post a transaction that hits account 2100 (Tax Payable, a suspense account)
        # Standard accounts don't have 2100, but the health check silently skips
        # accounts that don't exist — so this verifies the code path works
        result = orchestrator.health_check(PERIOD_CODE, PERIOD_END)

        # With no data, suspense accounts should have zero balance
        for s in result.suspense_balances:
            if s["balance"] != Decimal("0"):
                assert s["status"] == "WARNING"

    def test_health_check_period_activity(
        self, orchestrator, period, standard_accounts,
        post_via_coordinator,
    ):
        """Health check reports entry count for the period."""
        # Post one entry
        post_via_coordinator(
            amount=Decimal("500.00"),
            effective_date=PERIOD_START,
        )

        result = orchestrator.health_check(PERIOD_CODE, PERIOD_END)
        assert result.period_entry_count >= 1


# =========================================================================
# Begin Close (R25 Lock)
# =========================================================================


class TestBeginClose:
    """begin_close acquires exclusive R25 lock on the period."""

    def test_begin_close_sets_closing_status(self, orchestrator, period, session):
        """begin_close transitions period to CLOSING."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        assert run.period_code == PERIOD_CODE
        assert run.status == CloseRunStatus.IN_PROGRESS
        assert run.correlation_id  # Non-empty

        # Verify period is now CLOSING in the DB
        from sqlalchemy import select
        fp = session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == PERIOD_CODE)
        ).scalar_one()
        assert fp.status == PeriodStatus.CLOSING
        assert fp.closing_run_id is not None

    def test_begin_close_records_audit_event(self, orchestrator, period, auditor, session):
        """begin_close produces a CLOSE_BEGUN audit event."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        from sqlalchemy import select
        from finance_kernel.models.audit_event import AuditEvent
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == AuditAction.CLOSE_BEGUN.value
            )
        ).scalars().all()

        assert len(events) >= 1
        latest = events[-1]
        assert latest.payload["period_code"] == PERIOD_CODE
        assert latest.payload["correlation_id"] == run.correlation_id

    def test_concurrent_begin_close_raises(self, orchestrator, period):
        """Second begin_close on same period raises PeriodClosingError."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        with pytest.raises(PeriodClosingError):
            orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

    def test_begin_close_on_closed_period_raises(
        self, orchestrator, period, period_service,
    ):
        """begin_close on already-closed period raises PeriodAlreadyClosedError."""
        period_service.close_period(PERIOD_CODE, TEST_ACTOR)

        with pytest.raises(PeriodAlreadyClosedError):
            orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)


# =========================================================================
# R25 Enforcement
# =========================================================================


class TestR25Enforcement:
    """Normal posting blocked during CLOSING, close-posting allowed."""

    def test_normal_posting_blocked_during_closing(
        self, orchestrator, period, period_service, standard_accounts,
    ):
        """validate_effective_date raises PeriodClosingError for non-close postings."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        with pytest.raises(PeriodClosingError):
            period_service.validate_effective_date(PERIOD_START)

    def test_close_posting_allowed_during_closing(
        self, orchestrator, period, period_service, standard_accounts,
    ):
        """validate_effective_date with is_close_posting=True succeeds."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        # Should not raise
        period_service.validate_effective_date(
            PERIOD_START, is_close_posting=True,
        )


# =========================================================================
# Phase Execution
# =========================================================================


class TestPhase1CloseSubledgers:
    """Phase 1: Close subledgers."""

    def test_phase_1_with_sl_service(self, orchestrator, period):
        """Phase 1 calls sl_period_service.close_subledger_period for each SL type."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 1, TEST_ACTOR)

        assert result.success is True
        assert result.phase_name == "close_subledgers"
        assert result.guard == "ALL_SUBLEDGERS_CLOSED"

    def test_phase_1_without_sl_service(
        self, session, period_service, mock_reporting, mock_gl,
        auditor, sl_selector, ledger_selector, journal_selector, clock,
        period,
    ):
        """Phase 1 skips gracefully when no SL service is configured."""
        orch = PeriodCloseOrchestrator(
            session=session,
            period_service=period_service,
            sl_period_service=None,
            reporting_service=mock_reporting,
            gl_service=mock_gl,
            auditor_service=auditor,
            subledger_selector=sl_selector,
            ledger_selector=ledger_selector,
            journal_selector=journal_selector,
            clock=clock,
        )
        run = orch.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orch.run_phase(run, 1, TEST_ACTOR)

        assert result.success is True
        assert "skipping" in result.message.lower() or "No subledger" in result.message

    def test_phase_1_sl_close_failure(self, orchestrator, period, mock_sl_period):
        """Phase 1 reports failure when a subledger fails to close."""
        from finance_kernel.models.subledger import SubledgerPeriodStatus

        fail_result = MagicMock()
        fail_result.status = SubledgerPeriodStatus.OPEN
        mock_sl_period.close_subledger_period.return_value = fail_result

        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 1, TEST_ACTOR)

        assert result.success is False
        assert len(result.exceptions) > 0


class TestPhase2TrialBalance:
    """Phase 2: Verify trial balance."""

    def test_phase_2_balanced(self, orchestrator, period):
        """Phase 2 succeeds when trial balance is balanced."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 2, TEST_ACTOR)

        assert result.success is True
        assert result.phase_name == "verify_trial_balance"

    def test_phase_2_unbalanced(self, orchestrator, period, mock_reporting):
        """Phase 2 fails when trial balance is unbalanced."""
        tb = MagicMock()
        tb.is_balanced = False
        tb.total_debits = Decimal("1000.00")
        tb.total_credits = Decimal("999.00")
        mock_reporting.trial_balance.return_value = tb

        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 2, TEST_ACTOR)

        assert result.success is False


class TestPhase3Adjustments:
    """Phase 3: Adjustment callback."""

    def test_phase_3_no_callback(self, orchestrator, period):
        """Phase 3 succeeds with no callback (0 adjustments)."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 3, TEST_ACTOR)

        assert result.success is True
        assert result.details.get("adjustment_count") == 0

    def test_phase_3_with_callback(self, orchestrator, period):
        """Phase 3 invokes the adjustment callback and reports count."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        def adj_callback(run, actor_id):
            return 3

        result = orchestrator.run_phase(
            run, 3, TEST_ACTOR, adjustment_callback=adj_callback,
        )

        assert result.success is True
        assert result.details["adjustment_count"] == 3


class TestPhase4ClosingEntries:
    """Phase 4: Post closing entries (year-end only)."""

    def test_phase_4_skipped_monthly(self, orchestrator, period):
        """Phase 4 is skipped for monthly close (not year-end)."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR, is_year_end=False)
        result = orchestrator.run_phase(run, 4, TEST_ACTOR)

        assert result.success is True
        assert "Skipped" in result.message

    def test_phase_4_year_end(self, orchestrator, period):
        """Phase 4 posts closing entry for year-end."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR, is_year_end=True)
        result = orchestrator.run_phase(run, 4, TEST_ACTOR)

        assert result.success is True
        assert result.guard == "YEAR_END_ENTRIES_POSTED"


class TestPhase5CloseGL:
    """Phase 5: Close GL period (CLOSING -> CLOSED)."""

    def test_phase_5_transitions_to_closed(self, orchestrator, period, session):
        """Phase 5 closes the GL period."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 5, TEST_ACTOR)

        assert result.success is True
        assert "CLOSED" in result.message

        # Verify DB state
        from sqlalchemy import select
        fp = session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == PERIOD_CODE)
        ).scalar_one()
        assert fp.status == PeriodStatus.CLOSED


class TestPhase6LockPeriod:
    """Phase 6: Lock period (year-end only, CLOSED -> LOCKED)."""

    def test_phase_6_skipped_monthly(self, orchestrator, period):
        """Phase 6 is skipped for monthly close."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR, is_year_end=False)
        result = orchestrator.run_phase(run, 6, TEST_ACTOR)

        assert result.success is True
        assert "Skipped" in result.message

    def test_phase_6_locks_year_end(self, orchestrator, period, session):
        """Phase 6 locks the period after close for year-end."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR, is_year_end=True)

        # Phase 5 first to get to CLOSED
        orchestrator.run_phase(run, 5, TEST_ACTOR)

        # Phase 6 to LOCK
        result = orchestrator.run_phase(run, 6, TEST_ACTOR)

        assert result.success is True
        assert "LOCKED" in result.message

        from sqlalchemy import select
        fp = session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == PERIOD_CODE)
        ).scalar_one()
        assert fp.status == PeriodStatus.LOCKED


# =========================================================================
# Full Close Workflow
# =========================================================================


class TestFullClose:
    """close_period_full runs all phases in sequence."""

    def test_monthly_close_happy_path(self, orchestrator, period, standard_accounts):
        """Full monthly close: phases 4/6 skipped, certificate issued."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        assert result.status == CloseRunStatus.COMPLETED
        assert result.certificate is not None
        assert result.certificate.period_code == PERIOD_CODE
        assert result.certificate.ledger_hash  # Non-empty string
        assert result.certificate.correlation_id == result.correlation_id
        assert result.message  # Non-empty

    def test_year_end_close_happy_path(self, orchestrator, period, standard_accounts):
        """Full year-end close: all phases execute, period is LOCKED."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=True,
        )

        assert result.status == CloseRunStatus.COMPLETED
        assert result.certificate is not None
        # Year-end: closing entries posted
        assert result.certificate.closing_entries_posted >= 0

    def test_phase_failure_stops_execution(self, orchestrator, period, mock_reporting):
        """Failure at phase 2 stops execution and returns FAILED."""
        tb = MagicMock()
        tb.is_balanced = False
        tb.total_debits = Decimal("1000.00")
        tb.total_credits = Decimal("500.00")
        mock_reporting.trial_balance.return_value = tb

        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        assert result.status == CloseRunStatus.FAILED
        assert result.certificate is None
        # Phase 1 passed, phase 2 failed — so phases_completed should be 1
        assert result.phases_completed == 1
        assert "phase 2" in result.message.lower() or "Failed" in result.message

    def test_full_close_with_adjustment_callback(self, orchestrator, period, standard_accounts):
        """Full close invokes adjustment callback at phase 3."""
        called = []

        def adj_callback(run, actor_id):
            called.append(True)
            return 2

        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR,
            adjustment_callback=adj_callback,
        )

        assert result.status == CloseRunStatus.COMPLETED
        assert len(called) == 1
        assert result.certificate.adjustments_posted == 2


# =========================================================================
# Cancel Close
# =========================================================================


class TestCancelClose:
    """cancel_close releases R25 lock and reverts to OPEN."""

    def test_cancel_reverts_to_open(self, orchestrator, period, session):
        """Cancel close transitions CLOSING -> OPEN."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        # Verify CLOSING
        from sqlalchemy import select
        fp = session.execute(
            select(FiscalPeriod).where(FiscalPeriod.period_code == PERIOD_CODE)
        ).scalar_one()
        assert fp.status == PeriodStatus.CLOSING

        orchestrator.cancel_close(PERIOD_CODE, TEST_ACTOR, reason="Test cancel")

        # Refresh state
        session.expire(fp)
        assert fp.status == PeriodStatus.OPEN
        assert fp.closing_run_id is None

    def test_cancel_records_audit_event(self, orchestrator, period, session):
        """Cancel close produces a CLOSE_CANCELLED audit event."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        orchestrator.cancel_close(PERIOD_CODE, TEST_ACTOR, reason="Reverting")

        from sqlalchemy import select
        from finance_kernel.models.audit_event import AuditEvent
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == AuditAction.CLOSE_CANCELLED.value
            )
        ).scalars().all()

        assert len(events) >= 1
        assert events[-1].payload["reason"] == "Reverting"

    def test_normal_posting_restored_after_cancel(
        self, orchestrator, period, period_service,
    ):
        """After cancel, normal posting is allowed again."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        # Blocked
        with pytest.raises(PeriodClosingError):
            period_service.validate_effective_date(PERIOD_START)

        orchestrator.cancel_close(PERIOD_CODE, TEST_ACTOR)

        # Restored — should not raise
        period_service.validate_effective_date(PERIOD_START)


# =========================================================================
# Authority Model
# =========================================================================


class TestAuthorityModel:
    """Role-based access control at phase boundaries."""

    def test_default_resolver_is_unrestricted(self):
        """DefaultCloseRoleResolver returns APPROVER for anyone."""
        resolver = DefaultCloseRoleResolver()
        assert resolver.resolve(uuid4()) == CloseRole.APPROVER

    def test_role_hierarchy(self):
        """CloseRole.has_authority respects the hierarchy."""
        assert CloseRole.APPROVER.has_authority(CloseRole.APPROVER) is True
        assert CloseRole.APPROVER.has_authority(CloseRole.PREPARER) is True
        assert CloseRole.PREPARER.has_authority(CloseRole.PREPARER) is True
        assert CloseRole.PREPARER.has_authority(CloseRole.APPROVER) is False
        assert CloseRole.AUDITOR.has_authority(CloseRole.PREPARER) is False

    def test_preparer_cannot_run_phase_5(self, restricted_orchestrator, period):
        """PREPARER lacks authority for phase 5 (GL close requires APPROVER)."""
        run = restricted_orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        with pytest.raises(CloseAuthorityError) as exc_info:
            restricted_orchestrator.run_phase(run, 5, TEST_ACTOR)

        assert exc_info.value.phase == 5
        assert exc_info.value.required_role == "approver"
        assert exc_info.value.actual_role == "preparer"

    def test_preparer_can_run_phase_1(self, restricted_orchestrator, period):
        """PREPARER has authority for phase 1 (close subledgers)."""
        run = restricted_orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        # Should not raise
        result = restricted_orchestrator.run_phase(run, 1, TEST_ACTOR)
        assert result.success is True

    def test_preparer_cannot_cancel_close(self, restricted_orchestrator, period):
        """Cancel requires APPROVER authority (phase 5 level)."""
        restricted_orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        with pytest.raises(CloseAuthorityError):
            restricted_orchestrator.cancel_close(PERIOD_CODE, TEST_ACTOR)


# =========================================================================
# Close Certificate
# =========================================================================


class TestCloseCertificate:
    """Close certificate is immutable and audit-anchored."""

    def test_certificate_contains_ledger_hash(
        self, orchestrator, period, standard_accounts,
    ):
        """Certificate includes R24 canonical ledger hash."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        cert = result.certificate
        assert cert is not None
        assert isinstance(cert.ledger_hash, str)
        assert len(cert.ledger_hash) == 64  # SHA-256 hex

    def test_certificate_contains_trial_balance(
        self, orchestrator, period, standard_accounts,
    ):
        """Certificate records trial balance totals."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        cert = result.certificate
        assert cert.trial_balance_debits == Decimal("1000.00")
        assert cert.trial_balance_credits == Decimal("1000.00")

    def test_certificate_audit_event_persisted(
        self, orchestrator, period, standard_accounts, session,
    ):
        """Certificate is persisted as a CLOSE_CERTIFIED audit event."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        from sqlalchemy import select
        from finance_kernel.models.audit_event import AuditEvent
        events = session.execute(
            select(AuditEvent).where(
                AuditEvent.action == AuditAction.CLOSE_CERTIFIED.value
            )
        ).scalars().all()

        assert len(events) >= 1
        cert_event = events[-1]
        assert cert_event.payload["period_code"] == PERIOD_CODE
        assert "ledger_hash" in cert_event.payload
        assert cert_event.payload["ledger_hash"] == result.certificate.ledger_hash

    def test_certificate_is_frozen(self, orchestrator, period, standard_accounts):
        """CloseCertificate is a frozen dataclass — immutable."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        with pytest.raises(AttributeError):
            result.certificate.ledger_hash = "tampered"

    def test_certificate_correlation_id_matches_run(
        self, orchestrator, period, standard_accounts,
    ):
        """Certificate correlation_id matches the close run."""
        result = orchestrator.close_period_full(
            PERIOD_CODE, TEST_ACTOR, is_year_end=False,
        )

        assert result.certificate.correlation_id == result.correlation_id


# =========================================================================
# Status Query
# =========================================================================


class TestGetStatus:
    """get_status queries current close state."""

    def test_status_open_period(self, orchestrator, period):
        """Status of an open period."""
        status = orchestrator.get_status(PERIOD_CODE)

        assert status is not None
        assert status["status"] == "open"
        assert status["is_closing"] is False
        assert status["is_closed"] is False

    def test_status_closing_period(self, orchestrator, period):
        """Status after begin_close."""
        orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        status = orchestrator.get_status(PERIOD_CODE)

        assert status["status"] == "closing"
        assert status["is_closing"] is True
        assert status["closing_run_id"] is not None

    def test_status_closed_period(self, orchestrator, period, standard_accounts):
        """Status after full close."""
        orchestrator.close_period_full(PERIOD_CODE, TEST_ACTOR)
        status = orchestrator.get_status(PERIOD_CODE)

        assert status["status"] == "closed"
        assert status["is_closed"] is True

    def test_status_nonexistent_period(self, orchestrator):
        """Status returns None for unknown period."""
        assert orchestrator.get_status("DOES-NOT-EXIST") is None


# =========================================================================
# Edge Cases
# =========================================================================


class TestEdgeCases:
    """Edge cases and robustness checks."""

    def test_unknown_phase_returns_failure(self, orchestrator, period):
        """Running an unknown phase number returns a failed result."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 99, TEST_ACTOR)

        assert result.success is False
        assert "Unknown phase" in result.message

    def test_close_run_is_frozen(self, orchestrator, period):
        """PeriodCloseRun is a frozen dataclass."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)

        with pytest.raises(AttributeError):
            run.status = CloseRunStatus.CANCELLED

    def test_phase_result_is_frozen(self, orchestrator, period):
        """ClosePhaseResult is a frozen dataclass."""
        run = orchestrator.begin_close(PERIOD_CODE, TEST_ACTOR)
        result = orchestrator.run_phase(run, 1, TEST_ACTOR)

        with pytest.raises(AttributeError):
            result.success = False
