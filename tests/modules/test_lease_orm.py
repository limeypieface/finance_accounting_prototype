"""ORM round-trip tests for Lease module (ASC 842).

Covers all five models:
    - LeaseModel
    - LeasePaymentModel
    - ROUAssetModel
    - LeaseLiabilityModel
    - LeaseModificationModel
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.lease.orm import (
    LeaseModel,
    LeaseModificationModel,
    LeasePaymentModel,
    LeaseLiabilityModel,
    ROUAssetModel,
)
from tests.modules.conftest import TEST_LESSEE_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(session, test_actor_id, test_lessee_party, **overrides):
    """Create and flush a LeaseModel with sensible defaults."""
    fields = dict(
        lease_number=f"LEASE-{uuid4().hex[:8]}",
        lessee_id=TEST_LESSEE_ID,
        lessor_name="Acme Leasing Corp",
        commencement_date=date(2024, 1, 1),
        end_date=date(2028, 12, 31),
        classification="finance",
        status="active",
        monthly_payment=Decimal("5000.00"),
        discount_rate=Decimal("0.05"),
        currency="USD",
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    obj = LeaseModel(**fields)
    session.add(obj)
    session.flush()
    return obj


# ==========================================================================
# LeaseModel
# ==========================================================================


class TestLeaseModelORM:
    """Round-trip persistence tests for LeaseModel."""

    def test_create_and_query(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        queried = session.get(LeaseModel, lease.id)
        assert queried is not None
        assert queried.lessee_id == TEST_LESSEE_ID
        assert queried.lessor_name == "Acme Leasing Corp"
        assert queried.commencement_date == date(2024, 1, 1)
        assert queried.end_date == date(2028, 12, 31)
        assert queried.classification == "finance"
        assert queried.status == "active"
        assert queried.monthly_payment == Decimal("5000.00")
        assert queried.discount_rate == Decimal("0.05")
        assert queried.currency == "USD"

    def test_unique_lease_number(self, session, test_actor_id, test_lessee_party):
        number = f"LEASE-UNIQUE-{uuid4().hex[:6]}"
        _make_lease(
            session, test_actor_id, test_lessee_party,
            lease_number=number,
        )
        dup = LeaseModel(
            lease_number=number,
            lessee_id=TEST_LESSEE_ID,
            lessor_name="Another Lessor",
            commencement_date=date(2025, 1, 1),
            end_date=date(2029, 12, 31),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_lessee_id_invalid(self, session, test_actor_id):
        obj = LeaseModel(
            lease_number=f"LEASE-BAD-{uuid4().hex[:6]}",
            lessee_id=uuid4(),
            lessor_name="Ghost Lessor",
            commencement_date=date(2024, 1, 1),
            end_date=date(2028, 12, 31),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_lessee_party):
        obj = LeaseModel(
            lease_number=f"LEASE-DEF-{uuid4().hex[:6]}",
            lessee_id=TEST_LESSEE_ID,
            lessor_name="Default Lessor",
            commencement_date=date(2024, 1, 1),
            end_date=date(2028, 12, 31),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(LeaseModel, obj.id)
        assert queried.classification == "operating"
        assert queried.status == "draft"
        assert queried.monthly_payment == Decimal("0")
        assert queried.discount_rate == Decimal("0.05")
        assert queried.currency == "USD"

    def test_child_relationships_load(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        payment = LeasePaymentModel(
            lease_id=lease.id,
            payment_date=date(2024, 2, 1),
            amount=Decimal("5000.00"),
            payment_number=1,
            created_by_id=test_actor_id,
        )
        session.add(payment)
        session.flush()

        session.expire(lease)
        queried = session.get(LeaseModel, lease.id)
        assert len(queried.payments) == 1
        assert queried.payments[0].amount == Decimal("5000.00")


# ==========================================================================
# LeasePaymentModel
# ==========================================================================


class TestLeasePaymentModelORM:
    """Round-trip persistence tests for LeasePaymentModel."""

    def test_create_and_query(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        pmt = LeasePaymentModel(
            lease_id=lease.id,
            payment_date=date(2024, 2, 1),
            amount=Decimal("5000.00"),
            principal_portion=Decimal("3500.00"),
            interest_portion=Decimal("1500.00"),
            payment_number=1,
            created_by_id=test_actor_id,
        )
        session.add(pmt)
        session.flush()

        queried = session.get(LeasePaymentModel, pmt.id)
        assert queried is not None
        assert queried.lease_id == lease.id
        assert queried.payment_date == date(2024, 2, 1)
        assert queried.amount == Decimal("5000.00")
        assert queried.principal_portion == Decimal("3500.00")
        assert queried.interest_portion == Decimal("1500.00")
        assert queried.payment_number == 1

    def test_unique_lease_payment_number(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        first = LeasePaymentModel(
            lease_id=lease.id,
            payment_date=date(2024, 2, 1),
            amount=Decimal("5000.00"),
            payment_number=1,
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        dup = LeasePaymentModel(
            lease_id=lease.id,
            payment_date=date(2024, 3, 1),
            amount=Decimal("5000.00"),
            payment_number=1,  # same payment_number for same lease
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_lease_id_invalid(self, session, test_actor_id):
        obj = LeasePaymentModel(
            lease_id=uuid4(),
            payment_date=date(2024, 2, 1),
            amount=Decimal("5000.00"),
            payment_number=1,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        pmt = LeasePaymentModel(
            lease_id=lease.id,
            payment_date=date(2024, 2, 1),
            amount=Decimal("5000.00"),
            created_by_id=test_actor_id,
        )
        session.add(pmt)
        session.flush()

        queried = session.get(LeasePaymentModel, pmt.id)
        assert queried.principal_portion == Decimal("0")
        assert queried.interest_portion == Decimal("0")
        assert queried.payment_number == 0

    def test_parent_relationship(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        pmt = LeasePaymentModel(
            lease_id=lease.id,
            payment_date=date(2024, 2, 1),
            amount=Decimal("5000.00"),
            payment_number=1,
            created_by_id=test_actor_id,
        )
        session.add(pmt)
        session.flush()

        queried = session.get(LeasePaymentModel, pmt.id)
        assert queried.lease is not None
        assert queried.lease.id == lease.id


# ==========================================================================
# ROUAssetModel
# ==========================================================================


class TestROUAssetModelORM:
    """Round-trip persistence tests for ROUAssetModel."""

    def test_create_and_query(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        rou = ROUAssetModel(
            lease_id=lease.id,
            initial_value=Decimal("240000.00"),
            accumulated_amortization=Decimal("20000.00"),
            carrying_value=Decimal("220000.00"),
            commencement_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(rou)
        session.flush()

        queried = session.get(ROUAssetModel, rou.id)
        assert queried is not None
        assert queried.lease_id == lease.id
        assert queried.initial_value == Decimal("240000.00")
        assert queried.accumulated_amortization == Decimal("20000.00")
        assert queried.carrying_value == Decimal("220000.00")
        assert queried.commencement_date == date(2024, 1, 1)

    def test_unique_one_rou_per_lease(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        first = ROUAssetModel(
            lease_id=lease.id,
            initial_value=Decimal("240000.00"),
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        dup = ROUAssetModel(
            lease_id=lease.id,
            initial_value=Decimal("300000.00"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_lease_id_invalid(self, session, test_actor_id):
        obj = ROUAssetModel(
            lease_id=uuid4(),
            initial_value=Decimal("240000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        rou = ROUAssetModel(
            lease_id=lease.id,
            initial_value=Decimal("240000.00"),
            created_by_id=test_actor_id,
        )
        session.add(rou)
        session.flush()

        queried = session.get(ROUAssetModel, rou.id)
        assert queried.accumulated_amortization == Decimal("0")
        assert queried.carrying_value == Decimal("0")
        assert queried.commencement_date is None

    def test_parent_relationship(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        rou = ROUAssetModel(
            lease_id=lease.id,
            initial_value=Decimal("240000.00"),
            created_by_id=test_actor_id,
        )
        session.add(rou)
        session.flush()

        queried = session.get(ROUAssetModel, rou.id)
        assert queried.lease is not None
        assert queried.lease.id == lease.id

    def test_one_to_one_via_parent(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        rou = ROUAssetModel(
            lease_id=lease.id,
            initial_value=Decimal("240000.00"),
            carrying_value=Decimal("240000.00"),
            created_by_id=test_actor_id,
        )
        session.add(rou)
        session.flush()

        session.expire(lease)
        queried = session.get(LeaseModel, lease.id)
        assert queried.rou_asset is not None
        assert queried.rou_asset.initial_value == Decimal("240000.00")


# ==========================================================================
# LeaseLiabilityModel
# ==========================================================================


class TestLeaseLiabilityModelORM:
    """Round-trip persistence tests for LeaseLiabilityModel."""

    def test_create_and_query(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        liab = LeaseLiabilityModel(
            lease_id=lease.id,
            initial_value=Decimal("230000.00"),
            current_balance=Decimal("215000.00"),
            commencement_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(liab)
        session.flush()

        queried = session.get(LeaseLiabilityModel, liab.id)
        assert queried is not None
        assert queried.lease_id == lease.id
        assert queried.initial_value == Decimal("230000.00")
        assert queried.current_balance == Decimal("215000.00")
        assert queried.commencement_date == date(2024, 1, 1)

    def test_unique_one_liability_per_lease(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        first = LeaseLiabilityModel(
            lease_id=lease.id,
            initial_value=Decimal("230000.00"),
            created_by_id=test_actor_id,
        )
        session.add(first)
        session.flush()

        dup = LeaseLiabilityModel(
            lease_id=lease.id,
            initial_value=Decimal("250000.00"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_lease_id_invalid(self, session, test_actor_id):
        obj = LeaseLiabilityModel(
            lease_id=uuid4(),
            initial_value=Decimal("230000.00"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_defaults(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        liab = LeaseLiabilityModel(
            lease_id=lease.id,
            initial_value=Decimal("230000.00"),
            created_by_id=test_actor_id,
        )
        session.add(liab)
        session.flush()

        queried = session.get(LeaseLiabilityModel, liab.id)
        assert queried.current_balance == Decimal("0")
        assert queried.commencement_date is None

    def test_parent_relationship(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        liab = LeaseLiabilityModel(
            lease_id=lease.id,
            initial_value=Decimal("230000.00"),
            created_by_id=test_actor_id,
        )
        session.add(liab)
        session.flush()

        queried = session.get(LeaseLiabilityModel, liab.id)
        assert queried.lease is not None
        assert queried.lease.id == lease.id

    def test_one_to_one_via_parent(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        liab = LeaseLiabilityModel(
            lease_id=lease.id,
            initial_value=Decimal("230000.00"),
            current_balance=Decimal("230000.00"),
            created_by_id=test_actor_id,
        )
        session.add(liab)
        session.flush()

        session.expire(lease)
        queried = session.get(LeaseModel, lease.id)
        assert queried.liability is not None
        assert queried.liability.initial_value == Decimal("230000.00")


# ==========================================================================
# LeaseModificationModel
# ==========================================================================


class TestLeaseModificationModelORM:
    """Round-trip persistence tests for LeaseModificationModel."""

    def test_create_and_query(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        mod = LeaseModificationModel(
            lease_id=lease.id,
            modification_date=date(2025, 7, 1),
            description="Extended lease term by 2 years",
            new_monthly_payment=Decimal("5500.00"),
            new_end_date=date(2030, 12, 31),
            remeasurement_amount=Decimal("45000.00"),
            actor_id=test_actor_id,
            created_by_id=test_actor_id,
        )
        session.add(mod)
        session.flush()

        queried = session.get(LeaseModificationModel, mod.id)
        assert queried is not None
        assert queried.lease_id == lease.id
        assert queried.modification_date == date(2025, 7, 1)
        assert queried.description == "Extended lease term by 2 years"
        assert queried.new_monthly_payment == Decimal("5500.00")
        assert queried.new_end_date == date(2030, 12, 31)
        assert queried.remeasurement_amount == Decimal("45000.00")
        assert queried.actor_id == test_actor_id

    def test_defaults_and_nullable(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        mod = LeaseModificationModel(
            lease_id=lease.id,
            modification_date=date(2025, 1, 1),
            description="Minor lease amendment",
            created_by_id=test_actor_id,
        )
        session.add(mod)
        session.flush()

        queried = session.get(LeaseModificationModel, mod.id)
        assert queried.new_monthly_payment is None
        assert queried.new_end_date is None
        assert queried.remeasurement_amount == Decimal("0")
        assert queried.actor_id is None

    def test_fk_lease_id_invalid(self, session, test_actor_id):
        obj = LeaseModificationModel(
            lease_id=uuid4(),
            modification_date=date(2025, 7, 1),
            description="Orphan modification",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_parent_relationship(self, session, test_actor_id, test_lessee_party):
        lease = _make_lease(session, test_actor_id, test_lessee_party)

        mod = LeaseModificationModel(
            lease_id=lease.id,
            modification_date=date(2025, 10, 1),
            description="Payment renegotiation",
            new_monthly_payment=Decimal("4500.00"),
            remeasurement_amount=Decimal("-12000.00"),
            created_by_id=test_actor_id,
        )
        session.add(mod)
        session.flush()

        queried = session.get(LeaseModificationModel, mod.id)
        assert queried.lease is not None
        assert queried.lease.id == lease.id
