"""
ORM round-trip tests for AP (Accounts Payable) module.

Verifies: persist -> query -> field equality for all AP ORM models.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.ap.orm import (
    APInvoiceLineModel,
    APInvoiceModel,
    APPaymentBatchModel,
    APPaymentModel,
    APPaymentRunLineModel,
    APPaymentRunModel,
    APVendorHoldModel,
    VendorProfileModel,
)
from tests.modules.conftest import TEST_VENDOR_ID

# ---------------------------------------------------------------------------
# 1. VendorProfileModel
# ---------------------------------------------------------------------------


class TestVendorProfileModelORM:
    """Round-trip tests for VendorProfileModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        obj = VendorProfileModel(
            vendor_id=TEST_VENDOR_ID,
            code="VEND-001",
            name="Acme Corp",
            tax_id="12-3456789",
            payment_terms_days=45,
            default_payment_method="wire",
            default_gl_account_code="5100-000",
            is_active=True,
            is_1099_eligible=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(VendorProfileModel, obj.id)
        assert queried is not None
        assert queried.vendor_id == TEST_VENDOR_ID
        assert queried.code == "VEND-001"
        assert queried.name == "Acme Corp"
        assert queried.tax_id == "12-3456789"
        assert queried.payment_terms_days == 45
        assert queried.default_payment_method == "wire"
        assert queried.default_gl_account_code == "5100-000"
        assert queried.is_active is True
        assert queried.is_1099_eligible is True

    def test_defaults(self, session, test_actor_id, test_vendor_party):
        obj = VendorProfileModel(
            vendor_id=TEST_VENDOR_ID,
            code="VEND-DEF",
            name="Default Vendor",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(VendorProfileModel, obj.id)
        assert queried.payment_terms_days == 30
        assert queried.default_payment_method == "ach"
        assert queried.is_active is True
        assert queried.is_1099_eligible is False

    def test_fk_vendor_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = VendorProfileModel(
            vendor_id=uuid4(),
            code="VEND-ORPHAN",
            name="Orphan Vendor",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_code_constraint(self, session, test_actor_id, test_vendor_party):
        """Duplicate code raises IntegrityError."""
        obj1 = VendorProfileModel(
            vendor_id=TEST_VENDOR_ID,
            code="VEND-DUP",
            name="Vendor A",
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = VendorProfileModel(
            vendor_id=TEST_VENDOR_ID,
            code="VEND-DUP",
            name="Vendor B",
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# 2. APInvoiceModel
# ---------------------------------------------------------------------------


class TestAPInvoiceModelORM:
    """Round-trip tests for APInvoiceModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        obj = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("80.00"),
            total_amount=Decimal("1080.00"),
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APInvoiceModel, obj.id)
        assert queried is not None
        assert queried.vendor_id == TEST_VENDOR_ID
        assert queried.invoice_number == "INV-AP-001"
        assert queried.invoice_date == date(2025, 3, 1)
        assert queried.due_date == date(2025, 4, 1)
        assert queried.currency == "USD"
        assert queried.subtotal == Decimal("1000.00")
        assert queried.tax_amount == Decimal("80.00")
        assert queried.total_amount == Decimal("1080.00")
        assert queried.status == "draft"

    def test_fk_vendor_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = APInvoiceModel(
            vendor_id=uuid4(),
            invoice_number="INV-AP-ORPHAN",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_invoice_number_constraint(
        self, session, test_actor_id, test_vendor_party
    ):
        """Duplicate invoice_number raises IntegrityError."""
        obj1 = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-DUP",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-DUP",
            invoice_date=date(2025, 4, 1),
            due_date=date(2025, 5, 1),
            currency="USD",
            subtotal=Decimal("600.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("600.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_lines_relationship(self, session, test_actor_id, test_vendor_party):
        """Parent invoice with child lines loads the relationship."""
        invoice = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-REL",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("200.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = APInvoiceLineModel(
            invoice_id=invoice.id,
            line_number=1,
            description="Widget",
            quantity=Decimal("10"),
            unit_price=Decimal("20.00"),
            amount=Decimal("200.00"),
            gl_account_code="5100-000",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire_all()
        reloaded = session.get(APInvoiceModel, invoice.id)
        assert len(reloaded.lines) == 1
        assert reloaded.lines[0].description == "Widget"


# ---------------------------------------------------------------------------
# 3. APInvoiceLineModel
# ---------------------------------------------------------------------------


class TestAPInvoiceLineModelORM:
    """Round-trip tests for APInvoiceLineModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        invoice = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-LINE-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("300.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("300.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = APInvoiceLineModel(
            invoice_id=invoice.id,
            line_number=1,
            description="Consulting services",
            quantity=Decimal("5"),
            unit_price=Decimal("60.00"),
            amount=Decimal("300.00"),
            gl_account_code="5200-000",
            po_line_id=uuid4(),
            receipt_line_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(APInvoiceLineModel, line.id)
        assert queried is not None
        assert queried.invoice_id == invoice.id
        assert queried.line_number == 1
        assert queried.description == "Consulting services"
        assert queried.quantity == Decimal("5")
        assert queried.unit_price == Decimal("60.00")
        assert queried.amount == Decimal("300.00")
        assert queried.gl_account_code == "5200-000"

    def test_fk_invoice_id_constraint(self, session, test_actor_id):
        """FK to ap_invoices raises IntegrityError when invoice does not exist."""
        line = APInvoiceLineModel(
            invoice_id=uuid4(),
            line_number=1,
            description="Orphan line",
            quantity=Decimal("1"),
            unit_price=Decimal("10.00"),
            amount=Decimal("10.00"),
            gl_account_code="5100-000",
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# 4. APPaymentModel
# ---------------------------------------------------------------------------


class TestAPPaymentModelORM:
    """Round-trip tests for APPaymentModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        obj = APPaymentModel(
            vendor_id=TEST_VENDOR_ID,
            payment_date=date(2025, 4, 15),
            payment_method="ach",
            amount=Decimal("1080.00"),
            currency="USD",
            reference="PAY-001",
            status="draft",
            invoice_ids_json='["11111111-1111-1111-1111-111111111111"]',
            discount_taken=Decimal("10.00"),
            bank_account_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APPaymentModel, obj.id)
        assert queried is not None
        assert queried.vendor_id == TEST_VENDOR_ID
        assert queried.payment_date == date(2025, 4, 15)
        assert queried.payment_method == "ach"
        assert queried.amount == Decimal("1080.00")
        assert queried.currency == "USD"
        assert queried.reference == "PAY-001"
        assert queried.status == "draft"
        assert queried.discount_taken == Decimal("10.00")

    def test_fk_vendor_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = APPaymentModel(
            vendor_id=uuid4(),
            payment_date=date(2025, 4, 15),
            payment_method="check",
            amount=Decimal("500.00"),
            currency="USD",
            reference="PAY-ORPHAN",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_vendor_party):
        obj = APPaymentModel(
            vendor_id=TEST_VENDOR_ID,
            payment_date=date(2025, 4, 15),
            payment_method="ach",
            amount=Decimal("100.00"),
            currency="USD",
            reference="PAY-DEF",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APPaymentModel, obj.id)
        assert queried.status == "draft"
        assert queried.discount_taken == Decimal("0")


# ---------------------------------------------------------------------------
# 5. APPaymentBatchModel
# ---------------------------------------------------------------------------


class TestAPPaymentBatchModelORM:
    """Round-trip tests for APPaymentBatchModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = APPaymentBatchModel(
            batch_date=date(2025, 4, 20),
            payment_method="ach",
            payment_ids_json='["22222222-2222-2222-2222-222222222222"]',
            total_amount=Decimal("5000.00"),
            status="approved",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APPaymentBatchModel, obj.id)
        assert queried is not None
        assert queried.batch_date == date(2025, 4, 20)
        assert queried.payment_method == "ach"
        assert queried.total_amount == Decimal("5000.00")
        assert queried.status == "approved"

    def test_defaults(self, session, test_actor_id):
        obj = APPaymentBatchModel(
            batch_date=date(2025, 4, 20),
            payment_method="wire",
            total_amount=Decimal("1000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APPaymentBatchModel, obj.id)
        assert queried.status == "draft"


# ---------------------------------------------------------------------------
# 6. APPaymentRunModel
# ---------------------------------------------------------------------------


class TestAPPaymentRunModelORM:
    """Round-trip tests for APPaymentRunModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = APPaymentRunModel(
            payment_date=date(2025, 4, 25),
            currency="USD",
            status="executed",
            total_amount=Decimal("25000.00"),
            line_count=5,
            created_by=uuid4(),
            executed_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APPaymentRunModel, obj.id)
        assert queried is not None
        assert queried.payment_date == date(2025, 4, 25)
        assert queried.currency == "USD"
        assert queried.status == "executed"
        assert queried.total_amount == Decimal("25000.00")
        assert queried.line_count == 5

    def test_defaults(self, session, test_actor_id):
        obj = APPaymentRunModel(
            payment_date=date(2025, 4, 25),
            currency="EUR",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APPaymentRunModel, obj.id)
        assert queried.status == "draft"
        assert queried.total_amount == Decimal("0")
        assert queried.line_count == 0

    def test_lines_relationship(self, session, test_actor_id, test_vendor_party):
        """Parent run with child line loads the relationship."""
        run = APPaymentRunModel(
            payment_date=date(2025, 4, 25),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        # Need an invoice for the FK
        invoice = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-RUN-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = APPaymentRunLineModel(
            run_id=run.id,
            invoice_id=invoice.id,
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("500.00"),
            discount_amount=Decimal("5.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire_all()
        reloaded = session.get(APPaymentRunModel, run.id)
        assert len(reloaded.lines) == 1
        assert reloaded.lines[0].amount == Decimal("500.00")


# ---------------------------------------------------------------------------
# 7. APPaymentRunLineModel
# ---------------------------------------------------------------------------


class TestAPPaymentRunLineModelORM:
    """Round-trip tests for APPaymentRunLineModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        run = APPaymentRunModel(
            payment_date=date(2025, 4, 25),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        invoice = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-RUNLINE-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("750.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("750.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = APPaymentRunLineModel(
            run_id=run.id,
            invoice_id=invoice.id,
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("750.00"),
            discount_amount=Decimal("15.00"),
            payment_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(APPaymentRunLineModel, line.id)
        assert queried is not None
        assert queried.run_id == run.id
        assert queried.invoice_id == invoice.id
        assert queried.vendor_id == TEST_VENDOR_ID
        assert queried.amount == Decimal("750.00")
        assert queried.discount_amount == Decimal("15.00")

    def test_fk_run_id_constraint(self, session, test_actor_id, test_vendor_party):
        """FK to ap_payment_runs raises IntegrityError when run does not exist."""
        invoice = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-RUNLINE-FK",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = APPaymentRunLineModel(
            run_id=uuid4(),
            invoice_id=invoice.id,
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_invoice_id_constraint(self, session, test_actor_id, test_vendor_party):
        """FK to ap_invoices raises IntegrityError when invoice does not exist."""
        run = APPaymentRunModel(
            payment_date=date(2025, 4, 25),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        line = APPaymentRunLineModel(
            run_id=run.id,
            invoice_id=uuid4(),
            vendor_id=TEST_VENDOR_ID,
            amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_vendor_id_constraint(self, session, test_actor_id, test_vendor_party):
        """FK to parties raises IntegrityError when vendor does not exist."""
        run = APPaymentRunModel(
            payment_date=date(2025, 4, 25),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(run)
        session.flush()

        invoice = APInvoiceModel(
            vendor_id=TEST_VENDOR_ID,
            invoice_number="INV-AP-RUNLINE-VFK",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = APPaymentRunLineModel(
            run_id=run.id,
            invoice_id=invoice.id,
            vendor_id=uuid4(),
            amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# 8. APVendorHoldModel
# ---------------------------------------------------------------------------


class TestAPVendorHoldModelORM:
    """Round-trip tests for APVendorHoldModel."""

    def test_create_and_query(self, session, test_actor_id, test_vendor_party):
        held_by = uuid4()
        obj = APVendorHoldModel(
            vendor_id=TEST_VENDOR_ID,
            reason="Payment dispute",
            hold_date=date(2025, 5, 1),
            held_by=held_by,
            status="active",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APVendorHoldModel, obj.id)
        assert queried is not None
        assert queried.vendor_id == TEST_VENDOR_ID
        assert queried.reason == "Payment dispute"
        assert queried.hold_date == date(2025, 5, 1)
        assert queried.held_by == held_by
        assert queried.status == "active"
        assert queried.released_date is None
        assert queried.released_by is None

    def test_fk_vendor_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = APVendorHoldModel(
            vendor_id=uuid4(),
            reason="Test hold",
            hold_date=date(2025, 5, 1),
            held_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_vendor_party):
        obj = APVendorHoldModel(
            vendor_id=TEST_VENDOR_ID,
            reason="Default test",
            hold_date=date(2025, 5, 1),
            held_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(APVendorHoldModel, obj.id)
        assert queried.status == "active"
