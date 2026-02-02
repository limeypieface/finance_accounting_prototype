"""
finance_batch.domain.types -- Pure frozen dataclasses for the batch system.

Phase 0 of BATCH_PROCESSING_PLAN.  ZERO I/O.

Follows the pattern of finance_services/_close_types.py and
finance_ingestion/domain/types.py: frozen dataclasses with enum
status fields and tuples for immutable collections.

Invariants enforced:
    - R6 (replay safety): all DTOs are frozen dataclasses (immutable).
    - BT-2 (idempotency): BatchJob carries an idempotency_key for uniqueness.
    - BT-7 (max retry safety): BatchJob/BatchItemResult carry max_retries/retry_count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


# =============================================================================
# Status enums
# =============================================================================


class BatchJobStatus(str, Enum):
    """Job-level lifecycle status."""

    PENDING = "pending"  # Created, not yet started
    RUNNING = "running"  # Execution in progress
    COMPLETED = "completed"  # All items processed successfully
    FAILED = "failed"  # Job-level failure (no items succeeded)
    CANCELLED = "cancelled"  # Cancelled before or during execution
    PARTIALLY_COMPLETED = "partially_completed"  # Some items failed


class BatchItemStatus(str, Enum):
    """Per-item lifecycle status within a batch job."""

    PENDING = "pending"  # Not yet processed
    PROCESSING = "processing"  # Currently executing
    SUCCEEDED = "succeeded"  # Processed successfully
    FAILED = "failed"  # Processing failed
    SKIPPED = "skipped"  # Intentionally skipped (e.g., already processed)
    RETRYING = "retrying"  # Retry in progress after prior failure


class ScheduleFrequency(str, Enum):
    """Recurrence frequency for scheduled batch jobs."""

    ONCE = "once"  # Fire once, no recurrence
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    PERIOD_END = "period_end"  # Triggered externally by period close
    ON_DEMAND = "on_demand"  # Manual trigger only


# =============================================================================
# Job DTOs
# =============================================================================


@dataclass(frozen=True)
class BatchJob:
    """Immutable snapshot of a batch job.

    BT-2: ``idempotency_key`` is UNIQUE -- re-submitting the same key
    returns the existing job rather than creating a duplicate.

    BT-3: ``seq`` is allocated via SequenceService for monotonic ordering.
    """

    job_id: UUID
    job_name: str  # Human-readable label (e.g., "Monthly Depreciation Jan-26")
    task_type: str  # Registered task key (e.g., "assets.mass_depreciation")
    status: BatchJobStatus
    idempotency_key: str  # BT-2: UNIQUE constraint
    parameters: dict[str, Any] = field(default_factory=dict)
    total_items: int = 0
    succeeded_items: int = 0
    failed_items: int = 0
    skipped_items: int = 0
    max_retries: int = 3  # BT-7: per-item retry ceiling
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by: UUID | None = None
    correlation_id: str | None = None
    error_summary: str | None = None
    seq: int | None = None  # BT-3: monotonic via SequenceService


@dataclass(frozen=True)
class BatchItemResult:
    """Immutable result of processing a single batch item.

    BT-1: Each item runs in its own SAVEPOINT -- failure of one item
    does not abort the batch.

    BT-7: ``retry_count`` tracks how many times this item has been
    retried.  The executor will not exceed ``max_retries`` on the parent job.
    """

    item_index: int  # 0-indexed position in the batch
    item_key: str  # Business identifier (e.g., asset_id, invoice_id)
    status: BatchItemStatus
    error_code: str | None = None
    error_message: str | None = None
    result_data: dict[str, Any] | None = None  # e.g., {"journal_entry_id": "..."}
    retry_count: int = 0
    duration_ms: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class BatchRunResult:
    """Immutable result of executing a complete batch job.

    Returned by ``BatchExecutor.execute_job()``.
    """

    job_id: UUID
    status: BatchJobStatus
    total_items: int
    succeeded: int
    failed: int
    skipped: int
    item_results: tuple[BatchItemResult, ...] = ()
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0
    correlation_id: str | None = None


# =============================================================================
# Schedule DTOs
# =============================================================================


@dataclass(frozen=True)
class JobSchedule:
    """Immutable snapshot of a recurring job schedule.

    BT-6: Schedule evaluation (``should_fire``) is pure -- the scheduler
    reads ``next_run_at`` and the current clock, with no side effects.
    """

    schedule_id: UUID
    job_name: str  # Human-readable label
    task_type: str  # Registered task key
    frequency: ScheduleFrequency
    parameters: dict[str, Any] = field(default_factory=dict)
    cron_expression: str | None = None  # Fine-grained timing
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: BatchJobStatus | None = None
    is_active: bool = True
    max_retries: int = 3
    created_by: UUID | None = None
    legal_entity: str | None = None  # Scope restriction
