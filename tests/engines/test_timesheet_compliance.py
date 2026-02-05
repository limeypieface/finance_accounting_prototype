"""
Tests for Timesheet DCAA Compliance Engine.

Covers:
- D1: Daily recording validation (FAR 31.201-2(d))
- D3: Total time accounting (CAS 418)
- D4: Concurrent work overlap detection (CAS 418)
- D5: Correction by reversal validation (R10)
- Daily hours sanity check
"""

from datetime import date, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_engines.timesheet_compliance import (
    compute_total_time_record,
    detect_concurrent_overlaps,
    validate_all_entries_daily_recording,
    validate_correction_reversal,
    validate_daily_recording,
    validate_no_excessive_daily_hours,
    validate_total_time_accounting,
)
from finance_modules.payroll.dcaa_types import ChargeType, TimesheetEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    work_date: date = date(2026, 1, 5),
    charge_code: str = "PROJ-001",
    charge_type: ChargeType = ChargeType.DIRECT,
    hours: Decimal = Decimal("8"),
    pay_code: str = "REGULAR",
    start_time: time | None = None,
    end_time: time | None = None,
    is_billable: bool = True,
) -> TimesheetEntry:
    return TimesheetEntry(
        entry_id=uuid4(),
        work_date=work_date,
        charge_code=charge_code,
        charge_type=charge_type,
        hours=hours,
        pay_code=pay_code,
        start_time=start_time,
        end_time=end_time,
        is_billable=is_billable,
    )


# ===========================================================================
# D1: Daily Recording
# ===========================================================================


class TestDailyRecording:
    """D1: Entries must be submitted within max_retroactive_days."""

    def test_same_day_submission_valid(self):
        is_valid, msg = validate_daily_recording(
            date(2026, 1, 5), date(2026, 1, 5), max_retroactive_days=7,
        )
        assert is_valid
        assert msg is None

    def test_within_window_valid(self):
        is_valid, msg = validate_daily_recording(
            date(2026, 1, 5), date(2026, 1, 10), max_retroactive_days=7,
        )
        assert is_valid

    def test_exactly_at_window_valid(self):
        is_valid, msg = validate_daily_recording(
            date(2026, 1, 1), date(2026, 1, 8), max_retroactive_days=7,
        )
        assert is_valid

    def test_beyond_window_invalid(self):
        is_valid, msg = validate_daily_recording(
            date(2026, 1, 1), date(2026, 1, 15), max_retroactive_days=7,
        )
        assert not is_valid
        assert "14 days late" in msg
        assert "FAR 31.201-2(d)" in msg

    def test_future_work_date_invalid(self):
        is_valid, msg = validate_daily_recording(
            date(2026, 1, 10), date(2026, 1, 5), max_retroactive_days=7,
        )
        assert not is_valid
        assert "cannot precede" in msg

    def test_zero_day_window(self):
        is_valid, _ = validate_daily_recording(
            date(2026, 1, 5), date(2026, 1, 5), max_retroactive_days=0,
        )
        assert is_valid

    def test_zero_day_window_next_day_fails(self):
        is_valid, _ = validate_daily_recording(
            date(2026, 1, 5), date(2026, 1, 6), max_retroactive_days=0,
        )
        assert not is_valid


class TestBatchDailyRecording:
    """D1: Validate all entries in a submission."""

    def test_all_entries_valid(self):
        entries = (
            _entry(work_date=date(2026, 1, 5)),
            _entry(work_date=date(2026, 1, 6)),
        )
        violations = validate_all_entries_daily_recording(
            entries, date(2026, 1, 7), max_retroactive_days=7,
        )
        assert violations == []

    def test_one_entry_late(self):
        entries = (
            _entry(work_date=date(2026, 1, 5)),
            _entry(work_date=date(2025, 12, 20)),  # way too old
        )
        violations = validate_all_entries_daily_recording(
            entries, date(2026, 1, 7), max_retroactive_days=7,
        )
        assert len(violations) == 1
        assert violations[0][0] == entries[1].entry_id

    def test_all_entries_late(self):
        entries = (
            _entry(work_date=date(2025, 12, 1)),
            _entry(work_date=date(2025, 12, 2)),
        )
        violations = validate_all_entries_daily_recording(
            entries, date(2026, 1, 15), max_retroactive_days=7,
        )
        assert len(violations) == 2


# ===========================================================================
# D3: Total Time Accounting
# ===========================================================================


class TestTotalTimeAccounting:
    """D3: All hours must account for expected total (CAS 418)."""

    def test_exact_match_compliant(self):
        entries = (
            _entry(hours=Decimal("6"), charge_type=ChargeType.DIRECT),
            _entry(hours=Decimal("2"), charge_type=ChargeType.INDIRECT),
        )
        is_ok, variance = validate_total_time_accounting(
            entries, expected_hours=Decimal("8"),
        )
        assert is_ok
        assert variance == Decimal("0")

    def test_within_tolerance_compliant(self):
        entries = (
            _entry(hours=Decimal("7.80"), charge_type=ChargeType.DIRECT),
        )
        is_ok, variance = validate_total_time_accounting(
            entries, expected_hours=Decimal("8"), tolerance=Decimal("0.25"),
        )
        assert is_ok

    def test_exceeds_tolerance_not_compliant(self):
        entries = (
            _entry(hours=Decimal("7"), charge_type=ChargeType.DIRECT),
        )
        is_ok, variance = validate_total_time_accounting(
            entries, expected_hours=Decimal("8"), tolerance=Decimal("0.25"),
        )
        assert not is_ok
        assert variance == Decimal("-1")

    def test_over_expected_within_tolerance(self):
        entries = (
            _entry(hours=Decimal("8.20"), charge_type=ChargeType.DIRECT),
        )
        is_ok, _ = validate_total_time_accounting(
            entries, expected_hours=Decimal("8"), tolerance=Decimal("0.25"),
        )
        assert is_ok

    def test_over_expected_exceeds_tolerance(self):
        entries = (
            _entry(hours=Decimal("9"), charge_type=ChargeType.DIRECT),
        )
        is_ok, _ = validate_total_time_accounting(
            entries, expected_hours=Decimal("8"), tolerance=Decimal("0.25"),
        )
        assert not is_ok


class TestComputeTotalTimeRecord:
    """D3: Compute full CAS 418 total time record."""

    def test_all_types_summed(self):
        entries = (
            _entry(hours=Decimal("6"), charge_type=ChargeType.DIRECT),
            _entry(hours=Decimal("1"), charge_type=ChargeType.INDIRECT),
            _entry(hours=Decimal("0.5"), charge_type=ChargeType.LEAVE),
            _entry(hours=Decimal("0.5"), charge_type=ChargeType.UNCOMPENSATED),
        )
        record = compute_total_time_record(
            employee_id=uuid4(),
            pay_period_id=uuid4(),
            entries=entries,
            expected_hours=Decimal("8"),
        )
        assert record.direct_hours == Decimal("6")
        assert record.indirect_hours == Decimal("1")
        assert record.leave_hours == Decimal("0.5")
        assert record.uncompensated_hours == Decimal("0.5")
        assert record.total_recorded_hours == Decimal("8")
        assert record.is_compliant

    def test_non_compliant_record(self):
        entries = (
            _entry(hours=Decimal("5"), charge_type=ChargeType.DIRECT),
        )
        record = compute_total_time_record(
            employee_id=uuid4(),
            pay_period_id=uuid4(),
            entries=entries,
            expected_hours=Decimal("8"),
        )
        assert not record.is_compliant
        assert record.variance == Decimal("-3")


# ===========================================================================
# D4: Concurrent Overlap Detection
# ===========================================================================


class TestConcurrentOverlap:
    """D4: No overlapping charges on different charge codes."""

    def test_no_overlap_without_times(self):
        entries = (
            _entry(charge_code="PROJ-001", hours=Decimal("4")),
            _entry(charge_code="PROJ-002", hours=Decimal("4")),
        )
        result = detect_concurrent_overlaps(entries)
        assert result.is_valid  # no times = no overlap detection

    def test_no_overlap_sequential(self):
        entries = (
            _entry(
                charge_code="PROJ-001", hours=Decimal("4"),
                start_time=time(8, 0), end_time=time(12, 0),
            ),
            _entry(
                charge_code="PROJ-002", hours=Decimal("4"),
                start_time=time(13, 0), end_time=time(17, 0),
            ),
        )
        result = detect_concurrent_overlaps(entries)
        assert result.is_valid
        assert result.overlapping_entries == ()

    def test_overlap_detected(self):
        entries = (
            _entry(
                charge_code="PROJ-001", hours=Decimal("4"),
                start_time=time(8, 0), end_time=time(12, 0),
            ),
            _entry(
                charge_code="PROJ-002", hours=Decimal("4"),
                start_time=time(11, 0), end_time=time(15, 0),
            ),
        )
        result = detect_concurrent_overlaps(entries)
        assert not result.is_valid
        assert len(result.overlapping_entries) == 1

    def test_same_charge_code_overlap_ok(self):
        entries = (
            _entry(
                charge_code="PROJ-001", hours=Decimal("4"),
                start_time=time(8, 0), end_time=time(12, 0),
            ),
            _entry(
                charge_code="PROJ-001", hours=Decimal("4"),
                start_time=time(10, 0), end_time=time(14, 0),
            ),
        )
        result = detect_concurrent_overlaps(entries)
        assert result.is_valid  # same charge code is OK

    def test_multiple_overlaps(self):
        entries = (
            _entry(
                charge_code="A", hours=Decimal("8"),
                start_time=time(8, 0), end_time=time(16, 0),
            ),
            _entry(
                charge_code="B", hours=Decimal("8"),
                start_time=time(8, 0), end_time=time(16, 0),
            ),
            _entry(
                charge_code="C", hours=Decimal("8"),
                start_time=time(8, 0), end_time=time(16, 0),
            ),
        )
        result = detect_concurrent_overlaps(entries)
        assert not result.is_valid
        assert len(result.overlapping_entries) == 3  # A-B, A-C, B-C


# ===========================================================================
# Daily Hours Validation
# ===========================================================================


class TestExcessiveDailyHours:
    """Sanity check: no day exceeds max hours."""

    def test_normal_day_valid(self):
        entries = (
            _entry(hours=Decimal("8"), work_date=date(2026, 1, 5)),
        )
        is_valid, violations = validate_no_excessive_daily_hours(entries)
        assert is_valid
        assert violations == {}

    def test_excessive_hours_detected(self):
        entries = (
            _entry(hours=Decimal("16"), work_date=date(2026, 1, 5)),
            _entry(hours=Decimal("10"), work_date=date(2026, 1, 5)),
        )
        is_valid, violations = validate_no_excessive_daily_hours(entries)
        assert not is_valid
        assert date(2026, 1, 5) in violations
        assert violations[date(2026, 1, 5)] == Decimal("26")

    def test_split_across_days_ok(self):
        entries = (
            _entry(hours=Decimal("12"), work_date=date(2026, 1, 5)),
            _entry(hours=Decimal("12"), work_date=date(2026, 1, 6)),
        )
        is_valid, _ = validate_no_excessive_daily_hours(entries)
        assert is_valid

    def test_exactly_24_valid(self):
        entries = (
            _entry(hours=Decimal("24"), work_date=date(2026, 1, 5)),
        )
        is_valid, _ = validate_no_excessive_daily_hours(entries)
        assert is_valid


# ===========================================================================
# D5: Correction by Reversal
# ===========================================================================


class TestCorrectionReversal:
    """D5: Corrections must have reversal + replacement (R10)."""

    def test_valid_correction(self):
        is_valid, msg = validate_correction_reversal(
            original_entry_id=uuid4(),
            reversal_event_exists=True,
            new_entry_exists=True,
        )
        assert is_valid
        assert msg is None

    def test_missing_reversal(self):
        is_valid, msg = validate_correction_reversal(
            original_entry_id=uuid4(),
            reversal_event_exists=False,
            new_entry_exists=True,
        )
        assert not is_valid
        assert "reversal event" in msg
        assert "R10/D5" in msg

    def test_missing_new_entry(self):
        is_valid, msg = validate_correction_reversal(
            original_entry_id=uuid4(),
            reversal_event_exists=True,
            new_entry_exists=False,
        )
        assert not is_valid
        assert "replacement entry" in msg

    def test_both_missing(self):
        is_valid, msg = validate_correction_reversal(
            original_entry_id=uuid4(),
            reversal_event_exists=False,
            new_entry_exists=False,
        )
        assert not is_valid
        assert "reversal event" in msg
