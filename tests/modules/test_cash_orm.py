"""
ORM round-trip tests for Cash Management module.

Verifies: persist -> query -> field equality for all Cash ORM models.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.cash.orm import (
    BankAccountModel,
    BankStatementLineModel,
    BankStatementModel,
    BankTransactionModel,
    ReconciliationMatchModel,
    ReconciliationModel,
)
from tests.modules.conftest import TEST_BANK_ACCOUNT_ID


# ---------------------------------------------------------------------------
# 1. BankAccountModel
# ---------------------------------------------------------------------------


class TestBankAccountModelORM:
    """Round-trip tests for BankAccountModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = BankAccountModel(
            code="BANK-001",
            name="Operating Account",
            institution="First National",
            account_number_masked="****5678",
            currency="USD",
            gl_account_code="1010-000",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BankAccountModel, obj.id)
        assert queried is not None
        assert queried.code == "BANK-001"
        assert queried.name == "Operating Account"
        assert queried.institution == "First National"
        assert queried.account_number_masked == "****5678"
        assert queried.currency == "USD"
        assert queried.gl_account_code == "1010-000"
        assert queried.is_active is True

    def test_unique_code_constraint(self, session, test_actor_id):
        """Duplicate code raises IntegrityError."""
        obj1 = BankAccountModel(
            code="BANK-DUP",
            name="Account A",
            institution="Bank A",
            account_number_masked="****1111",
            currency="USD",
            gl_account_code="1010-000",
            created_by_id=test_actor_id,
        )
        session.add(obj1)
        session.flush()

        obj2 = BankAccountModel(
            code="BANK-DUP",
            name="Account B",
            institution="Bank B",
            account_number_masked="****2222",
            currency="EUR",
            gl_account_code="1011-000",
            created_by_id=test_actor_id,
        )
        session.add(obj2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id):
        obj = BankAccountModel(
            code="BANK-DEF",
            name="Default Account",
            institution="Default Bank",
            account_number_masked="****0000",
            currency="USD",
            gl_account_code="1010-000",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BankAccountModel, obj.id)
        assert queried.is_active is True

    def test_transactions_relationship(self, session, test_actor_id):
        """Parent bank account with child transaction loads the relationship."""
        acct = BankAccountModel(
            code="BANK-TXN-REL",
            name="Txn Relationship Account",
            institution="Test Bank",
            account_number_masked="****9999",
            currency="USD",
            gl_account_code="1010-000",
            created_by_id=test_actor_id,
        )
        session.add(acct)
        session.flush()

        txn = BankTransactionModel(
            bank_account_id=acct.id,
            transaction_date=date(2025, 3, 15),
            amount=Decimal("500.00"),
            transaction_type="deposit",
            reference="DEP-001",
            description="Customer payment",
            reconciled=False,
            matched_journal_line_id=None,
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        session.expire_all()
        reloaded = session.get(BankAccountModel, acct.id)
        assert len(reloaded.transactions) == 1
        assert reloaded.transactions[0].reference == "DEP-001"

    def test_statements_relationship(self, session, test_actor_id):
        """Parent bank account with child statement loads the relationship."""
        acct = BankAccountModel(
            code="BANK-STMT-REL",
            name="Stmt Relationship Account",
            institution="Test Bank",
            account_number_masked="****8888",
            currency="USD",
            gl_account_code="1010-000",
            created_by_id=test_actor_id,
        )
        session.add(acct)
        session.flush()

        stmt = BankStatementModel(
            bank_account_id=acct.id,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("10000.00"),
            closing_balance=Decimal("12500.00"),
            line_count=5,
            format="MT940",
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        session.expire_all()
        reloaded = session.get(BankAccountModel, acct.id)
        assert len(reloaded.statements) == 1
        assert reloaded.statements[0].closing_balance == Decimal("12500.00")

    def test_reconciliations_relationship(self, session, test_actor_id):
        """Parent bank account with child reconciliation loads the relationship."""
        acct = BankAccountModel(
            code="BANK-REC-REL",
            name="Recon Relationship Account",
            institution="Test Bank",
            account_number_masked="****7777",
            currency="USD",
            gl_account_code="1010-000",
            created_by_id=test_actor_id,
        )
        session.add(acct)
        session.flush()

        recon = ReconciliationModel(
            bank_account_id=acct.id,
            statement_date=date(2025, 3, 31),
            statement_balance=Decimal("12500.00"),
            book_balance=Decimal("12400.00"),
            variance=Decimal("100.00"),
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(recon)
        session.flush()

        session.expire_all()
        reloaded = session.get(BankAccountModel, acct.id)
        assert len(reloaded.reconciliations) == 1
        assert reloaded.reconciliations[0].variance == Decimal("100.00")


# ---------------------------------------------------------------------------
# 2. BankTransactionModel
# ---------------------------------------------------------------------------


class TestBankTransactionModelORM:
    """Round-trip tests for BankTransactionModel."""

    def test_create_and_query(self, session, test_actor_id, test_bank_account):
        journal_line_id = uuid4()
        obj = BankTransactionModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            transaction_date=date(2025, 3, 15),
            amount=Decimal("-1200.50"),
            transaction_type="payment",
            reference="CHK-1001",
            description="Vendor payment",
            external_id="EXT-ABC-123",
            reconciled=True,
            matched_journal_line_id=journal_line_id,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BankTransactionModel, obj.id)
        assert queried is not None
        assert queried.bank_account_id == TEST_BANK_ACCOUNT_ID
        assert queried.transaction_date == date(2025, 3, 15)
        assert queried.amount == Decimal("-1200.50")
        assert queried.transaction_type == "payment"
        assert queried.reference == "CHK-1001"
        assert queried.description == "Vendor payment"
        assert queried.external_id == "EXT-ABC-123"
        assert queried.reconciled is True
        assert queried.matched_journal_line_id == journal_line_id

    def test_fk_bank_account_id_constraint(self, session, test_actor_id):
        """FK to cash_bank_accounts raises IntegrityError when account does not exist."""
        obj = BankTransactionModel(
            bank_account_id=uuid4(),
            transaction_date=date(2025, 3, 15),
            amount=Decimal("100.00"),
            transaction_type="deposit",
            reference="DEP-ORPHAN",
            matched_journal_line_id=None,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_bank_account):
        obj = BankTransactionModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            transaction_date=date(2025, 3, 15),
            amount=Decimal("250.00"),
            transaction_type="deposit",
            reference="DEP-DEF",
            matched_journal_line_id=None,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BankTransactionModel, obj.id)
        assert queried.description == ""
        assert queried.reconciled is False


# ---------------------------------------------------------------------------
# 3. ReconciliationModel
# ---------------------------------------------------------------------------


class TestReconciliationModelORM:
    """Round-trip tests for ReconciliationModel."""

    def test_create_and_query(self, session, test_actor_id, test_bank_account):
        completed_by = uuid4()
        obj = ReconciliationModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            statement_balance=Decimal("50000.00"),
            book_balance=Decimal("49800.00"),
            adjusted_book_balance=Decimal("50000.00"),
            variance=Decimal("0.00"),
            status="completed",
            completed_by_id=completed_by,
            completed_at=date(2025, 4, 2),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ReconciliationModel, obj.id)
        assert queried is not None
        assert queried.bank_account_id == TEST_BANK_ACCOUNT_ID
        assert queried.statement_date == date(2025, 3, 31)
        assert queried.statement_balance == Decimal("50000.00")
        assert queried.book_balance == Decimal("49800.00")
        assert queried.adjusted_book_balance == Decimal("50000.00")
        assert queried.variance == Decimal("0.00")
        assert queried.status == "completed"
        assert queried.completed_by_id == completed_by
        assert queried.completed_at == date(2025, 4, 2)

    def test_fk_bank_account_id_constraint(self, session, test_actor_id):
        """FK to cash_bank_accounts raises IntegrityError when account does not exist."""
        obj = ReconciliationModel(
            bank_account_id=uuid4(),
            statement_date=date(2025, 3, 31),
            statement_balance=Decimal("1000.00"),
            book_balance=Decimal("1000.00"),
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_bank_account):
        obj = ReconciliationModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            statement_balance=Decimal("5000.00"),
            book_balance=Decimal("5000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(ReconciliationModel, obj.id)
        assert queried.status == "draft"


# ---------------------------------------------------------------------------
# 4. BankStatementModel
# ---------------------------------------------------------------------------


class TestBankStatementModelORM:
    """Round-trip tests for BankStatementModel."""

    def test_create_and_query(self, session, test_actor_id, test_bank_account):
        obj = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("10000.00"),
            closing_balance=Decimal("12500.00"),
            line_count=15,
            format="BAI2",
            currency="EUR",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BankStatementModel, obj.id)
        assert queried is not None
        assert queried.bank_account_id == TEST_BANK_ACCOUNT_ID
        assert queried.statement_date == date(2025, 3, 31)
        assert queried.opening_balance == Decimal("10000.00")
        assert queried.closing_balance == Decimal("12500.00")
        assert queried.line_count == 15
        assert queried.format == "BAI2"
        assert queried.currency == "EUR"

    def test_fk_bank_account_id_constraint(self, session, test_actor_id):
        """FK to cash_bank_accounts raises IntegrityError when account does not exist."""
        obj = BankStatementModel(
            bank_account_id=uuid4(),
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("100.00"),
            closing_balance=Decimal("200.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_bank_account):
        obj = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("1000.00"),
            closing_balance=Decimal("2000.00"),
            line_count=3,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(BankStatementModel, obj.id)
        assert queried.format == "MT940"
        assert queried.currency == "USD"

    def test_lines_relationship(self, session, test_actor_id, test_bank_account):
        """Parent statement with child line loads the relationship."""
        stmt = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("5000.00"),
            closing_balance=Decimal("5500.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        line = BankStatementLineModel(
            statement_id=stmt.id,
            transaction_date=date(2025, 3, 20),
            amount=Decimal("500.00"),
            reference="TXN-REF-001",
            description="Incoming wire",
            transaction_type="CRDT",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        session.expire_all()
        reloaded = session.get(BankStatementModel, stmt.id)
        assert len(reloaded.lines) == 1
        assert reloaded.lines[0].reference == "TXN-REF-001"


# ---------------------------------------------------------------------------
# 5. BankStatementLineModel
# ---------------------------------------------------------------------------


class TestBankStatementLineModelORM:
    """Round-trip tests for BankStatementLineModel."""

    def test_create_and_query(self, session, test_actor_id, test_bank_account):
        stmt = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("8000.00"),
            closing_balance=Decimal("8750.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        line = BankStatementLineModel(
            statement_id=stmt.id,
            transaction_date=date(2025, 3, 25),
            amount=Decimal("750.00"),
            reference="WIRE-IN-001",
            description="Customer wire transfer",
            transaction_type="CRDT",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(BankStatementLineModel, line.id)
        assert queried is not None
        assert queried.statement_id == stmt.id
        assert queried.transaction_date == date(2025, 3, 25)
        assert queried.amount == Decimal("750.00")
        assert queried.reference == "WIRE-IN-001"
        assert queried.description == "Customer wire transfer"
        assert queried.transaction_type == "CRDT"

    def test_fk_statement_id_constraint(self, session, test_actor_id):
        """FK to cash_bank_statements raises IntegrityError when statement does not exist."""
        line = BankStatementLineModel(
            statement_id=uuid4(),
            transaction_date=date(2025, 3, 25),
            amount=Decimal("100.00"),
            reference="ORPHAN-REF",
            created_by_id=test_actor_id,
        )
        session.add(line)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_bank_account):
        stmt = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("1000.00"),
            closing_balance=Decimal("1100.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        line = BankStatementLineModel(
            statement_id=stmt.id,
            transaction_date=date(2025, 3, 28),
            amount=Decimal("100.00"),
            reference="DEF-REF",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(BankStatementLineModel, line.id)
        assert queried.description == ""
        assert queried.transaction_type == "UNKNOWN"

    def test_matches_relationship(self, session, test_actor_id, test_bank_account):
        """Parent statement line with child match loads the relationship."""
        stmt = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("2000.00"),
            closing_balance=Decimal("2300.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        line = BankStatementLineModel(
            statement_id=stmt.id,
            transaction_date=date(2025, 3, 20),
            amount=Decimal("300.00"),
            reference="MATCH-REF",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        match = ReconciliationMatchModel(
            statement_line_id=line.id,
            journal_line_id=uuid4(),
            match_confidence=Decimal("0.95"),
            match_method="auto",
            created_by_id=test_actor_id,
        )
        session.add(match)
        session.flush()

        session.expire_all()
        reloaded = session.get(BankStatementLineModel, line.id)
        assert len(reloaded.matches) == 1
        assert reloaded.matches[0].match_method == "auto"


# ---------------------------------------------------------------------------
# 6. ReconciliationMatchModel
# ---------------------------------------------------------------------------


class TestReconciliationMatchModelORM:
    """Round-trip tests for ReconciliationMatchModel."""

    def test_create_and_query(self, session, test_actor_id, test_bank_account):
        stmt = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("3000.00"),
            closing_balance=Decimal("3200.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        line = BankStatementLineModel(
            statement_id=stmt.id,
            transaction_date=date(2025, 3, 22),
            amount=Decimal("200.00"),
            reference="RECON-REF",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        journal_line_id = uuid4()
        match = ReconciliationMatchModel(
            statement_line_id=line.id,
            journal_line_id=journal_line_id,
            match_confidence=Decimal("0.85"),
            match_method="rule_based",
            created_by_id=test_actor_id,
        )
        session.add(match)
        session.flush()

        queried = session.get(ReconciliationMatchModel, match.id)
        assert queried is not None
        assert queried.statement_line_id == line.id
        assert queried.journal_line_id == journal_line_id
        assert queried.match_confidence == Decimal("0.85")
        assert queried.match_method == "rule_based"

    def test_fk_statement_line_id_constraint(self, session, test_actor_id):
        """FK to cash_bank_statement_lines raises IntegrityError when line does not exist."""
        match = ReconciliationMatchModel(
            statement_line_id=uuid4(),
            journal_line_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(match)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_bank_account):
        stmt = BankStatementModel(
            bank_account_id=TEST_BANK_ACCOUNT_ID,
            statement_date=date(2025, 3, 31),
            opening_balance=Decimal("1000.00"),
            closing_balance=Decimal("1050.00"),
            line_count=1,
            created_by_id=test_actor_id,
        )
        session.add(stmt)
        session.flush()

        line = BankStatementLineModel(
            statement_id=stmt.id,
            transaction_date=date(2025, 3, 29),
            amount=Decimal("50.00"),
            reference="DEF-MATCH",
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        match = ReconciliationMatchModel(
            statement_line_id=line.id,
            created_by_id=test_actor_id,
        )
        session.add(match)
        session.flush()

        queried = session.get(ReconciliationMatchModel, match.id)
        assert queried.match_confidence == Decimal("1.0")
        assert queried.match_method == "manual"
        assert queried.journal_line_id is None
