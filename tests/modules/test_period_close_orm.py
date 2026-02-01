"""ORM round-trip tests for Period Close models (finance_services layer).

Covers:
- PeriodCloseRunModel
- CloseCertificateModel

Tests verify persistence round-trips (create, flush, query), unique
constraints, and field defaults.

Note: These models reside in ``finance_services.orm``, not
``finance_modules/``.  They have no FK constraints to kernel tables --
fields like ``started_by``, ``closed_by``, and ``certificate_id`` are
bare UUID columns.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_services.orm import (
    PeriodCloseRunModel,
    CloseCertificateModel,
)


# ---------------------------------------------------------------------------
# PeriodCloseRunModel
# ---------------------------------------------------------------------------


class TestPeriodCloseRunModelORM:
    """Round-trip persistence tests for PeriodCloseRunModel."""

    def test_create_and_query(self, session, test_actor_id):
        started_by_id = uuid4()
        cert_id = uuid4()
        started_at = datetime(2024, 6, 30, 23, 0, 0, tzinfo=timezone.utc)
        completed_at = datetime(2024, 6, 30, 23, 45, 0, tzinfo=timezone.utc)

        run = PeriodCloseRunModel(
            period_code="2024-06",
            fiscal_year=2024,
            is_year_end=False,
            status="completed",
            current_phase=5,
            correlation_id="corr-2024-06-001",
            started_at=started_at,
            started_by=started_by_id,
            completed_at=completed_at,
            ledger_hash="abc123def456" * 8,
            certificate_id=cert_id,
            phases_completed=5,
            phases_skipped=0,
            failure_message=None,
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        queried = session.get(PeriodCloseRunModel, run.id)
        assert queried is not None
        assert queried.period_code == "2024-06"
        assert queried.fiscal_year == 2024
        assert queried.is_year_end is False
        assert queried.status == "completed"
        assert queried.current_phase == 5
        assert queried.correlation_id == "corr-2024-06-001"
        assert queried.started_at == started_at
        assert queried.started_by == started_by_id
        assert queried.completed_at == completed_at
        assert queried.ledger_hash == "abc123def456" * 8
        assert queried.certificate_id == cert_id
        assert queried.phases_completed == 5
        assert queried.phases_skipped == 0
        assert queried.failure_message is None

    def test_create_with_defaults(self, session, test_actor_id):
        run = PeriodCloseRunModel(
            period_code="2024-07",
            fiscal_year=2024,
            correlation_id="corr-2024-07-001",
            started_at=datetime(2024, 7, 31, 22, 0, 0, tzinfo=timezone.utc),
            started_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        queried = session.get(PeriodCloseRunModel, run.id)
        assert queried.is_year_end is False
        assert queried.status == "in_progress"
        assert queried.current_phase == 0
        assert queried.completed_at is None
        assert queried.ledger_hash is None
        assert queried.certificate_id is None
        assert queried.phases_completed == 0
        assert queried.phases_skipped == 0
        assert queried.failure_message is None

    def test_create_failed_run(self, session, test_actor_id):
        run = PeriodCloseRunModel(
            period_code="2024-08",
            fiscal_year=2024,
            correlation_id="corr-2024-08-fail",
            started_at=datetime(2024, 8, 31, 23, 0, 0, tzinfo=timezone.utc),
            started_by=uuid4(),
            status="failed",
            current_phase=3,
            phases_completed=2,
            phases_skipped=1,
            failure_message="Subledger AP failed to reconcile: delta = $0.03",
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        queried = session.get(PeriodCloseRunModel, run.id)
        assert queried.status == "failed"
        assert queried.failure_message == "Subledger AP failed to reconcile: delta = $0.03"
        assert queried.phases_completed == 2
        assert queried.phases_skipped == 1

    def test_unique_constraint_correlation_id(self, session, test_actor_id):
        """correlation_id must be unique."""
        run1 = PeriodCloseRunModel(
            period_code="2024-09",
            fiscal_year=2024,
            correlation_id="corr-dup-test",
            started_at=datetime(2024, 9, 30, 23, 0, 0, tzinfo=timezone.utc),
            started_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(run1)
        session.flush()

        run2 = PeriodCloseRunModel(
            period_code="2024-10",
            fiscal_year=2024,
            correlation_id="corr-dup-test",
            started_at=datetime(2024, 10, 31, 23, 0, 0, tzinfo=timezone.utc),
            started_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(run2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_year_end_close_run(self, session, test_actor_id):
        run = PeriodCloseRunModel(
            period_code="2024-12",
            fiscal_year=2024,
            is_year_end=True,
            correlation_id="corr-2024-12-ye",
            started_at=datetime(2024, 12, 31, 23, 0, 0, tzinfo=timezone.utc),
            started_by=uuid4(),
            status="completed",
            current_phase=8,
            phases_completed=8,
            ledger_hash="yearendhash" * 10,
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        queried = session.get(PeriodCloseRunModel, run.id)
        assert queried.is_year_end is True
        assert queried.fiscal_year == 2024
        assert queried.period_code == "2024-12"


# ---------------------------------------------------------------------------
# CloseCertificateModel
# ---------------------------------------------------------------------------


class TestCloseCertificateModelORM:
    """Round-trip persistence tests for CloseCertificateModel."""

    def test_create_and_query(self, session, test_actor_id):
        closed_by_id = uuid4()
        approved_by_id = uuid4()
        audit_event_id = uuid4()
        closed_at = datetime(2024, 6, 30, 23, 59, 0, tzinfo=timezone.utc)

        cert = CloseCertificateModel(
            period_code="2024-06",
            closed_at=closed_at,
            closed_by=closed_by_id,
            approved_by=approved_by_id,
            correlation_id="corr-cert-2024-06",
            ledger_hash="certledgerhash" * 8,
            trial_balance_debits=Decimal("1500000.00"),
            trial_balance_credits=Decimal("1500000.00"),
            subledgers_closed_json='["AP", "AR", "GL", "Inventory"]',
            adjustments_posted=3,
            closing_entries_posted=5,
            phases_completed=7,
            phases_skipped=1,
            audit_event_id=audit_event_id,
            created_by_id=test_actor_id,
        )
        session.add(cert)
        session.flush()

        queried = session.get(CloseCertificateModel, cert.id)
        assert queried is not None
        assert queried.period_code == "2024-06"
        assert queried.closed_at == closed_at
        assert queried.closed_by == closed_by_id
        assert queried.approved_by == approved_by_id
        assert queried.correlation_id == "corr-cert-2024-06"
        assert queried.ledger_hash == "certledgerhash" * 8
        assert queried.trial_balance_debits == Decimal("1500000.00")
        assert queried.trial_balance_credits == Decimal("1500000.00")
        assert queried.subledgers_closed_json == '["AP", "AR", "GL", "Inventory"]'
        assert queried.adjustments_posted == 3
        assert queried.closing_entries_posted == 5
        assert queried.phases_completed == 7
        assert queried.phases_skipped == 1
        assert queried.audit_event_id == audit_event_id

    def test_create_with_defaults(self, session, test_actor_id):
        cert = CloseCertificateModel(
            period_code="2024-07",
            closed_at=datetime(2024, 7, 31, 23, 59, 0, tzinfo=timezone.utc),
            closed_by=uuid4(),
            correlation_id="corr-cert-2024-07",
            ledger_hash="defaulthash" * 10,
            created_by_id=test_actor_id,
        )
        session.add(cert)
        session.flush()

        queried = session.get(CloseCertificateModel, cert.id)
        assert queried.approved_by is None
        assert queried.trial_balance_debits == Decimal("0")
        assert queried.trial_balance_credits == Decimal("0")
        assert queried.subledgers_closed_json is None
        assert queried.adjustments_posted == 0
        assert queried.closing_entries_posted == 0
        assert queried.phases_completed == 0
        assert queried.phases_skipped == 0
        assert queried.audit_event_id is None

    def test_unique_constraint_period_correlation(self, session, test_actor_id):
        """(period_code, correlation_id) must be unique."""
        cert1 = CloseCertificateModel(
            period_code="2024-09",
            closed_at=datetime(2024, 9, 30, 23, 59, 0, tzinfo=timezone.utc),
            closed_by=uuid4(),
            correlation_id="corr-cert-dup",
            ledger_hash="hash1" * 20,
            created_by_id=test_actor_id,
        )
        session.add(cert1)
        session.flush()

        cert2 = CloseCertificateModel(
            period_code="2024-09",
            closed_at=datetime(2024, 9, 30, 23, 59, 59, tzinfo=timezone.utc),
            closed_by=uuid4(),
            correlation_id="corr-cert-dup",
            ledger_hash="hash2" * 20,
            created_by_id=test_actor_id,
        )
        session.add(cert2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_period_different_correlation_allowed(self, session, test_actor_id):
        """Same period_code with different correlation_id is allowed."""
        cert1 = CloseCertificateModel(
            period_code="2024-10",
            closed_at=datetime(2024, 10, 31, 23, 59, 0, tzinfo=timezone.utc),
            closed_by=uuid4(),
            correlation_id="corr-cert-10-A",
            ledger_hash="hashA" * 20,
            created_by_id=test_actor_id,
        )
        cert2 = CloseCertificateModel(
            period_code="2024-10",
            closed_at=datetime(2024, 10, 31, 23, 59, 30, tzinfo=timezone.utc),
            closed_by=uuid4(),
            correlation_id="corr-cert-10-B",
            ledger_hash="hashB" * 20,
            created_by_id=test_actor_id,
        )
        session.add_all([cert1, cert2])
        session.flush()

        queried1 = session.get(CloseCertificateModel, cert1.id)
        queried2 = session.get(CloseCertificateModel, cert2.id)
        assert queried1 is not None
        assert queried2 is not None
        assert queried1.correlation_id != queried2.correlation_id

    def test_balanced_trial_balance(self, session, test_actor_id):
        """Certificate can record balanced trial balance totals."""
        cert = CloseCertificateModel(
            period_code="2024-11",
            closed_at=datetime(2024, 11, 30, 23, 59, 0, tzinfo=timezone.utc),
            closed_by=uuid4(),
            correlation_id="corr-cert-2024-11-bal",
            ledger_hash="balancedhash" * 8,
            trial_balance_debits=Decimal("9876543.210000000"),
            trial_balance_credits=Decimal("9876543.210000000"),
            created_by_id=test_actor_id,
        )
        session.add(cert)
        session.flush()

        queried = session.get(CloseCertificateModel, cert.id)
        assert queried.trial_balance_debits == queried.trial_balance_credits
        assert queried.trial_balance_debits == Decimal("9876543.210000000")
