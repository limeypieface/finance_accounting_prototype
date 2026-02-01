"""ORM round-trip tests for Payroll module.

Verifies that every Payroll ORM model can be persisted, queried back with
correct field values, and that FK / unique constraints are enforced by
the database.

Models under test (9):
    EmployeeModel, PayPeriodModel, TimecardModel, TimecardLineModel,
    PayrollRunModel, PaycheckModel, WithholdingResultModel,
    BenefitsDeductionModel, EmployerContributionModel
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.payroll.orm import (
    BenefitsDeductionModel,
    EmployeeModel,
    EmployerContributionModel,
    PaycheckModel,
    PayPeriodModel,
    PayrollRunModel,
    TimecardLineModel,
    TimecardModel,
    WithholdingResultModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_employee(session, test_actor_id, **overrides):
    """Create and flush an EmployeeModel with sensible defaults."""
    defaults = dict(
        employee_number=f"EMP-{uuid4().hex[:8]}",
        first_name="Jane",
        last_name="Doe",
        pay_type="salary",
        pay_frequency="biweekly",
        base_pay=Decimal("75000.00"),
        is_active=True,
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = EmployeeModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


def _make_pay_period(session, test_actor_id, **overrides):
    """Create and flush a PayPeriodModel with sensible defaults."""
    defaults = dict(
        period_number=1,
        year=2024,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 14),
        pay_date=date(2024, 1, 19),
        pay_frequency="biweekly",
        is_closed=False,
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = PayPeriodModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


def _make_timecard(session, test_actor_id, employee_id, pay_period_id, **overrides):
    """Create and flush a TimecardModel with sensible defaults."""
    defaults = dict(
        employee_id=employee_id,
        pay_period_id=pay_period_id,
        total_regular_hours=Decimal("80.00"),
        total_overtime_hours=Decimal("0.00"),
        status="open",
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = TimecardModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


def _make_payroll_run(session, test_actor_id, pay_period_id, **overrides):
    """Create and flush a PayrollRunModel with sensible defaults."""
    defaults = dict(
        pay_period_id=pay_period_id,
        run_date=date(2024, 1, 18),
        total_gross=Decimal("50000.00"),
        total_taxes=Decimal("12000.00"),
        total_deductions=Decimal("3000.00"),
        total_net=Decimal("35000.00"),
        employee_count=10,
        status="draft",
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = PayrollRunModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


# ===================================================================
# EmployeeModel
# ===================================================================


class TestEmployeeModelORM:
    """Round-trip persistence tests for EmployeeModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = EmployeeModel(
            employee_number="EMP-001",
            first_name="Alice",
            last_name="Smith",
            pay_type="salary",
            pay_frequency="biweekly",
            base_pay=Decimal("85000.00"),
            hire_date=date(2023, 3, 15),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(EmployeeModel, obj.id)
        assert queried is not None
        assert queried.employee_number == "EMP-001"
        assert queried.first_name == "Alice"
        assert queried.last_name == "Smith"
        assert queried.pay_type == "salary"
        assert queried.pay_frequency == "biweekly"
        assert queried.base_pay == Decimal("85000.00")
        assert queried.hire_date == date(2023, 3, 15)
        assert queried.is_active is True
        assert queried.termination_date is None

    def test_hourly_employee(self, session, test_actor_id):
        obj = EmployeeModel(
            employee_number="EMP-002",
            first_name="Bob",
            last_name="Johnson",
            pay_type="hourly",
            pay_frequency="weekly",
            base_pay=Decimal("25.50"),
            department_id=uuid4(),
            cost_center_id=uuid4(),
            hire_date=date(2024, 1, 1),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(EmployeeModel, obj.id)
        assert queried.pay_type == "hourly"
        assert queried.pay_frequency == "weekly"
        assert queried.base_pay == Decimal("25.50")
        assert queried.department_id is not None
        assert queried.cost_center_id is not None

    def test_terminated_employee(self, session, test_actor_id):
        obj = EmployeeModel(
            employee_number="EMP-003",
            first_name="Carol",
            last_name="Williams",
            pay_type="salary",
            pay_frequency="semimonthly",
            base_pay=Decimal("60000.00"),
            hire_date=date(2020, 6, 1),
            termination_date=date(2024, 3, 31),
            is_active=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(EmployeeModel, obj.id)
        assert queried.termination_date == date(2024, 3, 31)
        assert queried.is_active is False

    def test_unique_employee_number(self, session, test_actor_id):
        _make_employee(session, test_actor_id, employee_number="DUP-EMP")
        dup = EmployeeModel(
            employee_number="DUP-EMP",
            first_name="Other",
            last_name="Person",
            pay_type="hourly",
            pay_frequency="weekly",
            base_pay=Decimal("20.00"),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# PayPeriodModel
# ===================================================================


class TestPayPeriodModelORM:
    """Round-trip persistence tests for PayPeriodModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = PayPeriodModel(
            period_number=1,
            year=2024,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 14),
            pay_date=date(2024, 1, 19),
            pay_frequency="biweekly",
            is_closed=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PayPeriodModel, obj.id)
        assert queried is not None
        assert queried.period_number == 1
        assert queried.year == 2024
        assert queried.start_date == date(2024, 1, 1)
        assert queried.end_date == date(2024, 1, 14)
        assert queried.pay_date == date(2024, 1, 19)
        assert queried.pay_frequency == "biweekly"
        assert queried.is_closed is False

    def test_closed_period(self, session, test_actor_id):
        obj = PayPeriodModel(
            period_number=25,
            year=2023,
            start_date=date(2023, 12, 18),
            end_date=date(2023, 12, 31),
            pay_date=date(2024, 1, 5),
            pay_frequency="biweekly",
            is_closed=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PayPeriodModel, obj.id)
        assert queried.is_closed is True

    def test_unique_period_number_year_frequency(self, session, test_actor_id):
        _make_pay_period(session, test_actor_id, period_number=99, year=2024, pay_frequency="biweekly")
        dup = PayPeriodModel(
            period_number=99,
            year=2024,
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 14),
            pay_date=date(2024, 6, 19),
            pay_frequency="biweekly",
            is_closed=False,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_period_different_frequency_allowed(self, session, test_actor_id):
        """Same period_number+year but different frequency should be allowed."""
        _make_pay_period(session, test_actor_id, period_number=50, year=2024, pay_frequency="biweekly")
        obj = PayPeriodModel(
            period_number=50,
            year=2024,
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 30),
            pay_date=date(2024, 7, 5),
            pay_frequency="monthly",
            is_closed=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()
        assert session.get(PayPeriodModel, obj.id) is not None


# ===================================================================
# TimecardModel
# ===================================================================


class TestTimecardModelORM:
    """Round-trip persistence tests for TimecardModel."""

    def test_create_and_query(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=10, year=2024)
        obj = TimecardModel(
            employee_id=emp.id,
            pay_period_id=pp.id,
            total_regular_hours=Decimal("80.00"),
            total_overtime_hours=Decimal("5.50"),
            status="submitted",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TimecardModel, obj.id)
        assert queried is not None
        assert queried.employee_id == emp.id
        assert queried.pay_period_id == pp.id
        assert queried.total_regular_hours == Decimal("80.00")
        assert queried.total_overtime_hours == Decimal("5.50")
        assert queried.status == "submitted"

    def test_employee_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id, employee_number="REL-EMP", first_name="Rel")
        pp = _make_pay_period(session, test_actor_id, period_number=11, year=2024)
        tc = _make_timecard(session, test_actor_id, emp.id, pp.id)

        queried = session.get(TimecardModel, tc.id)
        assert queried.employee is not None
        assert queried.employee.first_name == "Rel"

    def test_pay_period_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=12, year=2024)
        tc = _make_timecard(session, test_actor_id, emp.id, pp.id)

        queried = session.get(TimecardModel, tc.id)
        assert queried.pay_period is not None
        assert queried.pay_period.period_number == 12

    def test_lines_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=13, year=2024)
        tc = _make_timecard(session, test_actor_id, emp.id, pp.id)
        line = TimecardLineModel(
            timecard_id=tc.id,
            work_date=date(2024, 1, 2),
            hours=Decimal("8.00"),
            pay_code="regular",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire(tc, ["lines"])
        queried = session.get(TimecardModel, tc.id)
        assert len(queried.lines) == 1
        assert queried.lines[0].pay_code == "regular"

    def test_unique_employee_period(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=14, year=2024)
        _make_timecard(session, test_actor_id, emp.id, pp.id)
        dup = TimecardModel(
            employee_id=emp.id,
            pay_period_id=pp.id,
            total_regular_hours=Decimal("40.00"),
            total_overtime_hours=Decimal("0.00"),
            status="open",
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_employee_nonexistent(self, session, test_actor_id):
        pp = _make_pay_period(session, test_actor_id, period_number=15, year=2024)
        obj = TimecardModel(
            employee_id=uuid4(),
            pay_period_id=pp.id,
            total_regular_hours=Decimal("80.00"),
            total_overtime_hours=Decimal("0.00"),
            status="open",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_pay_period_nonexistent(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        obj = TimecardModel(
            employee_id=emp.id,
            pay_period_id=uuid4(),
            total_regular_hours=Decimal("80.00"),
            total_overtime_hours=Decimal("0.00"),
            status="open",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# TimecardLineModel
# ===================================================================


class TestTimecardLineModelORM:
    """Round-trip persistence tests for TimecardLineModel."""

    def test_create_and_query(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=20, year=2024)
        tc = _make_timecard(session, test_actor_id, emp.id, pp.id)
        obj = TimecardLineModel(
            timecard_id=tc.id,
            work_date=date(2024, 1, 2),
            hours=Decimal("8.00"),
            pay_code="regular",
            project_id=uuid4(),
            work_order_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TimecardLineModel, obj.id)
        assert queried is not None
        assert queried.timecard_id == tc.id
        assert queried.work_date == date(2024, 1, 2)
        assert queried.hours == Decimal("8.00")
        assert queried.pay_code == "regular"
        assert queried.project_id is not None
        assert queried.work_order_id is not None

    def test_timecard_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=21, year=2024)
        tc = _make_timecard(session, test_actor_id, emp.id, pp.id, status="approved")
        line = TimecardLineModel(
            timecard_id=tc.id,
            work_date=date(2024, 1, 3),
            hours=Decimal("4.00"),
            pay_code="overtime",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(TimecardLineModel, line.id)
        assert queried.timecard is not None
        assert queried.timecard.status == "approved"

    def test_fk_timecard_nonexistent(self, session, test_actor_id):
        obj = TimecardLineModel(
            timecard_id=uuid4(),
            work_date=date(2024, 1, 1),
            hours=Decimal("8.00"),
            pay_code="regular",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_multiple_lines_per_timecard(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=22, year=2024)
        tc = _make_timecard(session, test_actor_id, emp.id, pp.id)
        for day_offset in range(5):
            line = TimecardLineModel(
                timecard_id=tc.id,
                work_date=date(2024, 1, 2 + day_offset),
                hours=Decimal("8.00"),
                pay_code="regular",
                created_by_id=test_actor_id,
            )
            session.add(line)
        session.flush()

        session.expire(tc, ["lines"])
        queried = session.get(TimecardModel, tc.id)
        assert len(queried.lines) == 5


# ===================================================================
# PayrollRunModel
# ===================================================================


class TestPayrollRunModelORM:
    """Round-trip persistence tests for PayrollRunModel."""

    def test_create_and_query(self, session, test_actor_id):
        pp = _make_pay_period(session, test_actor_id, period_number=30, year=2024)
        obj = PayrollRunModel(
            pay_period_id=pp.id,
            run_date=date(2024, 1, 18),
            total_gross=Decimal("125000.00"),
            total_taxes=Decimal("31250.00"),
            total_deductions=Decimal("8000.00"),
            total_net=Decimal("85750.00"),
            employee_count=25,
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PayrollRunModel, obj.id)
        assert queried is not None
        assert queried.pay_period_id == pp.id
        assert queried.run_date == date(2024, 1, 18)
        assert queried.total_gross == Decimal("125000.00")
        assert queried.total_taxes == Decimal("31250.00")
        assert queried.total_deductions == Decimal("8000.00")
        assert queried.total_net == Decimal("85750.00")
        assert queried.employee_count == 25
        assert queried.status == "draft"
        assert queried.approved_by is None
        assert queried.approved_date is None

    def test_approved_run(self, session, test_actor_id):
        pp = _make_pay_period(session, test_actor_id, period_number=31, year=2024)
        approver = uuid4()
        obj = PayrollRunModel(
            pay_period_id=pp.id,
            run_date=date(2024, 1, 18),
            total_gross=Decimal("50000.00"),
            total_taxes=Decimal("12000.00"),
            total_deductions=Decimal("3000.00"),
            total_net=Decimal("35000.00"),
            employee_count=10,
            status="approved",
            approved_by=approver,
            approved_date=date(2024, 1, 17),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PayrollRunModel, obj.id)
        assert queried.status == "approved"
        assert queried.approved_by == approver
        assert queried.approved_date == date(2024, 1, 17)

    def test_pay_period_relationship(self, session, test_actor_id):
        pp = _make_pay_period(session, test_actor_id, period_number=32, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)

        queried = session.get(PayrollRunModel, run.id)
        assert queried.pay_period is not None
        assert queried.pay_period.period_number == 32

    def test_paychecks_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=33, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)
        check = PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("5000.00"),
            federal_tax=Decimal("800.00"),
            state_tax=Decimal("300.00"),
            social_security=Decimal("310.00"),
            medicare=Decimal("72.50"),
            other_deductions=Decimal("200.00"),
            net_pay=Decimal("3317.50"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(check)
        session.flush()

        session.expire(run, ["paychecks"])
        queried = session.get(PayrollRunModel, run.id)
        assert len(queried.paychecks) == 1

    def test_fk_pay_period_nonexistent(self, session, test_actor_id):
        obj = PayrollRunModel(
            pay_period_id=uuid4(),
            run_date=date(2024, 1, 18),
            total_gross=Decimal("50000.00"),
            total_taxes=Decimal("12000.00"),
            total_deductions=Decimal("3000.00"),
            total_net=Decimal("35000.00"),
            employee_count=10,
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# PaycheckModel
# ===================================================================


class TestPaycheckModelORM:
    """Round-trip persistence tests for PaycheckModel."""

    def test_create_and_query(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=40, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)
        obj = PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("3269.23"),
            federal_tax=Decimal("523.08"),
            state_tax=Decimal("196.15"),
            social_security=Decimal("202.69"),
            medicare=Decimal("47.40"),
            other_deductions=Decimal("150.00"),
            net_pay=Decimal("2149.91"),
            check_number="CHK-10001",
            direct_deposit=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PaycheckModel, obj.id)
        assert queried is not None
        assert queried.payroll_run_id == run.id
        assert queried.employee_id == emp.id
        assert queried.pay_period_id == pp.id
        assert queried.gross_pay == Decimal("3269.23")
        assert queried.federal_tax == Decimal("523.08")
        assert queried.state_tax == Decimal("196.15")
        assert queried.social_security == Decimal("202.69")
        assert queried.medicare == Decimal("47.40")
        assert queried.other_deductions == Decimal("150.00")
        assert queried.net_pay == Decimal("2149.91")
        assert queried.check_number == "CHK-10001"
        assert queried.direct_deposit is False

    def test_direct_deposit_paycheck(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=41, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)
        obj = PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("5000.00"),
            net_pay=Decimal("3500.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PaycheckModel, obj.id)
        assert queried.direct_deposit is True
        assert queried.check_number is None

    def test_relationships(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id, employee_number="PAY-REL-EMP", first_name="Pay")
        pp = _make_pay_period(session, test_actor_id, period_number=42, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)
        obj = PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("4000.00"),
            net_pay=Decimal("2800.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PaycheckModel, obj.id)
        assert queried.payroll_run is not None
        assert queried.employee is not None
        assert queried.employee.first_name == "Pay"
        assert queried.pay_period is not None
        assert queried.pay_period.period_number == 42

    def test_unique_run_employee(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=43, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)
        PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("5000.00"),
            net_pay=Decimal("3500.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("5000.00"),
            net_pay=Decimal("3500.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = PaycheckModel(
            payroll_run_id=run.id,
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("1000.00"),
            net_pay=Decimal("700.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_payroll_run_nonexistent(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        pp = _make_pay_period(session, test_actor_id, period_number=44, year=2024)
        obj = PaycheckModel(
            payroll_run_id=uuid4(),
            employee_id=emp.id,
            pay_period_id=pp.id,
            gross_pay=Decimal("5000.00"),
            net_pay=Decimal("3500.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_employee_nonexistent(self, session, test_actor_id):
        pp = _make_pay_period(session, test_actor_id, period_number=45, year=2024)
        run = _make_payroll_run(session, test_actor_id, pp.id)
        obj = PaycheckModel(
            payroll_run_id=run.id,
            employee_id=uuid4(),
            pay_period_id=pp.id,
            gross_pay=Decimal("5000.00"),
            net_pay=Decimal("3500.00"),
            direct_deposit=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# WithholdingResultModel
# ===================================================================


class TestWithholdingResultModelORM:
    """Round-trip persistence tests for WithholdingResultModel."""

    def test_create_and_query(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        obj = WithholdingResultModel(
            employee_id=emp.id,
            gross_pay=Decimal("5000.00"),
            federal_withholding=Decimal("800.00"),
            state_withholding=Decimal("300.00"),
            social_security=Decimal("310.00"),
            medicare=Decimal("72.50"),
            total_deductions=Decimal("1482.50"),
            net_pay=Decimal("3517.50"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(WithholdingResultModel, obj.id)
        assert queried is not None
        assert queried.employee_id == emp.id
        assert queried.gross_pay == Decimal("5000.00")
        assert queried.federal_withholding == Decimal("800.00")
        assert queried.state_withholding == Decimal("300.00")
        assert queried.social_security == Decimal("310.00")
        assert queried.medicare == Decimal("72.50")
        assert queried.total_deductions == Decimal("1482.50")
        assert queried.net_pay == Decimal("3517.50")

    def test_employee_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id, employee_number="WH-EMP", first_name="Withhold")
        obj = WithholdingResultModel(
            employee_id=emp.id,
            gross_pay=Decimal("3000.00"),
            federal_withholding=Decimal("480.00"),
            state_withholding=Decimal("180.00"),
            social_security=Decimal("186.00"),
            medicare=Decimal("43.50"),
            total_deductions=Decimal("889.50"),
            net_pay=Decimal("2110.50"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(WithholdingResultModel, obj.id)
        assert queried.employee is not None
        assert queried.employee.first_name == "Withhold"

    def test_fk_employee_nonexistent(self, session, test_actor_id):
        obj = WithholdingResultModel(
            employee_id=uuid4(),
            gross_pay=Decimal("5000.00"),
            federal_withholding=Decimal("800.00"),
            state_withholding=Decimal("300.00"),
            social_security=Decimal("310.00"),
            medicare=Decimal("72.50"),
            total_deductions=Decimal("1482.50"),
            net_pay=Decimal("3517.50"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# BenefitsDeductionModel
# ===================================================================


class TestBenefitsDeductionModelORM:
    """Round-trip persistence tests for BenefitsDeductionModel."""

    def test_create_and_query(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        obj = BenefitsDeductionModel(
            employee_id=emp.id,
            plan_name="Medical - PPO",
            employee_amount=Decimal("250.00"),
            employer_amount=Decimal("750.00"),
            period="2024-PP01",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BenefitsDeductionModel, obj.id)
        assert queried is not None
        assert queried.employee_id == emp.id
        assert queried.plan_name == "Medical - PPO"
        assert queried.employee_amount == Decimal("250.00")
        assert queried.employer_amount == Decimal("750.00")
        assert queried.period == "2024-PP01"

    def test_employee_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id, employee_number="BEN-EMP", first_name="Benefits")
        obj = BenefitsDeductionModel(
            employee_id=emp.id,
            plan_name="Dental",
            employee_amount=Decimal("50.00"),
            employer_amount=Decimal("100.00"),
            period="2024-PP02",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BenefitsDeductionModel, obj.id)
        assert queried.employee is not None
        assert queried.employee.first_name == "Benefits"

    def test_fk_employee_nonexistent(self, session, test_actor_id):
        obj = BenefitsDeductionModel(
            employee_id=uuid4(),
            plan_name="Medical",
            employee_amount=Decimal("250.00"),
            employer_amount=Decimal("750.00"),
            period="2024-PP01",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# EmployerContributionModel
# ===================================================================


class TestEmployerContributionModelORM:
    """Round-trip persistence tests for EmployerContributionModel."""

    def test_create_and_query(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id)
        obj = EmployerContributionModel(
            employee_id=emp.id,
            plan_name="401k Match",
            amount=Decimal("500.00"),
            period="2024-PP01",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(EmployerContributionModel, obj.id)
        assert queried is not None
        assert queried.employee_id == emp.id
        assert queried.plan_name == "401k Match"
        assert queried.amount == Decimal("500.00")
        assert queried.period == "2024-PP01"

    def test_employee_relationship(self, session, test_actor_id):
        emp = _make_employee(session, test_actor_id, employee_number="CONT-EMP", first_name="Contrib")
        obj = EmployerContributionModel(
            employee_id=emp.id,
            plan_name="HSA Employer Contribution",
            amount=Decimal("125.00"),
            period="2024-PP03",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(EmployerContributionModel, obj.id)
        assert queried.employee is not None
        assert queried.employee.first_name == "Contrib"

    def test_fk_employee_nonexistent(self, session, test_actor_id):
        obj = EmployerContributionModel(
            employee_id=uuid4(),
            plan_name="401k Match",
            amount=Decimal("500.00"),
            period="2024-PP01",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
