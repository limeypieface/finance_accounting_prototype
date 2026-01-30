"""
Blocked Party Tests - Supplier/Customer Hold Enforcement.

Tests verify that business operations are blocked when parties are on hold:
- Frozen parties blocked from all transactions
- Closed parties blocked from new transactions
- Freeze/unfreeze lifecycle
- Credit limit enforcement
- Party status property checks

Uses real architecture:
- PartyService (finance_kernel.services.party_service) for party management
- PartyInfo DTO for immutable party data
- PartyStatus (ACTIVE, FROZEN, CLOSED) for lifecycle
- PartyFrozenError / PartyInactiveError for guard enforcement
- check_credit_limit() for credit checks

CRITICAL: Party holds ensure blocked suppliers/customers cannot transact.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from finance_kernel.exceptions import (
    PartyFrozenError,
    PartyInactiveError,
    PartyNotFoundError,
)
from finance_kernel.models.party import PartyStatus, PartyType
from finance_kernel.services.party_service import PartyInfo, PartyService


# =============================================================================
# Helpers
# =============================================================================

def _create_supplier(
    svc: PartyService,
    code: str = "SUP-001",
    name: str = "Acme Corp",
    actor_id=None,
) -> PartyInfo:
    """Create an active supplier via PartyService."""
    return svc.create_party(
        party_code=code,
        party_type=PartyType.SUPPLIER,
        name=name,
        actor_id=actor_id or uuid4(),
    )


def _create_customer(
    svc: PartyService,
    code: str = "CUST-001",
    name: str = "Widget Inc",
    credit_limit: Decimal | None = None,
    actor_id=None,
) -> PartyInfo:
    """Create an active customer via PartyService."""
    return svc.create_party(
        party_code=code,
        party_type=PartyType.CUSTOMER,
        name=name,
        actor_id=actor_id or uuid4(),
        credit_limit=credit_limit,
        credit_currency="USD" if credit_limit is not None else None,
    )


# =============================================================================
# Test: Active Parties Can Transact
# =============================================================================

class TestActivePartyCanTransact:
    """Active parties pass validation checks."""

    def test_active_supplier_can_transact(self, session):
        """Active supplier passes validate_can_transact."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        result = svc.validate_can_transact(supplier.party_code)

        assert result.status == PartyStatus.ACTIVE
        assert result.can_transact

    def test_active_customer_can_transact(self, session):
        """Active customer passes validate_can_transact."""
        svc = PartyService(session)
        customer = _create_customer(svc)

        result = svc.validate_can_transact(customer.party_code)

        assert result.status == PartyStatus.ACTIVE
        assert result.can_transact

    def test_new_party_starts_active(self, session):
        """Newly created parties start with ACTIVE status."""
        svc = PartyService(session)
        party = _create_supplier(svc, code="SUP-NEW")

        assert party.status == PartyStatus.ACTIVE
        assert party.is_active
        assert party.can_transact
        assert not party.is_frozen


# =============================================================================
# Test: Frozen Party Blocks Transactions
# =============================================================================

class TestFrozenPartyBlocked:
    """Frozen parties are blocked from all transactions."""

    def test_frozen_supplier_cannot_transact(self, session):
        """Frozen supplier raises PartyFrozenError."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        svc.freeze_party(supplier.id)

        with pytest.raises(PartyFrozenError) as exc_info:
            svc.validate_can_transact(supplier.party_code)

        assert exc_info.value.party_code == supplier.party_code

    def test_frozen_customer_cannot_transact(self, session):
        """Frozen customer raises PartyFrozenError."""
        svc = PartyService(session)
        customer = _create_customer(svc)

        svc.freeze_party(customer.id)

        with pytest.raises(PartyFrozenError):
            svc.validate_can_transact(customer.party_code)

    def test_frozen_party_status_is_frozen(self, session):
        """Frozen party has FROZEN status and is_frozen=True."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        frozen = svc.freeze_party(supplier.id)

        assert frozen.status == PartyStatus.FROZEN
        assert frozen.is_frozen
        assert not frozen.can_transact

    def test_freeze_blocks_invoices_and_payments(self, session):
        """Frozen party blocks both invoice and payment operations.

        Unlike granular hold systems, FROZEN is a full block on all
        transaction types — there is no invoice-only or payment-only hold.
        """
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        svc.freeze_party(supplier.id)

        # Both "invoice" and "payment" operations should fail
        with pytest.raises(PartyFrozenError):
            svc.validate_can_transact(supplier.party_code)

    def test_freeze_error_includes_party_code(self, session):
        """PartyFrozenError includes party code for audit trail."""
        svc = PartyService(session)
        supplier = _create_supplier(svc, code="SUP-AUDIT")

        svc.freeze_party(supplier.id)

        with pytest.raises(PartyFrozenError) as exc_info:
            svc.validate_can_transact("SUP-AUDIT")

        assert exc_info.value.party_code == "SUP-AUDIT"
        assert "frozen" in str(exc_info.value).lower()


# =============================================================================
# Test: Closed Party Blocks Transactions
# =============================================================================

class TestClosedPartyBlocked:
    """Closed parties are permanently blocked."""

    def test_closed_party_cannot_transact(self, session):
        """Closed party raises PartyInactiveError."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        svc.close_party(supplier.id)

        with pytest.raises(PartyInactiveError):
            svc.validate_can_transact(supplier.party_code)

    def test_closed_party_is_inactive(self, session):
        """Closed party has CLOSED status and is_active=False."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        closed = svc.close_party(supplier.id)

        assert closed.status == PartyStatus.CLOSED
        assert not closed.is_active
        assert not closed.can_transact

    def test_deactivated_party_cannot_transact(self, session):
        """Deactivated party is blocked from new transactions."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        svc.deactivate_party(supplier.id)

        with pytest.raises(PartyInactiveError):
            svc.validate_can_transact(supplier.party_code)


# =============================================================================
# Test: Freeze / Unfreeze Lifecycle
# =============================================================================

class TestFreezeUnfreezeLifecycle:
    """Freeze and unfreeze operations work correctly."""

    def test_freeze_then_unfreeze_restores_transacting(self, session):
        """Unfreezing a party restores ability to transact."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        # Freeze
        svc.freeze_party(supplier.id)
        with pytest.raises(PartyFrozenError):
            svc.validate_can_transact(supplier.party_code)

        # Unfreeze
        unfrozen = svc.unfreeze_party(supplier.id)

        assert unfrozen.status == PartyStatus.ACTIVE
        assert unfrozen.can_transact

        # Should now pass validation
        result = svc.validate_can_transact(supplier.party_code)
        assert result.status == PartyStatus.ACTIVE

    def test_multiple_freeze_unfreeze_cycles(self, session):
        """Party can be frozen and unfrozen multiple times."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        for _ in range(3):
            frozen = svc.freeze_party(supplier.id)
            assert frozen.is_frozen

            unfrozen = svc.unfreeze_party(supplier.id)
            assert unfrozen.can_transact

    def test_freeze_returns_updated_dto(self, session):
        """freeze_party returns a PartyInfo with updated status."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        result = svc.freeze_party(supplier.id)

        assert isinstance(result, PartyInfo)
        assert result.id == supplier.id
        assert result.status == PartyStatus.FROZEN

    def test_reactivate_after_deactivation(self, session):
        """Reactivated party can transact again."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        svc.deactivate_party(supplier.id)
        with pytest.raises(PartyInactiveError):
            svc.validate_can_transact(supplier.party_code)

        svc.reactivate_party(supplier.id)
        result = svc.validate_can_transact(supplier.party_code)
        assert result.is_active


# =============================================================================
# Test: Credit Limit Enforcement
# =============================================================================

class TestCreditLimitEnforcement:
    """Credit limit enforcement for customers."""

    def test_transaction_within_credit_limit(self, session):
        """Transaction allowed when within credit limit."""
        svc = PartyService(session)
        customer = _create_customer(
            svc, credit_limit=Decimal("10000.00"),
        )

        result = svc.check_credit_limit(
            party_code=customer.party_code,
            amount=Decimal("3000.00"),
            currency="USD",
            current_balance=Decimal("5000.00"),
        )

        assert result is True

    def test_transaction_exceeds_credit_limit(self, session):
        """Transaction blocked when would exceed credit limit."""
        svc = PartyService(session)
        customer = _create_customer(
            svc, credit_limit=Decimal("10000.00"),
        )

        result = svc.check_credit_limit(
            party_code=customer.party_code,
            amount=Decimal("3000.00"),
            currency="USD",
            current_balance=Decimal("8000.00"),
        )

        assert result is False

    def test_transaction_exactly_at_credit_limit(self, session):
        """Transaction allowed when exactly at credit limit."""
        svc = PartyService(session)
        customer = _create_customer(
            svc, credit_limit=Decimal("10000.00"),
        )

        result = svc.check_credit_limit(
            party_code=customer.party_code,
            amount=Decimal("2000.00"),
            currency="USD",
            current_balance=Decimal("8000.00"),
        )

        assert result is True

    def test_no_credit_limit_allows_any_amount(self, session):
        """Any amount allowed when no credit limit set."""
        svc = PartyService(session)
        customer = _create_customer(svc, credit_limit=None)

        result = svc.check_credit_limit(
            party_code=customer.party_code,
            amount=Decimal("999999999.99"),
            currency="USD",
            current_balance=Decimal("1000000.00"),
        )

        assert result is True

    def test_zero_credit_limit_blocks_all(self, session):
        """Zero credit limit blocks all transactions."""
        svc = PartyService(session)
        customer = _create_customer(
            svc, credit_limit=Decimal("0"),
        )

        result = svc.check_credit_limit(
            party_code=customer.party_code,
            amount=Decimal("0.01"),
            currency="USD",
        )

        assert result is False


# =============================================================================
# Test: Party Status Properties
# =============================================================================

class TestPartyStatusProperties:
    """PartyInfo DTO properties reflect status correctly."""

    def test_active_party_properties(self, session):
        """Active party has correct property values."""
        svc = PartyService(session)
        party = _create_supplier(svc)

        assert party.is_active is True
        assert party.is_frozen is False
        assert party.can_transact is True

    def test_frozen_party_properties(self, session):
        """Frozen party has correct property values."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        frozen = svc.freeze_party(supplier.id)

        assert frozen.is_active is True  # Still active, just frozen
        assert frozen.is_frozen is True
        assert frozen.can_transact is False

    def test_closed_party_properties(self, session):
        """Closed party has correct property values."""
        svc = PartyService(session)
        supplier = _create_supplier(svc)

        closed = svc.close_party(supplier.id)

        assert closed.is_active is False
        assert closed.can_transact is False
        assert closed.status == PartyStatus.CLOSED


# =============================================================================
# Test: Edge Cases
# =============================================================================

class TestBlockedPartyEdgeCases:
    """Edge cases for party hold handling."""

    def test_nonexistent_party_raises_not_found(self, session):
        """Validating nonexistent party raises PartyNotFoundError."""
        svc = PartyService(session)

        with pytest.raises(PartyNotFoundError):
            svc.validate_can_transact("NO-SUCH-PARTY")

    def test_party_info_is_immutable(self, session):
        """PartyInfo DTO is frozen/immutable."""
        svc = PartyService(session)
        party = _create_supplier(svc)

        with pytest.raises(AttributeError):
            party.status = PartyStatus.FROZEN  # type: ignore[misc]

    def test_credit_check_on_nonexistent_party(self, session):
        """Credit check on nonexistent party raises not found."""
        svc = PartyService(session)

        with pytest.raises(PartyNotFoundError):
            svc.check_credit_limit("NO-SUCH-PARTY", Decimal("100"), "USD")

    def test_party_types_are_distinct(self, session):
        """Different party types are tracked correctly."""
        svc = PartyService(session)
        actor = uuid4()

        supplier = svc.create_party(
            party_code="SUP-TYPES",
            party_type=PartyType.SUPPLIER,
            name="Supplier",
            actor_id=actor,
        )
        customer = svc.create_party(
            party_code="CUST-TYPES",
            party_type=PartyType.CUSTOMER,
            name="Customer",
            actor_id=actor,
        )

        assert supplier.party_type == PartyType.SUPPLIER
        assert customer.party_type == PartyType.CUSTOMER

    def test_freeze_one_party_does_not_affect_another(self, session):
        """Freezing one party does not block other parties."""
        svc = PartyService(session)

        sup1 = _create_supplier(svc, code="SUP-A")
        sup2 = _create_supplier(svc, code="SUP-B")

        svc.freeze_party(sup1.id)

        # sup1 is blocked
        with pytest.raises(PartyFrozenError):
            svc.validate_can_transact("SUP-A")

        # sup2 is unaffected
        result = svc.validate_can_transact("SUP-B")
        assert result.can_transact


# =============================================================================
# Summary
# =============================================================================

class TestBlockedPartySummary:
    """Summary of blocked party test coverage."""

    def test_document_coverage(self):
        """
        Blocked Party Test Coverage:

        Active Parties:
        - Active supplier can transact
        - Active customer can transact
        - New party starts ACTIVE

        Frozen Parties (Full Hold):
        - Frozen supplier cannot transact
        - Frozen customer cannot transact
        - Frozen party has FROZEN status
        - Freeze blocks all operations (invoice + payment)
        - Error includes party code for audit

        Closed Parties:
        - Closed party cannot transact
        - Closed party is inactive
        - Deactivated party blocked

        Freeze/Unfreeze Lifecycle:
        - Freeze then unfreeze restores ability
        - Multiple freeze/unfreeze cycles
        - Freeze returns updated DTO
        - Reactivate after deactivation

        Credit Limits:
        - Within limit: allowed
        - Exceeds limit: blocked
        - Exactly at limit: allowed
        - No limit: any amount allowed
        - Zero limit: blocks all

        Party Status Properties:
        - Active: is_active=True, can_transact=True
        - Frozen: is_frozen=True, can_transact=False
        - Closed: is_active=False, can_transact=False

        Edge Cases:
        - Nonexistent party raises not found
        - PartyInfo is immutable
        - Credit check on nonexistent party
        - Party types are distinct
        - Freezing one party doesn't affect another

        Total: 25 tests covering blocked party enforcement with real architecture.
        """
        pass
