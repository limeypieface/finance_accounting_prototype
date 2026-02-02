"""
Tests for finance_batch.domain.schedule -- Phase 5.

Validates pure schedule evaluation functions: cron parsing,
should_fire(), compute_next_run(), and edge cases.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from finance_batch.domain.schedule import (
    CronSpec,
    _parse_cron_field,
    compute_next_run,
    matches_cron,
    parse_cron,
    should_fire,
)
from finance_batch.domain.types import (
    BatchJobStatus,
    JobSchedule,
    ScheduleFrequency,
)


# =============================================================================
# CronSpec tests
# =============================================================================


class TestCronSpec:
    def test_frozen(self):
        spec = CronSpec()
        with pytest.raises(FrozenInstanceError):
            spec.minutes = frozenset()  # type: ignore[misc]

    def test_defaults_cover_all_values(self):
        spec = CronSpec()
        assert spec.minutes == frozenset(range(60))
        assert spec.hours == frozenset(range(24))
        assert spec.days_of_month == frozenset(range(1, 32))
        assert spec.months == frozenset(range(1, 13))
        assert spec.days_of_week == frozenset(range(7))


# =============================================================================
# _parse_cron_field tests
# =============================================================================


class TestParseCronField:
    def test_wildcard(self):
        result = _parse_cron_field("*", 0, 59)
        assert result == frozenset(range(60))

    def test_single_value(self):
        result = _parse_cron_field("5", 0, 59)
        assert result == frozenset({5})

    def test_range(self):
        result = _parse_cron_field("1-5", 0, 59)
        assert result == frozenset({1, 2, 3, 4, 5})

    def test_step(self):
        result = _parse_cron_field("*/15", 0, 59)
        assert result == frozenset({0, 15, 30, 45})

    def test_range_with_step(self):
        result = _parse_cron_field("1-10/3", 0, 59)
        assert result == frozenset({1, 4, 7, 10})

    def test_comma_separated(self):
        result = _parse_cron_field("1,5,10", 0, 59)
        assert result == frozenset({1, 5, 10})

    def test_value_out_of_range(self):
        with pytest.raises(ValueError, match="outside range"):
            _parse_cron_field("60", 0, 59)

    def test_range_start_gt_end(self):
        with pytest.raises(ValueError, match="start > end"):
            _parse_cron_field("10-5", 0, 59)

    def test_step_zero(self):
        with pytest.raises(ValueError, match="Step must be positive"):
            _parse_cron_field("*/0", 0, 59)


# =============================================================================
# parse_cron tests
# =============================================================================


class TestParseCron:
    def test_every_minute(self):
        spec = parse_cron("* * * * *")
        assert spec.minutes == frozenset(range(60))
        assert spec.hours == frozenset(range(24))

    def test_daily_at_6am(self):
        spec = parse_cron("0 6 * * *")
        assert spec.minutes == frozenset({0})
        assert spec.hours == frozenset({6})

    def test_weekdays_only(self):
        spec = parse_cron("0 9 * * 1-5")
        assert spec.days_of_week == frozenset({1, 2, 3, 4, 5})

    def test_quarterly(self):
        spec = parse_cron("0 0 1 1,4,7,10 *")
        assert spec.months == frozenset({1, 4, 7, 10})
        assert spec.days_of_month == frozenset({1})

    def test_wrong_field_count_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("* * *")

    def test_six_fields_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("* * * * * *")


# =============================================================================
# matches_cron tests
# =============================================================================


class TestMatchesCron:
    def test_match(self):
        spec = parse_cron("0 6 * * *")
        dt = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        assert matches_cron(spec, dt) is True

    def test_no_match_wrong_minute(self):
        spec = parse_cron("0 6 * * *")
        dt = datetime(2026, 2, 1, 6, 5, tzinfo=timezone.utc)
        assert matches_cron(spec, dt) is False

    def test_no_match_wrong_hour(self):
        spec = parse_cron("0 6 * * *")
        dt = datetime(2026, 2, 1, 7, 0, tzinfo=timezone.utc)
        assert matches_cron(spec, dt) is False

    def test_weekday_match(self):
        spec = parse_cron("0 9 * * 0")  # Sunday
        # 2026-02-01 is a Sunday
        dt = datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc)
        assert matches_cron(spec, dt) is True

    def test_weekday_no_match(self):
        spec = parse_cron("0 9 * * 0")  # Sunday
        # 2026-02-02 is Monday
        dt = datetime(2026, 2, 2, 9, 0, tzinfo=timezone.utc)
        assert matches_cron(spec, dt) is False


# =============================================================================
# should_fire tests
# =============================================================================


def _make_schedule(**overrides) -> JobSchedule:
    defaults = dict(
        schedule_id=uuid4(),
        job_name="test_job",
        task_type="test.task",
        frequency=ScheduleFrequency.DAILY,
    )
    defaults.update(overrides)
    return JobSchedule(**defaults)


class TestShouldFire:
    def test_inactive_never_fires(self):
        schedule = _make_schedule(is_active=False)
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is False

    def test_on_demand_never_fires(self):
        schedule = _make_schedule(frequency=ScheduleFrequency.ON_DEMAND)
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is False

    def test_period_end_never_fires(self):
        schedule = _make_schedule(frequency=ScheduleFrequency.PERIOD_END)
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is False

    def test_once_fires_if_never_run(self):
        schedule = _make_schedule(
            frequency=ScheduleFrequency.ONCE,
            last_run_at=None,
        )
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is True

    def test_once_does_not_fire_if_already_run(self):
        schedule = _make_schedule(
            frequency=ScheduleFrequency.ONCE,
            last_run_at=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is False

    def test_daily_fires_after_next_run(self):
        next_run = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        schedule = _make_schedule(
            frequency=ScheduleFrequency.DAILY,
            next_run_at=next_run,
        )
        # At 6am
        assert should_fire(schedule, next_run) is True
        # After 6am
        after = next_run + timedelta(hours=1)
        assert should_fire(schedule, after) is True

    def test_daily_does_not_fire_before_next_run(self):
        next_run = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        schedule = _make_schedule(
            frequency=ScheduleFrequency.DAILY,
            next_run_at=next_run,
        )
        before = next_run - timedelta(hours=1)
        assert should_fire(schedule, before) is False

    def test_daily_fires_with_no_next_run(self):
        schedule = _make_schedule(
            frequency=ScheduleFrequency.DAILY,
            next_run_at=None,
        )
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is True

    def test_cron_match_required(self):
        next_run = datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc)
        schedule = _make_schedule(
            frequency=ScheduleFrequency.DAILY,
            next_run_at=next_run,
            cron_expression="0 6 * * *",  # Only at 6am
        )
        # 5am -- past next_run but doesn't match cron
        at_5am = datetime(2026, 2, 1, 5, 30, tzinfo=timezone.utc)
        assert should_fire(schedule, at_5am) is False

        # 6am -- past next_run and matches cron
        at_6am = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, at_6am) is True

    def test_invalid_cron_returns_false(self):
        schedule = _make_schedule(
            frequency=ScheduleFrequency.DAILY,
            cron_expression="bad cron",
        )
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert should_fire(schedule, now) is False


# =============================================================================
# compute_next_run tests
# =============================================================================


class TestComputeNextRun:
    def test_on_demand_returns_none(self):
        assert compute_next_run(ScheduleFrequency.ON_DEMAND, None) is None

    def test_period_end_returns_none(self):
        assert compute_next_run(ScheduleFrequency.PERIOD_END, None) is None

    def test_once_returns_none(self):
        last = datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert compute_next_run(ScheduleFrequency.ONCE, last) is None

    def test_none_last_run_returns_none(self):
        assert compute_next_run(ScheduleFrequency.DAILY, None) is None

    def test_hourly(self):
        last = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleFrequency.HOURLY, last)
        assert result == last + timedelta(hours=1)

    def test_daily(self):
        last = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleFrequency.DAILY, last)
        assert result == last + timedelta(days=1)

    def test_weekly(self):
        last = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleFrequency.WEEKLY, last)
        assert result == last + timedelta(weeks=1)

    def test_monthly(self):
        last = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        result = compute_next_run(ScheduleFrequency.MONTHLY, last)
        assert result == last + timedelta(days=30)

    def test_with_cron_expression(self):
        last = datetime(2026, 2, 1, 5, 30, tzinfo=timezone.utc)
        result = compute_next_run(
            ScheduleFrequency.DAILY,
            last,
            cron_expression="0 6 * * *",
        )
        # Should be next 6:00 AM
        assert result is not None
        assert result.hour == 6
        assert result.minute == 0
        assert result > last

    def test_with_base_time_override(self):
        last = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        base = datetime(2026, 2, 5, 6, 0, tzinfo=timezone.utc)
        result = compute_next_run(
            ScheduleFrequency.DAILY,
            last,
            base_time=base,
        )
        assert result == base + timedelta(days=1)

    def test_invalid_cron_falls_back_to_delta(self):
        last = datetime(2026, 2, 1, 6, 0, tzinfo=timezone.utc)
        result = compute_next_run(
            ScheduleFrequency.DAILY,
            last,
            cron_expression="bad cron",
        )
        # Falls back to frequency-based delta
        assert result == last + timedelta(days=1)
