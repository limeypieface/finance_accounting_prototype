"""
AP entity promoters: vendor (Party + VendorProfile) — Phase 8.

VendorPromoter creates or reuses Party (SUPPLIER) then creates VendorProfileModel.
Duplicate check by vendor profile code.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.models.party import Party, PartyStatus, PartyType

from finance_ingestion.promoters.base import PromoteResult

# Module ORM — promoters may import module ORM (ERP_INGESTION_PLAN)
from finance_modules.ap.orm import VendorProfileModel


def _str(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    return str(v).strip() if v is not None else default


def _optional_str(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return str(v).strip()


def _optional_int(d: dict[str, Any], key: str, default: int = 30) -> int:
    v = d.get(key)
    if v is None:
        return default
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _optional_bool(d: dict[str, Any], key: str, default: bool = False) -> bool:
    v = d.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


class VendorPromoter:
    """Promotes mapped_data to Party (SUPPLIER) + VendorProfileModel. Entity type: vendor."""

    entity_type: str = "vendor"

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
            party_type=PartyType.SUPPLIER,
            name=name,
            status=PartyStatus.ACTIVE,
            is_active=True,
            tax_id=_optional_str(mapped_data, "tax_id"),
            payment_terms_days=_optional_int(mapped_data, "payment_terms_days", 30),
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
        profile = VendorProfileModel(
            vendor_id=party.id,
            code=code,
            name=name,
            tax_id=_optional_str(mapped_data, "tax_id"),
            payment_terms_days=_optional_int(mapped_data, "payment_terms_days", 30),
            default_payment_method=_str(mapped_data, "default_payment_method") or "ach",
            default_gl_account_code=_optional_str(mapped_data, "default_gl_account_code"),
            is_active=_optional_bool(mapped_data, "is_active", True),
            is_1099_eligible=_optional_bool(mapped_data, "is_1099_eligible", False),
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
        stmt = select(VendorProfileModel).where(VendorProfileModel.code == code)
        return session.scalars(stmt).first() is not None
