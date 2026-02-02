"""
Pure schedule evaluation functions (Phase 5).

Contract:
    ``should_fire(schedule, as_of)`` and ``compute_next_run()`` are PURE --
    no I/O, no side effects.  The scheduler reads ``next_run_at`` and the
    current clock, with no side effects (BT-6).

Architecture: finance_batch/domain.  ZERO I/O.

Invariants enforced:
    BT-6 -- Schedule evaluation is pure.
    BT-4 -- All timestamps from caller (no datetime.now() calls).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from finance_batch.domain.types import JobSchedule, ScheduleFrequency


# =============================================================================
# CronSpec (lightweight cron parser)
# =============================================================================


@dataclass(frozen=True)
class CronSpec:
    """Parsed cron expression (minute hour day_of_month month day_of_week).

    Each field is a frozenset of valid integer values.
    Supports: *, values, ranges (1-5), steps (*/5, 1-10/2).
    """

    minutes: frozenset[int] = field(default_factory=lambda: frozenset(range(60)))
    hours: frozenset[int] = field(default_factory=lambda: frozenset(range(24)))
    days_of_month: frozenset[int] = field(default_factory=lambda: frozenset(range(1, 32)))
    months: frozenset[int] = field(default_factory=lambda: frozenset(range(1, 13)))
    days_of_week: frozenset[int] = field(default_factory=lambda: frozenset(range(7)))


def _parse_cron_field(field_str: str, min_val: int, max_val: int) -> frozenset[int]:
    """Parse a single cron field into a frozenset of valid values.

    Supports:
        * -- all values
        N -- single value
        N-M -- range
        */N -- step from min
        N-M/S -- range with step

    Raises:
        ValueError: If the field is syntactically invalid or values out of range.
    """
    values: set[int] = set()

    for part in field_str.split(","):
        part = part.strip()

        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"Step must be positive: {step}")

            if range_part == "*":
                start, end = min_val, max_val
            elif "-" in range_part:
                s, e = range_part.split("-", 1)
                start, end = int(s), int(e)
            else:
                start = int(range_part)
                end = max_val

            for v in range(start, end + 1, step):
                if min_val <= v <= max_val:
                    values.add(v)

        elif part == "*":
            values.update(range(min_val, max_val + 1))

        elif "-" in part:
            s, e = part.split("-", 1)
            start, end = int(s), int(e)
            if start > end:
                raise ValueError(f"Range start > end: {start}-{end}")
            for v in range(start, end + 1):
                if min_val <= v <= max_val:
                    values.add(v)

        else:
            v = int(part)
            if v < min_val or v > max_val:
                raise ValueError(
                    f"Value {v} outside range [{min_val}, {max_val}]"
                )
            values.add(v)

    return frozenset(values)


def parse_cron(expression: str) -> CronSpec:
    """Parse a 5-field cron expression into a CronSpec.

    Format: ``minute hour day_of_month month day_of_week``

    Raises:
        ValueError: If expression is malformed.
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cron expression must have 5 fields, got {len(parts)}: '{expression}'"
        )

    return CronSpec(
        minutes=_parse_cron_field(parts[0], 0, 59),
        hours=_parse_cron_field(parts[1], 0, 23),
        days_of_month=_parse_cron_field(parts[2], 1, 31),
        months=_parse_cron_field(parts[3], 1, 12),
        days_of_week=_parse_cron_field(parts[4], 0, 6),
    )


def matches_cron(spec: CronSpec, dt: datetime) -> bool:
    """Check if a datetime matches a cron spec (BT-6 pure).

    Cron convention: 0=Sunday, 1=Monday, ..., 6=Saturday.
    Python datetime.weekday(): 0=Monday, ..., 6=Sunday.
    """
    # Convert Python weekday (0=Mon) to cron weekday (0=Sun)
    cron_dow = (dt.weekday() + 1) % 7
    return (
        dt.minute in spec.minutes
        and dt.hour in spec.hours
        and dt.day in spec.days_of_month
        and dt.month in spec.months
        and cron_dow in spec.days_of_week
    )


# =============================================================================
# Schedule evaluation (pure)
# =============================================================================


def should_fire(schedule: JobSchedule, as_of: datetime) -> bool:
    """Determine if a schedule should fire at the given time (BT-6 pure).

    Rules:
        - Inactive schedules never fire.
        - ON_DEMAND never fires automatically.
        - PERIOD_END never fires automatically (triggered by period close).
        - ONCE fires if never run before.
        - For time-based frequencies: fires if ``as_of >= next_run_at``.
        - If cron_expression is set, also checks cron match.
    """
    if not schedule.is_active:
        return False

    if schedule.frequency == ScheduleFrequency.ON_DEMAND:
        return False

    if schedule.frequency == ScheduleFrequency.PERIOD_END:
        return False

    if schedule.frequency == ScheduleFrequency.ONCE:
        return schedule.last_run_at is None

    # Time-based: check next_run_at
    if schedule.next_run_at is not None:
        if as_of < schedule.next_run_at:
            return False

    # If cron expression is set, verify match
    if schedule.cron_expression:
        try:
            spec = parse_cron(schedule.cron_expression)
            if not matches_cron(spec, as_of):
                return False
        except ValueError:
            return False

    return True


def compute_next_run(
    frequency: ScheduleFrequency,
    last_run_at: datetime | None,
    cron_expression: str | None = None,
    base_time: datetime | None = None,
) -> datetime | None:
    """Compute the next run time for a schedule (BT-6 pure).

    Args:
        frequency: The schedule frequency.
        last_run_at: When the schedule last ran (None if never).
        cron_expression: Optional cron expression for fine-grained timing.
        base_time: Reference time (defaults to last_run_at if available).

    Returns:
        Next run datetime, or None for ON_DEMAND/PERIOD_END/ONCE.
    """
    if frequency in (
        ScheduleFrequency.ON_DEMAND,
        ScheduleFrequency.PERIOD_END,
        ScheduleFrequency.ONCE,
    ):
        return None

    base = base_time or last_run_at
    if base is None:
        return None

    # If cron expression provided, compute next matching time
    if cron_expression:
        try:
            spec = parse_cron(cron_expression)
            return _next_cron_match(spec, base)
        except ValueError:
            pass

    # Frequency-based delta
    delta_map = {
        ScheduleFrequency.HOURLY: timedelta(hours=1),
        ScheduleFrequency.DAILY: timedelta(days=1),
        ScheduleFrequency.WEEKLY: timedelta(weeks=1),
        ScheduleFrequency.MONTHLY: timedelta(days=30),  # Approximate
    }

    delta = delta_map.get(frequency)
    if delta is None:
        return None

    return base + delta


def _next_cron_match(spec: CronSpec, after: datetime) -> datetime:
    """Find the next datetime after ``after`` that matches the cron spec.

    Scans minute-by-minute up to 366 days (BT-6 bounded iteration).

    Raises:
        ValueError: If no match found within 366 days.
    """
    # Start from the next minute
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_iterations = 366 * 24 * 60  # ~1 year of minutes

    for _ in range(max_iterations):
        if matches_cron(spec, candidate):
            return candidate
        candidate += timedelta(minutes=1)

    raise ValueError(
        f"No cron match found within 366 days after {after}"
    )
