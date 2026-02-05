"""
Tests for DCAA Timesheet Domain Types.

Validates frozen dataclass invariants:
- TimesheetEntry: hours bounds, time range ordering
- TimesheetSubmission: date ordering, non-negative hours
- FloorCheck: non-negative charged_hours
- TotalTimeRecord: derived field computation
- TimesheetCorrection: immutability
- ConcurrentWorkCheck: value correctness
"""

from datetime import date, datetime, time
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_modules.payroll.dcaa_types import (
    ChargeType,
    ConcurrentWorkCheck,
    FloorCheck,
    FloorCheckResult,
    TimesheetCorrection,
    TimesheetEntry,
    TimesheetSubmission,
    TimesheetSubmissionStatus,
    TotalTimeRecord,
)


class TestTimesheetEntry:
    """TimesheetEntry frozen dataclass invariants."""

    def test_valid_entry(self):
        e = TimesheetEntry(
            entry_id=uuid4(),
            work_date=date(2026, 1, 5),
            charge_code="PROJ-001",
            charge_type=ChargeType.DIRECT,
            hours=Decimal("8"),
            pay_code="REGULAR",
        )
        assert e.hours == Decimal("8")
        assert e.charge_type == ChargeType.DIRECT

    def test_negative_hours_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            TimesheetEntry(
                entry_id=uuid4(),
                work_date=date(2026, 1, 5),
                charge_code="PROJ-001",
                charge_type=ChargeType.DIRECT,
                hours=Decimal("-1"),
                pay_code="REGULAR",
            )

    def test_hours_above_24_rejected(self):
        with pytest.raises(ValueError, match="exceed 24"):
            TimesheetEntry(
                entry_id=uuid4(),
                work_date=date(2026, 1, 5),
                charge_code="PROJ-001",
                charge_type=ChargeType.DIRECT,
                hours=Decimal("25"),
                pay_code="REGULAR",
            )

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="must be after"):
            TimesheetEntry(
                entry_id=uuid4(),
                work_date=date(2026, 1, 5),
                charge_code="PROJ-001",
                charge_type=ChargeType.DIRECT,
                hours=Decimal("4"),
                pay_code="REGULAR",
                start_time=time(14, 0),
                end_time=time(10, 0),
            )

    def test_zero_hours_valid(self):
        e = TimesheetEntry(
            entry_id=uuid4(),
            work_date=date(2026, 1, 5),
            charge_code="PROJ-001",
            charge_type=ChargeType.DIRECT,
            hours=Decimal("0"),
            pay_code="REGULAR",
        )
        assert e.hours == Decimal("0")

    def test_frozen_immutable(self):
        e = TimesheetEntry(
            entry_id=uuid4(),
            work_date=date(2026, 1, 5),
            charge_code="PROJ-001",
            charge_type=ChargeType.DIRECT,
            hours=Decimal("8"),
            pay_code="REGULAR",
        )
        with pytest.raises(AttributeError):
            e.hours = Decimal("10")

    def test_all_charge_types(self):
        for ct in ChargeType:
            e = TimesheetEntry(
                entry_id=uuid4(),
                work_date=date(2026, 1, 5),
                charge_code="TEST",
                charge_type=ct,
                hours=Decimal("1"),
                pay_code="REGULAR",
            )
            assert e.charge_type == ct


class TestTimesheetSubmission:
    """TimesheetSubmission frozen dataclass invariants."""

    def test_valid_submission(self):
        s = TimesheetSubmission(
            submission_id=uuid4(),
            employee_id=uuid4(),
            pay_period_id=uuid4(),
            work_week_start=date(2026, 1, 5),
            work_week_end=date(2026, 1, 9),
        )
        assert s.status == TimesheetSubmissionStatus.DRAFT

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="cannot precede"):
            TimesheetSubmission(
                submission_id=uuid4(),
                employee_id=uuid4(),
                pay_period_id=uuid4(),
                work_week_start=date(2026, 1, 9),
                work_week_end=date(2026, 1, 5),
            )

    def test_negative_total_hours_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            TimesheetSubmission(
                submission_id=uuid4(),
                employee_id=uuid4(),
                pay_period_id=uuid4(),
                work_week_start=date(2026, 1, 5),
                work_week_end=date(2026, 1, 9),
                total_hours=Decimal("-1"),
            )

    def test_all_statuses_valid(self):
        for status in TimesheetSubmissionStatus:
            s = TimesheetSubmission(
                submission_id=uuid4(),
                employee_id=uuid4(),
                pay_period_id=uuid4(),
                work_week_start=date(2026, 1, 5),
                work_week_end=date(2026, 1, 9),
                status=status,
            )
            assert s.status == status


class TestFloorCheck:
    """FloorCheck frozen dataclass invariants."""

    def test_valid_check(self):
        fc = FloorCheck(
            check_id=uuid4(),
            employee_id=uuid4(),
            check_date=date(2026, 1, 5),
            check_time=time(14, 30),
            observed_location="Building A, Room 201",
            observed_activity="Writing code",
            charged_contract_id="FA8750-21-C-0001",
            charged_hours=Decimal("6"),
            checker_id=uuid4(),
            result=FloorCheckResult.CONFIRMED,
        )
        assert fc.result == FloorCheckResult.CONFIRMED

    def test_negative_charged_hours_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            FloorCheck(
                check_id=uuid4(),
                employee_id=uuid4(),
                check_date=date(2026, 1, 5),
                check_time=time(14, 30),
                observed_location="Building A",
                observed_activity="Working",
                charged_contract_id=None,
                charged_hours=Decimal("-1"),
                checker_id=uuid4(),
                result=FloorCheckResult.CONFIRMED,
            )

    def test_discrepancy_result(self):
        fc = FloorCheck(
            check_id=uuid4(),
            employee_id=uuid4(),
            check_date=date(2026, 1, 5),
            check_time=time(14, 30),
            observed_location="Building B",
            observed_activity="Personal call",
            charged_contract_id="FA8750-21-C-0001",
            charged_hours=Decimal("8"),
            checker_id=uuid4(),
            result=FloorCheckResult.DISCREPANCY,
            discrepancy_note="Employee on personal call, not working on charged project",
        )
        assert fc.result == FloorCheckResult.DISCREPANCY
        assert fc.discrepancy_note is not None


class TestTotalTimeRecord:
    """TotalTimeRecord derived field computation (CAS 418)."""

    def test_derived_fields_computed(self):
        record = TotalTimeRecord(
            employee_id=uuid4(),
            pay_period_id=uuid4(),
            expected_hours=Decimal("40"),
            direct_hours=Decimal("30"),
            indirect_hours=Decimal("5"),
            leave_hours=Decimal("5"),
        )
        assert record.total_recorded_hours == Decimal("40")
        assert record.variance == Decimal("0")
        assert record.is_compliant

    def test_non_compliant_variance(self):
        record = TotalTimeRecord(
            employee_id=uuid4(),
            pay_period_id=uuid4(),
            expected_hours=Decimal("40"),
            direct_hours=Decimal("30"),
            indirect_hours=Decimal("5"),
            leave_hours=Decimal("0"),
        )
        assert record.total_recorded_hours == Decimal("35")
        assert record.variance == Decimal("-5")
        assert not record.is_compliant


class TestTimesheetCorrection:
    """TimesheetCorrection immutability."""

    def test_valid_correction(self):
        c = TimesheetCorrection(
            correction_id=uuid4(),
            original_entry_id=uuid4(),
            reversal_event_id=uuid4(),
            new_entry_id=uuid4(),
            reason="Wrong charge code",
            corrected_at=datetime(2026, 1, 5, 14, 30),
            corrected_by=uuid4(),
        )
        assert c.reason == "Wrong charge code"

    def test_frozen_immutable(self):
        c = TimesheetCorrection(
            correction_id=uuid4(),
            original_entry_id=uuid4(),
            reversal_event_id=uuid4(),
            new_entry_id=uuid4(),
            reason="Wrong charge code",
            corrected_at=datetime(2026, 1, 5, 14, 30),
            corrected_by=uuid4(),
        )
        with pytest.raises(AttributeError):
            c.reason = "Different reason"


class TestConcurrentWorkCheck:
    """ConcurrentWorkCheck value correctness."""

    def test_valid_check(self):
        check = ConcurrentWorkCheck(
            employee_id=uuid4(),
            work_date=date(2026, 1, 5),
            is_valid=True,
            total_hours=Decimal("8"),
        )
        assert check.is_valid
        assert check.overlapping_entries == ()

    def test_invalid_with_overlaps(self):
        check = ConcurrentWorkCheck(
            employee_id=uuid4(),
            work_date=date(2026, 1, 5),
            is_valid=False,
            overlapping_entries=((uuid4(), uuid4()),),
            total_hours=Decimal("16"),
        )
        assert not check.is_valid
        assert len(check.overlapping_entries) == 1
