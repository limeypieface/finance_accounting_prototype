"""
Tests for batch audit trail (BT-5) -- Phase 9.

Validates that BatchExecutor creates AuditEvents for job start, complete,
fail, cancel, and item failure.  Uses real AuditorService with in-memory
SQLite.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from finance_kernel.db.base import Base
from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.sequence_service import SequenceService

from finance_batch.domain.types import BatchItemStatus, BatchJobStatus
from finance_batch.services.executor import BatchExecutor
from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTaskResult,
    TaskRegistry,
)


# =============================================================================
# Test tasks
# =============================================================================


class AuditSuccessTask:
    @property
    def task_type(self) -> str:
        return "test.audit_success"

    @property
    def description(self) -> str:
        return "Audit success task"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return (
            BatchItemInput(item_index=0, item_key="item-000"),
            BatchItemInput(item_index=1, item_key="item-001"),
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={"ok": True},
        )


class AuditAllFailTask:
    @property
    def task_type(self) -> str:
        return "test.audit_all_fail"

    @property
    def description(self) -> str:
        return "All items fail"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return (
            BatchItemInput(item_index=0, item_key="item-000"),
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(
            status=BatchItemStatus.FAILED,
            error_code="AUDIT_FAIL",
            error_message="Deliberate failure",
        )


class AuditExceptionTask:
    @property
    def task_type(self) -> str:
        return "test.audit_exception"

    @property
    def description(self) -> str:
        return "Raises exception"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return (
            BatchItemInput(item_index=0, item_key="bomb"),
            BatchItemInput(item_index=1, item_key="safe"),
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        if item.item_key == "bomb":
            raise RuntimeError("Boom!")
        return BatchTaskResult(status=BatchItemStatus.SUCCEEDED)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db_session(engine):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def clock():
    return DeterministicClock(
        fixed_time=datetime(2026, 2, 1, 12, 0, 0),
    )


@pytest.fixture
def auditor(db_session, clock):
    return AuditorService(session=db_session, clock=clock)


@pytest.fixture
def registry():
    reg = TaskRegistry()
    reg.register(AuditSuccessTask())
    reg.register(AuditAllFailTask())
    reg.register(AuditExceptionTask())
    return reg


@pytest.fixture
def executor(db_session, registry, clock, auditor):
    return BatchExecutor(
        session=db_session,
        task_registry=registry,
        clock=clock,
        auditor_service=auditor,
    )


def _get_audit_events(session: Session) -> list[AuditEvent]:
    return session.execute(
        select(AuditEvent).order_by(AuditEvent.seq)
    ).scalars().all()


# =============================================================================
# BT-5: Audit events for job lifecycle
# =============================================================================


class TestAuditJobStarted:
    def test_job_start_creates_audit_event(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Audit Test",
            task_type="test.audit_success",
            idempotency_key="audit-start-001",
            actor_id=actor,
        )
        executor.execute_job(job.job_id, actor)

        events = _get_audit_events(db_session)
        start_events = [
            e for e in events
            if e.action == AuditAction.BATCH_JOB_STARTED.value
        ]
        assert len(start_events) >= 1


class TestAuditJobCompleted:
    def test_successful_job_creates_completed_event(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Success Audit",
            task_type="test.audit_success",
            idempotency_key="audit-complete-001",
            actor_id=actor,
        )
        executor.execute_job(job.job_id, actor)

        events = _get_audit_events(db_session)
        complete_events = [
            e for e in events
            if e.action == AuditAction.BATCH_JOB_COMPLETED.value
        ]
        assert len(complete_events) == 1


class TestAuditJobFailed:
    def test_all_fail_creates_failed_event(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Fail Audit",
            task_type="test.audit_all_fail",
            idempotency_key="audit-fail-001",
            actor_id=actor,
        )
        executor.execute_job(job.job_id, actor)

        events = _get_audit_events(db_session)
        fail_events = [
            e for e in events
            if e.action == AuditAction.BATCH_JOB_FAILED.value
        ]
        assert len(fail_events) == 1


class TestAuditItemFailed:
    def test_item_failure_creates_audit_event(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Item Fail Audit",
            task_type="test.audit_all_fail",
            idempotency_key="audit-item-fail-001",
            actor_id=actor,
        )
        executor.execute_job(job.job_id, actor)

        events = _get_audit_events(db_session)
        item_fail_events = [
            e for e in events
            if e.action == AuditAction.BATCH_ITEM_FAILED.value
        ]
        assert len(item_fail_events) == 1

    def test_exception_item_creates_audit_event(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Exception Audit",
            task_type="test.audit_exception",
            idempotency_key="audit-exception-001",
            actor_id=actor,
        )
        executor.execute_job(job.job_id, actor)

        events = _get_audit_events(db_session)
        item_fail_events = [
            e for e in events
            if e.action == AuditAction.BATCH_ITEM_FAILED.value
        ]
        # "bomb" item should have an audit event
        assert len(item_fail_events) == 1


class TestAuditJobCancelled:
    def test_cancel_creates_audit_event(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Cancel Audit",
            task_type="test.audit_success",
            idempotency_key="audit-cancel-001",
            actor_id=actor,
        )
        executor.cancel_job(job.job_id, "No longer needed", actor)

        events = _get_audit_events(db_session)
        cancel_events = [
            e for e in events
            if e.action == AuditAction.BATCH_JOB_CANCELLED.value
        ]
        assert len(cancel_events) == 1


class TestAuditCompleteness:
    def test_full_lifecycle_audit_trail(self, executor, db_session):
        """Verify complete audit trail for a job with mixed results."""
        actor = uuid4()
        job = executor.submit_job(
            job_name="Full Lifecycle",
            task_type="test.audit_exception",
            idempotency_key="audit-lifecycle-001",
            actor_id=actor,
        )
        result = executor.execute_job(job.job_id, actor)

        events = _get_audit_events(db_session)
        actions = [e.action for e in events]

        # Should have: at least 1 start, 1 item_failed, 1 completed
        assert AuditAction.BATCH_JOB_STARTED.value in actions
        assert AuditAction.BATCH_ITEM_FAILED.value in actions
        assert AuditAction.BATCH_JOB_COMPLETED.value in actions
