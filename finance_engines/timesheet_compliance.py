"""
Timesheet DCAA Compliance Engine (``finance_engines.timesheet_compliance``).

Responsibility
--------------
Pure validation functions for DCAA-compliant timekeeping:

* D1 -- daily recording enforcement (FAR 31.201-2(d))
* D3 -- total time accounting (CAS 418)
* D4 -- concurrent work overlap detection (CAS 418)
* D5 -- correction by reversal validation (R10)

Architecture position
---------------------
**Engines layer** -- pure functional core.  ZERO I/O, ZERO database,
ZERO clock reads.  May only import from ``finance_kernel.domain.values``.
All dates/times are passed as explicit parameters.

Invariants enforced
-------------------
* No ``datetime.now()`` or ``date.today()`` calls.
* No ORM, no services, no config imports.
* All functions are deterministic: same inputs = same outputs.

Failure modes
-------------
* Returns validation results (not exceptions) for business rule violations.
* Raises ``ValueError`` only for programming errors (invalid arguments).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, time, timedelta
from decimal import Decimal
from uuid import UUID

from finance_modules.payroll.dcaa_types import (
    ChargeType,
    ConcurrentWorkCheck,
    TimesheetEntry,
    TotalTimeRecord,
)


# ---------------------------------------------------------------------------
# D1: Daily recording validation (FAR 31.201-2(d))
# ---------------------------------------------------------------------------


def validate_daily_recording(
    work_date: date,
    submission_date: date,
    max_retroactive_days: int,
) -> tuple[bool, str | None]:
    """Validate that a time entry is submitted within the allowed window.

    DCAA requires time to be recorded daily or at least weekly.  This
    function enforces a configurable maximum number of days between the
    work_date and the submission_date.

    Args:
        work_date: The date the work was performed.
        submission_date: The date the entry is being submitted.
        max_retroactive_days: Maximum calendar days allowed between
            work_date and submission_date.

    Returns:
        Tuple of (is_valid, error_message).
        error_message is None when is_valid is True.
    """
    if submission_date < work_date:
        return False, (
            f"Submission date ({submission_date}) cannot precede "
            f"work date ({work_date})"
        )

    days_elapsed = (submission_date - work_date).days
    if days_elapsed > max_retroactive_days:
        return False, (
            f"Time entry for {work_date} submitted {days_elapsed} days late "
            f"(maximum allowed: {max_retroactive_days} days). "
            f"FAR 31.201-2(d) requires timely recording."
        )

    return True, None


def validate_all_entries_daily_recording(
    entries: tuple[TimesheetEntry, ...],
    submission_date: date,
    max_retroactive_days: int,
) -> list[tuple[UUID, str]]:
    """Validate daily recording for all entries in a submission.

    Returns:
        List of (entry_id, error_message) for entries that violate D1.
        Empty list means all entries are compliant.
    """
    violations = []
    for entry in entries:
        is_valid, message = validate_daily_recording(
            entry.work_date, submission_date, max_retroactive_days,
        )
        if not is_valid:
            violations.append((entry.entry_id, message))
    return violations


# ---------------------------------------------------------------------------
# D3: Total time accounting (CAS 418)
# ---------------------------------------------------------------------------


def compute_total_time_record(
    employee_id: UUID,
    pay_period_id: UUID,
    entries: tuple[TimesheetEntry, ...],
    expected_hours: Decimal,
    tolerance: Decimal = Decimal("0.25"),
) -> TotalTimeRecord:
    """Compute a CAS 418 total time accounting record.

    All hours (direct, indirect, leave, uncompensated) must be accounted
    for and should sum to the expected hours within tolerance.

    Args:
        employee_id: The employee being checked.
        pay_period_id: The pay period being checked.
        entries: All time entries for the employee in this period.
        expected_hours: Expected total hours (e.g., 40 for a standard week).
        tolerance: Acceptable variance from expected hours.

    Returns:
        TotalTimeRecord with computed totals and compliance flag.
    """
    direct = Decimal("0")
    indirect = Decimal("0")
    leave = Decimal("0")
    uncompensated = Decimal("0")

    for entry in entries:
        if entry.charge_type == ChargeType.DIRECT:
            direct += entry.hours
        elif entry.charge_type == ChargeType.INDIRECT:
            indirect += entry.hours
        elif entry.charge_type == ChargeType.LEAVE:
            leave += entry.hours
        elif entry.charge_type == ChargeType.UNCOMPENSATED:
            uncompensated += entry.hours

    return TotalTimeRecord(
        employee_id=employee_id,
        pay_period_id=pay_period_id,
        expected_hours=expected_hours,
        direct_hours=direct,
        indirect_hours=indirect,
        leave_hours=leave,
        uncompensated_hours=uncompensated,
        tolerance=tolerance,
    )


def validate_total_time_accounting(
    entries: tuple[TimesheetEntry, ...],
    expected_hours: Decimal,
    tolerance: Decimal = Decimal("0.25"),
) -> tuple[bool, Decimal]:
    """Quick check: do entries sum to expected hours within tolerance?

    Returns:
        Tuple of (is_compliant, variance).
    """
    total = sum((e.hours for e in entries), Decimal("0"))
    variance = total - expected_hours
    is_compliant = abs(variance) <= tolerance
    return is_compliant, variance


# ---------------------------------------------------------------------------
# D4: Concurrent work overlap detection (CAS 418)
# ---------------------------------------------------------------------------


def detect_concurrent_overlaps(
    entries: tuple[TimesheetEntry, ...],
) -> ConcurrentWorkCheck:
    """Detect overlapping time entries on different charge codes.

    An employee cannot be working on two different contracts/charge codes
    at the same time.  This checks for time range overlaps when
    start_time and end_time are provided.

    Args:
        entries: All time entries to check (should be for one employee,
            one work_date).

    Returns:
        ConcurrentWorkCheck with overlap details.
    """
    if not entries:
        return ConcurrentWorkCheck(
            employee_id=entries[0].entry_id if entries else UUID(int=0),
            work_date=date(1970, 1, 1),
            is_valid=True,
        )

    # Group entries by work_date
    by_date: dict[date, list[TimesheetEntry]] = defaultdict(list)
    for entry in entries:
        by_date[entry.work_date].append(entry)

    employee_id = entries[0].entry_id  # placeholder if no entries
    overlaps: list[tuple[UUID, UUID]] = []

    for work_date_key, day_entries in by_date.items():
        employee_id = day_entries[0].entry_id

        # Check for time range overlaps between different charge codes
        timed_entries = [
            e for e in day_entries
            if e.start_time is not None and e.end_time is not None
        ]

        for i in range(len(timed_entries)):
            for j in range(i + 1, len(timed_entries)):
                a = timed_entries[i]
                b = timed_entries[j]

                # Skip if same charge code (working on same project is OK)
                if a.charge_code == b.charge_code:
                    continue

                # Check time overlap
                if _times_overlap(a.start_time, a.end_time, b.start_time, b.end_time):
                    overlaps.append((a.entry_id, b.entry_id))

    total_hours = sum((e.hours for e in entries), Decimal("0"))

    return ConcurrentWorkCheck(
        employee_id=entries[0].entry_id,
        work_date=entries[0].work_date,
        is_valid=len(overlaps) == 0,
        overlapping_entries=tuple(overlaps),
        total_hours=total_hours,
    )


def _times_overlap(
    start_a: time, end_a: time,
    start_b: time, end_b: time,
) -> bool:
    """Check if two time ranges overlap (assumes same day, no midnight crossing)."""
    return start_a < end_b and start_b < end_a


# ---------------------------------------------------------------------------
# Daily hours validation
# ---------------------------------------------------------------------------


def validate_no_excessive_daily_hours(
    entries: tuple[TimesheetEntry, ...],
    max_daily_hours: Decimal = Decimal("24"),
) -> tuple[bool, dict[date, Decimal]]:
    """Check that total hours per date do not exceed a maximum.

    Args:
        entries: Time entries to validate.
        max_daily_hours: Maximum hours allowed per calendar day.

    Returns:
        Tuple of (is_valid, {date: total_hours} for days exceeding max).
    """
    by_date: dict[date, Decimal] = defaultdict(Decimal)
    for entry in entries:
        by_date[entry.work_date] += entry.hours

    violations = {
        d: total for d, total in by_date.items()
        if total > max_daily_hours
    }

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# D5: Correction by reversal validation (R10)
# ---------------------------------------------------------------------------


def validate_correction_reversal(
    original_entry_id: UUID,
    reversal_event_exists: bool,
    new_entry_exists: bool,
) -> tuple[bool, str | None]:
    """Validate that a timesheet correction follows the reversal pattern.

    Corrections must:
    1. Have a reversal event for the original entry.
    2. Have a new replacement entry.
    Direct mutations of existing entries are forbidden (R10).

    Args:
        original_entry_id: The entry being corrected.
        reversal_event_exists: Whether a reversal event has been posted.
        new_entry_exists: Whether a replacement entry exists.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not reversal_event_exists:
        return False, (
            f"Correction of entry {original_entry_id} requires a reversal "
            f"event. Direct mutation is forbidden (R10/D5)."
        )

    if not new_entry_exists:
        return False, (
            f"Correction of entry {original_entry_id} requires a replacement "
            f"entry after reversal."
        )

    return True, None
