"""
Payroll ORM Persistence Models (``finance_modules.payroll.orm``).

Responsibility:
    SQLAlchemy ORM models that persist the frozen dataclass DTOs defined in
    ``finance_modules.payroll.models``.  Each ORM class mirrors a DTO and
    provides ``to_dto()`` / ``from_dto()`` round-trip conversion.

Architecture position:
    **Modules layer** -- persistence companions to the pure DTO models.
    Inherits from ``TrackedBase`` (kernel DB base) which provides:
    id (UUID PK, auto-generated), created_at, updated_at,
    created_by_id (NOT NULL UUID), updated_by_id (nullable UUID).

Invariants enforced:
    - All monetary fields use Decimal (maps to Numeric(38,9)) -- NEVER float.
    - Enum fields stored as String(50) containing the enum .value string.
    - FK relationships within the payroll module use explicit ForeignKey.
    - FK to kernel Party uses ForeignKey("parties.id").
    - GL account codes stored as String(50) -- no FK to kernel Account.

Audit relevance:
    Payroll records are SOX-critical.  Employee pay, withholding breakdowns,
    and benefits deductions must be traceable for tax compliance and audit.
    TrackedBase audit columns (created_at, updated_at, created_by_id,
    updated_by_id) are inherited by every model.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase

# ---------------------------------------------------------------------------
# EmployeeModel
# ---------------------------------------------------------------------------

class EmployeeModel(TrackedBase):
    """
    ORM model for ``Employee`` -- an employee for payroll purposes.

    Contract:
        Each employee has a unique ``employee_number``.  Pay type and
        frequency determine gross-to-net calculation logic.  Department
        and cost center drive GL account resolution for expense allocation.

    Guarantees:
        - ``employee_number`` is unique (uq_payroll_employee_number).
        - ``pay_type`` and ``pay_frequency`` store enum .value strings.
        - ``base_pay`` is always Decimal (Numeric(38,9)).
    """

    __tablename__ = "payroll_employees"

    employee_number: Mapped[str] = mapped_column(String(50), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    pay_type: Mapped[str] = mapped_column(String(50), nullable=False)
    pay_frequency: Mapped[str] = mapped_column(String(50), nullable=False)
    base_pay: Mapped[Decimal] = mapped_column(nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(nullable=True)
    cost_center_id: Mapped[UUID | None] = mapped_column(nullable=True)
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("employee_number", name="uq_payroll_employee_number"),
        Index("idx_payroll_employee_active", "is_active"),
        Index("idx_payroll_employee_department", "department_id"),
        Index("idx_payroll_employee_cost_center", "cost_center_id"),
        Index("idx_payroll_employee_pay_type", "pay_type"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import Employee, PayFrequency, PayType
        return Employee(
            id=self.id,
            employee_number=self.employee_number,
            first_name=self.first_name,
            last_name=self.last_name,
            pay_type=PayType(self.pay_type),
            pay_frequency=PayFrequency(self.pay_frequency),
            base_pay=self.base_pay,
            department_id=self.department_id,
            cost_center_id=self.cost_center_id,
            hire_date=self.hire_date,
            termination_date=self.termination_date,
            is_active=self.is_active,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "EmployeeModel":
        return cls(
            id=dto.id,
            employee_number=dto.employee_number,
            first_name=dto.first_name,
            last_name=dto.last_name,
            pay_type=dto.pay_type.value if hasattr(dto.pay_type, "value") else dto.pay_type,
            pay_frequency=dto.pay_frequency.value if hasattr(dto.pay_frequency, "value") else dto.pay_frequency,
            base_pay=dto.base_pay,
            department_id=dto.department_id,
            cost_center_id=dto.cost_center_id,
            hire_date=dto.hire_date,
            termination_date=dto.termination_date,
            is_active=dto.is_active,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<EmployeeModel {self.employee_number}: "
            f"{self.first_name} {self.last_name} ({self.pay_type})>"
        )


# ---------------------------------------------------------------------------
# PayPeriodModel
# ---------------------------------------------------------------------------

class PayPeriodModel(TrackedBase):
    """
    ORM model for ``PayPeriod`` -- a payroll period.

    Contract:
        Each pay period is identified by ``period_number`` + ``year`` +
        ``pay_frequency``.  Once closed (``is_closed=True``), no new
        timecards or payroll runs may reference it.
    """

    __tablename__ = "payroll_pay_periods"

    period_number: Mapped[int] = mapped_column(nullable=False)
    year: Mapped[int] = mapped_column(nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_frequency: Mapped[str] = mapped_column(String(50), nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "period_number", "year", "pay_frequency",
            name="uq_payroll_pay_period_number_year_freq",
        ),
        Index("idx_payroll_pay_period_dates", "start_date", "end_date"),
        Index("idx_payroll_pay_period_year", "year"),
        Index("idx_payroll_pay_period_closed", "is_closed"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import PayFrequency, PayPeriod
        return PayPeriod(
            id=self.id,
            period_number=self.period_number,
            year=self.year,
            start_date=self.start_date,
            end_date=self.end_date,
            pay_date=self.pay_date,
            pay_frequency=PayFrequency(self.pay_frequency),
            is_closed=self.is_closed,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PayPeriodModel":
        return cls(
            id=dto.id,
            period_number=dto.period_number,
            year=dto.year,
            start_date=dto.start_date,
            end_date=dto.end_date,
            pay_date=dto.pay_date,
            pay_frequency=dto.pay_frequency.value if hasattr(dto.pay_frequency, "value") else dto.pay_frequency,
            is_closed=dto.is_closed,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<PayPeriodModel {self.year}-{self.period_number} "
            f"({self.pay_frequency}) closed={self.is_closed}>"
        )


# ---------------------------------------------------------------------------
# TimecardModel
# ---------------------------------------------------------------------------

class TimecardModel(TrackedBase):
    """
    ORM model for ``Timecard`` -- an employee timecard for a pay period.

    Contract:
        Each timecard belongs to one employee and one pay period.  The
        ``lines`` relationship provides the detailed time entries.
        Status follows the TimecardStatus lifecycle.
    """

    __tablename__ = "payroll_timecards"

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    pay_period_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_pay_periods.id"), nullable=False,
    )
    total_regular_hours: Mapped[Decimal] = mapped_column(
        default=Decimal("0"), nullable=False,
    )
    total_overtime_hours: Mapped[Decimal] = mapped_column(
        default=Decimal("0"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")

    # Relationships
    employee: Mapped["EmployeeModel"] = relationship(
        "EmployeeModel", lazy="select",
    )
    pay_period: Mapped["PayPeriodModel"] = relationship(
        "PayPeriodModel", lazy="select",
    )
    lines: Mapped[list["TimecardLineModel"]] = relationship(
        "TimecardLineModel",
        back_populates="timecard",
        lazy="select",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "employee_id", "pay_period_id",
            name="uq_payroll_timecard_employee_period",
        ),
        Index("idx_payroll_timecard_employee", "employee_id"),
        Index("idx_payroll_timecard_period", "pay_period_id"),
        Index("idx_payroll_timecard_status", "status"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import Timecard, TimecardStatus
        return Timecard(
            id=self.id,
            employee_id=self.employee_id,
            pay_period_id=self.pay_period_id,
            total_regular_hours=self.total_regular_hours,
            total_overtime_hours=self.total_overtime_hours,
            status=TimecardStatus(self.status),
            lines=tuple(line.to_dto() for line in self.lines) if self.lines else (),
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TimecardModel":
        model = cls(
            id=dto.id,
            employee_id=dto.employee_id,
            pay_period_id=dto.pay_period_id,
            total_regular_hours=dto.total_regular_hours,
            total_overtime_hours=dto.total_overtime_hours,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            created_by_id=created_by_id,
        )
        # Attach child lines if present
        if dto.lines:
            model.lines = [
                TimecardLineModel.from_dto(line, created_by_id=created_by_id)
                for line in dto.lines
            ]
        return model

    def __repr__(self) -> str:
        return (
            f"<TimecardModel employee={self.employee_id} "
            f"period={self.pay_period_id} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# TimecardLineModel
# ---------------------------------------------------------------------------

class TimecardLineModel(TrackedBase):
    """
    ORM model for ``TimecardLine`` -- a single time entry on a timecard.

    Contract:
        Each line belongs to exactly one timecard.  ``pay_code`` classifies
        the type of hours (regular, overtime, sick, vacation, etc.).
        Optional ``project_id`` and ``work_order_id`` enable project-based
        cost allocation.
    """

    __tablename__ = "payroll_timecard_lines"

    timecard_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_timecards.id"), nullable=False,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(nullable=False)
    pay_code: Mapped[str] = mapped_column(String(50), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(nullable=True)
    work_order_id: Mapped[UUID | None] = mapped_column(nullable=True)

    # Relationship back to parent timecard
    timecard: Mapped["TimecardModel"] = relationship(
        "TimecardModel", back_populates="lines",
    )

    __table_args__ = (
        Index("idx_payroll_timecard_line_timecard", "timecard_id"),
        Index("idx_payroll_timecard_line_date", "work_date"),
        Index("idx_payroll_timecard_line_pay_code", "pay_code"),
        Index("idx_payroll_timecard_line_project", "project_id"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import TimecardLine
        return TimecardLine(
            id=self.id,
            timecard_id=self.timecard_id,
            work_date=self.work_date,
            hours=self.hours,
            pay_code=self.pay_code,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "TimecardLineModel":
        return cls(
            id=dto.id,
            timecard_id=dto.timecard_id,
            work_date=dto.work_date,
            hours=dto.hours,
            pay_code=dto.pay_code,
            project_id=dto.project_id,
            work_order_id=dto.work_order_id,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<TimecardLineModel timecard={self.timecard_id} "
            f"date={self.work_date} hours={self.hours} code={self.pay_code}>"
        )


# ---------------------------------------------------------------------------
# PayrollRunModel
# ---------------------------------------------------------------------------

class PayrollRunModel(TrackedBase):
    """
    ORM model for ``PayrollRun`` -- a payroll processing run.

    Contract:
        Each run is tied to a pay period.  Status follows the
        PayrollRunStatus lifecycle (DRAFT -> CALCULATING -> CALCULATED ->
        APPROVED -> PROCESSING -> COMPLETED | REVERSED).  Once COMPLETED,
        the run is immutable from a business perspective.
    """

    __tablename__ = "payroll_runs"

    pay_period_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_pay_periods.id"), nullable=False,
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    total_taxes: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    total_deductions: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    total_net: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    employee_count: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    approved_by: Mapped[UUID | None] = mapped_column(nullable=True)
    approved_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationships
    pay_period: Mapped["PayPeriodModel"] = relationship(
        "PayPeriodModel", lazy="select",
    )
    paychecks: Mapped[list["PaycheckModel"]] = relationship(
        "PaycheckModel", back_populates="payroll_run", lazy="select",
    )

    __table_args__ = (
        Index("idx_payroll_run_period", "pay_period_id"),
        Index("idx_payroll_run_status", "status"),
        Index("idx_payroll_run_date", "run_date"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import PayrollRun, PayrollRunStatus
        return PayrollRun(
            id=self.id,
            pay_period_id=self.pay_period_id,
            run_date=self.run_date,
            total_gross=self.total_gross,
            total_taxes=self.total_taxes,
            total_deductions=self.total_deductions,
            total_net=self.total_net,
            employee_count=self.employee_count,
            status=PayrollRunStatus(self.status),
            approved_by=self.approved_by,
            approved_date=self.approved_date,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PayrollRunModel":
        return cls(
            id=dto.id,
            pay_period_id=dto.pay_period_id,
            run_date=dto.run_date,
            total_gross=dto.total_gross,
            total_taxes=dto.total_taxes,
            total_deductions=dto.total_deductions,
            total_net=dto.total_net,
            employee_count=dto.employee_count,
            status=dto.status.value if hasattr(dto.status, "value") else dto.status,
            approved_by=dto.approved_by,
            approved_date=dto.approved_date,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<PayrollRunModel period={self.pay_period_id} "
            f"date={self.run_date} status={self.status} "
            f"employees={self.employee_count}>"
        )


# ---------------------------------------------------------------------------
# PaycheckModel
# ---------------------------------------------------------------------------

class PaycheckModel(TrackedBase):
    """
    ORM model for ``Paycheck`` -- an employee paycheck.

    Contract:
        Each paycheck belongs to a payroll run, an employee, and a pay
        period.  The breakdown columns (federal_tax, state_tax, etc.)
        must reconcile: ``net_pay = gross_pay - sum(all deductions)``.
    """

    __tablename__ = "payroll_paychecks"

    payroll_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_runs.id"), nullable=False,
    )
    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    pay_period_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_pay_periods.id"), nullable=False,
    )
    gross_pay: Mapped[Decimal] = mapped_column(nullable=False)
    federal_tax: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    state_tax: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    social_security: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    medicare: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    other_deductions: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    net_pay: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    check_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    direct_deposit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    payroll_run: Mapped["PayrollRunModel"] = relationship(
        "PayrollRunModel", back_populates="paychecks",
    )
    employee: Mapped["EmployeeModel"] = relationship(
        "EmployeeModel", lazy="select",
    )
    pay_period: Mapped["PayPeriodModel"] = relationship(
        "PayPeriodModel", lazy="select",
    )

    __table_args__ = (
        UniqueConstraint(
            "payroll_run_id", "employee_id",
            name="uq_payroll_paycheck_run_employee",
        ),
        Index("idx_payroll_paycheck_run", "payroll_run_id"),
        Index("idx_payroll_paycheck_employee", "employee_id"),
        Index("idx_payroll_paycheck_period", "pay_period_id"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import Paycheck
        return Paycheck(
            id=self.id,
            payroll_run_id=self.payroll_run_id,
            employee_id=self.employee_id,
            pay_period_id=self.pay_period_id,
            gross_pay=self.gross_pay,
            federal_tax=self.federal_tax,
            state_tax=self.state_tax,
            social_security=self.social_security,
            medicare=self.medicare,
            other_deductions=self.other_deductions,
            net_pay=self.net_pay,
            check_number=self.check_number,
            direct_deposit=self.direct_deposit,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "PaycheckModel":
        return cls(
            id=dto.id,
            payroll_run_id=dto.payroll_run_id,
            employee_id=dto.employee_id,
            pay_period_id=dto.pay_period_id,
            gross_pay=dto.gross_pay,
            federal_tax=dto.federal_tax,
            state_tax=dto.state_tax,
            social_security=dto.social_security,
            medicare=dto.medicare,
            other_deductions=dto.other_deductions,
            net_pay=dto.net_pay,
            check_number=dto.check_number,
            direct_deposit=dto.direct_deposit,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<PaycheckModel employee={self.employee_id} "
            f"gross={self.gross_pay} net={self.net_pay}>"
        )


# ---------------------------------------------------------------------------
# WithholdingResultModel
# ---------------------------------------------------------------------------

class WithholdingResultModel(TrackedBase):
    """
    ORM model for ``WithholdingResult`` -- result of gross-to-net payroll
    calculation.

    Contract:
        Each withholding result captures the full tax breakdown for a
        single employee's gross-to-net calculation.  Used for audit trail
        and recalculation verification.
    """

    __tablename__ = "payroll_withholding_results"

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    gross_pay: Mapped[Decimal] = mapped_column(nullable=False)
    federal_withholding: Mapped[Decimal] = mapped_column(nullable=False)
    state_withholding: Mapped[Decimal] = mapped_column(nullable=False)
    social_security: Mapped[Decimal] = mapped_column(nullable=False)
    medicare: Mapped[Decimal] = mapped_column(nullable=False)
    total_deductions: Mapped[Decimal] = mapped_column(nullable=False)
    net_pay: Mapped[Decimal] = mapped_column(nullable=False)

    # Relationships
    employee: Mapped["EmployeeModel"] = relationship(
        "EmployeeModel", lazy="select",
    )

    __table_args__ = (
        Index("idx_payroll_withholding_employee", "employee_id"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import WithholdingResult
        return WithholdingResult(
            id=self.id,
            employee_id=self.employee_id,
            gross_pay=self.gross_pay,
            federal_withholding=self.federal_withholding,
            state_withholding=self.state_withholding,
            social_security=self.social_security,
            medicare=self.medicare,
            total_deductions=self.total_deductions,
            net_pay=self.net_pay,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "WithholdingResultModel":
        return cls(
            id=dto.id,
            employee_id=dto.employee_id,
            gross_pay=dto.gross_pay,
            federal_withholding=dto.federal_withholding,
            state_withholding=dto.state_withholding,
            social_security=dto.social_security,
            medicare=dto.medicare,
            total_deductions=dto.total_deductions,
            net_pay=dto.net_pay,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<WithholdingResultModel employee={self.employee_id} "
            f"gross={self.gross_pay} net={self.net_pay}>"
        )


# ---------------------------------------------------------------------------
# BenefitsDeductionModel
# ---------------------------------------------------------------------------

class BenefitsDeductionModel(TrackedBase):
    """
    ORM model for ``BenefitsDeduction`` -- a benefits deduction from an
    employee's paycheck.

    Contract:
        Each deduction links to an employee and a plan.  Both the employee
        and employer contribution amounts are tracked for total cost
        reporting and GL posting.
    """

    __tablename__ = "payroll_benefits_deductions"

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    plan_name: Mapped[str] = mapped_column(String(255), nullable=False)
    employee_amount: Mapped[Decimal] = mapped_column(nullable=False)
    employer_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"), nullable=False)
    period: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # Relationships
    employee: Mapped["EmployeeModel"] = relationship(
        "EmployeeModel", lazy="select",
    )

    __table_args__ = (
        Index("idx_payroll_benefits_employee", "employee_id"),
        Index("idx_payroll_benefits_plan", "plan_name"),
        Index("idx_payroll_benefits_period", "period"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import BenefitsDeduction
        return BenefitsDeduction(
            id=self.id,
            employee_id=self.employee_id,
            plan_name=self.plan_name,
            employee_amount=self.employee_amount,
            employer_amount=self.employer_amount,
            period=self.period,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "BenefitsDeductionModel":
        return cls(
            id=dto.id,
            employee_id=dto.employee_id,
            plan_name=dto.plan_name,
            employee_amount=dto.employee_amount,
            employer_amount=dto.employer_amount,
            period=dto.period,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<BenefitsDeductionModel employee={self.employee_id} "
            f"plan={self.plan_name} amount={self.employee_amount}>"
        )


# ---------------------------------------------------------------------------
# EmployerContributionModel
# ---------------------------------------------------------------------------

class EmployerContributionModel(TrackedBase):
    """
    ORM model for ``EmployerContribution`` -- an employer contribution to a
    benefits plan.

    Contract:
        Each contribution links to an employee and a plan for a given
        period.  Used for total compensation reporting, GL expense
        allocation, and benefits plan reconciliation.
    """

    __tablename__ = "payroll_employer_contributions"

    employee_id: Mapped[UUID] = mapped_column(
        ForeignKey("payroll_employees.id"), nullable=False,
    )
    plan_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    period: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # Relationships
    employee: Mapped["EmployeeModel"] = relationship(
        "EmployeeModel", lazy="select",
    )

    __table_args__ = (
        Index("idx_payroll_contrib_employee", "employee_id"),
        Index("idx_payroll_contrib_plan", "plan_name"),
        Index("idx_payroll_contrib_period", "period"),
    )

    def to_dto(self):
        from finance_modules.payroll.models import EmployerContribution
        return EmployerContribution(
            id=self.id,
            employee_id=self.employee_id,
            plan_name=self.plan_name,
            amount=self.amount,
            period=self.period,
        )

    @classmethod
    def from_dto(cls, dto, created_by_id: UUID) -> "EmployerContributionModel":
        return cls(
            id=dto.id,
            employee_id=dto.employee_id,
            plan_name=dto.plan_name,
            amount=dto.amount,
            period=dto.period,
            created_by_id=created_by_id,
        )

    def __repr__(self) -> str:
        return (
            f"<EmployerContributionModel employee={self.employee_id} "
            f"plan={self.plan_name} amount={self.amount}>"
        )
