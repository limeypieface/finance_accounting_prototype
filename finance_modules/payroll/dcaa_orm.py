"""
DCAA Timesheet ORM Models (``finance_modules.payroll.dcaa_orm``).

Responsibility
--------------
SQLAlchemy ORM models that persist the DCAA timesheet compliance DTOs
defined in ``finance_modules.payroll.dcaa_types``.  Each ORM class mirrors
a DTO and provides ``to_dto()`` / ``from_dto()`` round-trip conversion.

Architecture position
---------------------
**Modules layer** -- persistence companions to the pure DTO models.
Inherits from ``TrackedBase`` (kernel DB base) which provides:
id (UUID PK), created_at, updated_at, created_by_id, updated_by_id.

Invariants enforced
-------------------
* D1 -- work_date proximity: columns support daily recording validation.
* D2 -- approval workflow: status column tracks submission lifecycle.
* D4 -- concurrent overlap: start_time/end_time indexed for overlap queries.
* D5 -- correction by reversal: correction model links original/reversal/new.
* D9 -- floor check immutability: FloorCheckModel is append-only.
* All monetary/hour fields use Decimal (Numeric(38,9)) -- NEVER float.
"""

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase


# ---------------------------------------------------------------------------
# TimesheetSubmissionModel
# ---------------------------------------------------------------------------


class TimesheetSubmissionModel(TrackedBase):
    """ORM model for ``TimesheetSubmission``.

    A timesheet submission groups daily entries for supervisor approval.
    Only one active submission per employee per work week.
    """

    __tablename__ = "payroll_timesheet_submissions"

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "pay_period_id", "work_week_start",
            name="uq_timesheet_submission_employee_week",
        ),
        Index("idx_timesheet_sub_employee", "employee_id"),
        Index("idx_timesheet_sub_status", "status"),
        Index("idx_timesheet_sub_period", "pay_period_id"),
        Index("idx_timesheet_sub_week", "work_week_start", "work_week_end"),
    )

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    pay_period_id: Mapped[UUID] = mapped_column(nullable=False)
    work_week_start: Mapped[date] = mapped_column(Date, nullable=False)
    work_week_end: Mapped[date] = mapped_column(Date, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_hours: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")

    # Relationships
    entries: Mapped[list["TimesheetEntryModel"]] = relationship(
        "TimesheetEntryModel",
        back_populates="submission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dto(self):
        from finance_modules.payroll.dcaa_types import (
            TimesheetSubmission,
            TimesheetSubmissionStatus,
        )

        return TimesheetSubmission(
            submission_id=self.id,
            employee_id=self.employee_id,
            pay_period_id=self.pay_period_id,
            work_week_start=self.work_week_start,
            work_week_end=self.work_week_end,
            submitted_at=self.submitted_at,
            entries=tuple(e.to_dto() for e in self.entries),
            total_hours=self.total_hours,
            status=TimesheetSubmissionStatus(self.status),
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TimesheetSubmissionModel":
        return cls(
            id=dto.submission_id,
            employee_id=dto.employee_id,
            pay_period_id=dto.pay_period_id,
            work_week_start=dto.work_week_start,
            work_week_end=dto.work_week_end,
            submitted_at=dto.submitted_at,
            total_hours=dto.total_hours,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# TimesheetEntryModel
# ---------------------------------------------------------------------------


class TimesheetEntryModel(TrackedBase):
    """ORM model for ``TimesheetEntry``.

    A single daily time entry within a submission.  Indexed for overlap
    detection queries (D4) on (employee_id via submission, work_date).
    """

    __tablename__ = "payroll_timesheet_entries"

    __table_args__ = (
        Index(
            "idx_timesheet_entry_sub_date",
            "submission_id", "work_date",
        ),
        Index("idx_timesheet_entry_charge", "charge_code"),
        Index("idx_timesheet_entry_date", "work_date"),
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_timesheet_submissions.id"), nullable=False,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    charge_code: Mapped[str] = mapped_column(String(100), nullable=False)
    charge_type: Mapped[str] = mapped_column(String(50), nullable=False, default="direct")
    hours: Mapped[Decimal] = mapped_column(nullable=False)
    pay_code: Mapped[str] = mapped_column(String(50), nullable=False, default="REGULAR")
    labor_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_billable: Mapped[bool] = mapped_column(Boolean, default=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    # Relationship
    submission: Mapped["TimesheetSubmissionModel"] = relationship(
        "TimesheetSubmissionModel", back_populates="entries",
    )

    def to_dto(self):
        from finance_modules.payroll.dcaa_types import ChargeType, TimesheetEntry

        return TimesheetEntry(
            entry_id=self.id,
            work_date=self.work_date,
            charge_code=self.charge_code,
            charge_type=ChargeType(self.charge_type),
            hours=self.hours,
            pay_code=self.pay_code,
            labor_category=self.labor_category,
            task_description=self.task_description,
            is_billable=self.is_billable,
            start_time=self.start_time,
            end_time=self.end_time,
        )

    @classmethod
    def from_dto(cls, dto, submission_id: UUID, created_by_id: UUID) -> "TimesheetEntryModel":
        return cls(
            id=dto.entry_id,
            submission_id=submission_id,
            work_date=dto.work_date,
            charge_code=dto.charge_code,
            charge_type=dto.charge_type.value if hasattr(dto.charge_type, "value") else dto.charge_type,
            hours=dto.hours,
            pay_code=dto.pay_code,
            labor_category=dto.labor_category,
            task_description=dto.task_description,
            is_billable=dto.is_billable,
            start_time=dto.start_time,
            end_time=dto.end_time,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# FloorCheckModel (append-only — D9)
# ---------------------------------------------------------------------------


class FloorCheckModel(TrackedBase):
    """ORM model for ``FloorCheck``.

    Floor check records are append-only (D9).  Once created they must
    never be modified or deleted.  Immutability is enforced by ORM event
    listeners on TrackedBase and by this model's design.
    """

    __tablename__ = "payroll_floor_checks"

    __table_args__ = (
        Index("idx_floor_check_employee", "employee_id"),
        Index("idx_floor_check_date", "check_date"),
        Index("idx_floor_check_result", "result"),
    )

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    check_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_time: Mapped[time] = mapped_column(Time, nullable=False)
    observed_location: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_activity: Mapped[str] = mapped_column(String(500), nullable=False)
    charged_contract_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    charged_hours: Mapped[Decimal] = mapped_column(nullable=False)
    checker_id: Mapped[UUID] = mapped_column(nullable=False)
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    discrepancy_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dto(self):
        from finance_modules.payroll.dcaa_types import FloorCheck, FloorCheckResult

        return FloorCheck(
            check_id=self.id,
            employee_id=self.employee_id,
            check_date=self.check_date,
            check_time=self.check_time,
            observed_location=self.observed_location,
            observed_activity=self.observed_activity,
            charged_contract_id=self.charged_contract_id,
            charged_hours=self.charged_hours,
            checker_id=self.checker_id,
            result=FloorCheckResult(self.result),
            discrepancy_note=self.discrepancy_note,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "FloorCheckModel":
        return cls(
            id=dto.check_id,
            employee_id=dto.employee_id,
            check_date=dto.check_date,
            check_time=dto.check_time,
            observed_location=dto.observed_location,
            observed_activity=dto.observed_activity,
            charged_contract_id=dto.charged_contract_id,
            charged_hours=dto.charged_hours,
            checker_id=dto.checker_id,
            result=dto.result.value if hasattr(dto.result, "value") else dto.result,
            discrepancy_note=dto.discrepancy_note,
            created_by_id=created_by_id,
        )


# ---------------------------------------------------------------------------
# TimesheetCorrectionModel (append-only — D5)
# ---------------------------------------------------------------------------


class TimesheetCorrectionModel(TrackedBase):
    """ORM model for ``TimesheetCorrection``.

    Records the chain: original_entry → reversal_event → new_entry.
    Append-only: once created, corrections must never be modified.
    """

    __tablename__ = "payroll_timesheet_corrections"

    __table_args__ = (
        Index("idx_correction_original", "original_entry_id"),
        Index("idx_correction_new", "new_entry_id"),
    )

    original_entry_id: Mapped[UUID] = mapped_column(nullable=False)
    reversal_event_id: Mapped[UUID] = mapped_column(nullable=False)
    new_entry_id: Mapped[UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    corrected_by: Mapped[UUID] = mapped_column(nullable=False)

    def to_dto(self):
        from finance_modules.payroll.dcaa_types import TimesheetCorrection

        return TimesheetCorrection(
            correction_id=self.id,
            original_entry_id=self.original_entry_id,
            reversal_event_id=self.reversal_event_id,
            new_entry_id=self.new_entry_id,
            reason=self.reason,
            corrected_at=self.corrected_at,
            corrected_by=self.corrected_by,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TimesheetCorrectionModel":
        return cls(
            id=dto.correction_id,
            original_entry_id=dto.original_entry_id,
            reversal_event_id=dto.reversal_event_id,
            new_entry_id=dto.new_entry_id,
            reason=dto.reason,
            corrected_at=dto.corrected_at,
            corrected_by=dto.corrected_by,
            created_by_id=created_by_id,
        )
