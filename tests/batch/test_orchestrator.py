"""
Tests for finance_batch.orchestrator -- Phase 10.

Validates BatchOrchestrator DI container: from_session factory, create_executor,
create_scheduler, property accessors, and default task registry wiring.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance_kernel.db.base import Base
from finance_kernel.domain.clock import DeterministicClock

from finance_batch.domain.types import BatchItemStatus, BatchJobStatus
from finance_batch.orchestrator import BatchOrchestrator, _default_task_registry
from finance_batch.services.executor import BatchExecutor
from finance_batch.services.scheduler import BatchScheduler
from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTaskResult,
    TaskRegistry,
)


# =============================================================================
# Fixtures
# =============================================================================


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
    return DeterministicClock(
        fixed_time=datetime(2026, 2, 1, 12, 0, 0),
    )


# =============================================================================
# Default task registry
# =============================================================================


class TestDefaultTaskRegistry:
    def test_has_all_10_tasks(self):
        registry = _default_task_registry()
        assert len(registry) == 10

    def test_contains_all_expected_task_types(self):
        registry = _default_task_registry()
        expected = [
            "ap.payment_run",
            "ap.invoice_match",
            "ar.dunning_letters",
            "ar.small_balance_write_off",
            "assets.mass_depreciation",
            "cash.auto_reconcile",
            "credit_loss.ecl_calculation",
            "gl.recurring_entries",
            "gl.period_end_revaluation",
            "payroll.labor_allocation",
        ]
        for task_type in expected:
            assert task_type in registry, f"Missing task: {task_type}"


# =============================================================================
# from_session factory
# =============================================================================


class TestFromSession:
    def test_creates_orchestrator(self, db_session, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        assert isinstance(orch, BatchOrchestrator)

    def test_uses_provided_clock(self, db_session, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        assert orch.clock is clock

    def test_uses_provided_actor_id(self, db_session, clock):
        actor = uuid4()
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
            actor_id=actor,
        )
        assert orch.actor_id == actor

    def test_default_registry_has_10_tasks(self, db_session, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        assert len(orch.task_registry) == 10

    def test_custom_registry(self, db_session, clock):
        custom = TaskRegistry()
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
            task_registry=custom,
        )
        assert len(orch.task_registry) == 0


# =============================================================================
# create_executor
# =============================================================================


class TestCreateExecutor:
    def test_returns_executor(self, db_session, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        executor = orch.create_executor()
        assert isinstance(executor, BatchExecutor)

    def test_executor_can_submit_job(self, db_session, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        executor = orch.create_executor()

        job = executor.submit_job(
            job_name="Test Job",
            task_type="credit_loss.ecl_calculation",
            idempotency_key="orch-test-001",
            actor_id=uuid4(),
            parameters={"segments": [{"segment_name": "test"}]},
        )
        assert job.status == BatchJobStatus.PENDING

    def test_executor_with_alternate_session(self, engine, clock):
        SessionLocal = sessionmaker(bind=engine)
        session1 = SessionLocal()
        session2 = SessionLocal()

        orch = BatchOrchestrator.from_session(
            session=session1,
            clock=clock,
        )
        executor = orch.create_executor(session=session2)
        assert isinstance(executor, BatchExecutor)

        session1.close()
        session2.close()


# =============================================================================
# create_scheduler
# =============================================================================


class TestCreateScheduler:
    def test_returns_scheduler(self, db_session, session_factory, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        scheduler = orch.create_scheduler(
            session_factory=session_factory,
            tick_interval_seconds=5,
        )
        assert isinstance(scheduler, BatchScheduler)

    def test_scheduler_tick_with_no_schedules(self, db_session, session_factory, clock):
        orch = BatchOrchestrator.from_session(
            session=db_session,
            clock=clock,
        )
        scheduler = orch.create_scheduler(
            session_factory=session_factory,
        )
        # Should return 0 (no schedules to fire)
        assert scheduler.tick() == 0


# =============================================================================
# Properties
# =============================================================================


class TestProperties:
    def test_session_property(self, db_session, clock):
        orch = BatchOrchestrator.from_session(session=db_session, clock=clock)
        assert orch.session is db_session

    def test_clock_property(self, db_session, clock):
        orch = BatchOrchestrator.from_session(session=db_session, clock=clock)
        assert orch.clock is clock

    def test_task_registry_property(self, db_session, clock):
        orch = BatchOrchestrator.from_session(session=db_session, clock=clock)
        assert isinstance(orch.task_registry, TaskRegistry)

    def test_actor_id_property(self, db_session, clock):
        actor = uuid4()
        orch = BatchOrchestrator.from_session(
            session=db_session, clock=clock, actor_id=actor,
        )
        assert orch.actor_id == actor
