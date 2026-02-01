"""ORM round-trip tests for the Procurement module.

Covers:
- PurchaseRequisitionModel
- RequisitionLineModel
- ReceivingReportModel

Tests verify persistence round-trips (create, flush, query), parent-child
relationships, FK constraints, and unique constraints.

Note: Sindri references (sindri_po_reference, sindri_po_line_reference) are
String(100) with NO FK constraints, so no FK constraint tests for those.
PurchaseRequisitionModel.requester_id has a FK to parties.id, tested here.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.procurement.orm import (
    PurchaseRequisitionModel,
    RequisitionLineModel,
    ReceivingReportModel,
)
from tests.modules.conftest import TEST_VENDOR_ID


# ---------------------------------------------------------------------------
# PurchaseRequisitionModel
# ---------------------------------------------------------------------------


class TestPurchaseRequisitionModelORM:
    """Round-trip persistence tests for PurchaseRequisitionModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        req = PurchaseRequisitionModel(
            requisition_number="REQ-2024-001",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 3, 1),
            description="Office supplies requisition",
            total_amount=Decimal("2500.00"),
            currency="USD",
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        queried = session.get(PurchaseRequisitionModel, req.id)
        assert queried is not None
        assert queried.requisition_number == "REQ-2024-001"
        assert queried.requester_id == TEST_VENDOR_ID
        assert queried.request_date == date(2024, 3, 1)
        assert queried.description == "Office supplies requisition"
        assert queried.total_amount == Decimal("2500.00")
        assert queried.currency == "USD"
        assert queried.status == "draft"

    def test_create_with_defaults(self, session, test_actor_id, test_vendor_party):
        req = PurchaseRequisitionModel(
            requisition_number="REQ-2024-DEF",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 4, 1),
            description="Default test",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        queried = session.get(PurchaseRequisitionModel, req.id)
        assert queried.total_amount == Decimal("0")
        assert queried.currency == "USD"
        assert queried.status == "draft"
        assert queried.department_id is None
        assert queried.approved_by is None
        assert queried.approved_date is None
        assert queried.sindri_po_reference is None

    def test_create_with_approval_fields(self, session, test_actor_id, test_vendor_party):
        approver_id = uuid4()
        req = PurchaseRequisitionModel(
            requisition_number="REQ-2024-APPR",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 5, 1),
            description="Approved requisition",
            total_amount=Decimal("15000.00"),
            currency="USD",
            status="approved",
            department_id=uuid4(),
            approved_by=approver_id,
            approved_date=date(2024, 5, 5),
            sindri_po_reference="PO-SINDRI-001",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        queried = session.get(PurchaseRequisitionModel, req.id)
        assert queried.status == "approved"
        assert queried.approved_by == approver_id
        assert queried.approved_date == date(2024, 5, 5)
        assert queried.sindri_po_reference == "PO-SINDRI-001"

    def test_unique_constraint_requisition_number(self, session, test_actor_id, test_vendor_party):
        """requisition_number must be unique."""
        req1 = PurchaseRequisitionModel(
            requisition_number="REQ-DUP-001",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 1, 1),
            description="First requisition",
            created_by_id=test_actor_id,
        )
        session.add(req1)
        session.flush()

        req2 = PurchaseRequisitionModel(
            requisition_number="REQ-DUP-001",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 2, 1),
            description="Duplicate number",
            created_by_id=test_actor_id,
        )
        session.add(req2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_constraint_requester_id(self, session, test_actor_id):
        """requester_id must reference an existing Party."""
        req = PurchaseRequisitionModel(
            requisition_number="REQ-BAD-FK",
            requester_id=uuid4(),
            request_date=date(2024, 1, 1),
            description="Bad FK test",
            created_by_id=test_actor_id,
        )
        session.add(req)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_lines_relationship(self, session, test_actor_id, test_vendor_party):
        """Verify parent requisition loads child lines via relationship."""
        req = PurchaseRequisitionModel(
            requisition_number="REQ-LINES-001",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 6, 1),
            description="Requisition with lines",
            total_amount=Decimal("3500.00"),
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        line1 = RequisitionLineModel(
            requisition_id=req.id,
            line_number=1,
            description="Printer paper",
            quantity=Decimal("50"),
            unit_of_measure="BOX",
            estimated_unit_cost=Decimal("25.00"),
            estimated_total=Decimal("1250.00"),
            created_by_id=test_actor_id,
        )
        line2 = RequisitionLineModel(
            requisition_id=req.id,
            line_number=2,
            description="Toner cartridges",
            quantity=Decimal("10"),
            unit_of_measure="EA",
            estimated_unit_cost=Decimal("225.00"),
            estimated_total=Decimal("2250.00"),
            created_by_id=test_actor_id,
        )
        session.add_all([line1, line2])
        session.flush()

        session.expire(req)
        loaded = session.get(PurchaseRequisitionModel, req.id)
        assert len(loaded.lines) == 2
        line_numbers = {line.line_number for line in loaded.lines}
        assert line_numbers == {1, 2}


# ---------------------------------------------------------------------------
# RequisitionLineModel
# ---------------------------------------------------------------------------


class TestRequisitionLineModelORM:
    """Round-trip persistence tests for RequisitionLineModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        req = PurchaseRequisitionModel(
            requisition_number="REQ-LINE-TEST",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 7, 1),
            description="Parent for line test",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        item_id = uuid4()
        project_id = uuid4()
        line = RequisitionLineModel(
            requisition_id=req.id,
            line_number=1,
            item_id=item_id,
            description="Heavy-duty stapler",
            quantity=Decimal("5"),
            unit_of_measure="EA",
            estimated_unit_cost=Decimal("45.99"),
            estimated_total=Decimal("229.95"),
            required_date=date(2024, 7, 15),
            gl_account_code="5100-000",
            project_id=project_id,
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(RequisitionLineModel, line.id)
        assert queried is not None
        assert queried.requisition_id == req.id
        assert queried.line_number == 1
        assert queried.item_id == item_id
        assert queried.description == "Heavy-duty stapler"
        assert queried.quantity == Decimal("5")
        assert queried.unit_of_measure == "EA"
        assert queried.estimated_unit_cost == Decimal("45.99")
        assert queried.estimated_total == Decimal("229.95")
        assert queried.required_date == date(2024, 7, 15)
        assert queried.gl_account_code == "5100-000"
        assert queried.project_id == project_id

    def test_create_with_defaults(self, session, test_actor_id, test_vendor_party):
        req = PurchaseRequisitionModel(
            requisition_number="REQ-LINE-DEF",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 8, 1),
            description="Parent for default line",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        line = RequisitionLineModel(
            requisition_id=req.id,
            line_number=1,
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(RequisitionLineModel, line.id)
        assert queried.description == ""
        assert queried.quantity == Decimal("0")
        assert queried.unit_of_measure == "EA"
        assert queried.estimated_unit_cost == Decimal("0")
        assert queried.estimated_total == Decimal("0")
        assert queried.required_date is None
        assert queried.gl_account_code is None
        assert queried.item_id is None
        assert queried.project_id is None

    def test_fk_constraint_requisition_id(self, session, test_actor_id):
        """requisition_id must reference an existing PurchaseRequisitionModel."""
        line = RequisitionLineModel(
            requisition_id=uuid4(),
            line_number=1,
            description="Orphan line",
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_constraint_requisition_line_number(self, session, test_actor_id, test_vendor_party):
        """(requisition_id, line_number) must be unique."""
        req = PurchaseRequisitionModel(
            requisition_number="REQ-UQ-LINE",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 9, 1),
            description="Unique line test",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        line1 = RequisitionLineModel(
            requisition_id=req.id,
            line_number=1,
            description="Line one",
            created_by_id=test_actor_id,
        )
        session.add(line1)
        session.flush()

        line2 = RequisitionLineModel(
            requisition_id=req.id,
            line_number=1,
            description="Duplicate line number",
            created_by_id=test_actor_id,
        )
        session.add(line2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_requisition_relationship(self, session, test_actor_id, test_vendor_party):
        """Line should navigate back to parent requisition via relationship."""
        req = PurchaseRequisitionModel(
            requisition_number="REQ-NAV-BACK",
            requester_id=TEST_VENDOR_ID,
            request_date=date(2024, 10, 1),
            description="Parent for nav test",
            created_by_id=test_actor_id,
        )
        session.add(req)
        session.flush()

        line = RequisitionLineModel(
            requisition_id=req.id,
            line_number=1,
            description="Child line",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(RequisitionLineModel, line.id)
        assert queried.requisition is not None
        assert queried.requisition.requisition_number == "REQ-NAV-BACK"


# ---------------------------------------------------------------------------
# ReceivingReportModel
# ---------------------------------------------------------------------------


class TestReceivingReportModelORM:
    """Round-trip persistence tests for ReceivingReportModel."""

    def test_create_and_query(self, session, test_actor_id):
        receiver_id = uuid4()
        location_id = uuid4()
        report = ReceivingReportModel(
            receipt_number="RR-2024-001",
            sindri_po_reference="PO-SINDRI-100",
            sindri_po_line_reference="PO-SINDRI-100-L1",
            receipt_date=date(2024, 4, 15),
            quantity_received=Decimal("100"),
            quantity_accepted=Decimal("98"),
            quantity_rejected=Decimal("2"),
            status="inspected",
            receiver_id=receiver_id,
            location_id=location_id,
            lot_number="LOT-A001",
            description="Steel bolts shipment",
            currency="USD",
            unit_cost=Decimal("3.50"),
            total_cost=Decimal("343.00"),
            created_by_id=test_actor_id,
        )
        session.add(report)
        session.flush()

        queried = session.get(ReceivingReportModel, report.id)
        assert queried is not None
        assert queried.receipt_number == "RR-2024-001"
        assert queried.sindri_po_reference == "PO-SINDRI-100"
        assert queried.sindri_po_line_reference == "PO-SINDRI-100-L1"
        assert queried.receipt_date == date(2024, 4, 15)
        assert queried.quantity_received == Decimal("100")
        assert queried.quantity_accepted == Decimal("98")
        assert queried.quantity_rejected == Decimal("2")
        assert queried.status == "inspected"
        assert queried.receiver_id == receiver_id
        assert queried.location_id == location_id
        assert queried.lot_number == "LOT-A001"
        assert queried.description == "Steel bolts shipment"
        assert queried.currency == "USD"
        assert queried.unit_cost == Decimal("3.50")
        assert queried.total_cost == Decimal("343.00")

    def test_create_with_defaults(self, session, test_actor_id):
        report = ReceivingReportModel(
            receipt_number="RR-2024-DEF",
            receipt_date=date(2024, 5, 1),
            created_by_id=test_actor_id,
        )
        session.add(report)
        session.flush()

        queried = session.get(ReceivingReportModel, report.id)
        assert queried.sindri_po_reference is None
        assert queried.sindri_po_line_reference is None
        assert queried.quantity_received == Decimal("0")
        assert queried.quantity_accepted == Decimal("0")
        assert queried.quantity_rejected == Decimal("0")
        assert queried.status == "pending"
        assert queried.receiver_id is None
        assert queried.location_id is None
        assert queried.lot_number is None
        assert queried.description is None
        assert queried.currency == "USD"
        assert queried.unit_cost == Decimal("0")
        assert queried.total_cost == Decimal("0")

    def test_unique_constraint_receipt_number(self, session, test_actor_id):
        """receipt_number must be unique."""
        report1 = ReceivingReportModel(
            receipt_number="RR-DUP-001",
            receipt_date=date(2024, 6, 1),
            created_by_id=test_actor_id,
        )
        session.add(report1)
        session.flush()

        report2 = ReceivingReportModel(
            receipt_number="RR-DUP-001",
            receipt_date=date(2024, 6, 2),
            created_by_id=test_actor_id,
        )
        session.add(report2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_no_fk_on_sindri_references(self, session, test_actor_id):
        """Sindri references are String fields with no FK -- arbitrary values allowed."""
        report = ReceivingReportModel(
            receipt_number="RR-NO-FK-001",
            sindri_po_reference="NONEXISTENT-PO-99999",
            sindri_po_line_reference="NONEXISTENT-LINE-99999",
            receipt_date=date(2024, 7, 1),
            created_by_id=test_actor_id,
        )
        session.add(report)
        session.flush()

        queried = session.get(ReceivingReportModel, report.id)
        assert queried.sindri_po_reference == "NONEXISTENT-PO-99999"
        assert queried.sindri_po_line_reference == "NONEXISTENT-LINE-99999"
