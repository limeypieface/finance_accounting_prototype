"""ORM round-trip tests for the Intercompany module.

Covers:
- IntercompanyAgreementModel
- IntercompanyTransactionModel
- IntercompanySettlementModel

Tests verify persistence round-trips (create, flush, query), parent-child
relationships, FK constraints, and unique constraints.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.intercompany.orm import (
    IntercompanyAgreementModel,
    IntercompanyTransactionModel,
    IntercompanySettlementModel,
)


# ---------------------------------------------------------------------------
# IntercompanyAgreementModel
# ---------------------------------------------------------------------------


class TestIntercompanyAgreementModelORM:
    """Round-trip persistence tests for IntercompanyAgreementModel."""

    def test_create_and_query(self, session, test_actor_id):
        agreement = IntercompanyAgreementModel(
            entity_a="ENTITY-US",
            entity_b="ENTITY-UK",
            agreement_type="transfer",
            markup_rate=Decimal("0.05"),
            currency="USD",
            effective_from=date(2024, 1, 1),
            effective_to=date(2025, 12, 31),
            created_by_id=test_actor_id,
        )
        session.add(agreement)
        session.flush()

        queried = session.get(IntercompanyAgreementModel, agreement.id)
        assert queried is not None
        assert queried.entity_a == "ENTITY-US"
        assert queried.entity_b == "ENTITY-UK"
        assert queried.agreement_type == "transfer"
        assert queried.markup_rate == Decimal("0.05")
        assert queried.currency == "USD"
        assert queried.effective_from == date(2024, 1, 1)
        assert queried.effective_to == date(2025, 12, 31)

    def test_create_with_defaults(self, session, test_actor_id):
        agreement = IntercompanyAgreementModel(
            entity_a="ENTITY-DE",
            entity_b="ENTITY-FR",
            effective_from=date(2024, 6, 1),
            created_by_id=test_actor_id,
        )
        session.add(agreement)
        session.flush()

        queried = session.get(IntercompanyAgreementModel, agreement.id)
        assert queried is not None
        assert queried.agreement_type == "transfer"
        assert queried.markup_rate == Decimal("0")
        assert queried.currency == "USD"
        assert queried.effective_to is None

    def test_unique_constraint_entity_pair_type(self, session, test_actor_id):
        """(entity_a, entity_b, agreement_type) must be unique."""
        agreement1 = IntercompanyAgreementModel(
            entity_a="ENTITY-ALPHA",
            entity_b="ENTITY-BETA",
            agreement_type="service",
            effective_from=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(agreement1)
        session.flush()

        agreement2 = IntercompanyAgreementModel(
            entity_a="ENTITY-ALPHA",
            entity_b="ENTITY-BETA",
            agreement_type="service",
            effective_from=date(2025, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(agreement2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_transactions_relationship(self, session, test_actor_id):
        """Verify parent agreement loads child transactions via relationship."""
        agreement = IntercompanyAgreementModel(
            entity_a="ENTITY-P1",
            entity_b="ENTITY-P2",
            agreement_type="transfer",
            effective_from=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(agreement)
        session.flush()

        txn = IntercompanyTransactionModel(
            agreement_id=agreement.id,
            from_entity="ENTITY-P1",
            to_entity="ENTITY-P2",
            amount=Decimal("5000.00"),
            currency="USD",
            transaction_date=date(2024, 3, 15),
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        # Expire to force reload
        session.expire(agreement)
        loaded = session.get(IntercompanyAgreementModel, agreement.id)
        assert len(loaded.transactions) == 1
        assert loaded.transactions[0].amount == Decimal("5000.00")


# ---------------------------------------------------------------------------
# IntercompanyTransactionModel
# ---------------------------------------------------------------------------


class TestIntercompanyTransactionModelORM:
    """Round-trip persistence tests for IntercompanyTransactionModel."""

    def test_create_and_query(self, session, test_actor_id):
        txn = IntercompanyTransactionModel(
            from_entity="ENTITY-US",
            to_entity="ENTITY-UK",
            amount=Decimal("25000.50"),
            currency="GBP",
            transaction_date=date(2024, 7, 1),
            description="Quarterly management fee",
            status="posted",
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        queried = session.get(IntercompanyTransactionModel, txn.id)
        assert queried is not None
        assert queried.from_entity == "ENTITY-US"
        assert queried.to_entity == "ENTITY-UK"
        assert queried.amount == Decimal("25000.50")
        assert queried.currency == "GBP"
        assert queried.transaction_date == date(2024, 7, 1)
        assert queried.description == "Quarterly management fee"
        assert queried.status == "posted"
        assert queried.agreement_id is None

    def test_create_with_agreement_fk(self, session, test_actor_id):
        """Transaction can reference an existing agreement."""
        agreement = IntercompanyAgreementModel(
            entity_a="ENTITY-X",
            entity_b="ENTITY-Y",
            agreement_type="licensing",
            effective_from=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(agreement)
        session.flush()

        txn = IntercompanyTransactionModel(
            agreement_id=agreement.id,
            from_entity="ENTITY-X",
            to_entity="ENTITY-Y",
            amount=Decimal("10000.00"),
            currency="USD",
            transaction_date=date(2024, 4, 1),
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        queried = session.get(IntercompanyTransactionModel, txn.id)
        assert queried.agreement_id == agreement.id
        assert queried.agreement is not None
        assert queried.agreement.entity_a == "ENTITY-X"

    def test_fk_constraint_agreement_id(self, session, test_actor_id):
        """Referencing a nonexistent agreement_id raises IntegrityError."""
        txn = IntercompanyTransactionModel(
            agreement_id=uuid4(),
            from_entity="ENTITY-A",
            to_entity="ENTITY-B",
            amount=Decimal("1000.00"),
            currency="USD",
            transaction_date=date(2024, 1, 1),
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(txn)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_create_with_defaults(self, session, test_actor_id):
        txn = IntercompanyTransactionModel(
            from_entity="ENTITY-C",
            to_entity="ENTITY-D",
            transaction_date=date(2024, 5, 1),
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        queried = session.get(IntercompanyTransactionModel, txn.id)
        assert queried.amount == Decimal("0")
        assert queried.currency == "USD"
        assert queried.status == "pending"
        assert queried.description is None
        assert queried.source_event_id is None

    def test_create_with_source_event_id(self, session, test_actor_id):
        event_id = uuid4()
        txn = IntercompanyTransactionModel(
            from_entity="ENTITY-E",
            to_entity="ENTITY-F",
            amount=Decimal("7500.00"),
            currency="EUR",
            transaction_date=date(2024, 8, 15),
            source_event_id=event_id,
            status="posted",
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        queried = session.get(IntercompanyTransactionModel, txn.id)
        assert queried.source_event_id == event_id


# ---------------------------------------------------------------------------
# IntercompanySettlementModel
# ---------------------------------------------------------------------------


class TestIntercompanySettlementModelORM:
    """Round-trip persistence tests for IntercompanySettlementModel."""

    def test_create_and_query(self, session, test_actor_id):
        settled_by_id = uuid4()
        settlement = IntercompanySettlementModel(
            from_entity="ENTITY-US",
            to_entity="ENTITY-UK",
            amount=Decimal("50000.00"),
            currency="USD",
            settlement_date=date(2024, 6, 30),
            settlement_reference="SETTLE-2024-001",
            status="completed",
            payment_method="wire",
            period="2024-06",
            settled_by=settled_by_id,
            created_by_id=test_actor_id,
        )
        session.add(settlement)
        session.flush()

        queried = session.get(IntercompanySettlementModel, settlement.id)
        assert queried is not None
        assert queried.from_entity == "ENTITY-US"
        assert queried.to_entity == "ENTITY-UK"
        assert queried.amount == Decimal("50000.00")
        assert queried.currency == "USD"
        assert queried.settlement_date == date(2024, 6, 30)
        assert queried.settlement_reference == "SETTLE-2024-001"
        assert queried.status == "completed"
        assert queried.payment_method == "wire"
        assert queried.period == "2024-06"
        assert queried.settled_by == settled_by_id

    def test_create_with_defaults(self, session, test_actor_id):
        settlement = IntercompanySettlementModel(
            from_entity="ENTITY-DE",
            to_entity="ENTITY-FR",
            amount=Decimal("12345.67"),
            settlement_date=date(2024, 9, 30),
            period="2024-09",
            created_by_id=test_actor_id,
        )
        session.add(settlement)
        session.flush()

        queried = session.get(IntercompanySettlementModel, settlement.id)
        assert queried.currency == "USD"
        assert queried.status == "pending"
        assert queried.settlement_reference is None
        assert queried.payment_method is None
        assert queried.settled_by is None

    def test_multiple_settlements_same_entities(self, session, test_actor_id):
        """Multiple settlements between same entity pair are allowed."""
        for i in range(3):
            settlement = IntercompanySettlementModel(
                from_entity="ENTITY-MULTI-A",
                to_entity="ENTITY-MULTI-B",
                amount=Decimal(str(1000 * (i + 1))),
                settlement_date=date(2024, i + 1, 15),
                period=f"2024-{i + 1:02d}",
                created_by_id=test_actor_id,
            )
            session.add(settlement)

        session.flush()

        from sqlalchemy import select
        stmt = select(IntercompanySettlementModel).where(
            IntercompanySettlementModel.from_entity == "ENTITY-MULTI-A"
        )
        results = session.execute(stmt).scalars().all()
        assert len(results) == 3
