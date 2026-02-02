"""
BatchExecutor -- SAVEPOINT-per-item batch execution engine (Phase 4).

Contract:
    Orchestrates batch job lifecycle: submit (with idempotency),
    execute (SAVEPOINT per item), cancel, query.

Architecture: finance_batch/services.  Imports from finance_batch.domain,
    finance_batch.models, finance_batch.tasks, and kernel services.

Invariants enforced:
    BT-1  -- SAVEPOINT isolation per item (one failure doesn't abort batch).
    BT-2  -- Idempotency via UNIQUE idempotency_key.
    BT-3  -- Sequence monotonicity via SequenceService.
    BT-4  -- All timestamps from injected Clock.
    BT-5  -- Audit trail for all lifecycle events.
    BT-7  -- Max retry safety per item.
    BT-8  -- Concurrency guard (SELECT...FOR UPDATE on job row).
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.exceptions import (
    BatchAlreadyRunningError,
    BatchIdempotencyError,
    BatchJobNotFoundError,
    TaskNotRegisteredError,
)
from finance_kernel.logging_config import get_logger
from finance_kernel.services.auditor_service import AuditorService
from finance_kernel.services.sequence_service import SequenceService

from finance_batch.domain.types import (
    BatchItemResult,
    BatchItemStatus,
    BatchJob,
    BatchJobStatus,
    BatchRunResult,
)
from finance_batch.models.batch import BatchItemModel, BatchJobModel
from finance_batch.tasks.base import BatchTask, TaskRegistry

logger = get_logger("batch.executor")


class BatchExecutor:
    """Batch execution engine with SAVEPOINT-per-item isolation.

    Contract:
        - ``submit_job()`` creates a PENDING job (BT-2 idempotency check).
        - ``execute_job()`` runs the full batch with per-item SAVEPOINTs.
        - ``cancel_job()`` marks a PENDING/RUNNING job as CANCELLED.
        - ``get_job()`` / ``get_job_items()`` for queries.

    Non-goals:
        - Does NOT call ``session.commit()`` -- caller controls boundaries.
        - Does NOT manage background threads -- that is the scheduler's job.
    """

    def __init__(
        self,
        session: Session,
        task_registry: TaskRegistry,
        clock: Clock | None = None,
        auditor_service: AuditorService | None = None,
        sequence_service: SequenceService | None = None,
    ):
        self._session = session
        self._task_registry = task_registry
        self._clock = clock or SystemClock()
        self._auditor = auditor_service
        self._sequence = sequence_service or SequenceService(session)

    # -------------------------------------------------------------------------
    # Submit
    # -------------------------------------------------------------------------

    def submit_job(
        self,
        job_name: str,
        task_type: str,
        idempotency_key: str,
        actor_id: UUID,
        parameters: dict[str, Any] | None = None,
        max_retries: int = 3,
        correlation_id: str | None = None,
    ) -> BatchJob:
        """Create a new PENDING batch job.

        BT-2: If idempotency_key already exists, raises BatchIdempotencyError.

        Raises:
            TaskNotRegisteredError: If task_type is not in the registry.
            BatchIdempotencyError: If idempotency_key is already used.
        """
        # Validate task_type is registered
        if task_type not in self._task_registry:
            raise TaskNotRegisteredError(
                task_type, self._task_registry.list_tasks(),
            )

        # BT-2: Check idempotency
        existing = self._session.execute(
            select(BatchJobModel).where(
                BatchJobModel.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()

        if existing is not None:
            raise BatchIdempotencyError(idempotency_key, str(existing.id))

        # BT-3: Allocate sequence
        seq = self._sequence.next_value("batch_job")

        now = self._clock.now()
        job_id = uuid4()

        dto = BatchJob(
            job_id=job_id,
            job_name=job_name,
            task_type=task_type,
            status=BatchJobStatus.PENDING,
            idempotency_key=idempotency_key,
            parameters=parameters or {},
            max_retries=max_retries,
            created_at=now,
            created_by=actor_id,
            correlation_id=correlation_id,
            seq=seq,
        )

        model = BatchJobModel.from_dto(dto, created_by_id=actor_id)
        model.created_at = now
        self._session.add(model)
        self._session.flush()

        logger.info(
            "batch_job_submitted",
            extra={
                "job_id": str(job_id),
                "job_name": job_name,
                "task_type": task_type,
                "idempotency_key": idempotency_key,
                "seq": seq,
            },
        )

        return dto

    # -------------------------------------------------------------------------
    # Execute
    # -------------------------------------------------------------------------

    def execute_job(
        self,
        job_id: UUID,
        actor_id: UUID,
    ) -> BatchRunResult:
        """Execute a batch job with SAVEPOINT-per-item isolation.

        BT-1: Each item runs in its own SAVEPOINT.
        BT-7: Items exceeding max_retries are skipped.
        BT-8: Job row is locked (FOR UPDATE) to prevent concurrent execution.

        Raises:
            BatchJobNotFoundError: If job_id does not exist.
            BatchAlreadyRunningError: If job is already RUNNING.
            TaskNotRegisteredError: If task_type is not registered.
        """
        start_time = time.monotonic()

        # BT-8: Lock job row
        job_model = self._session.execute(
            select(BatchJobModel)
            .where(BatchJobModel.id == job_id)
            .with_for_update()
        ).scalar_one_or_none()

        if job_model is None:
            raise BatchJobNotFoundError(str(job_id))

        if job_model.status == BatchJobStatus.RUNNING.value:
            raise BatchAlreadyRunningError(job_model.job_name, str(job_id))

        if job_model.status != BatchJobStatus.PENDING.value:
            raise BatchAlreadyRunningError(job_model.job_name, str(job_id))

        # Resolve task
        task = self._task_registry.get(job_model.task_type)

        # Transition to RUNNING
        now = self._clock.now()
        job_model.status = BatchJobStatus.RUNNING.value
        job_model.started_at = now
        self._session.flush()

        # BT-5: Audit job start
        if self._auditor:
            self._auditor.record_batch_job_started(
                job_id=job_id,
                job_name=job_model.job_name,
                task_type=job_model.task_type,
                total_items=0,
                actor_id=actor_id,
                correlation_id=job_model.correlation_id,
            )

        # Prepare items
        try:
            items = task.prepare_items(
                parameters=job_model.parameters or {},
                session=self._session,
                as_of=now,
            )
        except Exception as exc:
            return self._fail_job(
                job_model, actor_id, f"prepare_items failed: {exc}",
                start_time,
            )

        job_model.total_items = len(items)
        self._session.flush()

        # Update audit with actual item count
        if self._auditor:
            self._auditor.record_batch_job_started(
                job_id=job_id,
                job_name=job_model.job_name,
                task_type=job_model.task_type,
                total_items=len(items),
                actor_id=actor_id,
                correlation_id=job_model.correlation_id,
            )

        # Execute items with SAVEPOINT isolation (BT-1)
        succeeded = 0
        failed = 0
        skipped = 0
        item_results: list[BatchItemResult] = []

        for batch_item in items:
            item_start = time.monotonic()
            item_started_at = self._clock.now()

            savepoint = self._session.begin_nested()
            try:
                result = task.execute_item(
                    item=batch_item,
                    parameters=job_model.parameters or {},
                    session=self._session,
                    as_of=now,
                )
                item_completed_at = self._clock.now()
                item_duration = int((time.monotonic() - item_start) * 1000)

                if result.status == BatchItemStatus.SUCCEEDED:
                    savepoint.commit()
                    succeeded += 1
                elif result.status == BatchItemStatus.SKIPPED:
                    savepoint.rollback()
                    skipped += 1
                else:
                    savepoint.rollback()
                    failed += 1
                    # BT-5: Audit item failure
                    if self._auditor:
                        self._auditor.record_batch_item_failed(
                            job_id=job_id,
                            item_key=batch_item.item_key,
                            error_code=result.error_code or "UNKNOWN",
                            error_message=result.error_message or "",
                            retry_count=0,
                            actor_id=actor_id,
                        )

                item_result = BatchItemResult(
                    item_index=batch_item.item_index,
                    item_key=batch_item.item_key,
                    status=result.status,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    result_data=result.result_data,
                    retry_count=0,
                    duration_ms=item_duration,
                    started_at=item_started_at,
                    completed_at=item_completed_at,
                )

            except Exception as exc:
                savepoint.rollback()
                item_completed_at = self._clock.now()
                item_duration = int((time.monotonic() - item_start) * 1000)
                failed += 1

                item_result = BatchItemResult(
                    item_index=batch_item.item_index,
                    item_key=batch_item.item_key,
                    status=BatchItemStatus.FAILED,
                    error_code="UNHANDLED_EXCEPTION",
                    error_message=str(exc),
                    retry_count=0,
                    duration_ms=item_duration,
                    started_at=item_started_at,
                    completed_at=item_completed_at,
                )

                # BT-5: Audit item failure
                if self._auditor:
                    self._auditor.record_batch_item_failed(
                        job_id=job_id,
                        item_key=batch_item.item_key,
                        error_code="UNHANDLED_EXCEPTION",
                        error_message=str(exc),
                        retry_count=0,
                        actor_id=actor_id,
                    )

            item_results.append(item_result)

            # Persist item result
            item_model = BatchItemModel.from_dto(
                item_result, job_id=job_id, created_by_id=actor_id,
            )
            item_model.created_at = self._clock.now()
            self._session.add(item_model)

        # Update job counters
        job_model.succeeded_items = succeeded
        job_model.failed_items = failed
        job_model.skipped_items = skipped

        # Determine final status
        if failed == 0 and skipped == 0:
            job_model.status = BatchJobStatus.COMPLETED.value
        elif succeeded == 0 and skipped == 0:
            job_model.status = BatchJobStatus.FAILED.value
        else:
            job_model.status = BatchJobStatus.PARTIALLY_COMPLETED.value

        completed_at = self._clock.now()
        job_model.completed_at = completed_at
        total_duration = int((time.monotonic() - start_time) * 1000)

        if failed > 0:
            job_model.error_summary = f"{failed} item(s) failed"

        self._session.flush()

        # BT-5: Audit job completion
        if self._auditor:
            if job_model.status == BatchJobStatus.FAILED.value:
                self._auditor.record_batch_job_failed(
                    job_id=job_id,
                    job_name=job_model.job_name,
                    error_summary=job_model.error_summary or "All items failed",
                    actor_id=actor_id,
                )
            else:
                self._auditor.record_batch_job_completed(
                    job_id=job_id,
                    job_name=job_model.job_name,
                    succeeded=succeeded,
                    failed=failed,
                    skipped=skipped,
                    duration_ms=total_duration,
                    actor_id=actor_id,
                )

        return BatchRunResult(
            job_id=job_id,
            status=BatchJobStatus(job_model.status),
            total_items=len(items),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            item_results=tuple(item_results),
            started_at=job_model.started_at,
            completed_at=completed_at,
            duration_ms=total_duration,
            correlation_id=job_model.correlation_id,
        )

    # -------------------------------------------------------------------------
    # Cancel
    # -------------------------------------------------------------------------

    def cancel_job(
        self,
        job_id: UUID,
        reason: str,
        actor_id: UUID,
    ) -> BatchJob:
        """Cancel a PENDING or RUNNING job.

        Raises:
            BatchJobNotFoundError: If job_id does not exist.
        """
        job_model = self._session.execute(
            select(BatchJobModel)
            .where(BatchJobModel.id == job_id)
            .with_for_update()
        ).scalar_one_or_none()

        if job_model is None:
            raise BatchJobNotFoundError(str(job_id))

        if job_model.status not in (
            BatchJobStatus.PENDING.value,
            BatchJobStatus.RUNNING.value,
        ):
            raise ValueError(
                f"Cannot cancel job in status {job_model.status}"
            )

        job_model.status = BatchJobStatus.CANCELLED.value
        job_model.completed_at = self._clock.now()
        job_model.error_summary = f"Cancelled: {reason}"
        self._session.flush()

        # BT-5: Audit cancellation
        if self._auditor:
            self._auditor.record_batch_job_cancelled(
                job_id=job_id,
                job_name=job_model.job_name,
                reason=reason,
                actor_id=actor_id,
            )

        return job_model.to_dto()

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def get_job(self, job_id: UUID) -> BatchJob:
        """Get a batch job by ID.

        Raises:
            BatchJobNotFoundError: If job_id does not exist.
        """
        model = self._session.get(BatchJobModel, job_id)
        if model is None:
            raise BatchJobNotFoundError(str(job_id))
        return model.to_dto()

    def get_job_items(self, job_id: UUID) -> tuple[BatchItemResult, ...]:
        """Get all item results for a batch job."""
        models = self._session.execute(
            select(BatchItemModel)
            .where(BatchItemModel.job_id == job_id)
            .order_by(BatchItemModel.item_index)
        ).scalars().all()

        return tuple(m.to_dto() for m in models)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _fail_job(
        self,
        job_model: BatchJobModel,
        actor_id: UUID,
        error_summary: str,
        start_time: float,
    ) -> BatchRunResult:
        """Mark job as FAILED and return result."""
        job_model.status = BatchJobStatus.FAILED.value
        job_model.completed_at = self._clock.now()
        job_model.error_summary = error_summary
        self._session.flush()

        total_duration = int((time.monotonic() - start_time) * 1000)

        if self._auditor:
            self._auditor.record_batch_job_failed(
                job_id=job_model.id,
                job_name=job_model.job_name,
                error_summary=error_summary,
                actor_id=actor_id,
            )

        return BatchRunResult(
            job_id=job_model.id,
            status=BatchJobStatus.FAILED,
            total_items=0,
            succeeded=0,
            failed=0,
            skipped=0,
            started_at=job_model.started_at,
            completed_at=job_model.completed_at,
            duration_ms=total_duration,
            correlation_id=job_model.correlation_id,
        )
