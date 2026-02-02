"""
Tests for batch schedule configuration (Phase 6).

Validates BatchScheduleDef schema, parse_batch_schedule loader,
and assembler integration with batch_schedules.yaml.
"""

from dataclasses import FrozenInstanceError

import pytest

from finance_config.loader import parse_batch_schedule
from finance_config.schema import BatchScheduleDef


# =============================================================================
# BatchScheduleDef tests
# =============================================================================


class TestBatchScheduleDef:
    def test_construction_minimal(self):
        sched = BatchScheduleDef(
            name="daily_recon",
            task_type="cash.auto_reconcile",
            frequency="daily",
        )
        assert sched.name == "daily_recon"
        assert sched.task_type == "cash.auto_reconcile"
        assert sched.frequency == "daily"
        assert sched.parameters == {}
        assert sched.cron_expression is None
        assert sched.max_retries == 3
        assert sched.is_active is True
        assert sched.legal_entity is None

    def test_construction_full(self):
        sched = BatchScheduleDef(
            name="monthly_depreciation",
            task_type="assets.mass_depreciation",
            frequency="monthly",
            parameters={"method": "straight_line"},
            cron_expression="0 2 1 * *",
            max_retries=5,
            is_active=True,
            legal_entity="ACME-US",
        )
        assert sched.parameters["method"] == "straight_line"
        assert sched.cron_expression == "0 2 1 * *"
        assert sched.max_retries == 5
        assert sched.legal_entity == "ACME-US"

    def test_frozen(self):
        sched = BatchScheduleDef(
            name="test",
            task_type="test.task",
            frequency="daily",
        )
        with pytest.raises(FrozenInstanceError):
            sched.name = "changed"  # type: ignore[misc]


# =============================================================================
# parse_batch_schedule tests
# =============================================================================


class TestParseBatchSchedule:
    def test_parse_minimal(self):
        data = {
            "name": "daily_recon",
            "task_type": "cash.auto_reconcile",
            "frequency": "daily",
        }
        result = parse_batch_schedule(data)

        assert isinstance(result, BatchScheduleDef)
        assert result.name == "daily_recon"
        assert result.task_type == "cash.auto_reconcile"
        assert result.frequency == "daily"
        assert result.parameters == {}
        assert result.is_active is True

    def test_parse_full(self):
        data = {
            "name": "monthly_depreciation",
            "task_type": "assets.mass_depreciation",
            "frequency": "monthly",
            "parameters": {"method": "straight_line", "effective_date": "2026-01-31"},
            "cron_expression": "0 2 1 * *",
            "max_retries": 5,
            "is_active": True,
            "legal_entity": "ACME-US",
        }
        result = parse_batch_schedule(data)

        assert result.parameters["method"] == "straight_line"
        assert result.cron_expression == "0 2 1 * *"
        assert result.max_retries == 5
        assert result.legal_entity == "ACME-US"

    def test_parse_inactive(self):
        data = {
            "name": "disabled_job",
            "task_type": "gl.recurring",
            "frequency": "daily",
            "is_active": False,
        }
        result = parse_batch_schedule(data)
        assert result.is_active is False

    def test_parse_missing_required_field_raises(self):
        with pytest.raises(KeyError):
            parse_batch_schedule({"name": "test"})

    def test_parse_defaults(self):
        data = {
            "name": "test",
            "task_type": "test.task",
            "frequency": "hourly",
        }
        result = parse_batch_schedule(data)
        assert result.max_retries == 3
        assert result.cron_expression is None
        assert result.legal_entity is None
        assert result.is_active is True
