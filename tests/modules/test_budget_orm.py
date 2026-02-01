"""ORM round-trip tests for Budget module.

Covers all five models:
    - BudgetModel (budget header / version container)
    - BudgetLineModel (line items)
    - BudgetVersionModel (amendment snapshots)
    - BudgetTransferModel (inter-line transfers)
    - BudgetAllocationModel (top-down allocations)
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.budget.orm import (
    BudgetAllocationModel,
    BudgetLineModel,
    BudgetModel,
    BudgetTransferModel,
    BudgetVersionModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_budget(session, test_actor_id, **overrides):
    """Create and flush a BudgetModel with sensible defaults."""
    fields = dict(
        name=f"FY2024 Budget {uuid4().hex[:6]}",
        fiscal_year=2024,
        status="draft",
        description="Annual operating budget",
        created_date=date(2024, 1, 1),
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    obj = BudgetModel(**fields)
    session.add(obj)
    session.flush()
    return obj


# ==========================================================================
# BudgetModel
# ==========================================================================


class TestBudgetModelORM:
    """Round-trip persistence tests for BudgetModel."""

    def test_create_and_query(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        queried = session.get(BudgetModel, budget.id)
        assert queried is not None
        assert queried.fiscal_year == 2024
        assert queried.status == "draft"
        assert queried.description == "Annual operating budget"
        assert queried.created_date == date(2024, 1, 1)

    def test_unique_name_fiscal_year(self, session, test_actor_id):
        name = f"Unique Budget {uuid4().hex[:6]}"
        _make_budget(session, test_actor_id, name=name, fiscal_year=2024)

        dup = BudgetModel(
            name=name,
            fiscal_year=2024,
            status="approved",
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_name_different_year_allowed(self, session, test_actor_id):
        name = f"Cross-Year Budget {uuid4().hex[:6]}"
        _make_budget(session, test_actor_id, name=name, fiscal_year=2024)

        other_year = BudgetModel(
            name=name,
            fiscal_year=2025,
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(other_year)
        session.flush()

        queried = session.get(BudgetModel, other_year.id)
        assert queried is not None
        assert queried.fiscal_year == 2025

    def test_defaults(self, session, test_actor_id):
        obj = BudgetModel(
            name=f"Minimal Budget {uuid4().hex[:6]}",
            fiscal_year=2024,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BudgetModel, obj.id)
        assert queried.status == "draft"
        assert queried.description is None
        assert queried.created_date is None

    def test_child_relationship_lines(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        line = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            amount=Decimal("10000.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire(budget)
        queried = session.get(BudgetModel, budget.id)
        assert len(queried.lines) == 1
        assert queried.lines[0].account_code == "5100"


# ==========================================================================
# BudgetLineModel
# ==========================================================================


class TestBudgetLineModelORM:
    """Round-trip persistence tests for BudgetLineModel."""

    def test_create_and_query(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        line = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            amount=Decimal("25000.00"),
            currency="USD",
            dimensions_json='[["department", "Engineering"]]',
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(BudgetLineModel, line.id)
        assert queried is not None
        assert queried.version_id == budget.id
        assert queried.account_code == "5100"
        assert queried.period == "2024-01"
        assert queried.amount == Decimal("25000.00")
        assert queried.currency == "USD"
        assert queried.dimensions_json == '[["department", "Engineering"]]'

    def test_unique_version_account_period(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        first = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        dup = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            amount=Decimal("30000.00"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_account_different_period_allowed(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        line1 = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        line2 = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-02",
            amount=Decimal("26000.00"),
            created_by_id=test_actor_id,
        )
        session.add_all([line1, line2])
        session.flush()

        queried1 = session.get(BudgetLineModel, line1.id)
        queried2 = session.get(BudgetLineModel, line2.id)
        assert queried1 is not None
        assert queried2 is not None
        assert queried1.period == "2024-01"
        assert queried2.period == "2024-02"

    def test_fk_version_id_invalid(self, session, test_actor_id):
        obj = BudgetLineModel(
            version_id=uuid4(),
            account_code="5100",
            period="2024-01",
            amount=Decimal("10000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        line = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(BudgetLineModel, line.id)
        assert queried.amount == Decimal("0")
        assert queried.currency == "USD"
        assert queried.dimensions_json is None

    def test_parent_relationship(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        line = BudgetLineModel(
            version_id=budget.id,
            account_code="5100",
            period="2024-01",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(BudgetLineModel, line.id)
        assert queried.budget is not None
        assert queried.budget.id == budget.id


# ==========================================================================
# BudgetVersionModel (amendment snapshots)
# ==========================================================================


class TestBudgetVersionModelORM:
    """Round-trip persistence tests for BudgetVersionModel."""

    def test_create_and_query(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        ver = BudgetVersionModel(
            budget_id=budget.id,
            version_number=1,
            amendment_date=date(2024, 3, 15),
            amendment_reason="Q1 revenue shortfall adjustment",
            previous_total=Decimal("1000000.00"),
            new_total=Decimal("950000.00"),
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(ver)
        session.flush()

        queried = session.get(BudgetVersionModel, ver.id)
        assert queried is not None
        assert queried.budget_id == budget.id
        assert queried.version_number == 1
        assert queried.amendment_date == date(2024, 3, 15)
        assert queried.amendment_reason == "Q1 revenue shortfall adjustment"
        assert queried.previous_total == Decimal("1000000.00")
        assert queried.new_total == Decimal("950000.00")
        assert queried.amended_by == test_actor_id

    def test_unique_budget_version_number(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        first = BudgetVersionModel(
            budget_id=budget.id,
            version_number=1,
            amendment_date=date(2024, 3, 15),
            amendment_reason="First amendment",
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        dup = BudgetVersionModel(
            budget_id=budget.id,
            version_number=1,
            amendment_date=date(2024, 6, 1),
            amendment_reason="Duplicate version number",
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_multiple_versions_allowed(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        v1 = BudgetVersionModel(
            budget_id=budget.id,
            version_number=1,
            amendment_date=date(2024, 3, 15),
            amendment_reason="Q1 adjustment",
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        v2 = BudgetVersionModel(
            budget_id=budget.id,
            version_number=2,
            amendment_date=date(2024, 6, 15),
            amendment_reason="Q2 adjustment",
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add_all([v1, v2])
        session.flush()

        q1 = session.get(BudgetVersionModel, v1.id)
        q2 = session.get(BudgetVersionModel, v2.id)
        assert q1 is not None
        assert q2 is not None
        assert q1.version_number == 1
        assert q2.version_number == 2

    def test_fk_budget_id_invalid(self, session, test_actor_id):
        obj = BudgetVersionModel(
            budget_id=uuid4(),
            version_number=1,
            amendment_date=date(2024, 3, 15),
            amendment_reason="Orphan version",
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        ver = BudgetVersionModel(
            budget_id=budget.id,
            version_number=1,
            amendment_date=date(2024, 3, 15),
            amendment_reason="Minimal amendment",
            amended_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(ver)
        session.flush()

        queried = session.get(BudgetVersionModel, ver.id)
        assert queried.previous_total == Decimal("0")
        assert queried.new_total == Decimal("0")


# ==========================================================================
# BudgetTransferModel
# ==========================================================================


class TestBudgetTransferModelORM:
    """Round-trip persistence tests for BudgetTransferModel."""

    def test_create_and_query(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        xfer = BudgetTransferModel(
            version_id=budget.id,
            from_account_code="5100",
            from_period="2024-01",
            to_account_code="5200",
            to_period="2024-01",
            amount=Decimal("5000.00"),
            currency="USD",
            transfer_date=date(2024, 2, 15),
            reason="Reallocate from OpEx to Travel",
            transferred_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()

        queried = session.get(BudgetTransferModel, xfer.id)
        assert queried is not None
        assert queried.version_id == budget.id
        assert queried.from_account_code == "5100"
        assert queried.from_period == "2024-01"
        assert queried.to_account_code == "5200"
        assert queried.to_period == "2024-01"
        assert queried.amount == Decimal("5000.00")
        assert queried.currency == "USD"
        assert queried.transfer_date == date(2024, 2, 15)
        assert queried.reason == "Reallocate from OpEx to Travel"
        assert queried.transferred_by == test_actor_id

    def test_fk_version_id_invalid(self, session, test_actor_id):
        obj = BudgetTransferModel(
            version_id=uuid4(),
            from_account_code="5100",
            from_period="2024-01",
            to_account_code="5200",
            to_period="2024-01",
            amount=Decimal("5000.00"),
            transfer_date=date(2024, 2, 15),
            transferred_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults_and_nullable(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        xfer = BudgetTransferModel(
            version_id=budget.id,
            from_account_code="5100",
            from_period="2024-01",
            to_account_code="5200",
            to_period="2024-01",
            amount=Decimal("3000.00"),
            transfer_date=date(2024, 2, 15),
            transferred_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()

        queried = session.get(BudgetTransferModel, xfer.id)
        assert queried.currency == "USD"
        assert queried.reason is None


# ==========================================================================
# BudgetAllocationModel
# ==========================================================================


class TestBudgetAllocationModelORM:
    """Round-trip persistence tests for BudgetAllocationModel."""

    def test_create_and_query(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        alloc = BudgetAllocationModel(
            version_id=budget.id,
            target_entity_type="department",
            target_entity_id="DEPT-ENG",
            account_code="5100",
            period="2024-01",
            allocated_amount=Decimal("150000.00"),
            currency="USD",
            allocation_method="headcount_based",
            allocation_date=date(2024, 1, 15),
            allocated_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        session.flush()

        queried = session.get(BudgetAllocationModel, alloc.id)
        assert queried is not None
        assert queried.version_id == budget.id
        assert queried.target_entity_type == "department"
        assert queried.target_entity_id == "DEPT-ENG"
        assert queried.account_code == "5100"
        assert queried.period == "2024-01"
        assert queried.allocated_amount == Decimal("150000.00")
        assert queried.currency == "USD"
        assert queried.allocation_method == "headcount_based"
        assert queried.allocation_date == date(2024, 1, 15)
        assert queried.allocated_by == test_actor_id

    def test_fk_version_id_invalid(self, session, test_actor_id):
        obj = BudgetAllocationModel(
            version_id=uuid4(),
            target_entity_type="department",
            target_entity_id="DEPT-GHOST",
            account_code="5100",
            period="2024-01",
            allocated_amount=Decimal("50000.00"),
            allocation_method="manual",
            allocation_date=date(2024, 1, 15),
            allocated_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        alloc = BudgetAllocationModel(
            version_id=budget.id,
            target_entity_type="cost_center",
            target_entity_id="CC-1001",
            account_code="5200",
            period="2024-02",
            allocation_date=date(2024, 1, 20),
            allocated_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        session.flush()

        queried = session.get(BudgetAllocationModel, alloc.id)
        assert queried.allocated_amount == Decimal("0")
        assert queried.currency == "USD"
        assert queried.allocation_method == "manual"

    def test_multiple_allocations_per_budget(self, session, test_actor_id):
        budget = _make_budget(session, test_actor_id)

        alloc1 = BudgetAllocationModel(
            version_id=budget.id,
            target_entity_type="department",
            target_entity_id="DEPT-ENG",
            account_code="5100",
            period="2024-01",
            allocated_amount=Decimal("150000.00"),
            allocation_method="manual",
            allocation_date=date(2024, 1, 15),
            allocated_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        alloc2 = BudgetAllocationModel(
            version_id=budget.id,
            target_entity_type="department",
            target_entity_id="DEPT-SALES",
            account_code="5200",
            period="2024-01",
            allocated_amount=Decimal("200000.00"),
            allocation_method="revenue_based",
            allocation_date=date(2024, 1, 15),
            allocated_by=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add_all([alloc1, alloc2])
        session.flush()

        q1 = session.get(BudgetAllocationModel, alloc1.id)
        q2 = session.get(BudgetAllocationModel, alloc2.id)
        assert q1 is not None
        assert q2 is not None
        assert q1.target_entity_id == "DEPT-ENG"
        assert q2.target_entity_id == "DEPT-SALES"
