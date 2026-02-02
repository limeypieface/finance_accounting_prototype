"""
ORM models for batch processing persistence (BATCH_PROCESSING_PLAN Phase 1).

Contract:
    BatchJobModel, BatchItemModel, and JobScheduleModel persist batch job
    state, per-item results, and recurring job schedules.  Each has
    ``to_dto()`` / ``from_dto()`` round-trip methods.

Architecture: finance_batch/models. Imports from finance_kernel.db.base only.

Invariants enforced:
    BT-2 -- ``idempotency_key`` is UNIQUE on BatchJobModel.
    BT-3 -- ``seq`` allocated via SequenceService (not set by ORM).
    BT-7 -- ``max_retries`` / ``retry_count`` tracked for safety.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString

if TYPE_CHECKING:
    from finance_batch.domain.types import BatchItemResult, BatchJob, JobSchedule


class BatchJobModel(TrackedBase):
    """Persistent batch job record (BT-2 idempotency, BT-3 sequence)."""

    __tablename__ = "batch_jobs"

    __table_args__ = (
        Index("ix_batch_jobs_status", "status"),
        Index("ix_batch_jobs_task_type", "task_type"),
        Index("ix_batch_jobs_created_at", "created_at"),
    )

    job_name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_type: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True,
    )
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    seq: Mapped[int | None] = mapped_column(nullable=True, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list["BatchItemModel"]] = relationship(
        "BatchItemModel",
        back_populates="job",
        foreign_keys="BatchItemModel.job_id",
    )

    def to_dto(self) -> BatchJob:
        from finance_batch.domain.types import BatchJob, BatchJobStatus

        return BatchJob(
            job_id=self.id,
            job_name=self.job_name,
            task_type=self.task_type,
            status=BatchJobStatus(self.status),
            idempotency_key=self.idempotency_key,
            parameters=self.parameters or {},
            total_items=self.total_items,
            succeeded_items=self.succeeded_items,
            failed_items=self.failed_items,
            skipped_items=self.skipped_items,
            max_retries=self.max_retries,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            created_by=self.created_by_id,
            correlation_id=self.correlation_id,
            error_summary=self.error_summary,
            seq=self.seq,
        )

    @classmethod
    def from_dto(cls, dto: BatchJob, created_by_id: UUID) -> BatchJobModel:
        return cls(
            id=dto.job_id,
            job_name=dto.job_name,
            task_type=dto.task_type,
            status=dto.status.value,
            idempotency_key=dto.idempotency_key,
            parameters=dto.parameters or None,
            total_items=dto.total_items,
            succeeded_items=dto.succeeded_items,
            failed_items=dto.failed_items,
            skipped_items=dto.skipped_items,
            max_retries=dto.max_retries,
            seq=dto.seq,
            started_at=dto.started_at,
            completed_at=dto.completed_at,
            correlation_id=dto.correlation_id,
            error_summary=dto.error_summary,
            created_by_id=created_by_id,
            updated_by_id=None,
        )


class BatchItemModel(TrackedBase):
    """Per-item result within a batch job (BT-1 SAVEPOINT, BT-7 retry)."""

    __tablename__ = "batch_items"

    __table_args__ = (
        Index("ix_batch_items_job_status", "job_id", "status"),
        Index("ix_batch_items_item_key", "item_key"),
    )

    job_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("batch_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    job: Mapped["BatchJobModel"] = relationship(
        "BatchJobModel",
        back_populates="items",
        foreign_keys=[job_id],
    )

    def to_dto(self) -> BatchItemResult:
        from finance_batch.domain.types import BatchItemResult, BatchItemStatus

        return BatchItemResult(
            item_index=self.item_index,
            item_key=self.item_key,
            status=BatchItemStatus(self.status),
            error_code=self.error_code,
            error_message=self.error_message,
            result_data=self.result_data,
            retry_count=self.retry_count,
            duration_ms=self.duration_ms,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_dto(
        cls, dto: BatchItemResult, job_id: UUID, created_by_id: UUID,
    ) -> BatchItemModel:
        return cls(
            job_id=job_id,
            item_index=dto.item_index,
            item_key=dto.item_key,
            status=dto.status.value,
            error_code=dto.error_code,
            error_message=dto.error_message,
            result_data=dto.result_data,
            retry_count=dto.retry_count,
            duration_ms=dto.duration_ms,
            started_at=dto.started_at,
            completed_at=dto.completed_at,
            created_by_id=created_by_id,
            updated_by_id=None,
        )


class JobScheduleModel(TrackedBase):
    """Recurring job schedule (BT-6 pure evaluation)."""

    __tablename__ = "job_schedules"

    __table_args__ = (
        Index("ix_job_schedules_active", "is_active"),
        Index("ix_job_schedules_next_run", "next_run_at"),
        Index("ix_job_schedules_task_type", "task_type"),
    )

    job_name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_type: Mapped[str] = mapped_column(String(200), nullable=False)
    frequency: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_run_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    legal_entity: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def to_dto(self) -> JobSchedule:
        from finance_batch.domain.types import (
            BatchJobStatus,
            JobSchedule,
            ScheduleFrequency,
        )

        return JobSchedule(
            schedule_id=self.id,
            job_name=self.job_name,
            task_type=self.task_type,
            frequency=ScheduleFrequency(self.frequency),
            parameters=self.parameters or {},
            cron_expression=self.cron_expression,
            next_run_at=self.next_run_at,
            last_run_at=self.last_run_at,
            last_run_status=(
                BatchJobStatus(self.last_run_status)
                if self.last_run_status
                else None
            ),
            is_active=self.is_active,
            max_retries=self.max_retries,
            created_by=self.created_by_id,
            legal_entity=self.legal_entity,
        )

    @classmethod
    def from_dto(cls, dto: JobSchedule, created_by_id: UUID) -> JobScheduleModel:
        return cls(
            id=dto.schedule_id,
            job_name=dto.job_name,
            task_type=dto.task_type,
            frequency=dto.frequency.value,
            parameters=dto.parameters or None,
            cron_expression=dto.cron_expression,
            next_run_at=dto.next_run_at,
            last_run_at=dto.last_run_at,
            last_run_status=(
                dto.last_run_status.value if dto.last_run_status else None
            ),
            is_active=dto.is_active,
            max_retries=dto.max_retries,
            legal_entity=dto.legal_entity,
            created_by_id=created_by_id,
            updated_by_id=None,
        )
