"""
Integration tests for finance_batch -- Phase 9.

End-to-end tests validating the full batch execution flow: submit, execute,
query results. Tests SAVEPOINT isolation, idempotent rerun, and multi-task
registry integration.

Uses in-memory SQLite for fast execution (no PostgreSQL required).
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance_kernel.db.base import Base
from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.exceptions import BatchIdempotencyError

from finance_batch.domain.types import (
    BatchItemStatus,
    BatchJobStatus,
    ScheduleFrequency,
)
from finance_batch.models.batch import (
    BatchItemModel,
    BatchJobModel,
    JobScheduleModel,
)
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


class DepreciationTask:
    """Simulates mass depreciation batch task."""

    @property
    def task_type(self) -> str:
        return "assets.mass_depreciation"

    @property
    def description(self) -> str:
        return "Mass depreciation calculation"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        asset_ids = parameters.get("asset_ids", [])
        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=aid,
                payload={"asset_id": aid},
            )
            for i, aid in enumerate(asset_ids)
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={
                "asset_id": item.payload["asset_id"],
                "depreciation_amount": "1000.00",
            },
        )


class PaymentRunTask:
    """Simulates AP payment run with mixed results."""

    @property
    def task_type(self) -> str:
        return "ap.payment_run"

    @property
    def description(self) -> str:
        return "Execute payment run"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        invoice_ids = parameters.get("invoice_ids", [])
        return tuple(
            BatchItemInput(
                item_index=i,
                item_key=iid,
                payload={"invoice_id": iid},
            )
            for i, iid in enumerate(invoice_ids)
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        # Simulate: first invoice fails, rest succeed
        if item.item_index == 0 and parameters.get("fail_first", False):
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="INSUFFICIENT_FUNDS",
                error_message="Bank account balance too low",
            )
        return BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={"invoice_id": item.payload["invoice_id"]},
        )


class ExplodingTask:
    """Task that raises an unhandled exception on specific items."""

    @property
    def task_type(self) -> str:
        return "test.exploding"

    @property
    def description(self) -> str:
        return "Raises exceptions"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return (
            BatchItemInput(item_index=0, item_key="bomb"),
            BatchItemInput(item_index=1, item_key="safe"),
            BatchItemInput(item_index=2, item_key="safe-2"),
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        if item.item_key == "bomb":
            raise RuntimeError("Kaboom!")
        return BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={"key": item.item_key},
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
def registry():
    reg = TaskRegistry()
    reg.register(DepreciationTask())
    reg.register(PaymentRunTask())
    reg.register(ExplodingTask())
    return reg


@pytest.fixture
def executor(db_session, registry, clock):
    return BatchExecutor(
        session=db_session,
        task_registry=registry,
        clock=clock,
    )


# =============================================================================
# End-to-end: mass depreciation
# =============================================================================


class TestMassDepreciationE2E:
    def test_full_depreciation_run(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Monthly Depreciation Jan-26",
            task_type="assets.mass_depreciation",
            idempotency_key="depreciation-2026-01",
            actor_id=actor,
            parameters={"asset_ids": ["A001", "A002", "A003"]},
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.COMPLETED
        assert result.total_items == 3
        assert result.succeeded == 3
        assert result.failed == 0

        # Verify item results
        items = executor.get_job_items(job.job_id)
        assert len(items) == 3
        assert all(i.status == BatchItemStatus.SUCCEEDED for i in items)
        assert items[0].result_data["depreciation_amount"] == "1000.00"

    def test_empty_asset_list(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Empty Run",
            task_type="assets.mass_depreciation",
            idempotency_key="depreciation-empty",
            actor_id=actor,
            parameters={"asset_ids": []},
        )

        result = executor.execute_job(job.job_id, actor)
        assert result.status == BatchJobStatus.COMPLETED
        assert result.total_items == 0


# =============================================================================
# End-to-end: payment run with partial failure
# =============================================================================


class TestPaymentRunE2E:
    def test_payment_run_partial_failure(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="AP Payment Run Feb-26",
            task_type="ap.payment_run",
            idempotency_key="payment-run-2026-02",
            actor_id=actor,
            parameters={
                "invoice_ids": ["INV-001", "INV-002", "INV-003"],
                "fail_first": True,
            },
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.PARTIALLY_COMPLETED
        assert result.succeeded == 2
        assert result.failed == 1

        # Verify failed item
        items = executor.get_job_items(job.job_id)
        failed = [i for i in items if i.status == BatchItemStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].error_code == "INSUFFICIENT_FUNDS"
        assert failed[0].item_key == "INV-001"


# =============================================================================
# BT-1: SAVEPOINT isolation (failed item doesn't abort batch)
# =============================================================================


class TestSavepointIsolation:
    def test_exception_in_item_continues_batch(self, executor):
        """BT-1: One item's exception doesn't abort the rest."""
        actor = uuid4()
        job = executor.submit_job(
            job_name="Explosion Test",
            task_type="test.exploding",
            idempotency_key="explode-001",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        # "bomb" failed, "safe" and "safe-2" succeeded
        assert result.status == BatchJobStatus.PARTIALLY_COMPLETED
        assert result.succeeded == 2
        assert result.failed == 1

        items = executor.get_job_items(job.job_id)
        bomb = [i for i in items if i.item_key == "bomb"]
        assert len(bomb) == 1
        assert bomb[0].status == BatchItemStatus.FAILED
        assert bomb[0].error_code == "UNHANDLED_EXCEPTION"
        assert "Kaboom" in bomb[0].error_message


# =============================================================================
# BT-2: Idempotent rerun
# =============================================================================


class TestIdempotentRerun:
    def test_same_key_rejected(self, executor):
        """BT-2: Duplicate idempotency_key raises BatchIdempotencyError."""
        actor = uuid4()
        executor.submit_job(
            job_name="First Run",
            task_type="assets.mass_depreciation",
            idempotency_key="unique-key-001",
            actor_id=actor,
        )

        with pytest.raises(BatchIdempotencyError):
            executor.submit_job(
                job_name="Second Run",
                task_type="assets.mass_depreciation",
                idempotency_key="unique-key-001",
                actor_id=actor,
            )

    def test_different_keys_allowed(self, executor):
        actor = uuid4()
        j1 = executor.submit_job(
            job_name="Run 1",
            task_type="assets.mass_depreciation",
            idempotency_key="key-001",
            actor_id=actor,
        )
        j2 = executor.submit_job(
            job_name="Run 2",
            task_type="assets.mass_depreciation",
            idempotency_key="key-002",
            actor_id=actor,
        )
        assert j1.job_id != j2.job_id


# =============================================================================
# Scheduler + Executor integration
# =============================================================================


class TestSchedulerExecutorIntegration:
    def test_scheduler_tick_fires_and_executes(self, engine, registry, clock):
        """Scheduler tick -> fires due schedule -> executor runs job."""
        SessionLocal = sessionmaker(bind=engine)

        def executor_factory(session: Session) -> BatchExecutor:
            return BatchExecutor(
                session=session,
                task_registry=registry,
                clock=clock,
            )

        scheduler = BatchScheduler(
            session_factory=SessionLocal,
            executor_factory=executor_factory,
            clock=clock,
            tick_interval_seconds=1,
        )

        # Create a due schedule
        setup_session = SessionLocal()
        sched = JobScheduleModel(
            id=uuid4(),
            job_name="Nightly Depreciation",
            task_type="assets.mass_depreciation",
            frequency="daily",
            is_active=True,
            next_run_at=datetime(2026, 2, 1, 11, 0, 0),
            parameters={"asset_ids": ["A001", "A002"]},
            max_retries=3,
            created_by_id=uuid4(),
            updated_by_id=None,
        )
        setup_session.add(sched)
        setup_session.commit()
        setup_session.close()

        # Tick
        fired = scheduler.tick()
        assert fired == 1

        # Verify job was created and executed
        verify_session = SessionLocal()
        jobs = verify_session.query(BatchJobModel).all()
        assert len(jobs) == 1
        assert jobs[0].status == BatchJobStatus.COMPLETED.value
        assert jobs[0].total_items == 2
        assert jobs[0].succeeded_items == 2

        items = verify_session.query(BatchItemModel).all()
        assert len(items) == 2
        assert all(i.status == BatchItemStatus.SUCCEEDED.value for i in items)
        verify_session.close()


# =============================================================================
# Multi-task registry integration
# =============================================================================


class TestMultiTaskRegistry:
    def test_multiple_task_types_in_same_registry(self, executor):
        actor = uuid4()

        # Submit and run depreciation
        dep_job = executor.submit_job(
            job_name="Depreciation",
            task_type="assets.mass_depreciation",
            idempotency_key="multi-dep",
            actor_id=actor,
            parameters={"asset_ids": ["A001"]},
        )
        dep_result = executor.execute_job(dep_job.job_id, actor)
        assert dep_result.status == BatchJobStatus.COMPLETED

        # Submit and run payment
        pay_job = executor.submit_job(
            job_name="Payment",
            task_type="ap.payment_run",
            idempotency_key="multi-pay",
            actor_id=actor,
            parameters={"invoice_ids": ["INV-001"]},
        )
        pay_result = executor.execute_job(pay_job.job_id, actor)
        assert pay_result.status == BatchJobStatus.COMPLETED

    def test_job_status_query_after_multiple_runs(self, executor):
        actor = uuid4()

        j1 = executor.submit_job(
            job_name="Job 1",
            task_type="assets.mass_depreciation",
            idempotency_key="query-j1",
            actor_id=actor,
            parameters={"asset_ids": ["A001"]},
        )
        executor.execute_job(j1.job_id, actor)

        j2 = executor.submit_job(
            job_name="Job 2",
            task_type="ap.payment_run",
            idempotency_key="query-j2",
            actor_id=actor,
            parameters={"invoice_ids": ["INV-001", "INV-002"], "fail_first": True},
        )
        executor.execute_job(j2.job_id, actor)

        job1 = executor.get_job(j1.job_id)
        job2 = executor.get_job(j2.job_id)

        assert job1.status == BatchJobStatus.COMPLETED
        assert job2.status == BatchJobStatus.PARTIALLY_COMPLETED
