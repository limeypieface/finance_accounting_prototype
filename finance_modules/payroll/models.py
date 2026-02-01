"""
Payroll Domain Models (``finance_modules.payroll.models``).

Responsibility
--------------
Frozen dataclass value objects representing the nouns of payroll:
employees, pay periods, timecards, paychecks, tax withholdings,
benefit deductions, and NACHA payment batches.

Architecture position
---------------------
**Modules layer** -- pure data definitions with ZERO I/O.  Consumed by
``PayrollService`` and returned to callers.  No dependency on kernel
services, database, or engines.

Invariants enforced
-------------------
* All models are ``frozen=True`` (immutable after construction).
* All monetary fields use ``Decimal`` -- NEVER ``float``.

Failure modes
-------------
* Construction with invalid enum values raises ``ValueError``.

Audit relevance
---------------
* Payroll records are SOX-critical.
* Withholding breakdowns must be traceable for tax compliance.
* NACHA batch records support payment reconciliation.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.payroll.models")


class PayFrequency(Enum):
    """Pay frequencies."""
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMIMONTHLY = "semimonthly"
    MONTHLY = "monthly"


class PayType(Enum):
    """Types of pay."""
    SALARY = "salary"
    HOURLY = "hourly"
    COMMISSION = "commission"


class PayrollRunStatus(Enum):
    """Payroll run lifecycle states."""
    DRAFT = "draft"
    CALCULATING = "calculating"
    CALCULATED = "calculated"
    APPROVED = "approved"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REVERSED = "reversed"


class TimecardStatus(Enum):
    """Timecard states."""
    OPEN = "open"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Employee:
    """An employee for payroll purposes."""
    id: UUID
    employee_number: str
    first_name: str
    last_name: str
    pay_type: PayType
    pay_frequency: PayFrequency
    base_pay: Decimal  # annual salary or hourly rate
    department_id: UUID | None = None
    cost_center_id: UUID | None = None
    hire_date: date | None = None
    termination_date: date | None = None
    is_active: bool = True

    def __post_init__(self):
        # Validate base_pay is non-negative
        if self.base_pay < 0:
            logger.warning(
                "employee_negative_base_pay",
                extra={
                    "employee_id": str(self.id),
                    "employee_number": self.employee_number,
                    "base_pay": str(self.base_pay),
                },
            )
            raise ValueError("base_pay cannot be negative")

        # Validate salaried employees have positive base_pay
        if self.pay_type == PayType.SALARY and self.base_pay <= 0:
            raise ValueError("Salaried employee must have positive base_pay")

        logger.debug(
            "employee_created",
            extra={
                "employee_id": str(self.id),
                "employee_number": self.employee_number,
                "pay_type": self.pay_type.value,
                "pay_frequency": self.pay_frequency.value,
                "is_active": self.is_active,
            },
        )


@dataclass(frozen=True)
class PayPeriod:
    """A payroll period."""
    id: UUID
    period_number: int
    year: int
    start_date: date
    end_date: date
    pay_date: date
    pay_frequency: PayFrequency
    is_closed: bool = False


@dataclass(frozen=True)
class TimecardLine:
    """A single time entry."""
    id: UUID
    timecard_id: UUID
    work_date: date
    hours: Decimal
    pay_code: str  # regular, overtime, sick, vacation
    project_id: UUID | None = None
    work_order_id: UUID | None = None


@dataclass(frozen=True)
class Timecard:
    """An employee timecard for a pay period."""
    id: UUID
    employee_id: UUID
    pay_period_id: UUID
    total_regular_hours: Decimal = Decimal("0")
    total_overtime_hours: Decimal = Decimal("0")
    status: TimecardStatus = TimecardStatus.OPEN
    lines: tuple[TimecardLine, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Paycheck:
    """An employee paycheck."""
    id: UUID
    payroll_run_id: UUID
    employee_id: UUID
    pay_period_id: UUID
    gross_pay: Decimal
    federal_tax: Decimal = Decimal("0")
    state_tax: Decimal = Decimal("0")
    social_security: Decimal = Decimal("0")
    medicare: Decimal = Decimal("0")
    other_deductions: Decimal = Decimal("0")
    net_pay: Decimal = Decimal("0")
    check_number: str | None = None
    direct_deposit: bool = True


@dataclass(frozen=True)
class PayrollRun:
    """A payroll processing run."""
    id: UUID
    pay_period_id: UUID
    run_date: date
    total_gross: Decimal = Decimal("0")
    total_taxes: Decimal = Decimal("0")
    total_deductions: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")
    employee_count: int = 0
    status: PayrollRunStatus = PayrollRunStatus.DRAFT
    approved_by: UUID | None = None
    approved_date: date | None = None


@dataclass(frozen=True)
class WithholdingResult:
    """Result of gross-to-net payroll calculation."""
    id: UUID
    employee_id: UUID
    gross_pay: Decimal
    federal_withholding: Decimal
    state_withholding: Decimal
    social_security: Decimal
    medicare: Decimal
    total_deductions: Decimal
    net_pay: Decimal


@dataclass(frozen=True)
class BenefitsDeduction:
    """A benefits deduction from an employee's paycheck."""
    id: UUID
    employee_id: UUID
    plan_name: str
    employee_amount: Decimal
    employer_amount: Decimal = Decimal("0")
    period: str = ""


@dataclass(frozen=True)
class EmployerContribution:
    """An employer contribution to a benefits plan."""
    id: UUID
    employee_id: UUID
    plan_name: str
    amount: Decimal
    period: str = ""
