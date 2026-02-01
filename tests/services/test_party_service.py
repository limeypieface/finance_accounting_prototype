"""
Tests for Party model and PartyService.

Covers:
- Party creation with all types
- Status transitions (freeze, unfreeze, close)
- Credit limit validation
- Transaction eligibility checks
- Query operations
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.exceptions import (
    PartyFrozenError,
    PartyInactiveError,
    PartyNotFoundError,
)
from finance_kernel.models.party import Party, PartyStatus, PartyType
from finance_kernel.services.party_service import PartyInfo, PartyService


class TestPartyModel:
    """Tests for Party ORM model."""

    def test_create_customer(self, session, test_actor_id):
        """Creates a customer party."""
        party = Party(
            party_code="CUST-001",
            party_type=PartyType.CUSTOMER,
            name="Acme Corp",
            credit_limit=Decimal("50000.00"),
            credit_currency="USD",
            payment_terms_days=30,
            created_by_id=test_actor_id,
        )
        session.add(party)
        session.flush()

        assert party.id is not None
        assert party.party_code == "CUST-001"
        assert party.party_type == PartyType.CUSTOMER
        assert party.status == PartyStatus.ACTIVE
        assert party.is_active is True
        assert party.credit_limit == Decimal("50000.00")

    def test_create_supplier(self, session, test_actor_id):
        """Creates a supplier party."""
        party = Party(
            party_code="SUPP-001",
            party_type=PartyType.SUPPLIER,
            name="Parts Inc",
            payment_terms_days=45,
            default_currency="EUR",
            created_by_id=test_actor_id,
        )
        session.add(party)
        session.flush()

        assert party.party_type == PartyType.SUPPLIER
        assert party.payment_terms_days == 45

    def test_create_employee(self, session, test_actor_id):
        """Creates an employee party."""
        party = Party(
            party_code="EMP-001",
            party_type=PartyType.EMPLOYEE,
            name="John Smith",
            tax_id="123-45-6789",
            created_by_id=test_actor_id,
        )
        session.add(party)
        session.flush()

        assert party.party_type == PartyType.EMPLOYEE
        assert party.tax_id == "123-45-6789"

    def test_create_intercompany(self, session, test_actor_id):
        """Creates an intercompany party."""
        party = Party(
            party_code="IC-UK-001",
            party_type=PartyType.INTERCOMPANY,
            name="UK Subsidiary Ltd",
            external_ref="IC-UK-ORACLE-123",
            created_by_id=test_actor_id,
        )
        session.add(party)
        session.flush()

        assert party.party_type == PartyType.INTERCOMPANY
        assert party.external_ref == "IC-UK-ORACLE-123"

    def test_is_frozen_property(self, session, test_actor_id):
        """is_frozen property reflects status."""
        party = Party(
            party_code="CUST-002",
            party_type=PartyType.CUSTOMER,
            name="Test Corp",
            created_by_id=test_actor_id,
        )
        session.add(party)
        session.flush()

        assert party.is_frozen is False

        party.status = PartyStatus.FROZEN
        session.flush()

        assert party.is_frozen is True

    def test_can_transact_property(self, session, test_actor_id):
        """can_transact reflects status and active flag."""
        party = Party(
            party_code="CUST-003",
            party_type=PartyType.CUSTOMER,
            name="Test Corp",
            created_by_id=test_actor_id,
        )
        session.add(party)
        session.flush()

        assert party.can_transact is True

        # Frozen party cannot transact
        party.status = PartyStatus.FROZEN
        assert party.can_transact is False

        # Reactivate but deactivate
        party.status = PartyStatus.ACTIVE
        party.is_active = False
        assert party.can_transact is False


class TestPartyService:
    """Tests for PartyService."""

    def test_create_party(self, session, test_actor_id):
        """Creates a party via service."""
        service = PartyService(session)

        party = service.create_party(
            party_code="CUST-100",
            party_type=PartyType.CUSTOMER,
            name="Test Customer",
            actor_id=test_actor_id,
            credit_limit=Decimal("10000.00"),
            credit_currency="USD",
            payment_terms_days=30,
        )

        assert isinstance(party, PartyInfo)
        assert party.party_code == "CUST-100"
        assert party.party_type == PartyType.CUSTOMER
        assert party.credit_limit == Decimal("10000.00")

    def test_get_by_id(self, session, test_actor_id):
        """Retrieves party by ID."""
        service = PartyService(session)

        created = service.create_party(
            party_code="CUST-101",
            party_type=PartyType.CUSTOMER,
            name="Test Customer",
            actor_id=test_actor_id,
        )

        retrieved = service.get_by_id(created.id)

        assert retrieved.id == created.id
        assert retrieved.party_code == "CUST-101"

    def test_get_by_id_not_found(self, session):
        """Raises PartyNotFoundError for unknown ID."""
        service = PartyService(session)

        with pytest.raises(PartyNotFoundError):
            service.get_by_id(uuid4())

    def test_get_by_code(self, session, test_actor_id):
        """Retrieves party by code."""
        service = PartyService(session)

        service.create_party(
            party_code="SUPP-101",
            party_type=PartyType.SUPPLIER,
            name="Test Supplier",
            actor_id=test_actor_id,
        )

        party = service.get_by_code("SUPP-101")

        assert party.party_code == "SUPP-101"

    def test_get_by_code_not_found(self, session):
        """Raises PartyNotFoundError for unknown code."""
        service = PartyService(session)

        with pytest.raises(PartyNotFoundError):
            service.get_by_code("NONEXISTENT")

    def test_find_by_code_returns_none(self, session):
        """find_by_code returns None for unknown code."""
        service = PartyService(session)

        result = service.find_by_code("NONEXISTENT")

        assert result is None

    def test_find_by_code_returns_party(self, session, test_actor_id):
        """find_by_code returns party when found."""
        service = PartyService(session)

        service.create_party(
            party_code="CUST-102",
            party_type=PartyType.CUSTOMER,
            name="Test",
            actor_id=test_actor_id,
        )

        result = service.find_by_code("CUST-102")

        assert result is not None
        assert result.party_code == "CUST-102"

    def test_list_by_type(self, session, test_actor_id):
        """Lists parties by type."""
        service = PartyService(session)

        service.create_party("CUST-200", PartyType.CUSTOMER, "Customer A", test_actor_id)
        service.create_party("CUST-201", PartyType.CUSTOMER, "Customer B", test_actor_id)
        service.create_party("SUPP-200", PartyType.SUPPLIER, "Supplier A", test_actor_id)

        customers = service.list_by_type(PartyType.CUSTOMER)
        suppliers = service.list_by_type(PartyType.SUPPLIER)

        assert len(customers) == 2
        assert len(suppliers) == 1

    def test_list_by_type_active_only(self, session, test_actor_id):
        """list_by_type filters inactive parties by default."""
        service = PartyService(session)

        active = service.create_party("CUST-300", PartyType.CUSTOMER, "Active", test_actor_id)
        inactive = service.create_party("CUST-301", PartyType.CUSTOMER, "Inactive", test_actor_id)
        service.deactivate_party(inactive.id)

        # Default: active only
        customers = service.list_by_type(PartyType.CUSTOMER, active_only=True)
        assert len(customers) == 1

        # Include inactive
        all_customers = service.list_by_type(PartyType.CUSTOMER, active_only=False)
        assert len(all_customers) == 2


class TestPartyStatusTransitions:
    """Tests for party status transitions."""

    def test_freeze_party(self, session, test_actor_id):
        """Freezes a party."""
        service = PartyService(session)

        party = service.create_party("CUST-400", PartyType.CUSTOMER, "Test", test_actor_id)
        assert party.status == PartyStatus.ACTIVE

        frozen = service.freeze_party(party.id)

        assert frozen.status == PartyStatus.FROZEN
        assert frozen.is_frozen is True
        assert frozen.can_transact is False

    def test_unfreeze_party(self, session, test_actor_id):
        """Unfreezes a party."""
        service = PartyService(session)

        party = service.create_party("CUST-401", PartyType.CUSTOMER, "Test", test_actor_id)
        service.freeze_party(party.id)

        unfrozen = service.unfreeze_party(party.id)

        assert unfrozen.status == PartyStatus.ACTIVE
        assert unfrozen.is_frozen is False
        assert unfrozen.can_transact is True

    def test_deactivate_party(self, session, test_actor_id):
        """Deactivates a party."""
        service = PartyService(session)

        party = service.create_party("CUST-402", PartyType.CUSTOMER, "Test", test_actor_id)

        deactivated = service.deactivate_party(party.id)

        assert deactivated.is_active is False
        assert deactivated.can_transact is False

    def test_reactivate_party(self, session, test_actor_id):
        """Reactivates a deactivated party."""
        service = PartyService(session)

        party = service.create_party("CUST-403", PartyType.CUSTOMER, "Test", test_actor_id)
        service.deactivate_party(party.id)

        reactivated = service.reactivate_party(party.id)

        assert reactivated.is_active is True
        assert reactivated.can_transact is True

    def test_close_party(self, session, test_actor_id):
        """Closes a party permanently."""
        service = PartyService(session)

        party = service.create_party("CUST-404", PartyType.CUSTOMER, "Test", test_actor_id)

        closed = service.close_party(party.id)

        assert closed.status == PartyStatus.CLOSED
        assert closed.is_active is False
        assert closed.can_transact is False


class TestPartyValidation:
    """Tests for party validation methods."""

    def test_validate_can_transact_success(self, session, test_actor_id):
        """Validates active party can transact."""
        service = PartyService(session)

        service.create_party("CUST-500", PartyType.CUSTOMER, "Test", test_actor_id)

        result = service.validate_can_transact("CUST-500")

        assert result.can_transact is True

    def test_validate_can_transact_frozen(self, session, test_actor_id):
        """Raises PartyFrozenError for frozen party."""
        service = PartyService(session)

        party = service.create_party("CUST-501", PartyType.CUSTOMER, "Test", test_actor_id)
        service.freeze_party(party.id)

        with pytest.raises(PartyFrozenError) as exc_info:
            service.validate_can_transact("CUST-501")

        assert exc_info.value.party_code == "CUST-501"

    def test_validate_can_transact_inactive(self, session, test_actor_id):
        """Raises PartyInactiveError for inactive party."""
        service = PartyService(session)

        party = service.create_party("CUST-502", PartyType.CUSTOMER, "Test", test_actor_id)
        service.deactivate_party(party.id)

        with pytest.raises(PartyInactiveError) as exc_info:
            service.validate_can_transact("CUST-502")

        assert exc_info.value.party_code == "CUST-502"

    def test_validate_can_transact_closed(self, session, test_actor_id):
        """Raises PartyInactiveError for closed party."""
        service = PartyService(session)

        party = service.create_party("CUST-503", PartyType.CUSTOMER, "Test", test_actor_id)
        service.close_party(party.id)

        with pytest.raises(PartyInactiveError):
            service.validate_can_transact("CUST-503")

    def test_validate_can_transact_not_found(self, session):
        """Raises PartyNotFoundError for unknown party."""
        service = PartyService(session)

        with pytest.raises(PartyNotFoundError):
            service.validate_can_transact("NONEXISTENT")


class TestCreditLimit:
    """Tests for credit limit checking."""

    def test_check_credit_limit_no_limit(self, session, test_actor_id):
        """Party without credit limit passes any amount."""
        service = PartyService(session)

        service.create_party("CUST-600", PartyType.CUSTOMER, "No Limit", test_actor_id)

        result = service.check_credit_limit(
            "CUST-600",
            amount=Decimal("1000000.00"),
            currency="USD",
        )

        assert result is True

    def test_check_credit_limit_within(self, session, test_actor_id):
        """Amount within credit limit passes."""
        service = PartyService(session)

        service.create_party(
            party_code="CUST-601",
            party_type=PartyType.CUSTOMER,
            name="With Limit",
            actor_id=test_actor_id,
            credit_limit=Decimal("10000.00"),
            credit_currency="USD",
        )

        result = service.check_credit_limit(
            "CUST-601",
            amount=Decimal("5000.00"),
            currency="USD",
        )

        assert result is True

    def test_check_credit_limit_exceeded(self, session, test_actor_id):
        """Amount exceeding credit limit fails."""
        service = PartyService(session)

        service.create_party(
            party_code="CUST-602",
            party_type=PartyType.CUSTOMER,
            name="With Limit",
            actor_id=test_actor_id,
            credit_limit=Decimal("10000.00"),
            credit_currency="USD",
        )

        result = service.check_credit_limit(
            "CUST-602",
            amount=Decimal("15000.00"),
            currency="USD",
        )

        assert result is False

    def test_check_credit_limit_with_balance(self, session, test_actor_id):
        """Considers existing balance when checking limit."""
        service = PartyService(session)

        service.create_party(
            party_code="CUST-603",
            party_type=PartyType.CUSTOMER,
            name="With Balance",
            actor_id=test_actor_id,
            credit_limit=Decimal("10000.00"),
            credit_currency="USD",
        )

        # Current balance 8000, new order 3000 = 11000 > 10000
        result = service.check_credit_limit(
            "CUST-603",
            amount=Decimal("3000.00"),
            currency="USD",
            current_balance=Decimal("8000.00"),
        )

        assert result is False

        # Current balance 5000, new order 3000 = 8000 < 10000
        result = service.check_credit_limit(
            "CUST-603",
            amount=Decimal("3000.00"),
            currency="USD",
            current_balance=Decimal("5000.00"),
        )

        assert result is True

    def test_check_credit_limit_currency_mismatch(self, session, test_actor_id):
        """Different currency bypasses credit check."""
        service = PartyService(session)

        service.create_party(
            party_code="CUST-604",
            party_type=PartyType.CUSTOMER,
            name="USD Limit",
            actor_id=test_actor_id,
            credit_limit=Decimal("10000.00"),
            credit_currency="USD",
        )

        # EUR transaction bypasses USD limit (would need FX conversion)
        result = service.check_credit_limit(
            "CUST-604",
            amount=Decimal("1000000.00"),
            currency="EUR",
        )

        assert result is True


class TestPartyUpdate:
    """Tests for party update operations."""

    def test_update_party_name(self, session, test_actor_id):
        """Updates party name."""
        service = PartyService(session)

        party = service.create_party("CUST-700", PartyType.CUSTOMER, "Old Name", test_actor_id)

        updated = service.update_party(party.id, name="New Name")

        assert updated.name == "New Name"

    def test_update_party_credit_limit(self, session, test_actor_id):
        """Updates party credit limit."""
        service = PartyService(session)

        party = service.create_party(
            party_code="CUST-701",
            party_type=PartyType.CUSTOMER,
            name="Test",
            actor_id=test_actor_id,
            credit_limit=Decimal("5000.00"),
        )

        updated = service.update_party(
            party.id,
            credit_limit=Decimal("10000.00"),
            credit_currency="USD",
        )

        assert updated.credit_limit == Decimal("10000.00")
        assert updated.credit_currency == "USD"

    def test_update_party_payment_terms(self, session, test_actor_id):
        """Updates party payment terms."""
        service = PartyService(session)

        party = service.create_party(
            party_code="SUPP-700",
            party_type=PartyType.SUPPLIER,
            name="Test Supplier",
            actor_id=test_actor_id,
            payment_terms_days=30,
        )

        updated = service.update_party(party.id, payment_terms_days=60)

        assert updated.payment_terms_days == 60

    def test_update_multiple_fields(self, session, test_actor_id):
        """Updates multiple party fields at once."""
        service = PartyService(session)

        party = service.create_party("CUST-702", PartyType.CUSTOMER, "Test", test_actor_id)

        updated = service.update_party(
            party.id,
            name="Updated Name",
            credit_limit=Decimal("25000.00"),
            credit_currency="EUR",
            payment_terms_days=45,
            default_currency="EUR",
        )

        assert updated.name == "Updated Name"
        assert updated.credit_limit == Decimal("25000.00")
        assert updated.credit_currency == "EUR"
        assert updated.payment_terms_days == 45
        assert updated.default_currency == "EUR"


class TestPartyInfoDTO:
    """Tests for PartyInfo DTO properties."""

    def test_dto_is_frozen(self, session, test_actor_id):
        """PartyInfo DTO has is_frozen property."""
        service = PartyService(session)

        party = service.create_party("CUST-800", PartyType.CUSTOMER, "Test", test_actor_id)
        assert party.is_frozen is False

        service.freeze_party(party.id)
        frozen = service.get_by_code("CUST-800")
        assert frozen.is_frozen is True

    def test_dto_can_transact(self, session, test_actor_id):
        """PartyInfo DTO has can_transact property."""
        service = PartyService(session)

        party = service.create_party("CUST-801", PartyType.CUSTOMER, "Test", test_actor_id)
        assert party.can_transact is True

        service.freeze_party(party.id)
        frozen = service.get_by_code("CUST-801")
        assert frozen.can_transact is False

    def test_dto_all_fields_populated(self, session, test_actor_id):
        """PartyInfo DTO includes all fields."""
        service = PartyService(session)

        party = service.create_party(
            party_code="CUST-802",
            party_type=PartyType.CUSTOMER,
            name="Full Party",
            actor_id=test_actor_id,
            credit_limit=Decimal("50000.00"),
            credit_currency="USD",
            payment_terms_days=30,
            default_currency="USD",
            tax_id="12-3456789",
            external_ref="CRM-12345",
        )

        assert party.id is not None
        assert party.party_code == "CUST-802"
        assert party.party_type == PartyType.CUSTOMER
        assert party.name == "Full Party"
        assert party.status == PartyStatus.ACTIVE
        assert party.is_active is True
        assert party.credit_limit == Decimal("50000.00")
        assert party.credit_currency == "USD"
        assert party.payment_terms_days == 30
        assert party.default_currency == "USD"
        assert party.tax_id == "12-3456789"
        assert party.external_ref == "CRM-12345"
