"""
Party promoter: staged mapped_data -> Party row (Phase 8).

Creates kernel Party rows. Duplicate check by party_code (or code).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.models.party import Party, PartyStatus, PartyType

from finance_ingestion.promoters.base import PromoteResult


def _str(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    return str(v).strip() if v is not None else default


def _optional_str(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return str(v).strip()


def _optional_int(d: dict[str, Any], key: str) -> int | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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


class PartyPromoter:
    """Promotes mapped_data to kernel Party row. Entity type: party."""

    entity_type: str = "party"

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
        **kwargs: Any,
    ) -> PromoteResult:
        party_code = _str(mapped_data, "party_code") or _str(mapped_data, "code")
        if not party_code:
            return PromoteResult(success=False, error="Missing party_code or code")

        name = _str(mapped_data, "name") or party_code
        party_type_str = _str(mapped_data, "party_type") or PartyType.SUPPLIER.value
        try:
            party_type = PartyType(party_type_str.lower())
        except ValueError:
            party_type = PartyType.SUPPLIER

        status_str = _str(mapped_data, "status") or PartyStatus.ACTIVE.value
        try:
            status = PartyStatus(status_str.lower())
        except ValueError:
            status = PartyStatus.ACTIVE

        is_active = mapped_data.get("is_active")
        if is_active is None:
            is_active = True
        elif isinstance(is_active, str):
            is_active = is_active.strip().lower() in ("true", "1", "yes")

        party = Party(
            party_code=party_code,
            party_type=party_type,
            name=name,
            status=status,
            is_active=bool(is_active),
            credit_limit=_optional_decimal(mapped_data, "credit_limit"),
            credit_currency=_optional_str(mapped_data, "credit_currency"),
            payment_terms_days=_optional_int(mapped_data, "payment_terms_days"),
            tax_id=_optional_str(mapped_data, "tax_id"),
            default_currency=_optional_str(mapped_data, "default_currency"),
            external_ref=_optional_str(mapped_data, "external_ref"),
            created_by_id=actor_id,
            updated_by_id=None,
        )
        session.add(party)
        session.flush()
        return PromoteResult(success=True, entity_id=party.id)

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        party_code = _str(mapped_data, "party_code") or _str(mapped_data, "code")
        if not party_code:
            return False
        stmt = select(Party).where(Party.party_code == party_code)
        return session.scalars(stmt).first() is not None
