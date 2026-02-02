"""
Tests for finance_batch.models -- Phase 1.

Validates ORM model construction, to_dto()/from_dto() round-trips,
index existence, and UNIQUE constraints.
"""

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
from finance_batch.models.batch import (
    BatchItemModel,
    BatchJobModel,
    JobScheduleModel,
)


# =============================================================================
# BatchJobModel tests
# =============================================================================


class TestBatchJobModel:
    def _make_job_dto(self, **overrides):
        defaults = dict(
            job_id=uuid4(),
            job_name="test_job",
            task_type="assets.mass_depreciation",
            status=BatchJobStatus.PENDING,
            idempotency_key="test-key-001",
        )
        defaults.update(overrides)
        return BatchJob(**defaults)

    def test_from_dto(self):
        dto = self._make_job_dto()
        actor = uuid4()
        model = BatchJobModel.from_dto(dto, created_by_id=actor)

        assert model.id == dto.job_id
        assert model.job_name == "test_job"
        assert model.task_type == "assets.mass_depreciation"
        assert model.status == "pending"
        assert model.idempotency_key == "test-key-001"
        assert model.total_items == 0
        assert model.succeeded_items == 0
        assert model.failed_items == 0
        assert model.skipped_items == 0
        assert model.max_retries == 3
        assert model.created_by_id == actor
        assert model.updated_by_id is None

    def test_from_dto_with_parameters(self):
        params = {"currency": "USD", "effective_date": "2026-01-31"}
        dto = self._make_job_dto(parameters=params)
        model = BatchJobModel.from_dto(dto, created_by_id=uuid4())

        assert model.parameters == params

    def test_from_dto_empty_parameters_stored_as_none(self):
        dto = self._make_job_dto(parameters={})
        model = BatchJobModel.from_dto(dto, created_by_id=uuid4())

        # Empty dict stored as None in JSON column
        assert model.parameters is None

    def test_to_dto_round_trip(self):
        now = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
        actor = uuid4()
        dto = self._make_job_dto(
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
        model = BatchJobModel.from_dto(dto, created_by_id=actor)
        # Simulate ORM setting created_at
        model.created_at = now

        result = model.to_dto()

        assert result.job_id == dto.job_id
        assert result.job_name == dto.job_name
        assert result.task_type == dto.task_type
        assert result.status == BatchJobStatus.PENDING
        assert result.idempotency_key == dto.idempotency_key
        assert result.total_items == 100
        assert result.succeeded_items == 95
        assert result.failed_items == 3
        assert result.skipped_items == 2
        assert result.max_retries == 5
        assert result.seq == 42
        assert result.correlation_id == "corr-001"
        assert result.error_summary == "3 items failed"

    def test_tablename(self):
        assert BatchJobModel.__tablename__ == "batch_jobs"

    def test_idempotency_key_unique_column(self):
        """BT-2: idempotency_key column must have unique=True."""
        col = BatchJobModel.__table__.c.idempotency_key
        # Check for unique constraint
        assert any(
            col.name in [c.name for c in uc.columns]
            for uc in BatchJobModel.__table__.constraints
            if hasattr(uc, "columns")
        ) or col.unique


# =============================================================================
# BatchItemModel tests
# =============================================================================


class TestBatchItemModel:
    def _make_item_dto(self, **overrides):
        defaults = dict(
            item_index=0,
            item_key="asset-001",
            status=BatchItemStatus.SUCCEEDED,
        )
        defaults.update(overrides)
        return BatchItemResult(**defaults)

    def test_from_dto(self):
        dto = self._make_item_dto(
            result_data={"journal_entry_id": "je-001"},
            duration_ms=150,
        )
        job_id = uuid4()
        actor = uuid4()
        model = BatchItemModel.from_dto(dto, job_id=job_id, created_by_id=actor)

        assert model.job_id == job_id
        assert model.item_index == 0
        assert model.item_key == "asset-001"
        assert model.status == "succeeded"
        assert model.result_data == {"journal_entry_id": "je-001"}
        assert model.duration_ms == 150
        assert model.created_by_id == actor

    def test_from_dto_failure(self):
        dto = self._make_item_dto(
            status=BatchItemStatus.FAILED,
            error_code="ACCOUNT_NOT_FOUND",
            error_message="Missing account",
            retry_count=2,
        )
        model = BatchItemModel.from_dto(dto, job_id=uuid4(), created_by_id=uuid4())

        assert model.status == "failed"
        assert model.error_code == "ACCOUNT_NOT_FOUND"
        assert model.error_message == "Missing account"
        assert model.retry_count == 2

    def test_to_dto_round_trip(self):
        now = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
        dto = self._make_item_dto(
            result_data={"key": "value"},
            duration_ms=200,
            started_at=now,
            completed_at=now,
        )
        job_id = uuid4()
        model = BatchItemModel.from_dto(dto, job_id=job_id, created_by_id=uuid4())

        result = model.to_dto()

        assert result.item_index == 0
        assert result.item_key == "asset-001"
        assert result.status == BatchItemStatus.SUCCEEDED
        assert result.result_data == {"key": "value"}
        assert result.duration_ms == 200
        assert result.started_at == now
        assert result.completed_at == now

    def test_tablename(self):
        assert BatchItemModel.__tablename__ == "batch_items"


# =============================================================================
# JobScheduleModel tests
# =============================================================================


class TestJobScheduleModel:
    def _make_schedule_dto(self, **overrides):
        defaults = dict(
            schedule_id=uuid4(),
            job_name="daily_recon",
            task_type="cash.auto_reconcile",
            frequency=ScheduleFrequency.DAILY,
        )
        defaults.update(overrides)
        return JobSchedule(**defaults)

    def test_from_dto(self):
        dto = self._make_schedule_dto()
        actor = uuid4()
        model = JobScheduleModel.from_dto(dto, created_by_id=actor)

        assert model.id == dto.schedule_id
        assert model.job_name == "daily_recon"
        assert model.task_type == "cash.auto_reconcile"
        assert model.frequency == "daily"
        assert model.is_active is True
        assert model.max_retries == 3
        assert model.created_by_id == actor

    def test_from_dto_with_cron(self):
        now = datetime(2026, 2, 2, 6, 0, 0, tzinfo=timezone.utc)
        dto = self._make_schedule_dto(
            cron_expression="0 6 * * *",
            next_run_at=now,
        )
        model = JobScheduleModel.from_dto(dto, created_by_id=uuid4())

        assert model.cron_expression == "0 6 * * *"
        assert model.next_run_at == now

    def test_from_dto_with_last_run_status(self):
        dto = self._make_schedule_dto(
            last_run_status=BatchJobStatus.COMPLETED,
        )
        model = JobScheduleModel.from_dto(dto, created_by_id=uuid4())

        assert model.last_run_status == "completed"

    def test_to_dto_round_trip(self):
        now = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
        dto = self._make_schedule_dto(
            cron_expression="0 6 * * *",
            next_run_at=now,
            last_run_at=now,
            last_run_status=BatchJobStatus.COMPLETED,
            is_active=False,
            max_retries=5,
            legal_entity="ACME-US",
        )
        actor = uuid4()
        model = JobScheduleModel.from_dto(dto, created_by_id=actor)
        # Simulate ORM setting created_by_id
        model.created_by_id = actor

        result = model.to_dto()

        assert result.schedule_id == dto.schedule_id
        assert result.job_name == "daily_recon"
        assert result.task_type == "cash.auto_reconcile"
        assert result.frequency == ScheduleFrequency.DAILY
        assert result.cron_expression == "0 6 * * *"
        assert result.next_run_at == now
        assert result.last_run_at == now
        assert result.last_run_status == BatchJobStatus.COMPLETED
        assert result.is_active is False
        assert result.max_retries == 5
        assert result.legal_entity == "ACME-US"

    def test_to_dto_null_last_run_status(self):
        dto = self._make_schedule_dto()
        model = JobScheduleModel.from_dto(dto, created_by_id=uuid4())

        result = model.to_dto()
        assert result.last_run_status is None

    def test_tablename(self):
        assert JobScheduleModel.__tablename__ == "job_schedules"
