"""
Tests for finance_batch.tasks.base -- Phase 3.

Validates BatchTask Protocol, BatchItemInput/BatchTaskResult DTOs,
TaskRegistry registration/lookup/listing, and default_task_registry().
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTask,
    BatchTaskResult,
    TaskRegistry,
    default_task_registry,
)


# =============================================================================
# Concrete test implementations of BatchTask
# =============================================================================


class FakeDepreciationTask:
    """Minimal BatchTask implementation for testing."""

    @property
    def task_type(self) -> str:
        return "assets.mass_depreciation"

    @property
    def description(self) -> str:
        return "Mass depreciation calculation"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return (
            BatchItemInput(item_index=0, item_key="asset-001"),
            BatchItemInput(item_index=1, item_key="asset-002"),
        )

    def execute_item(
        self,
        item: BatchItemInput,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={"journal_entry_id": "je-001"},
        )


class FakeReconcileTask:
    """Another BatchTask implementation for registry tests."""

    @property
    def task_type(self) -> str:
        return "cash.auto_reconcile"

    @property
    def description(self) -> str:
        return "Auto bank reconciliation"

    def prepare_items(
        self,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> tuple[BatchItemInput, ...]:
        return ()

    def execute_item(
        self,
        item: BatchItemInput,
        parameters: dict[str, Any],
        session: Session,
        as_of: datetime,
    ) -> BatchTaskResult:
        return BatchTaskResult(status=BatchItemStatus.SUCCEEDED)


# =============================================================================
# BatchItemInput tests
# =============================================================================


class TestBatchItemInput:
    def test_construction(self):
        item = BatchItemInput(item_index=0, item_key="asset-001")
        assert item.item_index == 0
        assert item.item_key == "asset-001"
        assert item.payload == {}

    def test_with_payload(self):
        item = BatchItemInput(
            item_index=3,
            item_key="inv-100",
            payload={"amount": "1500.00"},
        )
        assert item.payload["amount"] == "1500.00"

    def test_frozen(self):
        item = BatchItemInput(item_index=0, item_key="x")
        with pytest.raises(FrozenInstanceError):
            item.item_key = "y"  # type: ignore[misc]


# =============================================================================
# BatchTaskResult tests
# =============================================================================


class TestBatchTaskResult:
    def test_success(self):
        result = BatchTaskResult(
            status=BatchItemStatus.SUCCEEDED,
            result_data={"journal_entry_id": "je-001"},
        )
        assert result.status == BatchItemStatus.SUCCEEDED
        assert result.result_data == {"journal_entry_id": "je-001"}
        assert result.error_code is None
        assert result.error_message is None

    def test_failure(self):
        result = BatchTaskResult(
            status=BatchItemStatus.FAILED,
            error_code="ACCOUNT_NOT_FOUND",
            error_message="Depreciation account missing",
        )
        assert result.status == BatchItemStatus.FAILED
        assert result.error_code == "ACCOUNT_NOT_FOUND"

    def test_frozen(self):
        result = BatchTaskResult(status=BatchItemStatus.SUCCEEDED)
        with pytest.raises(FrozenInstanceError):
            result.status = BatchItemStatus.FAILED  # type: ignore[misc]

    def test_defaults(self):
        result = BatchTaskResult(status=BatchItemStatus.SUCCEEDED)
        assert result.result_data is None
        assert result.error_code is None
        assert result.error_message is None


# =============================================================================
# BatchTask Protocol tests
# =============================================================================


class TestBatchTaskProtocol:
    def test_fake_task_is_protocol_compliant(self):
        task = FakeDepreciationTask()
        assert isinstance(task, BatchTask)

    def test_task_type(self):
        task = FakeDepreciationTask()
        assert task.task_type == "assets.mass_depreciation"

    def test_description(self):
        task = FakeDepreciationTask()
        assert task.description == "Mass depreciation calculation"

    def test_prepare_items_returns_tuple(self):
        task = FakeDepreciationTask()
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        items = task.prepare_items({}, None, now)  # type: ignore[arg-type]
        assert isinstance(items, tuple)
        assert len(items) == 2
        assert items[0].item_key == "asset-001"

    def test_execute_item_returns_result(self):
        task = FakeDepreciationTask()
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        item = BatchItemInput(item_index=0, item_key="asset-001")
        result = task.execute_item(item, {}, None, now)  # type: ignore[arg-type]
        assert result.status == BatchItemStatus.SUCCEEDED


# =============================================================================
# TaskRegistry tests
# =============================================================================


class TestTaskRegistry:
    def test_register_and_get(self):
        registry = TaskRegistry()
        task = FakeDepreciationTask()
        registry.register(task)

        retrieved = registry.get("assets.mass_depreciation")
        assert retrieved is task

    def test_register_duplicate_raises(self):
        registry = TaskRegistry()
        registry.register(FakeDepreciationTask())

        with pytest.raises(ValueError, match="already registered"):
            registry.register(FakeDepreciationTask())

    def test_get_missing_raises(self):
        registry = TaskRegistry()

        with pytest.raises(KeyError, match="No task registered"):
            registry.get("nonexistent.task")

    def test_get_missing_shows_available(self):
        registry = TaskRegistry()
        registry.register(FakeDepreciationTask())

        with pytest.raises(KeyError, match="assets.mass_depreciation"):
            registry.get("nonexistent.task")

    def test_list_tasks_empty(self):
        registry = TaskRegistry()
        assert registry.list_tasks() == ()

    def test_list_tasks_sorted(self):
        registry = TaskRegistry()
        registry.register(FakeReconcileTask())
        registry.register(FakeDepreciationTask())

        assert registry.list_tasks() == (
            "assets.mass_depreciation",
            "cash.auto_reconcile",
        )

    def test_len(self):
        registry = TaskRegistry()
        assert len(registry) == 0
        registry.register(FakeDepreciationTask())
        assert len(registry) == 1

    def test_contains(self):
        registry = TaskRegistry()
        registry.register(FakeDepreciationTask())

        assert "assets.mass_depreciation" in registry
        assert "unknown.task" not in registry

    def test_multiple_registrations(self):
        registry = TaskRegistry()
        registry.register(FakeDepreciationTask())
        registry.register(FakeReconcileTask())

        assert len(registry) == 2
        assert registry.get("assets.mass_depreciation").task_type == "assets.mass_depreciation"
        assert registry.get("cash.auto_reconcile").task_type == "cash.auto_reconcile"


class TestDefaultTaskRegistry:
    def test_returns_empty_registry(self):
        registry = default_task_registry()
        assert isinstance(registry, TaskRegistry)
        assert len(registry) == 0

    def test_returns_new_instance_each_call(self):
        r1 = default_task_registry()
        r2 = default_task_registry()
        assert r1 is not r2
