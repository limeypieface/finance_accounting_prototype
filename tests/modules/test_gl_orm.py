"""ORM round-trip tests for General Ledger (GL) module.

Verifies that every GL ORM model can be persisted, queried back with
correct field values, and that FK / unique constraints are enforced by
the database.

Models under test (7):
    RecurringEntryModel, RecurringLineModel, JournalBatchModel,
    AccountReconciliationModel, PeriodCloseTaskModel,
    TranslationResultModel, RevaluationResultModel
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.gl.orm import (
    AccountReconciliationModel,
    JournalBatchModel,
    PeriodCloseTaskModel,
    RecurringEntryModel,
    RecurringLineModel,
    RevaluationResultModel,
    TranslationResultModel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recurring_entry(session, test_actor_id, **overrides):
    """Create and flush a RecurringEntryModel with sensible defaults."""
    defaults = dict(
        name=f"REC-{uuid4().hex[:8]}",
        description="Test recurring entry",
        frequency="monthly",
        start_date=date(2024, 1, 1),
        is_active=True,
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = RecurringEntryModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


# ===================================================================
# RecurringEntryModel
# ===================================================================


class TestRecurringEntryModelORM:
    """Round-trip persistence tests for RecurringEntryModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = RecurringEntryModel(
            name="Monthly Rent Accrual",
            description="Accrue monthly office rent expense",
            frequency="monthly",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RecurringEntryModel, obj.id)
        assert queried is not None
        assert queried.name == "Monthly Rent Accrual"
        assert queried.description == "Accrue monthly office rent expense"
        assert queried.frequency == "monthly"
        assert queried.start_date == date(2024, 1, 1)
        assert queried.end_date == date(2024, 12, 31)
        assert queried.last_generated_date is None
        assert queried.is_active is True

    def test_with_last_generated_date(self, session, test_actor_id):
        obj = RecurringEntryModel(
            name="Quarterly Insurance",
            description="Quarterly insurance amortization",
            frequency="quarterly",
            start_date=date(2024, 1, 1),
            last_generated_date=date(2024, 3, 31),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RecurringEntryModel, obj.id)
        assert queried.last_generated_date == date(2024, 3, 31)

    def test_inactive_entry(self, session, test_actor_id):
        obj = RecurringEntryModel(
            name="Deprecated Accrual",
            description="No longer needed",
            frequency="annually",
            start_date=date(2020, 1, 1),
            end_date=date(2023, 12, 31),
            is_active=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RecurringEntryModel, obj.id)
        assert queried.is_active is False

    def test_unique_name_constraint(self, session, test_actor_id):
        _make_recurring_entry(session, test_actor_id, name="UNIQUE-REC-NAME")
        dup = RecurringEntryModel(
            name="UNIQUE-REC-NAME",
            description="Duplicate",
            frequency="monthly",
            start_date=date(2024, 6, 1),
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_lines_relationship(self, session, test_actor_id):
        entry = _make_recurring_entry(session, test_actor_id, name="Lines-Test-Entry")
        line1 = RecurringLineModel(
            recurring_entry_id=entry.id,
            account_code="5100",
            side="debit",
            amount=Decimal("5000.00"),
            description="Rent expense",
            created_by_id=test_actor_id,
        )
        line2 = RecurringLineModel(
            recurring_entry_id=entry.id,
            account_code="2200",
            side="credit",
            amount=Decimal("5000.00"),
            description="Accrued rent",
            created_by_id=test_actor_id,
        )
        session.add_all([line1, line2])
        session.flush()

        session.expire(entry, ["lines"])
        queried = session.get(RecurringEntryModel, entry.id)
        assert len(queried.lines) == 2
        sides = {l.side for l in queried.lines}
        assert sides == {"debit", "credit"}


# ===================================================================
# RecurringLineModel
# ===================================================================


class TestRecurringLineModelORM:
    """Round-trip persistence tests for RecurringLineModel."""

    def test_create_and_query(self, session, test_actor_id):
        entry = _make_recurring_entry(session, test_actor_id)
        obj = RecurringLineModel(
            recurring_entry_id=entry.id,
            account_code="5100",
            side="debit",
            amount=Decimal("2500.00"),
            description="Office supplies expense",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RecurringLineModel, obj.id)
        assert queried is not None
        assert queried.recurring_entry_id == entry.id
        assert queried.account_code == "5100"
        assert queried.side == "debit"
        assert queried.amount == Decimal("2500.00")
        assert queried.description == "Office supplies expense"

    def test_nullable_description(self, session, test_actor_id):
        entry = _make_recurring_entry(session, test_actor_id)
        obj = RecurringLineModel(
            recurring_entry_id=entry.id,
            account_code="2000",
            side="credit",
            amount=Decimal("1000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RecurringLineModel, obj.id)
        assert queried.description is None

    def test_recurring_entry_relationship(self, session, test_actor_id):
        entry = _make_recurring_entry(session, test_actor_id, name="Parent-Entry-Test")
        line = RecurringLineModel(
            recurring_entry_id=entry.id,
            account_code="1000",
            side="debit",
            amount=Decimal("3000.00"),
            created_by_id=test_actor_id,
        )
        session.add(line)
        session.flush()

        queried = session.get(RecurringLineModel, line.id)
        assert queried.recurring_entry is not None
        assert queried.recurring_entry.name == "Parent-Entry-Test"

    def test_fk_recurring_entry_nonexistent(self, session, test_actor_id):
        obj = RecurringLineModel(
            recurring_entry_id=str(uuid4()),
            account_code="5100",
            side="debit",
            amount=Decimal("100.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# JournalBatchModel
# ===================================================================


class TestJournalBatchModelORM:
    """Round-trip persistence tests for JournalBatchModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = JournalBatchModel(
            batch_number="BATCH-2024-001",
            batch_date=date(2024, 1, 31),
            description="January month-end accruals",
            source="gl",
            entry_count=5,
            total_debits=Decimal("25000.00"),
            total_credits=Decimal("25000.00"),
            status="open",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(JournalBatchModel, obj.id)
        assert queried is not None
        assert queried.batch_number == "BATCH-2024-001"
        assert queried.batch_date == date(2024, 1, 31)
        assert queried.description == "January month-end accruals"
        assert queried.source == "gl"
        assert queried.entry_count == 5
        assert queried.total_debits == Decimal("25000.00")
        assert queried.total_credits == Decimal("25000.00")
        assert queried.status == "open"
        assert queried.approved_by_id is None

    def test_approved_batch(self, session, test_actor_id):
        approver = uuid4()
        obj = JournalBatchModel(
            batch_number="BATCH-2024-002",
            batch_date=date(2024, 2, 28),
            description="February adjustments",
            source="adjustment",
            entry_count=3,
            total_debits=Decimal("15000.00"),
            total_credits=Decimal("15000.00"),
            status="approved",
            approved_by_id=approver,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(JournalBatchModel, obj.id)
        assert queried.status == "approved"
        assert queried.approved_by_id == approver

    def test_unique_batch_number(self, session, test_actor_id):
        JournalBatchModel(
            batch_number="DUP-BATCH",
            batch_date=date(2024, 1, 1),
            description="First batch",
            source="gl",
            created_by_id=test_actor_id,
        )
        session.add(JournalBatchModel(
            batch_number="DUP-BATCH",
            batch_date=date(2024, 1, 1),
            description="First batch",
            source="gl",
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = JournalBatchModel(
            batch_number="DUP-BATCH",
            batch_date=date(2024, 2, 1),
            description="Duplicate batch",
            source="ap",
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# AccountReconciliationModel
# ===================================================================


class TestAccountReconciliationModelORM:
    """Round-trip persistence tests for AccountReconciliationModel."""

    def test_create_and_query(self, session, test_actor_id):
        account_id = uuid4()
        reconciler = uuid4()
        obj = AccountReconciliationModel(
            account_id=str(account_id),
            period="2024-01",
            reconciled_date=date(2024, 2, 5),
            reconciled_by_id=str(reconciler),
            status="reconciled",
            notes="Balance confirmed with bank statement",
            balance_confirmed=Decimal("150000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(AccountReconciliationModel, obj.id)
        assert queried is not None
        assert queried.account_id == str(account_id)
        assert queried.period == "2024-01"
        assert queried.reconciled_date == date(2024, 2, 5)
        assert queried.reconciled_by_id == str(reconciler)
        assert queried.status == "reconciled"
        assert queried.notes == "Balance confirmed with bank statement"
        assert queried.balance_confirmed == Decimal("150000.00")

    def test_pending_reconciliation(self, session, test_actor_id):
        obj = AccountReconciliationModel(
            account_id=str(uuid4()),
            period="2024-02",
            reconciled_date=date(2024, 3, 1),
            reconciled_by_id=str(uuid4()),
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(AccountReconciliationModel, obj.id)
        assert queried.status == "pending"
        assert queried.notes is None

    def test_unique_account_period(self, session, test_actor_id):
        acct = str(uuid4())
        AccountReconciliationModel(
            account_id=acct,
            period="2024-UNIQUE",
            reconciled_date=date(2024, 2, 5),
            reconciled_by_id=str(uuid4()),
            status="reconciled",
            balance_confirmed=Decimal("100000.00"),
            created_by_id=test_actor_id,
        )
        session.add(AccountReconciliationModel(
            account_id=acct,
            period="2024-UNIQUE",
            reconciled_date=date(2024, 2, 5),
            reconciled_by_id=str(uuid4()),
            status="reconciled",
            balance_confirmed=Decimal("100000.00"),
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = AccountReconciliationModel(
            account_id=acct,
            period="2024-UNIQUE",
            reconciled_date=date(2024, 2, 10),
            reconciled_by_id=str(uuid4()),
            status="exception",
            balance_confirmed=Decimal("99000.00"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_account_different_period_allowed(self, session, test_actor_id):
        acct = str(uuid4())
        obj1 = AccountReconciliationModel(
            account_id=acct,
            period="2024-01-MULTI",
            reconciled_date=date(2024, 2, 5),
            reconciled_by_id=str(uuid4()),
            status="reconciled",
            balance_confirmed=Decimal("100000.00"),
            created_by_id=test_actor_id,
        )
        obj2 = AccountReconciliationModel(
            account_id=acct,
            period="2024-02-MULTI",
            reconciled_date=date(2024, 3, 5),
            reconciled_by_id=str(uuid4()),
            status="reconciled",
            balance_confirmed=Decimal("110000.00"),
            created_by_id=test_actor_id,
        )
        session.add_all([obj1, obj2])
        session.flush()
        assert session.get(AccountReconciliationModel, obj1.id) is not None
        assert session.get(AccountReconciliationModel, obj2.id) is not None


# ===================================================================
# PeriodCloseTaskModel
# ===================================================================


class TestPeriodCloseTaskModelORM:
    """Round-trip persistence tests for PeriodCloseTaskModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = PeriodCloseTaskModel(
            period="2024-01",
            task_name="Reconcile bank accounts",
            module="cash",
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PeriodCloseTaskModel, obj.id)
        assert queried is not None
        assert queried.period == "2024-01"
        assert queried.task_name == "Reconcile bank accounts"
        assert queried.module == "cash"
        assert queried.status == "pending"
        assert queried.completed_by_id is None
        assert queried.completed_date is None

    def test_completed_task(self, session, test_actor_id):
        completer = uuid4()
        obj = PeriodCloseTaskModel(
            period="2024-01",
            task_name="Post depreciation entries",
            module="assets",
            status="completed",
            completed_by_id=str(completer),
            completed_date=date(2024, 2, 3),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(PeriodCloseTaskModel, obj.id)
        assert queried.status == "completed"
        assert queried.completed_by_id == str(completer)
        assert queried.completed_date == date(2024, 2, 3)

    def test_unique_period_task_module(self, session, test_actor_id):
        PeriodCloseTaskModel(
            period="2024-UQ",
            task_name="Accrue payroll",
            module="payroll",
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(PeriodCloseTaskModel(
            period="2024-UQ",
            task_name="Accrue payroll",
            module="payroll",
            status="pending",
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = PeriodCloseTaskModel(
            period="2024-UQ",
            task_name="Accrue payroll",
            module="payroll",
            status="completed",
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_task_different_modules_allowed(self, session, test_actor_id):
        obj1 = PeriodCloseTaskModel(
            period="2024-MULTI-MOD",
            task_name="Post accruals",
            module="ap",
            status="pending",
            created_by_id=test_actor_id,
        )
        obj2 = PeriodCloseTaskModel(
            period="2024-MULTI-MOD",
            task_name="Post accruals",
            module="ar",
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add_all([obj1, obj2])
        session.flush()
        assert session.get(PeriodCloseTaskModel, obj1.id) is not None
        assert session.get(PeriodCloseTaskModel, obj2.id) is not None


# ===================================================================
# TranslationResultModel
# ===================================================================


class TestTranslationResultModelORM:
    """Round-trip persistence tests for TranslationResultModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = TranslationResultModel(
            entity_id="ENTITY-001",
            period="2024-Q4",
            source_currency="EUR",
            target_currency="USD",
            method="current_rate",
            translated_amount=Decimal("1250000.00"),
            cta_amount=Decimal("15000.00"),
            exchange_rate=Decimal("1.0850"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TranslationResultModel, obj.id)
        assert queried is not None
        assert queried.entity_id == "ENTITY-001"
        assert queried.period == "2024-Q4"
        assert queried.source_currency == "EUR"
        assert queried.target_currency == "USD"
        assert queried.method == "current_rate"
        assert queried.translated_amount == Decimal("1250000.00")
        assert queried.cta_amount == Decimal("15000.00")
        assert queried.exchange_rate == Decimal("1.0850")

    def test_temporal_method(self, session, test_actor_id):
        obj = TranslationResultModel(
            entity_id="ENTITY-002",
            period="2024-Q4",
            source_currency="GBP",
            target_currency="USD",
            method="temporal",
            translated_amount=Decimal("800000.00"),
            cta_amount=Decimal("-5000.00"),
            exchange_rate=Decimal("1.2700"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TranslationResultModel, obj.id)
        assert queried.method == "temporal"
        assert queried.cta_amount == Decimal("-5000.00")

    def test_unique_entity_period_currencies(self, session, test_actor_id):
        TranslationResultModel(
            entity_id="DUP-ENTITY",
            period="2024-DUP",
            source_currency="EUR",
            target_currency="USD",
            method="current_rate",
            translated_amount=Decimal("100000.00"),
            cta_amount=Decimal("1000.00"),
            exchange_rate=Decimal("1.08"),
            created_by_id=test_actor_id,
        )
        session.add(TranslationResultModel(
            entity_id="DUP-ENTITY",
            period="2024-DUP",
            source_currency="EUR",
            target_currency="USD",
            method="current_rate",
            translated_amount=Decimal("100000.00"),
            cta_amount=Decimal("1000.00"),
            exchange_rate=Decimal("1.08"),
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = TranslationResultModel(
            entity_id="DUP-ENTITY",
            period="2024-DUP",
            source_currency="EUR",
            target_currency="USD",
            method="temporal",
            translated_amount=Decimal("105000.00"),
            cta_amount=Decimal("2000.00"),
            exchange_rate=Decimal("1.09"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_entity_different_currencies_allowed(self, session, test_actor_id):
        obj1 = TranslationResultModel(
            entity_id="MULTI-CCY",
            period="2024-Q4",
            source_currency="EUR",
            target_currency="USD",
            method="current_rate",
            translated_amount=Decimal("1000000.00"),
            cta_amount=Decimal("10000.00"),
            exchange_rate=Decimal("1.08"),
            created_by_id=test_actor_id,
        )
        obj2 = TranslationResultModel(
            entity_id="MULTI-CCY",
            period="2024-Q4",
            source_currency="GBP",
            target_currency="USD",
            method="current_rate",
            translated_amount=Decimal("800000.00"),
            cta_amount=Decimal("8000.00"),
            exchange_rate=Decimal("1.27"),
            created_by_id=test_actor_id,
        )
        session.add_all([obj1, obj2])
        session.flush()
        assert session.get(TranslationResultModel, obj1.id) is not None
        assert session.get(TranslationResultModel, obj2.id) is not None


# ===================================================================
# RevaluationResultModel
# ===================================================================


class TestRevaluationResultModelORM:
    """Round-trip persistence tests for RevaluationResultModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = RevaluationResultModel(
            period="2024-01",
            revaluation_date=date(2024, 1, 31),
            currencies_processed=5,
            total_gain=Decimal("12500.00"),
            total_loss=Decimal("3200.00"),
            entries_posted=8,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RevaluationResultModel, obj.id)
        assert queried is not None
        assert queried.period == "2024-01"
        assert queried.revaluation_date == date(2024, 1, 31)
        assert queried.currencies_processed == 5
        assert queried.total_gain == Decimal("12500.00")
        assert queried.total_loss == Decimal("3200.00")
        assert queried.entries_posted == 8

    def test_zero_gain_loss(self, session, test_actor_id):
        obj = RevaluationResultModel(
            period="2024-02",
            revaluation_date=date(2024, 2, 29),
            currencies_processed=3,
            total_gain=Decimal("0.00"),
            total_loss=Decimal("0.00"),
            entries_posted=0,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RevaluationResultModel, obj.id)
        assert queried.total_gain == Decimal("0.00")
        assert queried.total_loss == Decimal("0.00")
        assert queried.entries_posted == 0

    def test_unique_period_date(self, session, test_actor_id):
        RevaluationResultModel(
            period="2024-UQ",
            revaluation_date=date(2024, 1, 31),
            currencies_processed=2,
            total_gain=Decimal("1000.00"),
            total_loss=Decimal("500.00"),
            entries_posted=4,
            created_by_id=test_actor_id,
        )
        session.add(RevaluationResultModel(
            period="2024-UQ",
            revaluation_date=date(2024, 1, 31),
            currencies_processed=2,
            total_gain=Decimal("1000.00"),
            total_loss=Decimal("500.00"),
            entries_posted=4,
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = RevaluationResultModel(
            period="2024-UQ",
            revaluation_date=date(2024, 1, 31),
            currencies_processed=3,
            total_gain=Decimal("2000.00"),
            total_loss=Decimal("1000.00"),
            entries_posted=6,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_same_period_different_dates_allowed(self, session, test_actor_id):
        obj1 = RevaluationResultModel(
            period="2024-MULTI-DATE",
            revaluation_date=date(2024, 1, 15),
            currencies_processed=2,
            total_gain=Decimal("500.00"),
            total_loss=Decimal("200.00"),
            entries_posted=3,
            created_by_id=test_actor_id,
        )
        obj2 = RevaluationResultModel(
            period="2024-MULTI-DATE",
            revaluation_date=date(2024, 1, 31),
            currencies_processed=2,
            total_gain=Decimal("800.00"),
            total_loss=Decimal("300.00"),
            entries_posted=4,
            created_by_id=test_actor_id,
        )
        session.add_all([obj1, obj2])
        session.flush()
        assert session.get(RevaluationResultModel, obj1.id) is not None
        assert session.get(RevaluationResultModel, obj2.id) is not None
