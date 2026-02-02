"""
finance_batch.tasks -- Task protocol, registry, and module task implementations.

ZERO kernel/module/engine/service imports in base.py.
Module task files import from their respective finance_modules packages.
"""

from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTask,
    BatchTaskResult,
    TaskRegistry,
    default_task_registry,
)

__all__ = [
    "BatchItemInput",
    "BatchTask",
    "BatchTaskResult",
    "TaskRegistry",
    "default_task_registry",
]
