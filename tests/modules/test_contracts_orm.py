"""ORM round-trip tests for the Government Contracts module.

Verifies that all four Contracts ORM models can be persisted and queried,
that FK constraints to the kernel ``contracts`` table are enforced, and
that unique constraints reject duplicates.

Models under test:
    - ContractDeliverableModel
    - ContractMilestoneModel
    - ContractBillingModel
    - ContractFundingModel

Note: The parent ``Contract`` and ``ContractLineItem`` models live in the
kernel (``finance_kernel.models.contract``).  This module's models add
finance-specific tracking artifacts for DCAA compliance.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.contracts.orm import (
    ContractBillingModel,
    ContractDeliverableModel,
    ContractFundingModel,
    ContractMilestoneModel,
)
from tests.modules.conftest import TEST_CONTRACT_ID


# ---------------------------------------------------------------------------
# ContractDeliverableModel
# ---------------------------------------------------------------------------


class TestContractDeliverableModelORM:
    """Round-trip persistence tests for ContractDeliverableModel."""

    def test_create_and_query(self, session, test_actor_id, test_contract):
        """Insert a deliverable and read it back -- all fields must match."""
        deliverable = ContractDeliverableModel(
            contract_id=TEST_CONTRACT_ID,
            deliverable_number="CDRL-001",
            title="System Design Document",
            description="Detailed system architecture and design specification",
            status="pending",
            due_date=date(2024, 6, 30),
            submitted_date=None,
            accepted_date=None,
            accepted_by=None,
            rejection_reason=None,
            created_by_id=test_actor_id,
        )
        session.add(deliverable)
        session.flush()

        queried = session.get(ContractDeliverableModel, deliverable.id)
        assert queried is not None
        assert queried.contract_id == TEST_CONTRACT_ID
        assert queried.deliverable_number == "CDRL-001"
        assert queried.title == "System Design Document"
        assert queried.description == "Detailed system architecture and design specification"
        assert queried.status == "pending"
        assert queried.due_date == date(2024, 6, 30)
        assert queried.submitted_date is None
        assert queried.accepted_date is None
        assert queried.accepted_by is None
        assert queried.rejection_reason is None

    def test_submitted_and_accepted(self, session, test_actor_id, test_contract):
        """Deliverable with full lifecycle dates persists correctly."""
        reviewer_id = uuid4()
        deliverable = ContractDeliverableModel(
            contract_id=TEST_CONTRACT_ID,
            deliverable_number="CDRL-002",
            title="Test Plan",
            status="accepted",
            due_date=date(2024, 3, 31),
            submitted_date=date(2024, 3, 20),
            accepted_date=date(2024, 3, 25),
            accepted_by=reviewer_id,
            created_by_id=test_actor_id,
        )
        session.add(deliverable)
        session.flush()

        queried = session.get(ContractDeliverableModel, deliverable.id)
        assert queried.submitted_date == date(2024, 3, 20)
        assert queried.accepted_date == date(2024, 3, 25)
        assert queried.accepted_by == reviewer_id

    def test_unique_contract_deliverable_number(self, session, test_actor_id, test_contract):
        """Duplicate (contract_id, deliverable_number) must raise IntegrityError."""
        for tag in ("first", "duplicate"):
            deliverable = ContractDeliverableModel(
                contract_id=TEST_CONTRACT_ID,
                deliverable_number="CDRL-DUP",
                title=f"Deliverable {tag}",
                status="pending",
                created_by_id=test_actor_id,
            )
            session.add(deliverable)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_fk_contract_id_constraint(self, session, test_actor_id):
        """Nonexistent contract_id must raise IntegrityError."""
        deliverable = ContractDeliverableModel(
            contract_id=uuid4(),
            deliverable_number="CDRL-ORPHAN",
            title="Orphan Deliverable",
            status="pending",
            created_by_id=test_actor_id,
        )
        session.add(deliverable)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# ContractMilestoneModel
# ---------------------------------------------------------------------------


class TestContractMilestoneModelORM:
    """Round-trip persistence tests for ContractMilestoneModel."""

    def test_create_and_query(self, session, test_actor_id, test_contract):
        """Insert a milestone and read it back -- all fields must match."""
        milestone = ContractMilestoneModel(
            contract_id=TEST_CONTRACT_ID,
            name="PDR Complete",
            description="Preliminary Design Review completed",
            amount=Decimal("150000.00"),
            completion_pct=Decimal("0"),
            is_billed=False,
            billed_date=None,
            due_date=date(2024, 9, 30),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(milestone)
        session.flush()

        queried = session.get(ContractMilestoneModel, milestone.id)
        assert queried is not None
        assert queried.contract_id == TEST_CONTRACT_ID
        assert queried.name == "PDR Complete"
        assert queried.description == "Preliminary Design Review completed"
        assert queried.amount == Decimal("150000.00")
        assert queried.completion_pct == Decimal("0")
        assert queried.is_billed is False
        assert queried.billed_date is None
        assert queried.due_date == date(2024, 9, 30)
        assert queried.currency == "USD"

    def test_billed_milestone(self, session, test_actor_id, test_contract):
        """Milestone that has been billed persists with billed_date."""
        milestone = ContractMilestoneModel(
            contract_id=TEST_CONTRACT_ID,
            name="CDR Complete",
            amount=Decimal("250000.00"),
            completion_pct=Decimal("100.00"),
            is_billed=True,
            billed_date=date(2024, 12, 15),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(milestone)
        session.flush()

        queried = session.get(ContractMilestoneModel, milestone.id)
        assert queried.is_billed is True
        assert queried.billed_date == date(2024, 12, 15)
        assert queried.completion_pct == Decimal("100.00")

    def test_unique_contract_milestone_name(self, session, test_actor_id, test_contract):
        """Duplicate (contract_id, name) must raise IntegrityError."""
        for tag in ("first", "duplicate"):
            milestone = ContractMilestoneModel(
                contract_id=TEST_CONTRACT_ID,
                name="Milestone DUP",
                amount=Decimal("1000.00"),
                currency="USD",
                created_by_id=test_actor_id,
            )
            session.add(milestone)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_fk_contract_id_constraint(self, session, test_actor_id):
        """Nonexistent contract_id must raise IntegrityError."""
        milestone = ContractMilestoneModel(
            contract_id=uuid4(),
            name="Orphan Milestone",
            amount=Decimal("0"),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(milestone)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# ContractBillingModel
# ---------------------------------------------------------------------------


class TestContractBillingModelORM:
    """Round-trip persistence tests for ContractBillingModel."""

    def test_create_and_query(self, session, test_actor_id, test_contract):
        """Insert a billing and read it back -- all fields must match."""
        billing = ContractBillingModel(
            contract_id=TEST_CONTRACT_ID,
            billing_number="INV-2024-001",
            billing_type="progress",
            billing_period="2024-Q1",
            billing_date=date(2024, 3, 31),
            direct_costs=Decimal("80000.00"),
            indirect_costs=Decimal("24000.00"),
            fee_amount=Decimal("8320.00"),
            total_amount=Decimal("112320.00"),
            currency="USD",
            status="draft",
            approved_by=None,
            approved_date=None,
            milestone_id=None,
            created_by_id=test_actor_id,
        )
        session.add(billing)
        session.flush()

        queried = session.get(ContractBillingModel, billing.id)
        assert queried is not None
        assert queried.contract_id == TEST_CONTRACT_ID
        assert queried.billing_number == "INV-2024-001"
        assert queried.billing_type == "progress"
        assert queried.billing_period == "2024-Q1"
        assert queried.billing_date == date(2024, 3, 31)
        assert queried.direct_costs == Decimal("80000.00")
        assert queried.indirect_costs == Decimal("24000.00")
        assert queried.fee_amount == Decimal("8320.00")
        assert queried.total_amount == Decimal("112320.00")
        assert queried.currency == "USD"
        assert queried.status == "draft"
        assert queried.approved_by is None
        assert queried.approved_date is None
        assert queried.milestone_id is None

    def test_approved_billing(self, session, test_actor_id, test_contract):
        """Approved billing with approver and date persists correctly."""
        approver_id = uuid4()
        billing = ContractBillingModel(
            contract_id=TEST_CONTRACT_ID,
            billing_number="INV-2024-002",
            billing_type="cost_voucher",
            billing_period="2024-Q2",
            billing_date=date(2024, 6, 30),
            total_amount=Decimal("55000.00"),
            currency="USD",
            status="approved",
            approved_by=approver_id,
            approved_date=date(2024, 7, 5),
            created_by_id=test_actor_id,
        )
        session.add(billing)
        session.flush()

        queried = session.get(ContractBillingModel, billing.id)
        assert queried.status == "approved"
        assert queried.approved_by == approver_id
        assert queried.approved_date == date(2024, 7, 5)

    def test_billing_with_milestone_fk(self, session, test_actor_id, test_contract):
        """Billing linked to an existing milestone loads via FK."""
        milestone = ContractMilestoneModel(
            contract_id=TEST_CONTRACT_ID,
            name="Milestone for Billing",
            amount=Decimal("200000.00"),
            currency="USD",
            created_by_id=test_actor_id,
        )
        session.add(milestone)
        session.flush()

        billing = ContractBillingModel(
            contract_id=TEST_CONTRACT_ID,
            billing_number="INV-2024-003",
            billing_type="milestone",
            billing_period="2024-Q3",
            billing_date=date(2024, 9, 30),
            total_amount=Decimal("200000.00"),
            currency="USD",
            status="draft",
            milestone_id=milestone.id,
            created_by_id=test_actor_id,
        )
        session.add(billing)
        session.flush()

        queried = session.get(ContractBillingModel, billing.id)
        assert queried.milestone_id == milestone.id

    def test_fk_contract_id_constraint(self, session, test_actor_id):
        """Nonexistent contract_id must raise IntegrityError."""
        billing = ContractBillingModel(
            contract_id=uuid4(),
            billing_number="INV-ORPHAN-001",
            billing_type="progress",
            billing_period="2024-Q1",
            billing_date=date(2024, 3, 31),
            total_amount=Decimal("0"),
            currency="USD",
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(billing)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_milestone_id_constraint(self, session, test_actor_id, test_contract):
        """Nonexistent milestone_id must raise IntegrityError."""
        billing = ContractBillingModel(
            contract_id=TEST_CONTRACT_ID,
            billing_number="INV-BADFK-001",
            billing_type="milestone",
            billing_period="2024-Q4",
            billing_date=date(2024, 12, 31),
            total_amount=Decimal("0"),
            currency="USD",
            status="draft",
            milestone_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(billing)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ---------------------------------------------------------------------------
# ContractFundingModel
# ---------------------------------------------------------------------------


class TestContractFundingModelORM:
    """Round-trip persistence tests for ContractFundingModel."""

    def test_create_and_query(self, session, test_actor_id, test_contract):
        """Insert a funding action and read it back -- all fields must match."""
        authorizer_id = uuid4()
        funding = ContractFundingModel(
            contract_id=TEST_CONTRACT_ID,
            funding_action_number="MOD-001",
            funding_type="initial",
            amount=Decimal("500000.00"),
            cumulative_funded=Decimal("500000.00"),
            currency="USD",
            effective_date=date(2024, 1, 1),
            modification_number="P00001",
            document_reference="FA8750-24-F-0001",
            authorized_by=authorizer_id,
            created_by_id=test_actor_id,
        )
        session.add(funding)
        session.flush()

        queried = session.get(ContractFundingModel, funding.id)
        assert queried is not None
        assert queried.contract_id == TEST_CONTRACT_ID
        assert queried.funding_action_number == "MOD-001"
        assert queried.funding_type == "initial"
        assert queried.amount == Decimal("500000.00")
        assert queried.cumulative_funded == Decimal("500000.00")
        assert queried.currency == "USD"
        assert queried.effective_date == date(2024, 1, 1)
        assert queried.modification_number == "P00001"
        assert queried.document_reference == "FA8750-24-F-0001"
        assert queried.authorized_by == authorizer_id

    def test_nullable_optional_fields(self, session, test_actor_id, test_contract):
        """Funding with optional fields set to None persists correctly."""
        funding = ContractFundingModel(
            contract_id=TEST_CONTRACT_ID,
            funding_action_number="MOD-002",
            funding_type="incremental",
            amount=Decimal("100000.00"),
            cumulative_funded=Decimal("600000.00"),
            currency="USD",
            effective_date=date(2024, 7, 1),
            modification_number=None,
            document_reference=None,
            authorized_by=None,
            created_by_id=test_actor_id,
        )
        session.add(funding)
        session.flush()

        queried = session.get(ContractFundingModel, funding.id)
        assert queried.modification_number is None
        assert queried.document_reference is None
        assert queried.authorized_by is None

    def test_unique_contract_funding_action(self, session, test_actor_id, test_contract):
        """Duplicate (contract_id, funding_action_number) must raise IntegrityError."""
        for tag in ("first", "duplicate"):
            funding = ContractFundingModel(
                contract_id=TEST_CONTRACT_ID,
                funding_action_number="MOD-DUP",
                funding_type="incremental",
                amount=Decimal("50000.00"),
                cumulative_funded=Decimal("50000.00"),
                currency="USD",
                effective_date=date(2024, 8, 1),
                created_by_id=test_actor_id,
            )
            session.add(funding)
            if tag == "first":
                session.flush()
            else:
                with pytest.raises(IntegrityError):
                    session.flush()
                session.rollback()

    def test_fk_contract_id_constraint(self, session, test_actor_id):
        """Nonexistent contract_id must raise IntegrityError."""
        funding = ContractFundingModel(
            contract_id=uuid4(),
            funding_action_number="MOD-ORPHAN",
            funding_type="initial",
            amount=Decimal("0"),
            cumulative_funded=Decimal("0"),
            currency="USD",
            effective_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(funding)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
