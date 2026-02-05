"""
AR entity promoters: customer (Party + CustomerProfile) — Phase 8.

CustomerPromoter creates or reuses Party (CUSTOMER) then creates CustomerProfileModel.
Duplicate check by customer profile code.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.models.party import Party, PartyStatus, PartyType

from finance_ingestion.promoters.base import PromoteResult

from finance_modules.ar.orm import CustomerProfileModel


def _str(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    return str(v).strip() if v is not None else default


def _optional_str(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return str(v).strip()


def _optional_int(d: dict[str, Any], key: str, default: int = 0) -> int:
    v = d.get(key)
    if v is None:
        return default
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _optional_decimal(d: dict[str, Any], key: str) -> Decimal | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except (TypeError, ValueError):
        return None


def _optional_bool(d: dict[str, Any], key: str, default: bool = False) -> bool:
    v = d.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


class CustomerPromoter:
    """Promotes mapped_data to Party (CUSTOMER) + CustomerProfileModel. Entity type: customer."""

    entity_type: str = "customer"

    def _get_or_create_party(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
    ) -> Party | None:
        code = _str(mapped_data, "code") or _str(mapped_data, "party_code")
        if not code:
            return None
        stmt = select(Party).where(Party.party_code == code)
        party = session.scalars(stmt).first()
        if party is not None:
            return party
        name = _str(mapped_data, "name") or code
        party = Party(
            party_code=code,
            party_type=PartyType.CUSTOMER,
            name=name,
            status=PartyStatus.ACTIVE,
            is_active=True,
            credit_limit=_optional_decimal(mapped_data, "credit_limit"),
            credit_currency=_optional_str(mapped_data, "credit_currency"),
            payment_terms_days=_optional_int(mapped_data, "payment_terms_days", 30),
            tax_id=_optional_str(mapped_data, "tax_id"),
            created_by_id=actor_id,
            updated_by_id=None,
        )
        session.add(party)
        session.flush()
        return party

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
        **kwargs: Any,
    ) -> PromoteResult:
        code = _str(mapped_data, "code") or _str(mapped_data, "party_code")
        if not code:
            return PromoteResult(success=False, error="Missing code or party_code")

        party = self._get_or_create_party(mapped_data, session, actor_id)
        if party is None:
            return PromoteResult(success=False, error="Could not get or create Party")

        name = _str(mapped_data, "name") or code
        profile = CustomerProfileModel(
            customer_id=party.id,
            code=code,
            name=name,
            credit_limit=_optional_decimal(mapped_data, "credit_limit"),
            payment_terms_days=_optional_int(mapped_data, "payment_terms_days", 30),
            default_gl_account_code=_optional_str(mapped_data, "default_gl_account_code"),
            tax_exempt=_optional_bool(mapped_data, "tax_exempt", False),
            tax_id=_optional_str(mapped_data, "tax_id"),
            is_active=_optional_bool(mapped_data, "is_active", True),
            dunning_level=_optional_int(mapped_data, "dunning_level", 0),
            created_by_id=actor_id,
            updated_by_id=None,
        )
        session.add(profile)
        session.flush()
        return PromoteResult(success=True, entity_id=profile.id)

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        code = _str(mapped_data, "code") or _str(mapped_data, "party_code")
        if not code:
            return False
        stmt = select(CustomerProfileModel).where(CustomerProfileModel.code == code)
        return session.scalars(stmt).first() is not None
