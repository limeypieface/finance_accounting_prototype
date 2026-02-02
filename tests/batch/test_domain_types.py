"""
Tests for finance_batch.domain.types -- Phase 0.

Validates enum values, frozen dataclass construction, immutability,
defaults, and that all types follow the project DTO conventions.
"""

import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from finance_batch.domain.types import (
    BatchItemResult,
    BatchItemStatus,
    BatchJob,
    BatchJobStatus,
    BatchRunResult,
    JobSchedule,
    ScheduleFrequency,
)


# =============================================================================
# Enum tests
# =============================================================================


class TestBatchJobStatus:
    def test_values(self):
        assert set(BatchJobStatus) == {
            BatchJobStatus.PENDING,
            BatchJobStatus.RUNNING,
            BatchJobStatus.COMPLETED,
            BatchJobStatus.FAILED,
            BatchJobStatus.CANCELLED,
            BatchJobStatus.PARTIALLY_COMPLETED,
        }

    def test_string_values(self):
        assert BatchJobStatus.PENDING.value == "pending"
        assert BatchJobStatus.RUNNING.value == "running"
        assert BatchJobStatus.COMPLETED.value == "completed"
        assert BatchJobStatus.FAILED.value == "failed"
        assert BatchJobStatus.CANCELLED.value == "cancelled"
        assert BatchJobStatus.PARTIALLY_COMPLETED.value == "partially_completed"

    def test_is_str_enum(self):
        assert isinstance(BatchJobStatus.PENDING, str)


class TestBatchItemStatus:
    def test_values(self):
        assert set(BatchItemStatus) == {
            BatchItemStatus.PENDING,
            BatchItemStatus.PROCESSING,
            BatchItemStatus.SUCCEEDED,
            BatchItemStatus.FAILED,
            BatchItemStatus.SKIPPED,
            BatchItemStatus.RETRYING,
        }

    def test_string_values(self):
        assert BatchItemStatus.SUCCEEDED.value == "succeeded"
        assert BatchItemStatus.RETRYING.value == "retrying"


class TestScheduleFrequency:
    def test_values(self):
        assert set(ScheduleFrequency) == {
            ScheduleFrequency.ONCE,
            ScheduleFrequency.HOURLY,
            ScheduleFrequency.DAILY,
            ScheduleFrequency.WEEKLY,
            ScheduleFrequency.MONTHLY,
            ScheduleFrequency.PERIOD_END,
            ScheduleFrequency.ON_DEMAND,
        }

    def test_string_values(self):
        assert ScheduleFrequency.DAILY.value == "daily"
        assert ScheduleFrequency.PERIOD_END.value == "period_end"


# =============================================================================
# BatchJob tests
# =============================================================================


class TestBatchJob:
    def _make_job(self, **overrides):
        defaults = dict(
            job_id=uuid4(),
            job_name="test_job",
            task_type="assets.mass_depreciation",
            status=BatchJobStatus.PENDING,
            idempotency_key="test-key-001",
        )
        defaults.update(overrides)
        return BatchJob(**defaults)

    def test_construction_minimal(self):
        job = self._make_job()
        assert job.job_name == "test_job"
        assert job.task_type == "assets.mass_depreciation"
        assert job.status == BatchJobStatus.PENDING
        assert job.total_items == 0
        assert job.succeeded_items == 0
        assert job.failed_items == 0
        assert job.skipped_items == 0
        assert job.max_retries == 3
        assert job.parameters == {}
        assert job.started_at is None
        assert job.completed_at is None
        assert job.seq is None

    def test_frozen(self):
        job = self._make_job()
        with pytest.raises(FrozenInstanceError):
            job.status = BatchJobStatus.RUNNING  # type: ignore[misc]

    def test_with_parameters(self):
        params = {"currency": "USD", "effective_date": "2026-01-31"}
        job = self._make_job(parameters=params)
        assert job.parameters["currency"] == "USD"

    def test_with_all_fields(self):
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        actor = uuid4()
        job = self._make_job(
            total_items=100,
            succeeded_items=95,
            failed_items=3,
            skipped_items=2,
            max_retries=5,
            created_at=now,
            started_at=now,
            completed_at=now,
            created_by=actor,
            correlation_id="corr-001",
            error_summary="3 items failed",
            seq=42,
        )
        assert job.total_items == 100
        assert job.seq == 42
        assert job.created_by == actor


# =============================================================================
# BatchItemResult tests
# =============================================================================


class TestBatchItemResult:
    def test_construction_success(self):
        result = BatchItemResult(
            item_index=0,
            item_key="asset-001",
            status=BatchItemStatus.SUCCEEDED,
            result_data={"journal_entry_id": "je-001"},
            duration_ms=150,
        )
        assert result.item_index == 0
        assert result.item_key == "asset-001"
        assert result.status == BatchItemStatus.SUCCEEDED
        assert result.error_code is None
        assert result.duration_ms == 150

    def test_construction_failure(self):
        result = BatchItemResult(
            item_index=3,
            item_key="asset-004",
            status=BatchItemStatus.FAILED,
            error_code="ACCOUNT_NOT_FOUND",
            error_message="Depreciation account missing for asset-004",
            retry_count=2,
        )
        assert result.status == BatchItemStatus.FAILED
        assert result.error_code == "ACCOUNT_NOT_FOUND"
        assert result.retry_count == 2

    def test_frozen(self):
        result = BatchItemResult(
            item_index=0,
            item_key="x",
            status=BatchItemStatus.PENDING,
        )
        with pytest.raises(FrozenInstanceError):
            result.status = BatchItemStatus.SUCCEEDED  # type: ignore[misc]

    def test_defaults(self):
        result = BatchItemResult(
            item_index=0,
            item_key="x",
            status=BatchItemStatus.PENDING,
        )
        assert result.error_code is None
        assert result.error_message is None
        assert result.result_data is None
        assert result.retry_count == 0
        assert result.duration_ms == 0
        assert result.started_at is None
        assert result.completed_at is None


# =============================================================================
# BatchRunResult tests
# =============================================================================


class TestBatchRunResult:
    def test_construction(self):
        job_id = uuid4()
        items = (
            BatchItemResult(item_index=0, item_key="a", status=BatchItemStatus.SUCCEEDED),
            BatchItemResult(item_index=1, item_key="b", status=BatchItemStatus.FAILED, error_code="E1"),
        )
        result = BatchRunResult(
            job_id=job_id,
            status=BatchJobStatus.PARTIALLY_COMPLETED,
            total_items=2,
            succeeded=1,
            failed=1,
            skipped=0,
            item_results=items,
            duration_ms=500,
        )
        assert result.total_items == 2
        assert result.succeeded == 1
        assert result.failed == 1
        assert len(result.item_results) == 2
        assert result.duration_ms == 500

    def test_frozen(self):
        result = BatchRunResult(
            job_id=uuid4(),
            status=BatchJobStatus.COMPLETED,
            total_items=0,
            succeeded=0,
            failed=0,
            skipped=0,
        )
        with pytest.raises(FrozenInstanceError):
            result.status = BatchJobStatus.FAILED  # type: ignore[misc]

    def test_defaults(self):
        result = BatchRunResult(
            job_id=uuid4(),
            status=BatchJobStatus.COMPLETED,
            total_items=0,
            succeeded=0,
            failed=0,
            skipped=0,
        )
        assert result.item_results == ()
        assert result.started_at is None
        assert result.completed_at is None
        assert result.duration_ms == 0
        assert result.correlation_id is None


# =============================================================================
# JobSchedule tests
# =============================================================================


class TestJobSchedule:
    def _make_schedule(self, **overrides):
        defaults = dict(
            schedule_id=uuid4(),
            job_name="daily_recon",
            task_type="cash.auto_reconcile",
            frequency=ScheduleFrequency.DAILY,
        )
        defaults.update(overrides)
        return JobSchedule(**defaults)

    def test_construction_minimal(self):
        schedule = self._make_schedule()
        assert schedule.job_name == "daily_recon"
        assert schedule.frequency == ScheduleFrequency.DAILY
        assert schedule.is_active is True
        assert schedule.max_retries == 3
        assert schedule.parameters == {}
        assert schedule.cron_expression is None
        assert schedule.next_run_at is None
        assert schedule.last_run_at is None

    def test_with_cron(self):
        schedule = self._make_schedule(
            cron_expression="0 6 * * *",
            next_run_at=datetime(2026, 2, 2, 6, 0, 0, tzinfo=timezone.utc),
        )
        assert schedule.cron_expression == "0 6 * * *"
        assert schedule.next_run_at is not None

    def test_frozen(self):
        schedule = self._make_schedule()
        with pytest.raises(FrozenInstanceError):
            schedule.is_active = False  # type: ignore[misc]

    def test_with_legal_entity(self):
        schedule = self._make_schedule(legal_entity="ACME-US")
        assert schedule.legal_entity == "ACME-US"

    def test_inactive_schedule(self):
        schedule = self._make_schedule(is_active=False)
        assert schedule.is_active is False
