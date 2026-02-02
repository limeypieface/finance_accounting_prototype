"""
BatchScheduler -- In-process polling scheduler (Phase 8).

Contract:
    Polls due schedules on a configurable interval, evaluates
    ``should_fire()`` (pure, BT-6), and submits + executes jobs via
    ``BatchExecutor``.

Architecture: finance_batch/services.  Uses finance_batch.domain.schedule
    for pure evaluation and finance_batch.services.executor for execution.

Invariants enforced:
    BT-4  -- All timestamps from injected Clock.
    BT-6  -- Schedule evaluation is pure (should_fire).
    BT-10 -- Graceful shutdown (respects stop signal, completes current item).
"""

from __future__ import annotations

import threading
import time
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import get_logger

from finance_batch.domain.schedule import compute_next_run, should_fire
from finance_batch.domain.types import BatchJobStatus
from finance_batch.models.batch import JobScheduleModel
from finance_batch.services.executor import BatchExecutor

logger = get_logger("batch.scheduler")


class BatchScheduler:
    """In-process polling scheduler for batch job schedules.

    Contract:
        - ``tick()`` evaluates all active schedules, fires due ones.
        - ``start()`` / ``stop()`` for background thread operation.
        - Respects stop signal between items (BT-10).

    Non-goals:
        - NOT a distributed scheduler (no leader election).
        - Does NOT handle timezone conversions (expects UTC).
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        executor_factory: Callable[[Session], BatchExecutor],
        clock: Clock | None = None,
        actor_id: UUID | None = None,
        tick_interval_seconds: int = 60,
    ):
        self._session_factory = session_factory
        self._executor_factory = executor_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id or uuid4()
        self._tick_interval = tick_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def tick(self) -> int:
        """Evaluate and fire due schedules (public for testing).

        Returns the number of schedules that were fired.
        """
        session = self._session_factory()
        try:
            fired = self._evaluate_schedules(session)
            session.commit()
            return fired
        except Exception:
            session.rollback()
            logger.exception("scheduler_tick_failed")
            return 0
        finally:
            session.close()

    def start(self) -> None:
        """Start the scheduler in a background thread (BT-10)."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="batch-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("scheduler_started", extra={"tick_interval": self._tick_interval})

    def stop(self, timeout: float = 30.0) -> None:
        """Signal stop and wait for the scheduler to finish (BT-10).

        Args:
            timeout: Max seconds to wait for the thread to finish.
        """
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("scheduler_stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Background polling loop. Exits when stop_event is set (BT-10)."""
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("scheduler_tick_exception")
            # Wait for interval or until stopped
            self._stop_event.wait(timeout=self._tick_interval)

    def _evaluate_schedules(self, session: Session) -> int:
        """Query active schedules, evaluate, fire due ones."""
        now = self._clock.now()

        # Query all active schedules
        schedules = session.execute(
            select(JobScheduleModel).where(
                JobScheduleModel.is_active == True,  # noqa: E712
            )
        ).scalars().all()

        fired = 0

        for schedule_model in schedules:
            # BT-10: Check stop signal between schedules
            if self._stop_event.is_set():
                break

            schedule_dto = schedule_model.to_dto()

            # BT-6: Pure evaluation
            if not should_fire(schedule_dto, now):
                continue

            # Fire the schedule
            try:
                idempotency_key = (
                    f"schedule-{schedule_model.id}-"
                    f"{now.strftime('%Y%m%d-%H%M')}"
                )

                executor = self._executor_factory(session)

                job = executor.submit_job(
                    job_name=schedule_model.job_name,
                    task_type=schedule_model.task_type,
                    idempotency_key=idempotency_key,
                    actor_id=self._actor_id,
                    parameters=schedule_model.parameters or {},
                    max_retries=schedule_model.max_retries,
                )

                result = executor.execute_job(job.job_id, self._actor_id)

                # Update schedule after execution
                schedule_model.last_run_at = now
                schedule_model.last_run_status = result.status.value

                # Compute next run
                from finance_batch.domain.types import ScheduleFrequency

                next_run = compute_next_run(
                    frequency=ScheduleFrequency(schedule_model.frequency),
                    last_run_at=now,
                    cron_expression=schedule_model.cron_expression,
                )
                schedule_model.next_run_at = next_run

                fired += 1

                logger.info(
                    "schedule_fired",
                    extra={
                        "schedule_id": str(schedule_model.id),
                        "job_name": schedule_model.job_name,
                        "job_id": str(job.job_id),
                        "status": result.status.value,
                        "next_run_at": str(next_run) if next_run else None,
                    },
                )

            except Exception:
                logger.exception(
                    "schedule_fire_failed",
                    extra={
                        "schedule_id": str(schedule_model.id),
                        "job_name": schedule_model.job_name,
                    },
                )

        return fired
