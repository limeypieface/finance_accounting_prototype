"""
Account promoter: staged mapped_data -> Account row (Phase 8).

Creates kernel Account (COA) rows. Duplicate check by code.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.models.account import Account, AccountType, NormalBalance

from finance_ingestion.promoters.base import PromoteResult


def _str(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    return str(v).strip() if v is not None else default


def _optional_str(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return str(v).strip()


def _optional_bool(d: dict[str, Any], key: str, default: bool = True) -> bool:
    v = d.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _list_str(d: dict[str, Any], key: str) -> list[str] | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    return None


class AccountPromoter:
    """Promotes mapped_data to kernel Account row. Entity type: account."""

    entity_type: str = "account"

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
    ) -> PromoteResult:
        code = _str(mapped_data, "code")
        if not code:
            return PromoteResult(success=False, error="Missing code")

        name = _str(mapped_data, "name") or code
        account_type_str = _str(mapped_data, "account_type") or AccountType.ASSET.value
        try:
            account_type = AccountType(account_type_str.lower())
        except ValueError:
            account_type = AccountType.ASSET

        normal_balance_str = _str(mapped_data, "normal_balance") or NormalBalance.DEBIT.value
        try:
            normal_balance = NormalBalance(normal_balance_str.lower())
        except ValueError:
            normal_balance = NormalBalance.DEBIT

        is_active = _optional_bool(mapped_data, "is_active", True)
        tags = _list_str(mapped_data, "tags")
        parent_id = mapped_data.get("parent_id")
        if parent_id is not None and isinstance(parent_id, str):
            try:
                parent_id = UUID(parent_id)
            except (TypeError, ValueError):
                parent_id = None
        elif parent_id is not None and not isinstance(parent_id, UUID):
            parent_id = None

        account = Account(
            code=code,
            name=name,
            account_type=account_type,
            normal_balance=normal_balance,
            is_active=is_active,
            tags=tags,
            parent_id=parent_id,
            currency=_optional_str(mapped_data, "currency"),
            created_by_id=actor_id,
            updated_by_id=None,
        )
        session.add(account)
        session.flush()
        return PromoteResult(success=True, entity_id=account.id)

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        code = _str(mapped_data, "code")
        if not code:
            return False
        stmt = select(Account).where(Account.code == code)
        return session.scalars(stmt).first() is not None
