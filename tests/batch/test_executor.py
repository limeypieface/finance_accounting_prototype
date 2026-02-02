"""
Tests for finance_batch.services.executor -- Phase 4.

Validates BatchExecutor: submit_job, execute_job (SAVEPOINT-per-item),
cancel_job, get_job, get_job_items, idempotency, concurrency guard.

Uses in-memory SQLite for fast unit tests (no PostgreSQL required).
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance_kernel.db.base import Base
from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.exceptions import (
    BatchAlreadyRunningError,
    BatchIdempotencyError,
    BatchJobNotFoundError,
    TaskNotRegisteredError,
)

from finance_batch.domain.types import (
    BatchItemStatus,
    BatchJobStatus,
)
from finance_batch.models.batch import BatchItemModel, BatchJobModel
from finance_batch.services.executor import BatchExecutor
from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTask,
    BatchTaskResult,
    TaskRegistry,
)


# =============================================================================
# Test fixtures
# =============================================================================


class SuccessTask:
    """Task where all items succeed."""

    @property
    def task_type(self) -> str:
        return "test.success"

    @property
    def description(self) -> str:
        return "All items succeed"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        count = parameters.get("item_count", 3)
        return tuple(
            BatchItemInput(item_index=i, item_key=f"item-{i:03d}")
            for i in range(count)
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={"processed": item.item_key},
        )


class PartialFailTask:
    """Task where even-indexed items fail."""

    @property
    def task_type(self) -> str:
        return "test.partial_fail"

    @property
    def description(self) -> str:
        return "Even items fail"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return (
            BatchItemInput(item_index=0, item_key="item-000"),
            BatchItemInput(item_index=1, item_key="item-001"),
            BatchItemInput(item_index=2, item_key="item-002"),
        )

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        if item.item_index % 2 == 0:
            return BatchTaskResult(
                status=BatchItemStatus.FAILED,
                error_code="EVEN_INDEX",
                error_message=f"Item {item.item_key} has even index",
            )
        return BatchTaskResult(status=BatchItemStatus.SUCCEEDED)


class AllFailTask:
    """Task where all items fail."""

    @property
    def task_type(self) -> str:
        return "test.all_fail"

    @property
    def description(self) -> str:
        return "All items fail"

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
            status=BatchItemStatus.FAILED,
            error_code="ALWAYS_FAIL",
            error_message="This task always fails",
        )


class ExceptionTask:
    """Task that raises an exception during execute_item."""

    @property
    def task_type(self) -> str:
        return "test.exception"

    @property
    def description(self) -> str:
        return "Raises exceptions"

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
        if item.item_index == 0:
            raise RuntimeError("Unexpected error in item processing")
        return BatchTaskResult(status=BatchItemStatus.SUCCEEDED)


class PrepareFailTask:
    """Task that raises during prepare_items."""

    @property
    def task_type(self) -> str:
        return "test.prepare_fail"

    @property
    def description(self) -> str:
        return "Fails in prepare"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        raise ValueError("Cannot query eligible items")

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(status=BatchItemStatus.SUCCEEDED)


class EmptyTask:
    """Task with no items to process."""

    @property
    def task_type(self) -> str:
        return "test.empty"

    @property
    def description(self) -> str:
        return "Empty batch"

    def prepare_items(
        self, parameters: dict[str, Any], session: Session, as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return ()

    def execute_item(
        self, item: BatchItemInput, parameters: dict[str, Any],
        session: Session, as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(status=BatchItemStatus.SUCCEEDED)


class SkipTask:
    """Task where all items are skipped."""

    @property
    def task_type(self) -> str:
        return "test.skip"

    @property
    def description(self) -> str:
        return "All skipped"

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
        return BatchTaskResult(status=BatchItemStatus.SKIPPED)


def _make_registry() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(SuccessTask())
    registry.register(PartialFailTask())
    registry.register(AllFailTask())
    registry.register(ExceptionTask())
    registry.register(PrepareFailTask())
    registry.register(EmptyTask())
    registry.register(SkipTask())
    return registry


@pytest.fixture
def db_session():
    """In-memory SQLite session for fast unit tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def clock():
    return DeterministicClock(
        fixed_time=datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def registry():
    return _make_registry()


@pytest.fixture
def executor(db_session, registry, clock):
    # No auditor or sequence service for unit tests
    return BatchExecutor(
        session=db_session,
        task_registry=registry,
        clock=clock,
    )


# =============================================================================
# submit_job tests
# =============================================================================


class TestSubmitJob:
    def test_submit_creates_pending_job(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Test Job",
            task_type="test.success",
            idempotency_key="key-001",
            actor_id=actor,
        )

        assert job.job_name == "Test Job"
        assert job.task_type == "test.success"
        assert job.status == BatchJobStatus.PENDING
        assert job.idempotency_key == "key-001"
        assert job.created_by == actor
        assert job.seq is not None

    def test_submit_with_parameters(self, executor):
        params = {"currency": "USD", "effective_date": "2026-01-31"}
        job = executor.submit_job(
            job_name="Test Job",
            task_type="test.success",
            idempotency_key="key-002",
            actor_id=uuid4(),
            parameters=params,
        )
        assert job.parameters == params

    def test_submit_unregistered_task_raises(self, executor):
        with pytest.raises(TaskNotRegisteredError):
            executor.submit_job(
                job_name="Test Job",
                task_type="nonexistent.task",
                idempotency_key="key-003",
                actor_id=uuid4(),
            )

    def test_submit_duplicate_idempotency_key_raises(self, executor):
        actor = uuid4()
        executor.submit_job(
            job_name="Job 1",
            task_type="test.success",
            idempotency_key="dup-key",
            actor_id=actor,
        )

        with pytest.raises(BatchIdempotencyError):
            executor.submit_job(
                job_name="Job 2",
                task_type="test.success",
                idempotency_key="dup-key",
                actor_id=actor,
            )

    def test_submit_with_correlation_id(self, executor):
        job = executor.submit_job(
            job_name="Test Job",
            task_type="test.success",
            idempotency_key="key-004",
            actor_id=uuid4(),
            correlation_id="corr-001",
        )
        assert job.correlation_id == "corr-001"


# =============================================================================
# execute_job tests
# =============================================================================


class TestExecuteJob:
    def test_all_succeed(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Success Job",
            task_type="test.success",
            idempotency_key="exec-001",
            actor_id=actor,
            parameters={"item_count": 3},
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.COMPLETED
        assert result.total_items == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert result.skipped == 0
        assert len(result.item_results) == 3
        assert result.duration_ms >= 0

    def test_partial_failure(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Partial Fail",
            task_type="test.partial_fail",
            idempotency_key="exec-002",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.PARTIALLY_COMPLETED
        assert result.succeeded == 1  # item-001 (odd index)
        assert result.failed == 2  # item-000, item-002 (even indices)

    def test_all_fail(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="All Fail",
            task_type="test.all_fail",
            idempotency_key="exec-003",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.FAILED
        assert result.succeeded == 0
        assert result.failed == 2

    def test_exception_in_execute_item(self, executor):
        """BT-1: Exception in one item doesn't abort the batch."""
        actor = uuid4()
        job = executor.submit_job(
            job_name="Exception Job",
            task_type="test.exception",
            idempotency_key="exec-004",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.PARTIALLY_COMPLETED
        assert result.succeeded == 1
        assert result.failed == 1

        # Failed item has error details
        failed_items = [r for r in result.item_results if r.status == BatchItemStatus.FAILED]
        assert len(failed_items) == 1
        assert failed_items[0].error_code == "UNHANDLED_EXCEPTION"
        assert "Unexpected error" in failed_items[0].error_message

    def test_prepare_items_failure(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Prepare Fail",
            task_type="test.prepare_fail",
            idempotency_key="exec-005",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.FAILED
        assert result.total_items == 0

    def test_empty_batch_completes(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Empty Job",
            task_type="test.empty",
            idempotency_key="exec-006",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        assert result.status == BatchJobStatus.COMPLETED
        assert result.total_items == 0
        assert result.succeeded == 0

    def test_job_not_found_raises(self, executor):
        with pytest.raises(BatchJobNotFoundError):
            executor.execute_job(uuid4(), uuid4())

    def test_already_running_raises(self, executor, db_session):
        """BT-8: Cannot execute a job that is already running."""
        actor = uuid4()
        job = executor.submit_job(
            job_name="Lock Test",
            task_type="test.success",
            idempotency_key="exec-007",
            actor_id=actor,
        )

        # Manually set status to RUNNING
        model = db_session.get(BatchJobModel, job.job_id)
        model.status = BatchJobStatus.RUNNING.value
        db_session.flush()

        with pytest.raises(BatchAlreadyRunningError):
            executor.execute_job(job.job_id, actor)

    def test_skipped_items_partially_completed(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Skip Job",
            task_type="test.skip",
            idempotency_key="exec-008",
            actor_id=actor,
        )

        result = executor.execute_job(job.job_id, actor)

        # Skipped items -> PARTIALLY_COMPLETED (not all succeeded)
        assert result.status == BatchJobStatus.PARTIALLY_COMPLETED
        assert result.skipped == 1

    def test_item_results_persisted(self, executor, db_session):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Persist Test",
            task_type="test.success",
            idempotency_key="exec-009",
            actor_id=actor,
            parameters={"item_count": 2},
        )

        executor.execute_job(job.job_id, actor)

        # Verify items persisted in DB
        items = executor.get_job_items(job.job_id)
        assert len(items) == 2
        assert all(i.status == BatchItemStatus.SUCCEEDED for i in items)

    def test_job_status_updated_after_execution(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Status Test",
            task_type="test.success",
            idempotency_key="exec-010",
            actor_id=actor,
            parameters={"item_count": 1},
        )

        executor.execute_job(job.job_id, actor)

        updated = executor.get_job(job.job_id)
        assert updated.status == BatchJobStatus.COMPLETED
        assert updated.total_items == 1
        assert updated.succeeded_items == 1
        assert updated.started_at is not None
        assert updated.completed_at is not None


# =============================================================================
# cancel_job tests
# =============================================================================


class TestCancelJob:
    def test_cancel_pending_job(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Cancel Test",
            task_type="test.success",
            idempotency_key="cancel-001",
            actor_id=actor,
        )

        cancelled = executor.cancel_job(job.job_id, "No longer needed", actor)

        assert cancelled.status == BatchJobStatus.CANCELLED
        assert "No longer needed" in cancelled.error_summary

    def test_cancel_nonexistent_raises(self, executor):
        with pytest.raises(BatchJobNotFoundError):
            executor.cancel_job(uuid4(), "reason", uuid4())

    def test_cancel_completed_raises(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Completed Job",
            task_type="test.success",
            idempotency_key="cancel-002",
            actor_id=actor,
            parameters={"item_count": 1},
        )
        executor.execute_job(job.job_id, actor)

        with pytest.raises(ValueError, match="Cannot cancel"):
            executor.cancel_job(job.job_id, "Too late", actor)


# =============================================================================
# get_job / get_job_items tests
# =============================================================================


class TestQueryMethods:
    def test_get_job(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Query Test",
            task_type="test.success",
            idempotency_key="query-001",
            actor_id=actor,
        )

        result = executor.get_job(job.job_id)
        assert result.job_id == job.job_id
        assert result.job_name == "Query Test"

    def test_get_job_not_found(self, executor):
        with pytest.raises(BatchJobNotFoundError):
            executor.get_job(uuid4())

    def test_get_job_items_empty(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Empty Items",
            task_type="test.success",
            idempotency_key="query-002",
            actor_id=actor,
        )

        items = executor.get_job_items(job.job_id)
        assert items == ()

    def test_get_job_items_after_execution(self, executor):
        actor = uuid4()
        job = executor.submit_job(
            job_name="Items Test",
            task_type="test.partial_fail",
            idempotency_key="query-003",
            actor_id=actor,
        )
        executor.execute_job(job.job_id, actor)

        items = executor.get_job_items(job.job_id)
        assert len(items) == 3

        succeeded = [i for i in items if i.status == BatchItemStatus.SUCCEEDED]
        failed = [i for i in items if i.status == BatchItemStatus.FAILED]
        assert len(succeeded) == 1
        assert len(failed) == 2
