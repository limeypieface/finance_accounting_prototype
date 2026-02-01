"""ORM round-trip tests for the Expense module.

Verifies that all four Expense ORM models can be persisted and queried,
that parent-child relationships load correctly, that FK constraints are
enforced, and that unique constraints reject duplicates.

Models under test:
    - ExpenseReportModel
    - ExpenseLineModel
    - ExpensePolicyModel
    - ReimbursementModel
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.expense.orm import (
    ExpenseLineModel,
    ExpensePolicyModel,
    ExpenseReportModel,
    ReimbursementModel,
)
from tests.modules.conftest import (
    TEST_EMPLOYEE_ID,
    TEST_EXPENSE_REPORT_ID,
)


# ---------------------------------------------------------------------------
# ExpenseReportModel
# ---------------------------------------------------------------------------


class TestExpenseReportModelORM:
    """Round-trip persistence tests for ExpenseReportModel."""

    def test_create_and_query(self, session, test_actor_id, test_employee_party):
        """Insert an expense report and read it back -- all fields must match."""
        report = ExpenseReportModel(
            report_number="EXP-ORM-001",
            employee_id=TEST_EMPLOYEE_ID,
            report_date=date(2024, 3, 15),
            purpose="Conference travel",
            total_amount=Decimal("1250.50"),
            currency="USD",
            status="submitted",
            submitted_date=date(2024, 3, 16),
            created_by_id=test_actor_id,
        )
        session.add(report)
        session.flush()

        queried = session.get(ExpenseReportModel, report.id)
        assert queried is not None
        assert queried.report_number == "EXP-ORM-001"
        assert queried.employee_id == TEST_EMPLOYEE_ID
        assert queried.report_date == date(2024, 3, 15)
        assert queried.purpose == "Conference travel"
        assert queried.total_amount == Decimal("1250.50")
        assert queried.currency == "USD"
        assert queried.status == "submitted"
        assert queried.submitted_date == date(2024, 3, 16)
        assert queried.approved_date is None
        assert queried.approved_by is None
        assert queried.paid_date is None
        assert queried.project_id is None
        assert queried.department_id is None

    def test_unique_report_number(self, session, test_actor_id, test_employee_party):
        """Duplicate report_number must raise IntegrityError."""
        for suffix in ("A", "B"):
            report = ExpenseReportModel(
                report_number="EXP-DUP-001",
                employee_id=TEST_EMPLOYEE_ID,
                report_date=date(2024, 4, 1),
                purpose=f"Purpose {suffix}",
                total_amount=Decimal("0"),
                currency="USD",
                status="draft",
                created_by_id=test_actor_id,
            )
            session.add(report)
            if suffix == "A":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_fk_employee_id_constraint(self, session, test_actor_id):
        """Nonexistent employee_id must raise IntegrityError (FK to parties)."""
        report = ExpenseReportModel(
            report_number="EXP-BADFK-001",
            employee_id=uuid4(),
            report_date=date(2024, 5, 1),
            purpose="Bad FK test",
            total_amount=Decimal("0"),
            currency="USD",
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(report)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_lines(self, session, test_actor_id, test_employee_party):
        """Expense report loads its lines through the relationship."""
        report = ExpenseReportModel(
            report_number="EXP-REL-001",
            employee_id=TEST_EMPLOYEE_ID,
            report_date=date(2024, 6, 1),
            purpose="Relationship test",
            total_amount=Decimal("100.00"),
            currency="USD",
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(report)
        session.flush()

        line = ExpenseLineModel(
            report_id=report.id,
            line_number=1,
            expense_date=date(2024, 6, 1),
            category="meals",
            description="Client dinner",
            amount=Decimal("100.00"),
            currency="USD",
            payment_method="corporate_card",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire_all()
        refreshed = session.get(ExpenseReportModel, report.id)
        assert len(refreshed.lines) == 1
        assert refreshed.lines[0].description == "Client dinner"


# ---------------------------------------------------------------------------
# ExpenseLineModel
# ---------------------------------------------------------------------------


class TestExpenseLineModelORM:
    """Round-trip persistence tests for ExpenseLineModel."""

    def test_create_and_query(self, session, test_actor_id, test_expense_report):
        """Insert an expense line and read it back."""
        line = ExpenseLineModel(
            report_id=TEST_EXPENSE_REPORT_ID,
            line_number=1,
            expense_date=date(2024, 1, 10),
            category="lodging",
            description="Hotel stay",
            amount=Decimal("250.00"),
            currency="USD",
            payment_method="corporate_card",
            receipt_attached=True,
            billable=False,
            gl_account_code="6200-000",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(ExpenseLineModel, line.id)
        assert queried is not None
        assert queried.report_id == TEST_EXPENSE_REPORT_ID
        assert queried.line_number == 1
        assert queried.expense_date == date(2024, 1, 10)
        assert queried.category == "lodging"
        assert queried.description == "Hotel stay"
        assert queried.amount == Decimal("250.00")
        assert queried.currency == "USD"
        assert queried.payment_method == "corporate_card"
        assert queried.receipt_attached is True
        assert queried.billable is False
        assert queried.gl_account_code == "6200-000"
        assert queried.project_id is None
        assert queried.card_transaction_id is None
        assert queried.violation_notes is None

    def test_unique_report_line_number(self, session, test_actor_id, test_expense_report):
        """Duplicate (report_id, line_number) must raise IntegrityError."""
        for tag in ("first", "duplicate"):
            line = ExpenseLineModel(
                report_id=TEST_EXPENSE_REPORT_ID,
                line_number=99,
                expense_date=date(2024, 1, 11),
                category="meals",
                description=f"Meal ({tag})",
                amount=Decimal("50.00"),
                currency="USD",
                payment_method="cash",
                created_by_id=test_actor_id,
            )
            session.add(line)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_fk_report_id_constraint(self, session, test_actor_id):
        """Nonexistent report_id must raise IntegrityError."""
        line = ExpenseLineModel(
            report_id=uuid4(),
            line_number=1,
            expense_date=date(2024, 2, 1),
            category="transport",
            description="Taxi",
            amount=Decimal("30.00"),
            currency="USD",
            payment_method="cash",
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_parent_relationship(self, session, test_actor_id, test_expense_report):
        """Line's back_populates 'report' relationship loads the parent."""
        line = ExpenseLineModel(
            report_id=TEST_EXPENSE_REPORT_ID,
            line_number=2,
            expense_date=date(2024, 1, 12),
            category="airfare",
            description="Round trip flight",
            amount=Decimal("400.00"),
            currency="USD",
            payment_method="corporate_card",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire_all()
        refreshed = session.get(ExpenseLineModel, line.id)
        assert refreshed.report is not None
        assert refreshed.report.id == TEST_EXPENSE_REPORT_ID


# ---------------------------------------------------------------------------
# ExpensePolicyModel
# ---------------------------------------------------------------------------


class TestExpensePolicyModelORM:
    """Round-trip persistence tests for ExpensePolicyModel."""

    def test_create_and_query(self, session, test_actor_id):
        """Insert a policy and read it back."""
        policy = ExpensePolicyModel(
            category="lodging",
            daily_limit=Decimal("300.00"),
            per_transaction_limit=Decimal("500.00"),
            requires_receipt_above=Decimal("75.00"),
            requires_justification=True,
            created_by_id=test_actor_id,
        )
        session.add(policy)
        session.flush()

        queried = session.get(ExpensePolicyModel, policy.id)
        assert queried is not None
        assert queried.category == "lodging"
        assert queried.daily_limit == Decimal("300.00")
        assert queried.per_transaction_limit == Decimal("500.00")
        assert queried.requires_receipt_above == Decimal("75.00")
        assert queried.requires_justification is True

    def test_nullable_limits(self, session, test_actor_id):
        """Policy with all optional limits set to None persists correctly."""
        policy = ExpensePolicyModel(
            category="miscellaneous",
            daily_limit=None,
            per_transaction_limit=None,
            requires_receipt_above=None,
            requires_justification=False,
            created_by_id=test_actor_id,
        )
        session.add(policy)
        session.flush()

        queried = session.get(ExpensePolicyModel, policy.id)
        assert queried.daily_limit is None
        assert queried.per_transaction_limit is None
        assert queried.requires_receipt_above is None
        assert queried.requires_justification is False

    def test_unique_category(self, session, test_actor_id):
        """Duplicate category must raise IntegrityError."""
        for tag in ("first", "duplicate"):
            policy = ExpensePolicyModel(
                category="airfare_dup",
                daily_limit=Decimal("1000.00"),
                requires_justification=False,
                created_by_id=test_actor_id,
            )
            session.add(policy)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()


# ---------------------------------------------------------------------------
# ReimbursementModel
# ---------------------------------------------------------------------------


class TestReimbursementModelORM:
    """Round-trip persistence tests for ReimbursementModel."""

    def test_create_and_query(self, session, test_actor_id, test_expense_report):
        """Insert a reimbursement and read it back."""
        reimbursement = ReimbursementModel(
            report_id=TEST_EXPENSE_REPORT_ID,
            employee_id=TEST_EMPLOYEE_ID,
            amount=Decimal("500.00"),
            currency="USD",
            payment_date=date(2024, 2, 1),
            payment_method="direct_deposit",
            payment_reference="ACH-12345",
            created_by_id=test_actor_id,
        )
        session.add(reimbursement)
        session.flush()

        queried = session.get(ReimbursementModel, reimbursement.id)
        assert queried is not None
        assert queried.report_id == TEST_EXPENSE_REPORT_ID
        assert queried.employee_id == TEST_EMPLOYEE_ID
        assert queried.amount == Decimal("500.00")
        assert queried.currency == "USD"
        assert queried.payment_date == date(2024, 2, 1)
        assert queried.payment_method == "direct_deposit"
        assert queried.payment_reference == "ACH-12345"

    def test_nullable_payment_reference(self, session, test_actor_id, test_expense_report):
        """Reimbursement without payment_reference persists correctly."""
        reimbursement = ReimbursementModel(
            report_id=TEST_EXPENSE_REPORT_ID,
            employee_id=TEST_EMPLOYEE_ID,
            amount=Decimal("100.00"),
            currency="USD",
            payment_date=date(2024, 2, 15),
            payment_method="check",
            payment_reference=None,
            created_by_id=test_actor_id,
        )
        session.add(reimbursement)
        session.flush()

        queried = session.get(ReimbursementModel, reimbursement.id)
        assert queried.payment_reference is None

    def test_fk_report_id_constraint(self, session, test_actor_id, test_employee_party):
        """Nonexistent report_id must raise IntegrityError."""
        reimbursement = ReimbursementModel(
            report_id=uuid4(),
            employee_id=TEST_EMPLOYEE_ID,
            amount=Decimal("50.00"),
            currency="USD",
            payment_date=date(2024, 3, 1),
            payment_method="direct_deposit",
            created_by_id=test_actor_id,
        )
        session.add(reimbursement)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_employee_id_constraint(self, session, test_actor_id, test_expense_report):
        """Nonexistent employee_id must raise IntegrityError (FK to parties)."""
        reimbursement = ReimbursementModel(
            report_id=TEST_EXPENSE_REPORT_ID,
            employee_id=uuid4(),
            amount=Decimal("50.00"),
            currency="USD",
            payment_date=date(2024, 3, 1),
            payment_method="direct_deposit",
            created_by_id=test_actor_id,
        )
        session.add(reimbursement)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
