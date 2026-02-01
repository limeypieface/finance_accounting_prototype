"""
ORM round-trip tests for AR (Accounts Receivable) module.

Verifies: persist -> query -> field equality for all AR ORM models.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.ar.orm import (
    ARAutoApplyRuleModel,
    ARCreditDecisionModel,
    ARCreditMemoModel,
    ARDunningHistoryModel,
    ARInvoiceLineModel,
    ARInvoiceModel,
    ARReceiptAllocationModel,
    ARReceiptModel,
    CustomerProfileModel,
)
from tests.modules.conftest import TEST_CUSTOMER_ID

# ---------------------------------------------------------------------------
# 1. CustomerProfileModel
# ---------------------------------------------------------------------------


class TestCustomerProfileModelORM:
    """Round-trip tests for CustomerProfileModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        obj = CustomerProfileModel(
            customer_id=TEST_CUSTOMER_ID,
            code="CUST-001",
            name="Globex Corp",
            credit_limit=Decimal("50000.00"),
            payment_terms_days=60,
            default_gl_account_code="1100-000",
            tax_exempt=True,
            tax_id="98-7654321",
            is_active=True,
            dunning_level=0,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(CustomerProfileModel, obj.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.code == "CUST-001"
        assert queried.name == "Globex Corp"
        assert queried.credit_limit == Decimal("50000.00")
        assert queried.payment_terms_days == 60
        assert queried.default_gl_account_code == "1100-000"
        assert queried.tax_exempt is True
        assert queried.tax_id == "98-7654321"
        assert queried.is_active is True
        assert queried.dunning_level == 0

    def test_defaults(self, session, test_actor_id, test_customer_party):
        obj = CustomerProfileModel(
            customer_id=TEST_CUSTOMER_ID,
            code="CUST-DEF",
            name="Default Customer",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(CustomerProfileModel, obj.id)
        assert queried.payment_terms_days == 30
        assert queried.tax_exempt is False
        assert queried.is_active is True
        assert queried.dunning_level == 0

    def test_fk_customer_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = CustomerProfileModel(
            customer_id=uuid4(),
            code="CUST-ORPHAN",
            name="Orphan Customer",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_code_constraint(self, session, test_actor_id, test_customer_party):
        """Duplicate code raises IntegrityError."""
        obj1 = CustomerProfileModel(
            customer_id=TEST_CUSTOMER_ID,
            code="CUST-DUP",
            name="Customer A",
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = CustomerProfileModel(
            customer_id=TEST_CUSTOMER_ID,
            code="CUST-DUP",
            name="Customer B",
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# 2. ARInvoiceModel
# ---------------------------------------------------------------------------


class TestARInvoiceModelORM:
    """Round-trip tests for ARInvoiceModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        obj = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("2000.00"),
            tax_amount=Decimal("160.00"),
            total_amount=Decimal("2160.00"),
            balance_due=Decimal("2160.00"),
            status="posted",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARInvoiceModel, obj.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.invoice_number == "INV-AR-001"
        assert queried.invoice_date == date(2025, 3, 1)
        assert queried.due_date == date(2025, 4, 1)
        assert queried.currency == "USD"
        assert queried.subtotal == Decimal("2000.00")
        assert queried.tax_amount == Decimal("160.00")
        assert queried.total_amount == Decimal("2160.00")
        assert queried.balance_due == Decimal("2160.00")
        assert queried.status == "posted"

    def test_fk_customer_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = ARInvoiceModel(
            customer_id=uuid4(),
            invoice_number="INV-AR-ORPHAN",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            balance_due=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_invoice_number_constraint(
        self, session, test_actor_id, test_customer_party
    ):
        """Duplicate invoice_number raises IntegrityError."""
        obj1 = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-DUP",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            balance_due=Decimal("500.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-DUP",
            invoice_date=date(2025, 4, 1),
            due_date=date(2025, 5, 1),
            currency="USD",
            subtotal=Decimal("600.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("600.00"),
            balance_due=Decimal("600.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_lines_relationship(self, session, test_actor_id, test_customer_party):
        """Parent invoice with child lines loads the relationship."""
        invoice = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-REL",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("400.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("400.00"),
            balance_due=Decimal("400.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = ARInvoiceLineModel(
            invoice_id=invoice.id,
            line_number=1,
            description="Professional services",
            quantity=Decimal("8"),
            unit_price=Decimal("50.00"),
            amount=Decimal("400.00"),
            gl_account_code="4000-000",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire_all()
        reloaded = session.get(ARInvoiceModel, invoice.id)
        assert len(reloaded.lines) == 1
        assert reloaded.lines[0].description == "Professional services"


# ---------------------------------------------------------------------------
# 3. ARInvoiceLineModel
# ---------------------------------------------------------------------------


class TestARInvoiceLineModelORM:
    """Round-trip tests for ARInvoiceLineModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        invoice = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-LINE-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("600.00"),
            tax_amount=Decimal("48.00"),
            total_amount=Decimal("648.00"),
            balance_due=Decimal("648.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = ARInvoiceLineModel(
            invoice_id=invoice.id,
            line_number=1,
            description="Training session",
            quantity=Decimal("3"),
            unit_price=Decimal("200.00"),
            amount=Decimal("600.00"),
            gl_account_code="4000-000",
            tax_code="TX-STD",
            tax_amount=Decimal("48.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(ARInvoiceLineModel, line.id)
        assert queried is not None
        assert queried.invoice_id == invoice.id
        assert queried.line_number == 1
        assert queried.description == "Training session"
        assert queried.quantity == Decimal("3")
        assert queried.unit_price == Decimal("200.00")
        assert queried.amount == Decimal("600.00")
        assert queried.gl_account_code == "4000-000"
        assert queried.tax_code == "TX-STD"
        assert queried.tax_amount == Decimal("48.00")

    def test_fk_invoice_id_constraint(self, session, test_actor_id):
        """FK to ar_invoices raises IntegrityError when invoice does not exist."""
        line = ARInvoiceLineModel(
            invoice_id=uuid4(),
            line_number=1,
            description="Orphan line",
            quantity=Decimal("1"),
            unit_price=Decimal("10.00"),
            amount=Decimal("10.00"),
            gl_account_code="4000-000",
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_tax_amount_default(self, session, test_actor_id, test_customer_party):
        invoice = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-LINE-DEF",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            balance_due=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        line = ARInvoiceLineModel(
            invoice_id=invoice.id,
            line_number=1,
            description="No tax line",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            amount=Decimal("100.00"),
            gl_account_code="4000-000",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(ARInvoiceLineModel, line.id)
        assert queried.tax_amount == Decimal("0")


# ---------------------------------------------------------------------------
# 4. ARReceiptModel
# ---------------------------------------------------------------------------


class TestARReceiptModelORM:
    """Round-trip tests for ARReceiptModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        obj = ARReceiptModel(
            customer_id=TEST_CUSTOMER_ID,
            receipt_date=date(2025, 4, 10),
            amount=Decimal("2160.00"),
            currency="USD",
            payment_method="wire",
            reference="REC-001",
            status="allocated",
            bank_account_id=uuid4(),
            unallocated_amount=Decimal("0.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARReceiptModel, obj.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.receipt_date == date(2025, 4, 10)
        assert queried.amount == Decimal("2160.00")
        assert queried.currency == "USD"
        assert queried.payment_method == "wire"
        assert queried.reference == "REC-001"
        assert queried.status == "allocated"
        assert queried.unallocated_amount == Decimal("0.00")

    def test_fk_customer_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = ARReceiptModel(
            customer_id=uuid4(),
            receipt_date=date(2025, 4, 10),
            amount=Decimal("500.00"),
            currency="USD",
            payment_method="check",
            reference="REC-ORPHAN",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_customer_party):
        obj = ARReceiptModel(
            customer_id=TEST_CUSTOMER_ID,
            receipt_date=date(2025, 4, 10),
            amount=Decimal("1000.00"),
            currency="USD",
            payment_method="ach",
            reference="REC-DEF",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARReceiptModel, obj.id)
        assert queried.status == "unallocated"
        assert queried.unallocated_amount == Decimal("0")

    def test_allocations_relationship(
        self, session, test_actor_id, test_customer_party
    ):
        """Parent receipt with child allocation loads the relationship."""
        receipt = ARReceiptModel(
            customer_id=TEST_CUSTOMER_ID,
            receipt_date=date(2025, 4, 10),
            amount=Decimal("500.00"),
            currency="USD",
            payment_method="wire",
            reference="REC-REL",
            created_by_id=test_actor_id,
        )
        session.add(receipt)
        session.flush()

        invoice = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-ALLOC-001",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("500.00"),
            balance_due=Decimal("500.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        alloc = ARReceiptAllocationModel(
            receipt_id=receipt.id,
            invoice_id=invoice.id,
            amount=Decimal("500.00"),
            discount_taken=Decimal("0.00"),
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        session.flush()

        session.expire_all()
        reloaded = session.get(ARReceiptModel, receipt.id)
        assert len(reloaded.allocations) == 1
        assert reloaded.allocations[0].amount == Decimal("500.00")


# ---------------------------------------------------------------------------
# 5. ARReceiptAllocationModel
# ---------------------------------------------------------------------------


class TestARReceiptAllocationModelORM:
    """Round-trip tests for ARReceiptAllocationModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        receipt = ARReceiptModel(
            customer_id=TEST_CUSTOMER_ID,
            receipt_date=date(2025, 4, 10),
            amount=Decimal("800.00"),
            currency="USD",
            payment_method="wire",
            reference="REC-ALLOC-001",
            created_by_id=test_actor_id,
        )
        session.add(receipt)
        session.flush()

        invoice = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-ALLOC-002",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("800.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("800.00"),
            balance_due=Decimal("800.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        alloc = ARReceiptAllocationModel(
            receipt_id=receipt.id,
            invoice_id=invoice.id,
            amount=Decimal("800.00"),
            discount_taken=Decimal("16.00"),
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        session.flush()

        queried = session.get(ARReceiptAllocationModel, alloc.id)
        assert queried is not None
        assert queried.receipt_id == receipt.id
        assert queried.invoice_id == invoice.id
        assert queried.amount == Decimal("800.00")
        assert queried.discount_taken == Decimal("16.00")

    def test_fk_receipt_id_constraint(self, session, test_actor_id, test_customer_party):
        """FK to ar_receipts raises IntegrityError when receipt does not exist."""
        invoice = ARInvoiceModel(
            customer_id=TEST_CUSTOMER_ID,
            invoice_number="INV-AR-ALLOC-FK",
            invoice_date=date(2025, 3, 1),
            due_date=date(2025, 4, 1),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            balance_due=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(invoice)
        session.flush()

        alloc = ARReceiptAllocationModel(
            receipt_id=uuid4(),
            invoice_id=invoice.id,
            amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_invoice_id_constraint(self, session, test_actor_id, test_customer_party):
        """FK to ar_invoices raises IntegrityError when invoice does not exist."""
        receipt = ARReceiptModel(
            customer_id=TEST_CUSTOMER_ID,
            receipt_date=date(2025, 4, 10),
            amount=Decimal("100.00"),
            currency="USD",
            payment_method="check",
            reference="REC-ALLOC-FK",
            created_by_id=test_actor_id,
        )
        session.add(receipt)
        session.flush()

        alloc = ARReceiptAllocationModel(
            receipt_id=receipt.id,
            invoice_id=uuid4(),
            amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# 6. ARCreditMemoModel
# ---------------------------------------------------------------------------


class TestARCreditMemoModelORM:
    """Round-trip tests for ARCreditMemoModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        obj = ARCreditMemoModel(
            customer_id=TEST_CUSTOMER_ID,
            credit_memo_number="CM-001",
            issue_date=date(2025, 4, 5),
            amount=Decimal("200.00"),
            currency="USD",
            reason="Returned goods",
            status="posted",
            original_invoice_id=uuid4(),
            applied_to_invoice_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARCreditMemoModel, obj.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.credit_memo_number == "CM-001"
        assert queried.issue_date == date(2025, 4, 5)
        assert queried.amount == Decimal("200.00")
        assert queried.currency == "USD"
        assert queried.reason == "Returned goods"
        assert queried.status == "posted"

    def test_fk_customer_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = ARCreditMemoModel(
            customer_id=uuid4(),
            credit_memo_number="CM-ORPHAN",
            issue_date=date(2025, 4, 5),
            amount=Decimal("50.00"),
            currency="USD",
            reason="Test",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_credit_memo_number_constraint(
        self, session, test_actor_id, test_customer_party
    ):
        """Duplicate credit_memo_number raises IntegrityError."""
        obj1 = ARCreditMemoModel(
            customer_id=TEST_CUSTOMER_ID,
            credit_memo_number="CM-DUP",
            issue_date=date(2025, 4, 5),
            amount=Decimal("100.00"),
            currency="USD",
            reason="Reason A",
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = ARCreditMemoModel(
            customer_id=TEST_CUSTOMER_ID,
            credit_memo_number="CM-DUP",
            issue_date=date(2025, 4, 6),
            amount=Decimal("200.00"),
            currency="USD",
            reason="Reason B",
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_customer_party):
        obj = ARCreditMemoModel(
            customer_id=TEST_CUSTOMER_ID,
            credit_memo_number="CM-DEF",
            issue_date=date(2025, 4, 5),
            amount=Decimal("75.00"),
            currency="USD",
            reason="Default test",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARCreditMemoModel, obj.id)
        assert queried.status == "draft"


# ---------------------------------------------------------------------------
# 7. ARDunningHistoryModel
# ---------------------------------------------------------------------------


class TestARDunningHistoryModelORM:
    """Round-trip tests for ARDunningHistoryModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        obj = ARDunningHistoryModel(
            customer_id=TEST_CUSTOMER_ID,
            level="warning",
            sent_date=date(2025, 5, 1),
            as_of_date=date(2025, 4, 30),
            total_overdue=Decimal("3500.00"),
            invoice_count=3,
            currency="USD",
            notes="First reminder sent",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARDunningHistoryModel, obj.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.level == "warning"
        assert queried.sent_date == date(2025, 5, 1)
        assert queried.as_of_date == date(2025, 4, 30)
        assert queried.total_overdue == Decimal("3500.00")
        assert queried.invoice_count == 3
        assert queried.currency == "USD"
        assert queried.notes == "First reminder sent"

    def test_fk_customer_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = ARDunningHistoryModel(
            customer_id=uuid4(),
            level="warning",
            sent_date=date(2025, 5, 1),
            as_of_date=date(2025, 4, 30),
            total_overdue=Decimal("100.00"),
            invoice_count=1,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_customer_party):
        obj = ARDunningHistoryModel(
            customer_id=TEST_CUSTOMER_ID,
            level="notice",
            sent_date=date(2025, 5, 1),
            as_of_date=date(2025, 4, 30),
            total_overdue=Decimal("100.00"),
            invoice_count=1,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARDunningHistoryModel, obj.id)
        assert queried.currency == "USD"
        assert queried.notes is None


# ---------------------------------------------------------------------------
# 8. ARCreditDecisionModel
# ---------------------------------------------------------------------------


class TestARCreditDecisionModelORM:
    """Round-trip tests for ARCreditDecisionModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        decided_by = uuid4()
        obj = ARCreditDecisionModel(
            customer_id=TEST_CUSTOMER_ID,
            decision_date=date(2025, 3, 15),
            previous_limit=Decimal("25000.00"),
            new_limit=Decimal("50000.00"),
            order_amount=Decimal("30000.00"),
            approved=True,
            reason="Strong payment history",
            decided_by=decided_by,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARCreditDecisionModel, obj.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.decision_date == date(2025, 3, 15)
        assert queried.previous_limit == Decimal("25000.00")
        assert queried.new_limit == Decimal("50000.00")
        assert queried.order_amount == Decimal("30000.00")
        assert queried.approved is True
        assert queried.reason == "Strong payment history"
        assert queried.decided_by == decided_by

    def test_fk_customer_id_constraint(self, session, test_actor_id):
        """FK to parties raises IntegrityError when party does not exist."""
        obj = ARCreditDecisionModel(
            customer_id=uuid4(),
            decision_date=date(2025, 3, 15),
            approved=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_customer_party):
        obj = ARCreditDecisionModel(
            customer_id=TEST_CUSTOMER_ID,
            decision_date=date(2025, 3, 15),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARCreditDecisionModel, obj.id)
        assert queried.approved is True


# ---------------------------------------------------------------------------
# 9. ARAutoApplyRuleModel
# ---------------------------------------------------------------------------


class TestARAutoApplyRuleModelORM:
    """Round-trip tests for ARAutoApplyRuleModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = ARAutoApplyRuleModel(
            name="Exact Amount Match",
            priority=1,
            match_field="amount",
            tolerance=Decimal("0.01"),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARAutoApplyRuleModel, obj.id)
        assert queried is not None
        assert queried.name == "Exact Amount Match"
        assert queried.priority == 1
        assert queried.match_field == "amount"
        assert queried.tolerance == Decimal("0.01")
        assert queried.is_active is True

    def test_unique_name_constraint(self, session, test_actor_id):
        """Duplicate name raises IntegrityError."""
        obj1 = ARAutoApplyRuleModel(
            name="Reference Match",
            priority=2,
            match_field="reference",
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = ARAutoApplyRuleModel(
            name="Reference Match",
            priority=3,
            match_field="invoice_number",
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id):
        obj = ARAutoApplyRuleModel(
            name="Default Rule",
            priority=10,
            match_field="amount",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ARAutoApplyRuleModel, obj.id)
        assert queried.tolerance == Decimal("0")
        assert queried.is_active is True
