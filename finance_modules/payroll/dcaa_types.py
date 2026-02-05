"""
DCAA Timesheet Compliance Types (``finance_modules.payroll.dcaa_types``).

Responsibility
--------------
Frozen dataclass value objects for DCAA-compliant timekeeping: timesheet
submissions with supervisor approval, floor checks, total time accounting
(CAS 418), concurrent work overlap detection, and correction audit trails.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``PayrollService`` and the ``timesheet_compliance`` engine.  No dependency
on kernel services, database, or engines.

Invariants enforced
-------------------
* D1 -- DAILY_RECORDING: work_date proximity validated at submission (FAR 31.201-2(d))
* D2 -- SUPERVISOR_APPROVAL: submission status tracks approval gate (DCAA CAM 6-406)
* D3 -- TOTAL_TIME_BALANCE: all hours summed and compared to expected (CAS 418)
* D4 -- NO_CONCURRENT_OVERLAP: entries carry start/end times for detection (CAS 418)
* D5 -- CORRECTION_BY_REVERSAL: corrections model the reversal chain (R10)
* D9 -- FLOOR_CHECK_AUDIT: floor checks are immutable audit artifacts (DCAA CAM 6-406.3)
* All models are ``frozen=True`` (immutable after construction).
* All hour/monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.
* Negative hours raise ``ValueError``.

Audit relevance
---------------
* Timesheet submissions are DCAA-critical records.
* Floor checks are legally required verification artifacts.
* Corrections must produce a traceable reversal chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from uuid import UUID


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TimesheetSubmissionStatus(str, Enum):
    """Timesheet submission lifecycle states (D2)."""
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTION_PENDING = "correction_pending"


class FloorCheckResult(str, Enum):
    """Outcome of a DCAA floor check (D9)."""
    CONFIRMED = "confirmed"
    DISCREPANCY = "discrepancy"
    EMPLOYEE_ABSENT = "employee_absent"


class ChargeType(str, Enum):
    """Classification of hours for total time accounting (D3)."""
    DIRECT = "direct"
    INDIRECT = "indirect"
    LEAVE = "leave"
    UNCOMPENSATED = "uncompensated"


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimesheetEntry:
    """A single daily time entry within a submission (D1, D4).

    Carries start_time/end_time to support concurrent overlap detection.
    """
    entry_id: UUID
    work_date: date
    charge_code: str  # contract_id, indirect cost code, or leave code
    charge_type: ChargeType
    hours: Decimal
    pay_code: str  # REGULAR, OVERTIME, DOUBLE_TIME, SICK, VACATION, HOLIDAY
    labor_category: str | None = None
    task_description: str = ""
    is_billable: bool = False
    start_time: time | None = None  # for overlap detection (D4)
    end_time: time | None = None

    def __post_init__(self) -> None:
        if self.hours < Decimal("0"):
            raise ValueError(
                f"TimesheetEntry hours cannot be negative: {self.hours}"
            )
        if self.hours > Decimal("24"):
            raise ValueError(
                f"TimesheetEntry hours cannot exceed 24: {self.hours}"
            )
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError(
                f"end_time ({self.end_time}) must be after "
                f"start_time ({self.start_time})"
            )


@dataclass(frozen=True)
class TimesheetSubmission:
    """A timesheet submission for a pay period by an employee (D1, D2, D3).

    Groups daily ``TimesheetEntry`` records into a single submission that
    flows through the approval workflow before labor charges are posted.
    """
    submission_id: UUID
    employee_id: UUID
    pay_period_id: UUID
    work_week_start: date
    work_week_end: date
    submitted_at: datetime | None = None
    entries: tuple[TimesheetEntry, ...] = field(default_factory=tuple)
    total_hours: Decimal = Decimal("0")
    status: TimesheetSubmissionStatus = TimesheetSubmissionStatus.DRAFT

    def __post_init__(self) -> None:
        if self.work_week_end < self.work_week_start:
            raise ValueError(
                f"work_week_end ({self.work_week_end}) cannot precede "
                f"work_week_start ({self.work_week_start})"
            )
        if self.total_hours < Decimal("0"):
            raise ValueError(
                f"total_hours cannot be negative: {self.total_hours}"
            )


# ---------------------------------------------------------------------------
# Floor check (DCAA CAM 6-406.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FloorCheck:
    """A random after-the-fact verification record (D9).

    Floor checks are DCAA-required surprise observations confirming that
    employees are working on the projects they are charging.  These records
    are append-only audit artifacts and must never be modified.
    """
    check_id: UUID
    employee_id: UUID
    check_date: date
    check_time: time
    observed_location: str
    observed_activity: str
    charged_contract_id: str | None
    charged_hours: Decimal
    checker_id: UUID  # person performing the floor check
    result: FloorCheckResult
    discrepancy_note: str | None = None

    def __post_init__(self) -> None:
        if self.charged_hours < Decimal("0"):
            raise ValueError(
                f"charged_hours cannot be negative: {self.charged_hours}"
            )


# ---------------------------------------------------------------------------
# Total time accounting (CAS 418)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TotalTimeRecord:
    """CAS 418 total time accounting record per employee per period (D3).

    Verifies that all hours are accounted for:
    direct + indirect + leave = total_recorded ~ expected_hours.
    """
    employee_id: UUID
    pay_period_id: UUID
    expected_hours: Decimal
    direct_hours: Decimal
    indirect_hours: Decimal
    leave_hours: Decimal
    uncompensated_hours: Decimal = Decimal("0")
    total_recorded_hours: Decimal = Decimal("0")
    variance: Decimal = Decimal("0")
    tolerance: Decimal = Decimal("0.25")
    is_compliant: bool = True

    def __post_init__(self) -> None:
        # Recompute derived fields for safety (frozen override pattern)
        computed_total = (
            self.direct_hours
            + self.indirect_hours
            + self.leave_hours
            + self.uncompensated_hours
        )
        computed_variance = computed_total - self.expected_hours
        computed_compliant = abs(computed_variance) <= self.tolerance
        object.__setattr__(self, "total_recorded_hours", computed_total)
        object.__setattr__(self, "variance", computed_variance)
        object.__setattr__(self, "is_compliant", computed_compliant)


# ---------------------------------------------------------------------------
# Correction audit trail (D5 / R10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimesheetCorrection:
    """Correction record linking original and replacement entries (D5).

    Corrections NEVER mutate existing records.  Instead they produce:
    1. A reversal event for the original entry.
    2. A new entry with corrected data.
    This dataclass models the full chain for auditability.
    """
    correction_id: UUID
    original_entry_id: UUID
    reversal_event_id: UUID  # event that reversed the original
    new_entry_id: UUID  # replacement entry
    reason: str
    corrected_at: datetime
    corrected_by: UUID  # actor who initiated the correction


# ---------------------------------------------------------------------------
# Concurrent work overlap detection (D4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConcurrentWorkCheck:
    """Result of checking for overlapping time entries on the same date (D4).

    Employees cannot charge to multiple contracts at the same time.
    """
    employee_id: UUID
    work_date: date
    is_valid: bool
    overlapping_entries: tuple[tuple[UUID, UUID], ...] = field(
        default_factory=tuple
    )  # pairs of conflicting entry IDs
    total_hours: Decimal = Decimal("0")
    max_allowed_hours: Decimal = Decimal("24")
