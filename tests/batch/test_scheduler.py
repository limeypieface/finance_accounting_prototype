"""
Tests for finance_batch.services.scheduler -- Phase 8.

Validates BatchScheduler: tick() evaluation, schedule firing, next_run_at
updates, start/stop lifecycle, and graceful shutdown (BT-10).

Uses in-memory SQLite with real ORM models for fast unit tests.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance_kernel.db.base import Base
from finance_kernel.domain.clock import DeterministicClock

from finance_batch.domain.types import (
    BatchItemStatus,
    BatchJobStatus,
    ScheduleFrequency,
)
from finance_batch.models.batch import BatchJobModel, JobScheduleModel
from finance_batch.services.executor import BatchExecutor
from finance_batch.services.scheduler import BatchScheduler
from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTaskResult,
    TaskRegistry,
)


# =============================================================================
# Test task implementations
# =============================================================================


class SchedulerTestTask:
    """Simple task for scheduler testing -- always succeeds."""

    @property
    def task_type(self) -> str:
        return "test.scheduler_task"

    @property
    def description(self) -> str:
        return "Scheduler test task"

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
            status=BatchItemStatus.SUCCEEDED,
            result_data={"processed": True},
        )


class FailingSchedulerTask:
    """Task that always fails, for testing last_run_status."""

    @property
    def task_type(self) -> str:
        return "test.failing_task"

    @property
    def description(self) -> str:
        return "Always fails"

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
            error_code="ALWAYS_FAIL",
            error_message="This always fails",
        )


# =============================================================================
# Fixtures
# =============================================================================


def _make_registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(SchedulerTestTask())
    registry.register(FailingSchedulerTask())
    return registry


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def db_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def clock():
    # Use naive datetimes for SQLite compatibility (SQLite strips tzinfo)
    return DeterministicClock(
        fixed_time=datetime(2026, 2, 1, 12, 0, 0),
    )


@pytest.fixture
def registry():
    return _make_registry()


@pytest.fixture
def scheduler(session_factory, registry, clock):
    def executor_factory(session: Session) -> BatchExecutor:
        return BatchExecutor(
            session=session,
            task_registry=registry,
            clock=clock,
        )

    return BatchScheduler(
        session_factory=session_factory,
        executor_factory=executor_factory,
        clock=clock,
        tick_interval_seconds=1,
    )


def _create_due_schedule(
    session: Session,
    task_type: str = "test.scheduler_task",
    frequency: str = "daily",
    next_run_at: datetime | None = None,
    is_active: bool = True,
) -> JobScheduleModel:
    """Helper to create a schedule that should fire.

    Uses naive datetimes for SQLite compatibility.
    """
    model = JobScheduleModel(
        id=uuid4(),
        job_name=f"Test Schedule {uuid4().hex[:6]}",
        task_type=task_type,
        frequency=frequency,
        is_active=is_active,
        next_run_at=next_run_at or datetime(2026, 2, 1, 11, 0, 0),
        max_retries=3,
        created_by_id=uuid4(),
        updated_by_id=None,
    )
    session.add(model)
    session.commit()
    return model


# =============================================================================
# tick() -- basic evaluation
# =============================================================================


class TestTickBasic:
    def test_tick_no_schedules_returns_zero(self, scheduler):
        assert scheduler.tick() == 0

    def test_tick_fires_due_schedule(self, scheduler, db_session):
        _create_due_schedule(db_session)
        fired = scheduler.tick()
        assert fired == 1

    def test_tick_fires_multiple_due_schedules(self, scheduler, db_session):
        _create_due_schedule(db_session)
        _create_due_schedule(db_session)
        fired = scheduler.tick()
        assert fired == 2

    def test_tick_skips_inactive_schedule(self, scheduler, db_session):
        _create_due_schedule(db_session, is_active=False)
        fired = scheduler.tick()
        assert fired == 0

    def test_tick_skips_future_schedule(self, scheduler, db_session):
        future = datetime(2026, 3, 1, 12, 0, 0)
        _create_due_schedule(db_session, next_run_at=future)
        fired = scheduler.tick()
        assert fired == 0

    def test_tick_fires_past_due_schedule(self, scheduler, db_session):
        past = datetime(2026, 1, 1, 12, 0, 0)
        _create_due_schedule(db_session, next_run_at=past)
        fired = scheduler.tick()
        assert fired == 1


# =============================================================================
# tick() -- schedule updates
# =============================================================================


class TestTickUpdates:
    def test_tick_updates_last_run_at(self, scheduler, db_session, clock):
        sched = _create_due_schedule(db_session)
        scheduler.tick()

        db_session.refresh(sched)
        assert sched.last_run_at == clock.now()

    def test_tick_updates_last_run_status_completed(self, scheduler, db_session):
        sched = _create_due_schedule(db_session)
        scheduler.tick()

        db_session.refresh(sched)
        assert sched.last_run_status == BatchJobStatus.COMPLETED.value

    def test_tick_updates_last_run_status_failed(self, scheduler, db_session):
        sched = _create_due_schedule(db_session, task_type="test.failing_task")
        scheduler.tick()

        db_session.refresh(sched)
        assert sched.last_run_status == BatchJobStatus.FAILED.value

    def test_tick_updates_next_run_at(self, scheduler, db_session):
        sched = _create_due_schedule(db_session, frequency="daily")
        old_next = sched.next_run_at
        scheduler.tick()

        db_session.refresh(sched)
        # next_run_at should be updated to a future time
        assert sched.next_run_at is not None
        assert sched.next_run_at != old_next


# =============================================================================
# tick() -- idempotency key uniqueness
# =============================================================================


class TestTickIdempotency:
    def test_tick_creates_unique_job_per_schedule(self, scheduler, db_session, engine):
        _create_due_schedule(db_session)
        scheduler.tick()

        # Check a BatchJobModel was created
        sess2 = sessionmaker(bind=engine)()
        jobs = sess2.query(BatchJobModel).all()
        assert len(jobs) == 1
        assert jobs[0].idempotency_key.startswith("schedule-")
        sess2.close()


# =============================================================================
# start() / stop() lifecycle
# =============================================================================


class TestLifecycle:
    def test_start_creates_background_thread(self, scheduler):
        scheduler.start()
        assert scheduler.is_running is True
        scheduler.stop(timeout=2.0)

    def test_stop_terminates_thread(self, scheduler):
        scheduler.start()
        assert scheduler.is_running is True

        scheduler.stop(timeout=2.0)
        assert scheduler.is_running is False

    def test_double_start_is_noop(self, scheduler):
        scheduler.start()
        thread1 = scheduler._thread

        scheduler.start()  # Should not create new thread
        assert scheduler._thread is thread1

        scheduler.stop(timeout=2.0)

    def test_stop_without_start_is_safe(self, scheduler):
        # Should not raise
        scheduler.stop(timeout=1.0)
        assert scheduler.is_running is False
