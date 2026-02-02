"""
BatchTask protocol, supporting types, and TaskRegistry (Phase 3).

Contract:
    ``BatchTask`` defines the interface every batch task must implement.
    ``TaskRegistry`` stores registered tasks keyed by ``task_type``.
    ``default_task_registry()`` returns a fresh, empty registry.

Architecture:
    finance_batch/tasks.  ZERO imports from kernel/modules/engines/services.
    Only imports from finance_batch.domain (frozen DTOs) and stdlib.

Invariants enforced:
    BT-14 -- Task registry: one task per ``task_type`` string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus


# =============================================================================
# Supporting DTOs
# =============================================================================


@dataclass(frozen=True)
class BatchItemInput:
    """Input specification for a single batch item.

    Created by ``BatchTask.prepare_items()``.
    """

    item_index: int
    item_key: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchTaskResult:
    """Result returned by ``BatchTask.execute_item()``.

    The executor uses this to build ``BatchItemResult`` DTOs.
    """

    status: BatchItemStatus
    result_data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


# =============================================================================
# BatchTask Protocol
# =============================================================================


@runtime_checkable
class BatchTask(Protocol):
    """Protocol defining the interface for batch task implementations.

    Each implementation handles one ``task_type`` (e.g., "assets.mass_depreciation").

    Contract:
        - ``task_type``: unique string key registered in TaskRegistry.
        - ``description``: human-readable label for UI / audit trail.
        - ``prepare_items()``: queries eligible records, returns immutable tuple.
        - ``execute_item()``: processes ONE item within a SAVEPOINT.

    Non-goals:
        - Does NOT manage transactions -- the executor owns SAVEPOINT lifecycle.
        - Does NOT retry -- the executor handles retry logic (BT-7).
    """

    @property
    def task_type(self) -> str: ...

    @property
    def description(self) -> str: ...

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        """Query eligible items for this batch run.

        Args:
            parameters: Job-level parameters from BatchJob.parameters.
            session: Database session for querying eligible records.
            as_of: Clock-injected timestamp for determinism (BT-4).

        Returns:
            Immutable tuple of BatchItemInput, one per item to process.
        """
        ...

    def execute_item(
        self,
        item: BatchItemInput,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> BatchTaskResult:
        """Execute a single batch item within a SAVEPOINT.

        Args:
            item: The item to process.
            parameters: Job-level parameters.
            session: Database session (SAVEPOINT active).
            as_of: Clock-injected timestamp (BT-4).

        Returns:
            BatchTaskResult with status and optional result_data / error info.
        """
        ...


# =============================================================================
# TaskRegistry
# =============================================================================


class TaskRegistry:
    """Registry mapping task_type strings to BatchTask implementations.

    Contract:
        - ``register()`` adds a task; raises ValueError on duplicate.
        - ``get()`` retrieves by task_type; raises KeyError if missing.
        - ``list_tasks()`` returns all registered task_type strings.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, BatchTask] = {}

    def register(self, task: BatchTask) -> None:
        """Register a batch task implementation.

        Raises:
            ValueError: If a task with the same task_type is already registered.
        """
        if task.task_type in self._tasks:
            raise ValueError(
                f"Task type '{task.task_type}' is already registered"
            )
        self._tasks[task.task_type] = task

    def get(self, task_type: str) -> BatchTask:
        """Retrieve a registered task by task_type.

        Raises:
            KeyError: If no task is registered for the given task_type.
        """
        try:
            return self._tasks[task_type]
        except KeyError:
            raise KeyError(
                f"No task registered for type '{task_type}'. "
                f"Available: {sorted(self._tasks.keys())}"
            ) from None

    def list_tasks(self) -> tuple[str, ...]:
        """Return all registered task_type strings, sorted."""
        return tuple(sorted(self._tasks.keys()))

    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, task_type: str) -> bool:
        return task_type in self._tasks


def default_task_registry() -> TaskRegistry:
    """Create and return a fresh, empty TaskRegistry."""
    return TaskRegistry()
