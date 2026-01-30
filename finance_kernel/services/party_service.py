"""
Service layer for Party operations.

Manages customers, suppliers, employees, and intercompany entities.

R3 Compliance: Returns PartyInfo DTOs instead of ORM entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.exceptions import (
    PartyFrozenError,
    PartyInactiveError,
    PartyNotFoundError,
)
from finance_kernel.models.party import Party, PartyStatus, PartyType
from finance_kernel.services.base import BaseService


@dataclass(frozen=True)
class PartyInfo:
    """
    Immutable DTO for party data.

    R3 Compliance: Pure domain object, no ORM dependencies.
    """

    id: UUID
    party_code: str
    party_type: PartyType
    name: str
    status: PartyStatus
    is_active: bool
    credit_limit: Decimal | None
    credit_currency: str | None
    payment_terms_days: int | None
    default_currency: str | None
    tax_id: str | None
    external_ref: str | None

    @property
    def is_frozen(self) -> bool:
        """Check if party is frozen for transactions."""
        return self.status == PartyStatus.FROZEN

    @property
    def can_transact(self) -> bool:
        """Check if party can accept new transactions."""
        return self.is_active and self.status == PartyStatus.ACTIVE


class PartyService(BaseService[Party]):
    """
    Service for managing parties.

    Handles CRUD operations for customers, suppliers, employees,
    and intercompany entities. Enforces business rules for party
    status, credit limits, and transaction eligibility.

    R3 Compliance: All public methods return PartyInfo DTOs,
    not ORM Party entities.
    """

    def _to_dto(self, party: Party) -> PartyInfo:
        """Convert ORM Party to PartyInfo DTO."""
        return PartyInfo(
            id=party.id,
            party_code=party.party_code,
            party_type=party.party_type,
            name=party.name,
            status=party.status,
            is_active=party.is_active,
            credit_limit=party.credit_limit,
            credit_currency=party.credit_currency,
            payment_terms_days=party.payment_terms_days,
            default_currency=party.default_currency,
            tax_id=party.tax_id,
            external_ref=party.external_ref,
        )

    def _get_by_id(self, party_id: UUID) -> Party:
        """Get party by ID, raising if not found."""
        party = self.session.get(Party, party_id)
        if party is None:
            raise PartyNotFoundError(str(party_id))
        return party

    def _get_by_code(self, party_code: str) -> Party:
        """Get party by code, raising if not found."""
        stmt = select(Party).where(Party.party_code == party_code)
        party = self.session.execute(stmt).scalar_one_or_none()
        if party is None:
            raise PartyNotFoundError(party_code)
        return party

    def get_by_id(self, party_id: UUID) -> PartyInfo:
        """
        Get party by ID.

        Args:
            party_id: UUID of the party.

        Returns:
            PartyInfo DTO.

        Raises:
            PartyNotFoundError: If party doesn't exist.
        """
        return self._to_dto(self._get_by_id(party_id))

    def get_by_code(self, party_code: str) -> PartyInfo:
        """
        Get party by code.

        Args:
            party_code: Unique party code (e.g., "CUST-001").

        Returns:
            PartyInfo DTO.

        Raises:
            PartyNotFoundError: If party doesn't exist.
        """
        return self._to_dto(self._get_by_code(party_code))

    def find_by_code(self, party_code: str) -> PartyInfo | None:
        """
        Find party by code, returning None if not found.

        Args:
            party_code: Unique party code.

        Returns:
            PartyInfo DTO or None.
        """
        stmt = select(Party).where(Party.party_code == party_code)
        party = self.session.execute(stmt).scalar_one_or_none()
        return self._to_dto(party) if party else None

    def list_by_type(
        self,
        party_type: PartyType,
        active_only: bool = True,
    ) -> list[PartyInfo]:
        """
        List parties by type.

        Args:
            party_type: Type of party to list.
            active_only: If True, only return active parties.

        Returns:
            List of PartyInfo DTOs.
        """
        stmt = select(Party).where(Party.party_type == party_type)
        if active_only:
            stmt = stmt.where(Party.is_active == True)
        stmt = stmt.order_by(Party.party_code)

        parties = self.session.execute(stmt).scalars().all()
        return [self._to_dto(p) for p in parties]

    def create_party(
        self,
        party_code: str,
        party_type: PartyType,
        name: str,
        actor_id: UUID,
        credit_limit: Decimal | None = None,
        credit_currency: str | None = None,
        payment_terms_days: int | None = None,
        default_currency: str | None = None,
        tax_id: str | None = None,
        external_ref: str | None = None,
    ) -> PartyInfo:
        """
        Create a new party.

        Args:
            party_code: Unique identifier (e.g., "CUST-001", "SUPP-ABC").
            party_type: Type of party.
            name: Display name.
            actor_id: UUID of user/actor creating the party.
            credit_limit: Optional credit limit (for customers).
            credit_currency: Currency for credit limit.
            payment_terms_days: Default payment terms.
            default_currency: Default transaction currency.
            tax_id: Tax identification number.
            external_ref: External system reference.

        Returns:
            Created PartyInfo DTO.
        """
        party = Party(
            party_code=party_code,
            party_type=party_type,
            name=name,
            credit_limit=credit_limit,
            credit_currency=credit_currency,
            payment_terms_days=payment_terms_days,
            default_currency=default_currency,
            tax_id=tax_id,
            external_ref=external_ref,
            created_by_id=actor_id,
        )
        self.session.add(party)
        self.session.flush()
        return self._to_dto(party)

    def update_party(
        self,
        party_id: UUID,
        name: str | None = None,
        credit_limit: Decimal | None = None,
        credit_currency: str | None = None,
        payment_terms_days: int | None = None,
        default_currency: str | None = None,
        tax_id: str | None = None,
        external_ref: str | None = None,
    ) -> PartyInfo:
        """
        Update party details.

        Note: party_code and party_type cannot be changed.

        Args:
            party_id: UUID of party to update.
            name: New display name (if provided).
            credit_limit: New credit limit (if provided).
            credit_currency: New credit currency (if provided).
            payment_terms_days: New payment terms (if provided).
            default_currency: New default currency (if provided).
            tax_id: New tax ID (if provided).
            external_ref: New external reference (if provided).

        Returns:
            Updated PartyInfo DTO.
        """
        party = self._get_by_id(party_id)

        if name is not None:
            party.name = name
        if credit_limit is not None:
            party.credit_limit = credit_limit
        if credit_currency is not None:
            party.credit_currency = credit_currency
        if payment_terms_days is not None:
            party.payment_terms_days = payment_terms_days
        if default_currency is not None:
            party.default_currency = default_currency
        if tax_id is not None:
            party.tax_id = tax_id
        if external_ref is not None:
            party.external_ref = external_ref

        self.session.flush()
        return self._to_dto(party)

    def freeze_party(self, party_id: UUID, reason: str | None = None) -> PartyInfo:
        """
        Freeze a party to block new transactions.

        Frozen parties cannot be used in new transactions.
        Existing transactions remain unaffected.

        Args:
            party_id: UUID of party to freeze.
            reason: Optional reason for freezing.

        Returns:
            Updated PartyInfo DTO.
        """
        party = self._get_by_id(party_id)
        party.status = PartyStatus.FROZEN
        self.session.flush()
        return self._to_dto(party)

    def unfreeze_party(self, party_id: UUID) -> PartyInfo:
        """
        Unfreeze a party to allow new transactions.

        Args:
            party_id: UUID of party to unfreeze.

        Returns:
            Updated PartyInfo DTO.
        """
        party = self._get_by_id(party_id)
        party.status = PartyStatus.ACTIVE
        self.session.flush()
        return self._to_dto(party)

    def deactivate_party(self, party_id: UUID) -> PartyInfo:
        """
        Deactivate a party.

        Deactivated parties are hidden from selection lists
        but remain available for historical reference.

        Args:
            party_id: UUID of party to deactivate.

        Returns:
            Updated PartyInfo DTO.
        """
        party = self._get_by_id(party_id)
        party.is_active = False
        self.session.flush()
        return self._to_dto(party)

    def reactivate_party(self, party_id: UUID) -> PartyInfo:
        """
        Reactivate a previously deactivated party.

        Args:
            party_id: UUID of party to reactivate.

        Returns:
            Updated PartyInfo DTO.
        """
        party = self._get_by_id(party_id)
        party.is_active = True
        self.session.flush()
        return self._to_dto(party)

    def close_party(self, party_id: UUID) -> PartyInfo:
        """
        Close a party permanently.

        Closed parties cannot transact and cannot be reopened.
        Use this for parties that are permanently ended
        (e.g., customer gone out of business).

        Args:
            party_id: UUID of party to close.

        Returns:
            Updated PartyInfo DTO.
        """
        party = self._get_by_id(party_id)
        party.status = PartyStatus.CLOSED
        party.is_active = False
        self.session.flush()
        return self._to_dto(party)

    def validate_can_transact(self, party_code: str) -> PartyInfo:
        """
        Validate that a party can transact, raising if not.

        Guards check this before accepting transactions for a party.

        Args:
            party_code: Party code to validate.

        Returns:
            PartyInfo DTO if party can transact.

        Raises:
            PartyNotFoundError: If party doesn't exist.
            PartyFrozenError: If party is frozen.
            PartyInactiveError: If party is inactive.
        """
        party = self._get_by_code(party_code)

        if party.status == PartyStatus.FROZEN:
            raise PartyFrozenError(party_code)

        if not party.is_active or party.status == PartyStatus.CLOSED:
            raise PartyInactiveError(party_code)

        return self._to_dto(party)

    def check_credit_limit(
        self,
        party_code: str,
        amount: Decimal,
        currency: str,
        current_balance: Decimal | None = None,
    ) -> bool:
        """
        Check if a transaction is within the party's credit limit.

        Args:
            party_code: Party code to check.
            amount: Transaction amount.
            currency: Transaction currency.
            current_balance: Current outstanding balance (optional).
                If not provided, assumes zero balance.

        Returns:
            True if within limit or no limit set, False if would exceed.

        Note:
            This is a simple check. Production use would query
            the AR subledger for actual outstanding balance.
        """
        party = self._get_by_code(party_code)

        # No credit limit set - allow
        if party.credit_limit is None:
            return True

        # Currency mismatch - allow (would need FX conversion)
        if party.credit_currency and party.credit_currency != currency:
            return True

        # Check against limit
        balance = current_balance or Decimal("0")
        return (balance + amount) <= party.credit_limit
