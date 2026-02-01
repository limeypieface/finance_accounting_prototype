"""ORM round-trip tests for WIP (Work-in-Process) module."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.wip.orm import (
    WorkOrderModel,
    WorkOrderLineModel,
    OperationModel,
    LaborEntryModel,
    OverheadApplicationModel,
    ByproductRecordModel,
    ProductionCostSummaryModel,
    UnitCostBreakdownModel,
)


# ---------------------------------------------------------------------------
# Local helpers -- create parent rows with correct ORM field names
# ---------------------------------------------------------------------------

def _make_work_order(session, test_actor_id, **overrides):
    """Create a WorkOrderModel with sensible defaults."""
    fields = dict(
        order_number=f"WO-{uuid4().hex[:8]}",
        item_id=uuid4(),
        quantity_ordered=Decimal("100"),
        quantity_completed=Decimal("0"),
        quantity_scrapped=Decimal("0"),
        status="planned",
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    wo = WorkOrderModel(**fields)
    session.add(wo)
    session.flush()
    return wo


def _make_operation(session, test_actor_id, work_order_id, **overrides):
    """Create an OperationModel with sensible defaults."""
    fields = dict(
        work_order_id=work_order_id,
        sequence=10,
        work_center_id=uuid4(),
        description="Assembly",
        setup_time_hours=Decimal("0.5"),
        run_time_hours=Decimal("2.0"),
        labor_rate=Decimal("50.00"),
        overhead_rate=Decimal("25.00"),
        status="not_started",
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    op = OperationModel(**fields)
    session.add(op)
    session.flush()
    return op


# ===================================================================
# WorkOrderModel
# ===================================================================

class TestWorkOrderModelORM:

    def test_create_and_query(self, session, test_actor_id):
        item_id = uuid4()
        so_id = uuid4()
        wo = _make_work_order(
            session, test_actor_id,
            order_number="WO-ROUND-001",
            item_id=item_id,
            quantity_ordered=Decimal("500"),
            quantity_completed=Decimal("100"),
            quantity_scrapped=Decimal("5"),
            planned_start_date=date(2024, 3, 1),
            planned_end_date=date(2024, 3, 31),
            actual_start_date=date(2024, 3, 2),
            actual_end_date=None,
            status="released",
            sales_order_id=so_id,
        )

        queried = session.get(WorkOrderModel, wo.id)
        assert queried is not None
        assert queried.order_number == "WO-ROUND-001"
        assert queried.item_id == item_id
        assert queried.quantity_ordered == Decimal("500")
        assert queried.quantity_completed == Decimal("100")
        assert queried.quantity_scrapped == Decimal("5")
        assert queried.planned_start_date == date(2024, 3, 1)
        assert queried.planned_end_date == date(2024, 3, 31)
        assert queried.actual_start_date == date(2024, 3, 2)
        assert queried.actual_end_date is None
        assert queried.status == "released"
        assert queried.sales_order_id == so_id

    def test_unique_order_number_constraint(self, session, test_actor_id):
        _make_work_order(session, test_actor_id, order_number="WO-DUP-001")
        dup = WorkOrderModel(
            order_number="WO-DUP-001",
            item_id=uuid4(),
            quantity_ordered=Decimal("10"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_self_referential_parent(self, session, test_actor_id):
        parent = _make_work_order(session, test_actor_id, order_number="WO-PARENT-001")
        child = _make_work_order(
            session, test_actor_id,
            order_number="WO-CHILD-001",
            parent_work_order_id=parent.id,
        )

        queried = session.get(WorkOrderModel, child.id)
        assert queried.parent_work_order_id == parent.id
        assert queried.parent_work_order is not None
        assert queried.parent_work_order.order_number == "WO-PARENT-001"

    def test_fk_parent_work_order_nonexistent(self, session, test_actor_id):
        wo = WorkOrderModel(
            order_number=f"WO-{uuid4().hex[:8]}",
            item_id=uuid4(),
            quantity_ordered=Decimal("10"),
            parent_work_order_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(wo)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_nullable_optional_fields(self, session, test_actor_id):
        wo = _make_work_order(
            session, test_actor_id,
            planned_start_date=None,
            planned_end_date=None,
            actual_start_date=None,
            actual_end_date=None,
            parent_work_order_id=None,
            sales_order_id=None,
        )
        queried = session.get(WorkOrderModel, wo.id)
        assert queried.planned_start_date is None
        assert queried.planned_end_date is None
        assert queried.actual_start_date is None
        assert queried.actual_end_date is None
        assert queried.parent_work_order_id is None
        assert queried.sales_order_id is None

    def test_default_status_and_quantities(self, session, test_actor_id):
        wo = WorkOrderModel(
            order_number=f"WO-{uuid4().hex[:8]}",
            item_id=uuid4(),
            quantity_ordered=Decimal("50"),
            created_by_id=test_actor_id,
        )
        session.add(wo)
        session.flush()

        queried = session.get(WorkOrderModel, wo.id)
        assert queried.status == "planned"
        assert queried.quantity_completed == Decimal("0")
        assert queried.quantity_scrapped == Decimal("0")


# ===================================================================
# WorkOrderLineModel
# ===================================================================

class TestWorkOrderLineModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        item_id = uuid4()
        line = WorkOrderLineModel(
            work_order_id=wo.id,
            item_id=item_id,
            quantity_required=Decimal("200"),
            quantity_issued=Decimal("50"),
            unit_cost=Decimal("12.75"),
            operation_seq=20,
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(WorkOrderLineModel, line.id)
        assert queried is not None
        assert queried.work_order_id == wo.id
        assert queried.item_id == item_id
        assert queried.quantity_required == Decimal("200")
        assert queried.quantity_issued == Decimal("50")
        assert queried.unit_cost == Decimal("12.75")
        assert queried.operation_seq == 20

    def test_fk_to_work_order(self, session, test_actor_id):
        line = WorkOrderLineModel(
            work_order_id=uuid4(),
            item_id=uuid4(),
            quantity_required=Decimal("10"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        line = WorkOrderLineModel(
            work_order_id=wo.id,
            item_id=uuid4(),
            quantity_required=Decimal("100"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        assert line.work_order is not None
        assert line.work_order.id == wo.id
        session.refresh(wo)
        assert any(l.id == line.id for l in wo.lines)

    def test_defaults(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        line = WorkOrderLineModel(
            work_order_id=wo.id,
            item_id=uuid4(),
            quantity_required=Decimal("50"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(WorkOrderLineModel, line.id)
        assert queried.quantity_issued == Decimal("0")
        assert queried.unit_cost == Decimal("0")
        assert queried.operation_seq == 10


# ===================================================================
# OperationModel
# ===================================================================

class TestOperationModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        wc_id = uuid4()
        op = _make_operation(
            session, test_actor_id,
            work_order_id=wo.id,
            sequence=20,
            work_center_id=wc_id,
            description="Welding Station",
            setup_time_hours=Decimal("1.5"),
            run_time_hours=Decimal("4.0"),
            labor_rate=Decimal("65.00"),
            overhead_rate=Decimal("30.00"),
            status="in_progress",
            quantity_completed=Decimal("25"),
        )

        queried = session.get(OperationModel, op.id)
        assert queried is not None
        assert queried.work_order_id == wo.id
        assert queried.sequence == 20
        assert queried.work_center_id == wc_id
        assert queried.description == "Welding Station"
        assert queried.setup_time_hours == Decimal("1.5")
        assert queried.run_time_hours == Decimal("4.0")
        assert queried.labor_rate == Decimal("65.00")
        assert queried.overhead_rate == Decimal("30.00")
        assert queried.status == "in_progress"
        assert queried.quantity_completed == Decimal("25")

    def test_fk_to_work_order(self, session, test_actor_id):
        op = OperationModel(
            work_order_id=uuid4(),
            sequence=10,
            work_center_id=uuid4(),
            description="Phantom",
            created_by_id=test_actor_id,
        )
        session.add(op)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = _make_operation(session, test_actor_id, work_order_id=wo.id)

        assert op.work_order is not None
        assert op.work_order.id == wo.id
        session.refresh(wo)
        assert any(o.id == op.id for o in wo.operations)

    def test_defaults(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = OperationModel(
            work_order_id=wo.id,
            sequence=10,
            work_center_id=uuid4(),
            description="Default check",
            created_by_id=test_actor_id,
        )
        session.add(op)
        session.flush()

        queried = session.get(OperationModel, op.id)
        assert queried.setup_time_hours == Decimal("0")
        assert queried.run_time_hours == Decimal("0")
        assert queried.labor_rate == Decimal("0")
        assert queried.overhead_rate == Decimal("0")
        assert queried.status == "not_started"
        assert queried.quantity_completed == Decimal("0")


# ===================================================================
# LaborEntryModel
# ===================================================================

class TestLaborEntryModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = _make_operation(session, test_actor_id, work_order_id=wo.id)
        emp_id = uuid4()
        labor = LaborEntryModel(
            work_order_id=wo.id,
            operation_id=op.id,
            employee_id=emp_id,
            work_date=date(2024, 4, 10),
            hours=Decimal("8.0"),
            labor_rate=Decimal("50.00"),
            labor_cost=Decimal("400.00"),
            entry_type="run",
            created_by_id=test_actor_id,
        )
        session.add(labor)
        session.flush()

        queried = session.get(LaborEntryModel, labor.id)
        assert queried is not None
        assert queried.work_order_id == wo.id
        assert queried.operation_id == op.id
        assert queried.employee_id == emp_id
        assert queried.work_date == date(2024, 4, 10)
        assert queried.hours == Decimal("8.0")
        assert queried.labor_rate == Decimal("50.00")
        assert queried.labor_cost == Decimal("400.00")
        assert queried.entry_type == "run"

    def test_fk_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = _make_operation(session, test_actor_id, work_order_id=wo.id)
        labor = LaborEntryModel(
            work_order_id=uuid4(),  # nonexistent
            operation_id=op.id,
            employee_id=uuid4(),
            work_date=date(2024, 4, 10),
            hours=Decimal("1.0"),
            labor_rate=Decimal("50.00"),
            labor_cost=Decimal("50.00"),
            created_by_id=test_actor_id,
        )
        session.add(labor)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_to_operation(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        labor = LaborEntryModel(
            work_order_id=wo.id,
            operation_id=uuid4(),  # nonexistent
            employee_id=uuid4(),
            work_date=date(2024, 4, 10),
            hours=Decimal("1.0"),
            labor_rate=Decimal("50.00"),
            labor_cost=Decimal("50.00"),
            created_by_id=test_actor_id,
        )
        session.add(labor)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order_and_operation(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = _make_operation(session, test_actor_id, work_order_id=wo.id)
        labor = LaborEntryModel(
            work_order_id=wo.id,
            operation_id=op.id,
            employee_id=uuid4(),
            work_date=date(2024, 4, 15),
            hours=Decimal("4.0"),
            labor_rate=Decimal("55.00"),
            labor_cost=Decimal("220.00"),
            created_by_id=test_actor_id,
        )
        session.add(labor)
        session.flush()

        assert labor.work_order is not None
        assert labor.work_order.id == wo.id
        assert labor.operation is not None
        assert labor.operation.id == op.id
        session.refresh(wo)
        assert any(l.id == labor.id for l in wo.labor_entries)

    def test_entry_types(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = _make_operation(session, test_actor_id, work_order_id=wo.id)
        for entry_type in ("setup", "run", "rework"):
            labor = LaborEntryModel(
                work_order_id=wo.id,
                operation_id=op.id,
                employee_id=uuid4(),
                work_date=date(2024, 4, 20),
                hours=Decimal("2.0"),
                labor_rate=Decimal("40.00"),
                labor_cost=Decimal("80.00"),
                entry_type=entry_type,
                created_by_id=test_actor_id,
            )
            session.add(labor)
        session.flush()

    def test_default_entry_type(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        op = _make_operation(session, test_actor_id, work_order_id=wo.id)
        labor = LaborEntryModel(
            work_order_id=wo.id,
            operation_id=op.id,
            employee_id=uuid4(),
            work_date=date(2024, 5, 1),
            hours=Decimal("1.0"),
            labor_rate=Decimal("50.00"),
            labor_cost=Decimal("50.00"),
            created_by_id=test_actor_id,
        )
        session.add(labor)
        session.flush()

        queried = session.get(LaborEntryModel, labor.id)
        assert queried.entry_type == "run"


# ===================================================================
# OverheadApplicationModel
# ===================================================================

class TestOverheadApplicationModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        oh = OverheadApplicationModel(
            work_order_id=wo.id,
            application_date=date(2024, 4, 30),
            overhead_type="variable",
            basis="labor_hours",
            rate=Decimal("25.00"),
            quantity=Decimal("160"),
            amount=Decimal("4000.00"),
            created_by_id=test_actor_id,
        )
        session.add(oh)
        session.flush()

        queried = session.get(OverheadApplicationModel, oh.id)
        assert queried is not None
        assert queried.work_order_id == wo.id
        assert queried.application_date == date(2024, 4, 30)
        assert queried.overhead_type == "variable"
        assert queried.basis == "labor_hours"
        assert queried.rate == Decimal("25.00")
        assert queried.quantity == Decimal("160")
        assert queried.amount == Decimal("4000.00")

    def test_fk_to_work_order(self, session, test_actor_id):
        oh = OverheadApplicationModel(
            work_order_id=uuid4(),
            application_date=date(2024, 5, 31),
            overhead_type="fixed",
            basis="units",
            rate=Decimal("10.00"),
            quantity=Decimal("100"),
            amount=Decimal("1000.00"),
            created_by_id=test_actor_id,
        )
        session.add(oh)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        oh = OverheadApplicationModel(
            work_order_id=wo.id,
            application_date=date(2024, 6, 30),
            overhead_type="setup",
            basis="machine_hours",
            rate=Decimal("15.00"),
            quantity=Decimal("80"),
            amount=Decimal("1200.00"),
            created_by_id=test_actor_id,
        )
        session.add(oh)
        session.flush()

        assert oh.work_order is not None
        assert oh.work_order.id == wo.id
        session.refresh(wo)
        assert any(a.id == oh.id for a in wo.overhead_applications)

    def test_multiple_overhead_types(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        for oh_type, basis in [
            ("fixed", "units"),
            ("variable", "labor_hours"),
            ("setup", "machine_hours"),
        ]:
            oh = OverheadApplicationModel(
                work_order_id=wo.id,
                application_date=date(2024, 7, 31),
                overhead_type=oh_type,
                basis=basis,
                rate=Decimal("20.00"),
                quantity=Decimal("50"),
                amount=Decimal("1000.00"),
                created_by_id=test_actor_id,
            )
            session.add(oh)
        session.flush()


# ===================================================================
# ByproductRecordModel
# ===================================================================

class TestByproductRecordModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        item_id = uuid4()
        bp = ByproductRecordModel(
            job_id=wo.id,
            item_id=item_id,
            description="Metal shavings",
            value=Decimal("150.00"),
            quantity=Decimal("25.5"),
            created_by_id=test_actor_id,
        )
        session.add(bp)
        session.flush()

        queried = session.get(ByproductRecordModel, bp.id)
        assert queried is not None
        assert queried.job_id == wo.id
        assert queried.item_id == item_id
        assert queried.description == "Metal shavings"
        assert queried.value == Decimal("150.00")
        assert queried.quantity == Decimal("25.5")

    def test_fk_to_work_order(self, session, test_actor_id):
        bp = ByproductRecordModel(
            job_id=uuid4(),
            item_id=uuid4(),
            description="Orphan byproduct",
            value=Decimal("10.00"),
            created_by_id=test_actor_id,
        )
        session.add(bp)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        bp = ByproductRecordModel(
            job_id=wo.id,
            item_id=uuid4(),
            description="Sawdust",
            value=Decimal("5.00"),
            created_by_id=test_actor_id,
        )
        session.add(bp)
        session.flush()

        assert bp.work_order is not None
        assert bp.work_order.id == wo.id

    def test_default_quantity(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        bp = ByproductRecordModel(
            job_id=wo.id,
            item_id=uuid4(),
            description="Default qty check",
            value=Decimal("10.00"),
            created_by_id=test_actor_id,
        )
        session.add(bp)
        session.flush()

        queried = session.get(ByproductRecordModel, bp.id)
        assert queried.quantity == Decimal("1")


# ===================================================================
# ProductionCostSummaryModel
# ===================================================================

class TestProductionCostSummaryModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        pcs = ProductionCostSummaryModel(
            job_id=wo.id,
            material_cost=Decimal("5000.00"),
            labor_cost=Decimal("3000.00"),
            overhead_cost=Decimal("2000.00"),
            total_cost=Decimal("10000.00"),
            units_produced=Decimal("100"),
            created_by_id=test_actor_id,
        )
        session.add(pcs)
        session.flush()

        queried = session.get(ProductionCostSummaryModel, pcs.id)
        assert queried is not None
        assert queried.job_id == wo.id
        assert queried.material_cost == Decimal("5000.00")
        assert queried.labor_cost == Decimal("3000.00")
        assert queried.overhead_cost == Decimal("2000.00")
        assert queried.total_cost == Decimal("10000.00")
        assert queried.units_produced == Decimal("100")

    def test_fk_to_work_order(self, session, test_actor_id):
        pcs = ProductionCostSummaryModel(
            job_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(pcs)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        pcs = ProductionCostSummaryModel(
            job_id=wo.id,
            material_cost=Decimal("1000.00"),
            labor_cost=Decimal("500.00"),
            overhead_cost=Decimal("300.00"),
            total_cost=Decimal("1800.00"),
            units_produced=Decimal("50"),
            created_by_id=test_actor_id,
        )
        session.add(pcs)
        session.flush()

        assert pcs.work_order is not None
        assert pcs.work_order.id == wo.id

    def test_defaults(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        pcs = ProductionCostSummaryModel(
            job_id=wo.id,
            created_by_id=test_actor_id,
        )
        session.add(pcs)
        session.flush()

        queried = session.get(ProductionCostSummaryModel, pcs.id)
        assert queried.material_cost == Decimal("0")
        assert queried.labor_cost == Decimal("0")
        assert queried.overhead_cost == Decimal("0")
        assert queried.total_cost == Decimal("0")
        assert queried.units_produced == Decimal("0")


# ===================================================================
# UnitCostBreakdownModel
# ===================================================================

class TestUnitCostBreakdownModelORM:

    def test_create_and_query(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        ucb = UnitCostBreakdownModel(
            job_id=wo.id,
            units_produced=Decimal("100"),
            material_per_unit=Decimal("50.00"),
            labor_per_unit=Decimal("30.00"),
            overhead_per_unit=Decimal("20.00"),
            total_per_unit=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(ucb)
        session.flush()

        queried = session.get(UnitCostBreakdownModel, ucb.id)
        assert queried is not None
        assert queried.job_id == wo.id
        assert queried.units_produced == Decimal("100")
        assert queried.material_per_unit == Decimal("50.00")
        assert queried.labor_per_unit == Decimal("30.00")
        assert queried.overhead_per_unit == Decimal("20.00")
        assert queried.total_per_unit == Decimal("100.00")

    def test_fk_to_work_order(self, session, test_actor_id):
        ucb = UnitCostBreakdownModel(
            job_id=uuid4(),
            units_produced=Decimal("10"),
            material_per_unit=Decimal("5.00"),
            labor_per_unit=Decimal("3.00"),
            overhead_per_unit=Decimal("2.00"),
            total_per_unit=Decimal("10.00"),
            created_by_id=test_actor_id,
        )
        session.add(ucb)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_work_order(self, session, test_actor_id):
        wo = _make_work_order(session, test_actor_id)
        ucb = UnitCostBreakdownModel(
            job_id=wo.id,
            units_produced=Decimal("200"),
            material_per_unit=Decimal("25.00"),
            labor_per_unit=Decimal("15.00"),
            overhead_per_unit=Decimal("10.00"),
            total_per_unit=Decimal("50.00"),
            created_by_id=test_actor_id,
        )
        session.add(ucb)
        session.flush()

        assert ucb.work_order is not None
        assert ucb.work_order.id == wo.id
