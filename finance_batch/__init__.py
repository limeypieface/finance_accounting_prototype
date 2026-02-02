"""
finance_batch -- Batch processing and job scheduling infrastructure.

Provides a batch execution engine with per-item SAVEPOINT isolation,
progress tracking, retry, audit trail, and an in-process cron-like
scheduler.  Wraps existing module batch methods (mass depreciation,
payment runs, dunning, etc.) in a uniform framework.

Architecture:
    finance_batch/ is a top-level package.  Nothing in kernel/,
    engines/, modules/, or services/ imports from finance_batch.
    See plans/BATCH_PROCESSING_PLAN.md.

Invariants:
    BT-1  SAVEPOINT isolation per item
    BT-2  Job idempotency (UNIQUE idempotency_key)
    BT-3  Sequence monotonicity via SequenceService
    BT-4  Clock injection (no datetime.now() calls)
    BT-5  Audit trail for all lifecycle events
    BT-6  Schedule evaluation is pure
    BT-7  Max retry safety
    BT-8  Concurrency guard (one running instance per job)
    BT-9  No kernel/engine/module/service imports from batch
    BT-10 Graceful shutdown
"""
