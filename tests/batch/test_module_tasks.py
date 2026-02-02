"""
Tests for finance_batch.tasks module implementations -- Phase 7.

Validates all 10 module task classes: protocol compliance, task_type/description,
prepare_items with parameters, execute_item behavior, and TaskRegistry integration.

Uses mock sessions for DB-querying tasks and direct parameter passing for
parameter-based tasks.
"""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_batch.domain.types import BatchItemStatus
from finance_batch.tasks.base import (
    BatchItemInput,
    BatchTask,
    BatchTaskResult,
    TaskRegistry,
)

from finance_batch.tasks.ap_tasks import InvoiceMatchTask, PaymentRunTask
from finance_batch.tasks.ar_tasks import DunningLetterTask, SmallBalanceWriteOffTask
from finance_batch.tasks.assets_tasks import MassDepreciationTask
from finance_batch.tasks.cash_tasks import BankReconcileTask
from finance_batch.tasks.credit_loss_tasks import ECLCalculationTask
from finance_batch.tasks.gl_tasks import PeriodEndRevaluationTask, RecurringEntryTask
from finance_batch.tasks.payroll_tasks import LaborCostAllocationTask


# =============================================================================
# Fixtures
# =============================================================================

NOW = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

ALL_TASK_CLASSES = [
    PaymentRunTask,
    InvoiceMatchTask,
    DunningLetterTask,
    SmallBalanceWriteOffTask,
    MassDepreciationTask,
    BankReconcileTask,
    ECLCalculationTask,
    RecurringEntryTask,
    PeriodEndRevaluationTask,
    LaborCostAllocationTask,
]

EXPECTED_TASK_TYPES = {
    PaymentRunTask: "ap.payment_run",
    InvoiceMatchTask: "ap.invoice_match",
    DunningLetterTask: "ar.dunning_letters",
    SmallBalanceWriteOffTask: "ar.small_balance_write_off",
    MassDepreciationTask: "assets.mass_depreciation",
    BankReconcileTask: "cash.auto_reconcile",
    ECLCalculationTask: "credit_loss.ecl_calculation",
    RecurringEntryTask: "gl.recurring_entries",
    PeriodEndRevaluationTask: "gl.period_end_revaluation",
    LaborCostAllocationTask: "payroll.labor_allocation",
}


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy session for DB-querying tasks."""
    session = MagicMock(spec=Session)
    # Default: queries return empty results (supports chained .filter().all()
    # as well as plain .all() without filter)
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = []
    session.query.return_value = mock_query
    return session


# =============================================================================
# Protocol compliance
# =============================================================================


class TestProtocolCompliance:
    @pytest.mark.parametrize("task_cls", ALL_TASK_CLASSES)
    def test_implements_batch_task_protocol(self, task_cls):
        task = task_cls()
        assert isinstance(task, BatchTask)

    @pytest.mark.parametrize("task_cls", ALL_TASK_CLASSES)
    def test_task_type_is_string(self, task_cls):
        task = task_cls()
        assert isinstance(task.task_type, str)
        assert len(task.task_type) > 0

    @pytest.mark.parametrize("task_cls", ALL_TASK_CLASSES)
    def test_description_is_string(self, task_cls):
        task = task_cls()
        assert isinstance(task.description, str)
        assert len(task.description) > 0


# =============================================================================
# Task type correctness
# =============================================================================


class TestTaskTypes:
    @pytest.mark.parametrize(
        "task_cls,expected_type",
        list(EXPECTED_TASK_TYPES.items()),
    )
    def test_task_type_matches_expected(self, task_cls, expected_type):
        task = task_cls()
        assert task.task_type == expected_type

    def test_all_task_types_unique(self):
        types = [cls().task_type for cls in ALL_TASK_CLASSES]
        assert len(types) == len(set(types)), f"Duplicate task types: {types}"

    def test_all_task_types_dotted_convention(self):
        for cls in ALL_TASK_CLASSES:
            task_type = cls().task_type
            assert "." in task_type, (
                f"{cls.__name__}.task_type = '{task_type}' missing dot separator"
            )


# =============================================================================
# execute_item tests (no DB required)
# =============================================================================


class TestExecuteItem:
    @pytest.mark.parametrize("task_cls", ALL_TASK_CLASSES)
    def test_execute_item_returns_succeeded(self, task_cls, mock_session):
        task = task_cls()
        item = BatchItemInput(item_index=0, item_key="test-item-001", payload={})
        result = task.execute_item(item, {}, mock_session, NOW)

        assert isinstance(result, BatchTaskResult)
        assert result.status == BatchItemStatus.SUCCEEDED

    @pytest.mark.parametrize("task_cls", ALL_TASK_CLASSES)
    def test_execute_item_returns_result_data(self, task_cls, mock_session):
        task = task_cls()
        item = BatchItemInput(item_index=0, item_key="test-item-001", payload={})
        result = task.execute_item(item, {}, mock_session, NOW)

        assert result.result_data is not None
        assert isinstance(result.result_data, dict)

    def test_payment_run_returns_invoice_id(self, mock_session):
        task = PaymentRunTask()
        item = BatchItemInput(
            item_index=0,
            item_key="inv-001",
            payload={"invoice_id": "inv-001"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["invoice_id"] == "inv-001"

    def test_invoice_match_returns_invoice_id(self, mock_session):
        task = InvoiceMatchTask()
        item = BatchItemInput(
            item_index=0,
            item_key="inv-002",
            payload={"invoice_id": "inv-002"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["invoice_id"] == "inv-002"

    def test_depreciation_returns_asset_id(self, mock_session):
        task = MassDepreciationTask()
        item = BatchItemInput(
            item_index=0,
            item_key="asset-001",
            payload={"asset_id": "asset-001"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["asset_id"] == "asset-001"

    def test_reconcile_returns_statement_line_id(self, mock_session):
        task = BankReconcileTask()
        item = BatchItemInput(
            item_index=0,
            item_key="line-001",
            payload={"statement_line_id": "line-001"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["statement_line_id"] == "line-001"

    def test_recurring_entry_returns_template_id(self, mock_session):
        task = RecurringEntryTask()
        item = BatchItemInput(
            item_index=0,
            item_key="tpl-001",
            payload={"template_id": "tpl-001"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["template_id"] == "tpl-001"

    def test_ecl_returns_segment(self, mock_session):
        task = ECLCalculationTask()
        item = BatchItemInput(
            item_index=0,
            item_key="commercial-loans",
            payload={"segment_name": "commercial-loans"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["segment"] == "commercial-loans"

    def test_revaluation_returns_item_key(self, mock_session):
        task = PeriodEndRevaluationTask()
        item = BatchItemInput(
            item_index=0,
            item_key="1100-EUR",
            payload={"account_code": "1100", "currency": "EUR"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["item_key"] == "1100-EUR"

    def test_labor_allocation_returns_payroll_run_id(self, mock_session):
        task = LaborCostAllocationTask()
        item = BatchItemInput(
            item_index=0,
            item_key="run-001",
            payload={"payroll_run_id": "run-001"},
        )
        result = task.execute_item(item, {}, mock_session, NOW)
        assert result.result_data["payroll_run_id"] == "run-001"


# =============================================================================
# prepare_items -- parameter-based tasks (no DB query needed)
# =============================================================================


class TestPrepareItemsParameterBased:
    def test_ecl_prepare_items_from_segments(self, mock_session):
        task = ECLCalculationTask()
        params = {
            "segments": [
                {"segment_name": "commercial-loans", "pd": "0.02"},
                {"segment_name": "consumer-loans", "pd": "0.05"},
                {"segment_name": "mortgage", "pd": "0.01"},
            ],
        }
        items = task.prepare_items(params, mock_session, NOW)

        assert isinstance(items, tuple)
        assert len(items) == 3
        assert items[0].item_key == "commercial-loans"
        assert items[1].item_key == "consumer-loans"
        assert items[2].item_key == "mortgage"
        assert items[0].item_index == 0
        assert items[2].item_index == 2

    def test_ecl_empty_segments(self, mock_session):
        task = ECLCalculationTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_revaluation_prepare_items_from_params(self, mock_session):
        task = PeriodEndRevaluationTask()
        params = {
            "revaluation_items": [
                {"account_code": "1100", "currency": "EUR"},
                {"account_code": "1200", "currency": "GBP"},
            ],
        }
        items = task.prepare_items(params, mock_session, NOW)

        assert isinstance(items, tuple)
        assert len(items) == 2
        assert items[0].item_key == "1100-EUR"
        assert items[1].item_key == "1200-GBP"

    def test_revaluation_empty_items(self, mock_session):
        task = PeriodEndRevaluationTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()


# =============================================================================
# prepare_items -- DB-querying tasks (mock session returns empty)
# =============================================================================


class TestPrepareItemsDBBased:
    """Test prepare_items for DB-querying tasks with mock session returning empty."""

    def test_payment_run_empty_when_no_approved(self, mock_session):
        task = PaymentRunTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_invoice_match_empty_when_no_pending(self, mock_session):
        task = InvoiceMatchTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_dunning_empty_when_no_overdue(self, mock_session):
        task = DunningLetterTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_write_off_empty_when_no_open(self, mock_session):
        task = SmallBalanceWriteOffTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_depreciation_empty_when_no_active(self, mock_session):
        task = MassDepreciationTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_reconcile_empty_when_no_unmatched(self, mock_session):
        task = BankReconcileTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_recurring_entry_empty_when_no_templates(self, mock_session):
        task = RecurringEntryTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()

    def test_labor_allocation_empty_when_no_runs(self, mock_session):
        task = LaborCostAllocationTask()
        items = task.prepare_items({}, mock_session, NOW)
        assert items == ()


# =============================================================================
# TaskRegistry integration
# =============================================================================


class TestRegistryIntegration:
    def test_register_all_tasks(self):
        registry = TaskRegistry()
        for cls in ALL_TASK_CLASSES:
            registry.register(cls())

        assert len(registry) == 10

    def test_all_tasks_retrievable(self):
        registry = TaskRegistry()
        for cls in ALL_TASK_CLASSES:
            registry.register(cls())

        for cls, expected_type in EXPECTED_TASK_TYPES.items():
            retrieved = registry.get(expected_type)
            assert isinstance(retrieved, cls)

    def test_list_tasks_sorted(self):
        registry = TaskRegistry()
        for cls in ALL_TASK_CLASSES:
            registry.register(cls())

        task_types = registry.list_tasks()
        assert task_types == tuple(sorted(task_types))
        assert len(task_types) == 10

    def test_no_duplicate_task_types(self):
        registry = TaskRegistry()
        for cls in ALL_TASK_CLASSES:
            registry.register(cls())

        # Second registration of same type should raise
        with pytest.raises(ValueError, match="already registered"):
            registry.register(PaymentRunTask())
