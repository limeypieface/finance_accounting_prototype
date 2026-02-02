"""
Tests for batch processing exceptions and audit actions -- Phase 2.

Validates exception hierarchy, error codes, message formatting,
and AuditAction enum extensions.
"""

import pytest

from finance_kernel.exceptions import (
    BatchAlreadyRunningError,
    BatchError,
    BatchIdempotencyError,
    BatchJobNotFoundError,
    BatchRetryExhaustedError,
    FinanceKernelError,
    InvalidCronExpressionError,
    ScheduleError,
    TaskNotRegisteredError,
)
from finance_kernel.models.audit_event import AuditAction


# =============================================================================
# Exception hierarchy tests
# =============================================================================


class TestBatchExceptionHierarchy:
    def test_batch_error_is_kernel_error(self):
        assert issubclass(BatchError, FinanceKernelError)

    def test_batch_error_code(self):
        assert BatchError.code == "BATCH_ERROR"

    def test_batch_job_not_found_inherits(self):
        assert issubclass(BatchJobNotFoundError, BatchError)

    def test_batch_already_running_inherits(self):
        assert issubclass(BatchAlreadyRunningError, BatchError)

    def test_batch_idempotency_inherits(self):
        assert issubclass(BatchIdempotencyError, BatchError)

    def test_batch_retry_exhausted_inherits(self):
        assert issubclass(BatchRetryExhaustedError, BatchError)

    def test_task_not_registered_inherits(self):
        assert issubclass(TaskNotRegisteredError, BatchError)

    def test_schedule_error_is_kernel_error(self):
        assert issubclass(ScheduleError, FinanceKernelError)

    def test_invalid_cron_inherits(self):
        assert issubclass(InvalidCronExpressionError, ScheduleError)


# =============================================================================
# Exception construction tests
# =============================================================================


class TestBatchJobNotFoundError:
    def test_construction(self):
        exc = BatchJobNotFoundError("job-123")
        assert exc.job_id == "job-123"
        assert "job-123" in str(exc)
        assert exc.code == "BATCH_JOB_NOT_FOUND"


class TestBatchAlreadyRunningError:
    def test_construction(self):
        exc = BatchAlreadyRunningError("daily_recon", "job-456")
        assert exc.job_name == "daily_recon"
        assert exc.running_job_id == "job-456"
        assert "daily_recon" in str(exc)
        assert "job-456" in str(exc)
        assert exc.code == "BATCH_ALREADY_RUNNING"


class TestBatchIdempotencyError:
    def test_construction(self):
        exc = BatchIdempotencyError("key-001", "job-789")
        assert exc.idempotency_key == "key-001"
        assert exc.existing_job_id == "job-789"
        assert "key-001" in str(exc)
        assert exc.code == "BATCH_IDEMPOTENCY_CONFLICT"


class TestBatchRetryExhaustedError:
    def test_construction(self):
        exc = BatchRetryExhaustedError("asset-004", 3, 3)
        assert exc.item_key == "asset-004"
        assert exc.max_retries == 3
        assert exc.retry_count == 3
        assert "asset-004" in str(exc)
        assert "3/3" in str(exc)
        assert exc.code == "BATCH_RETRY_EXHAUSTED"


class TestTaskNotRegisteredError:
    def test_construction_without_available(self):
        exc = TaskNotRegisteredError("unknown.task")
        assert exc.task_type == "unknown.task"
        assert "unknown.task" in str(exc)
        assert exc.code == "TASK_NOT_REGISTERED"

    def test_construction_with_available(self):
        exc = TaskNotRegisteredError(
            "unknown.task",
            available=("assets.depreciation", "cash.reconcile"),
        )
        assert exc.available == ("assets.depreciation", "cash.reconcile")
        assert "Available" in str(exc)


class TestInvalidCronExpressionError:
    def test_construction(self):
        exc = InvalidCronExpressionError("*/5 * * *", "expected 5 fields")
        assert exc.expression == "*/5 * * *"
        assert exc.reason == "expected 5 fields"
        assert "*/5 * * *" in str(exc)
        assert exc.code == "INVALID_CRON_EXPRESSION"


# =============================================================================
# AuditAction extension tests
# =============================================================================


class TestBatchAuditActions:
    def test_batch_job_started(self):
        assert AuditAction.BATCH_JOB_STARTED.value == "batch_job_started"

    def test_batch_job_completed(self):
        assert AuditAction.BATCH_JOB_COMPLETED.value == "batch_job_completed"

    def test_batch_job_failed(self):
        assert AuditAction.BATCH_JOB_FAILED.value == "batch_job_failed"

    def test_batch_job_cancelled(self):
        assert AuditAction.BATCH_JOB_CANCELLED.value == "batch_job_cancelled"

    def test_batch_item_failed(self):
        assert AuditAction.BATCH_ITEM_FAILED.value == "batch_item_failed"

    def test_schedule_triggered(self):
        assert AuditAction.SCHEDULE_TRIGGERED.value == "schedule_triggered"

    def test_all_batch_actions_are_str_enum(self):
        batch_actions = [
            AuditAction.BATCH_JOB_STARTED,
            AuditAction.BATCH_JOB_COMPLETED,
            AuditAction.BATCH_JOB_FAILED,
            AuditAction.BATCH_JOB_CANCELLED,
            AuditAction.BATCH_ITEM_FAILED,
            AuditAction.SCHEDULE_TRIGGERED,
        ]
        for action in batch_actions:
            assert isinstance(action, str)
            assert isinstance(action, AuditAction)
