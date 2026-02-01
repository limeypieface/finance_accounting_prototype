"""ORM round-trip tests for Revenue module (ASC 606).

Covers all six models:
    - RevenueContractModel (Step 1)
    - PerformanceObligationModel (Step 2)
    - TransactionPriceModel (Step 3)
    - SSPAllocationModel (Step 4)
    - RecognitionScheduleModel (Step 5)
    - ContractModificationModel (ASC 606-10-25-12)
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.revenue.orm import (
    ContractModificationModel,
    PerformanceObligationModel,
    RecognitionScheduleModel,
    RevenueContractModel,
    SSPAllocationModel,
    TransactionPriceModel,
)
from tests.modules.conftest import TEST_CUSTOMER_ID, TEST_REVENUE_CONTRACT_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contract(session, test_actor_id, test_customer_party, **overrides):
    """Create and flush a RevenueContractModel with sensible defaults."""
    fields = dict(
        customer_id=TEST_CUSTOMER_ID,
        contract_number=f"REV-{uuid4().hex[:8]}",
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        total_consideration=Decimal("100000.00"),
        variable_consideration=Decimal("5000.00"),
        status="active",
        currency="USD",
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    obj = RevenueContractModel(**fields)
    session.add(obj)
    session.flush()
    return obj


def _make_obligation(session, test_actor_id, contract_id, **overrides):
    """Create and flush a PerformanceObligationModel."""
    fields = dict(
        contract_id=contract_id,
        description="Software license delivery",
        is_distinct=True,
        standalone_selling_price=Decimal("60000.00"),
        allocated_price=Decimal("57000.00"),
        recognition_method="over_time_output",
        satisfied=False,
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    obj = PerformanceObligationModel(**fields)
    session.add(obj)
    session.flush()
    return obj


# ==========================================================================
# RevenueContractModel
# ==========================================================================


class TestRevenueContractModelORM:
    """Round-trip persistence tests for RevenueContractModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)

        queried = session.get(RevenueContractModel, contract.id)
        assert queried is not None
        assert queried.customer_id == TEST_CUSTOMER_ID
        assert queried.start_date == date(2024, 1, 1)
        assert queried.end_date == date(2025, 12, 31)
        assert queried.total_consideration == Decimal("100000.00")
        assert queried.variable_consideration == Decimal("5000.00")
        assert queried.status == "active"
        assert queried.currency == "USD"

    def test_unique_contract_number(self, session, test_actor_id, test_customer_party):
        number = f"REV-UNIQUE-{uuid4().hex[:6]}"
        _make_contract(
            session, test_actor_id, test_customer_party,
            contract_number=number,
        )
        dup = RevenueContractModel(
            customer_id=TEST_CUSTOMER_ID,
            contract_number=number,
            start_date=date(2024, 6, 1),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_customer_id_invalid(self, session, test_actor_id):
        obj = RevenueContractModel(
            customer_id=uuid4(),
            contract_number=f"REV-BAD-{uuid4().hex[:6]}",
            start_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_customer_party):
        obj = RevenueContractModel(
            customer_id=TEST_CUSTOMER_ID,
            contract_number=f"REV-DEF-{uuid4().hex[:6]}",
            start_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(RevenueContractModel, obj.id)
        assert queried.total_consideration == Decimal("0")
        assert queried.variable_consideration == Decimal("0")
        assert queried.status == "identified"
        assert queried.currency == "USD"

    def test_child_relationships_load(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        _make_obligation(session, test_actor_id, contract.id)

        session.expire(contract)
        queried = session.get(RevenueContractModel, contract.id)
        assert len(queried.obligations) == 1
        assert queried.obligations[0].description == "Software license delivery"


# ==========================================================================
# PerformanceObligationModel
# ==========================================================================


class TestPerformanceObligationModelORM:
    """Round-trip persistence tests for PerformanceObligationModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        queried = session.get(PerformanceObligationModel, po.id)
        assert queried is not None
        assert queried.contract_id == contract.id
        assert queried.description == "Software license delivery"
        assert queried.is_distinct is True
        assert queried.standalone_selling_price == Decimal("60000.00")
        assert queried.allocated_price == Decimal("57000.00")
        assert queried.recognition_method == "over_time_output"
        assert queried.satisfied is False
        assert queried.satisfaction_date is None

    def test_satisfied_with_date(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(
            session, test_actor_id, contract.id,
            satisfied=True,
            satisfaction_date=date(2024, 6, 15),
        )

        queried = session.get(PerformanceObligationModel, po.id)
        assert queried.satisfied is True
        assert queried.satisfaction_date == date(2024, 6, 15)

    def test_fk_contract_id_invalid(self, session, test_actor_id):
        obj = PerformanceObligationModel(
            contract_id=uuid4(),
            description="Phantom obligation",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_parent_relationship(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        queried = session.get(PerformanceObligationModel, po.id)
        assert queried.contract is not None
        assert queried.contract.id == contract.id


# ==========================================================================
# TransactionPriceModel
# ==========================================================================


class TestTransactionPriceModelORM:
    """Round-trip persistence tests for TransactionPriceModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        tp = TransactionPriceModel(
            contract_id=contract.id,
            base_price=Decimal("80000.00"),
            variable_consideration=Decimal("10000.00"),
            constraint_applied=True,
            financing_component=Decimal("2000.00"),
            noncash_consideration=Decimal("500.00"),
            consideration_payable=Decimal("1500.00"),
            total_transaction_price=Decimal("91000.00"),
            created_by_id=test_actor_id,
        )
        session.add(tp)
        session.flush()

        queried = session.get(TransactionPriceModel, tp.id)
        assert queried is not None
        assert queried.base_price == Decimal("80000.00")
        assert queried.variable_consideration == Decimal("10000.00")
        assert queried.constraint_applied is True
        assert queried.financing_component == Decimal("2000.00")
        assert queried.noncash_consideration == Decimal("500.00")
        assert queried.consideration_payable == Decimal("1500.00")
        assert queried.total_transaction_price == Decimal("91000.00")

    def test_defaults(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        tp = TransactionPriceModel(
            contract_id=contract.id,
            base_price=Decimal("50000.00"),
            created_by_id=test_actor_id,
        )
        session.add(tp)
        session.flush()

        queried = session.get(TransactionPriceModel, tp.id)
        assert queried.variable_consideration == Decimal("0")
        assert queried.constraint_applied is False
        assert queried.financing_component == Decimal("0")
        assert queried.noncash_consideration == Decimal("0")
        assert queried.consideration_payable == Decimal("0")
        assert queried.total_transaction_price == Decimal("0")

    def test_fk_contract_id_invalid(self, session, test_actor_id):
        obj = TransactionPriceModel(
            contract_id=uuid4(),
            base_price=Decimal("10000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_parent_relationship(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        tp = TransactionPriceModel(
            contract_id=contract.id,
            base_price=Decimal("75000.00"),
            created_by_id=test_actor_id,
        )
        session.add(tp)
        session.flush()

        queried = session.get(TransactionPriceModel, tp.id)
        assert queried.contract is not None
        assert queried.contract.id == contract.id


# ==========================================================================
# SSPAllocationModel
# ==========================================================================


class TestSSPAllocationModelORM:
    """Round-trip persistence tests for SSPAllocationModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        alloc = SSPAllocationModel(
            contract_id=contract.id,
            obligation_id=po.id,
            standalone_selling_price=Decimal("60000.00"),
            allocated_amount=Decimal("57000.00"),
            allocation_percentage=Decimal("0.60"),
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        session.flush()

        queried = session.get(SSPAllocationModel, alloc.id)
        assert queried is not None
        assert queried.contract_id == contract.id
        assert queried.obligation_id == po.id
        assert queried.standalone_selling_price == Decimal("60000.00")
        assert queried.allocated_amount == Decimal("57000.00")
        assert queried.allocation_percentage == Decimal("0.60")

    def test_unique_contract_obligation(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        # First allocation
        first = SSPAllocationModel(
            contract_id=contract.id,
            obligation_id=po.id,
            standalone_selling_price=Decimal("60000.00"),
            allocated_amount=Decimal("57000.00"),
            allocation_percentage=Decimal("0.60"),
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        # Duplicate (same contract + obligation)
        dup = SSPAllocationModel(
            contract_id=contract.id,
            obligation_id=po.id,
            standalone_selling_price=Decimal("40000.00"),
            allocated_amount=Decimal("38000.00"),
            allocation_percentage=Decimal("0.40"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_contract_id_invalid(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        obj = SSPAllocationModel(
            contract_id=uuid4(),
            obligation_id=po.id,
            standalone_selling_price=Decimal("60000.00"),
            allocated_amount=Decimal("57000.00"),
            allocation_percentage=Decimal("0.60"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_obligation_id_invalid(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)

        obj = SSPAllocationModel(
            contract_id=contract.id,
            obligation_id=uuid4(),
            standalone_selling_price=Decimal("60000.00"),
            allocated_amount=Decimal("57000.00"),
            allocation_percentage=Decimal("0.60"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationships(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        alloc = SSPAllocationModel(
            contract_id=contract.id,
            obligation_id=po.id,
            standalone_selling_price=Decimal("60000.00"),
            allocated_amount=Decimal("57000.00"),
            allocation_percentage=Decimal("0.60"),
            created_by_id=test_actor_id,
        )
        session.add(alloc)
        session.flush()

        queried = session.get(SSPAllocationModel, alloc.id)
        assert queried.contract is not None
        assert queried.contract.id == contract.id
        assert queried.obligation is not None
        assert queried.obligation.id == po.id


# ==========================================================================
# RecognitionScheduleModel
# ==========================================================================


class TestRecognitionScheduleModelORM:
    """Round-trip persistence tests for RecognitionScheduleModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        sched = RecognitionScheduleModel(
            contract_id=contract.id,
            obligation_id=po.id,
            period="2024-Q1",
            amount=Decimal("25000.00"),
            recognized=False,
            created_by_id=test_actor_id,
        )
        session.add(sched)
        session.flush()

        queried = session.get(RecognitionScheduleModel, sched.id)
        assert queried is not None
        assert queried.contract_id == contract.id
        assert queried.obligation_id == po.id
        assert queried.period == "2024-Q1"
        assert queried.amount == Decimal("25000.00")
        assert queried.recognized is False
        assert queried.recognized_date is None

    def test_recognized_with_date(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        sched = RecognitionScheduleModel(
            contract_id=contract.id,
            obligation_id=po.id,
            period="2024-Q2",
            amount=Decimal("25000.00"),
            recognized=True,
            recognized_date=date(2024, 6, 30),
            created_by_id=test_actor_id,
        )
        session.add(sched)
        session.flush()

        queried = session.get(RecognitionScheduleModel, sched.id)
        assert queried.recognized is True
        assert queried.recognized_date == date(2024, 6, 30)

    def test_unique_contract_obligation_period(
        self, session, test_actor_id, test_customer_party,
    ):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        first = RecognitionScheduleModel(
            contract_id=contract.id,
            obligation_id=po.id,
            period="2024-Q1",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        dup = RecognitionScheduleModel(
            contract_id=contract.id,
            obligation_id=po.id,
            period="2024-Q1",
            amount=Decimal("30000.00"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_contract_id_invalid(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        obj = RecognitionScheduleModel(
            contract_id=uuid4(),
            obligation_id=po.id,
            period="2024-Q1",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_obligation_id_invalid(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)

        obj = RecognitionScheduleModel(
            contract_id=contract.id,
            obligation_id=uuid4(),
            period="2024-Q1",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationships(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)
        po = _make_obligation(session, test_actor_id, contract.id)

        sched = RecognitionScheduleModel(
            contract_id=contract.id,
            obligation_id=po.id,
            period="2024-Q3",
            amount=Decimal("25000.00"),
            created_by_id=test_actor_id,
        )
        session.add(sched)
        session.flush()

        queried = session.get(RecognitionScheduleModel, sched.id)
        assert queried.contract is not None
        assert queried.contract.id == contract.id
        assert queried.obligation is not None
        assert queried.obligation.id == po.id


# ==========================================================================
# ContractModificationModel
# ==========================================================================


class TestContractModificationModelORM:
    """Round-trip persistence tests for ContractModificationModel."""

    def test_create_and_query(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)

        mod = ContractModificationModel(
            contract_id=contract.id,
            modification_date=date(2024, 7, 1),
            modification_type="cumulative_catch_up",
            description="Added implementation services",
            price_change=Decimal("15000.00"),
            scope_change="Added Phase 2 implementation scope",
            actor_id=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(mod)
        session.flush()

        queried = session.get(ContractModificationModel, mod.id)
        assert queried is not None
        assert queried.contract_id == contract.id
        assert queried.modification_date == date(2024, 7, 1)
        assert queried.modification_type == "cumulative_catch_up"
        assert queried.description == "Added implementation services"
        assert queried.price_change == Decimal("15000.00")
        assert queried.scope_change == "Added Phase 2 implementation scope"
        assert queried.actor_id == test_actor_id

    def test_defaults_and_nullable(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)

        mod = ContractModificationModel(
            contract_id=contract.id,
            modification_date=date(2024, 8, 1),
            modification_type="prospective",
            description="Minor scope adjustment",
            created_by_id=test_actor_id,
        )
        session.add(mod)
        session.flush()

        queried = session.get(ContractModificationModel, mod.id)
        assert queried.price_change == Decimal("0")
        assert queried.scope_change is None
        assert queried.actor_id is None

    def test_fk_contract_id_invalid(self, session, test_actor_id):
        obj = ContractModificationModel(
            contract_id=uuid4(),
            modification_date=date(2024, 7, 1),
            modification_type="separate_contract",
            description="Orphan modification",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_parent_relationship(self, session, test_actor_id, test_customer_party):
        contract = _make_contract(session, test_actor_id, test_customer_party)

        mod = ContractModificationModel(
            contract_id=contract.id,
            modification_date=date(2024, 9, 1),
            modification_type="termination",
            description="Contract terminated early",
            price_change=Decimal("-20000.00"),
            created_by_id=test_actor_id,
        )
        session.add(mod)
        session.flush()

        queried = session.get(ContractModificationModel, mod.id)
        assert queried.contract is not None
        assert queried.contract.id == contract.id
