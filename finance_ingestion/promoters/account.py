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


# QuickBooks Online "Type" values -> our AccountType (for import mapping)
# Includes both singular and plural forms (e.g. QBO export may use "Long Term Liabilities").
_QB_TYPE_TO_ACCOUNT_TYPE: dict[str, AccountType] = {
    "bank": AccountType.ASSET,
    "accounts receivable": AccountType.ASSET,
    "other current asset": AccountType.ASSET,
    "other current assets": AccountType.ASSET,
    "fixed asset": AccountType.ASSET,
    "fixed assets": AccountType.ASSET,
    "other asset": AccountType.ASSET,
    "other assets": AccountType.ASSET,
    "accounts payable": AccountType.LIABILITY,
    "credit card": AccountType.LIABILITY,
    "other current liability": AccountType.LIABILITY,
    "other current liabilities": AccountType.LIABILITY,
    "long term liability": AccountType.LIABILITY,
    "long term liabilities": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "income": AccountType.REVENUE,
    "other income": AccountType.REVENUE,
    "cost of goods sold": AccountType.EXPENSE,
    "expense": AccountType.EXPENSE,
    "expenses": AccountType.EXPENSE,
    "other expense": AccountType.EXPENSE,
}


def _parse_account_type(raw: str) -> AccountType:
    """Parse account type from CSV; accept our enum values or QuickBooks Online Type names."""
    s = (raw or "").strip().lower()
    if not s:
        return AccountType.ASSET
    try:
        return AccountType(s)
    except ValueError:
        pass
    return _QB_TYPE_TO_ACCOUNT_TYPE.get(s, AccountType.ASSET)


def _default_normal_balance(account_type: AccountType) -> NormalBalance:
    """Default normal balance from account type when not provided in import."""
    if account_type in (AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE):
        return NormalBalance.CREDIT
    return NormalBalance.DEBIT


def _account_type_and_normal_balance_from_code(code: str) -> tuple[AccountType, NormalBalance] | None:
    """Derive account_type and normal_balance from COA code prefix.
    Rules: Assets=Debit, Liabilities=Credit, Equity=Credit, Revenue=Credit, Expenses=Debit.
    Returns (AccountType, NormalBalance) when code follows our scheme (1-6 prefix), else None."""
    if not code or not code.strip():
        return None
    c = code.strip()
    if c.startswith("SL-"):
        return (AccountType.ASSET, NormalBalance.DEBIT)
    if not c[0].isdigit():
        return None
    prefix = int(c[0])
    if prefix == 1:
        return (AccountType.ASSET, NormalBalance.DEBIT)
    if prefix == 2:
        return (AccountType.LIABILITY, NormalBalance.CREDIT)
    if prefix == 3:
        return (AccountType.EQUITY, NormalBalance.CREDIT)
    if prefix == 4:
        return (AccountType.REVENUE, NormalBalance.CREDIT)
    if prefix in (5, 6):
        return (AccountType.EXPENSE, NormalBalance.DEBIT)
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
        **kwargs: Any,
    ) -> PromoteResult:
        code = _str(mapped_data, "code")
        name = _str(mapped_data, "name")
        # Require code to be present; do not use name as code when code is missing (promotion contract).
        if not code:
            return PromoteResult(success=False, error="Missing code")

        account_key_to_target_code = kwargs.get("account_key_to_target_code")
        account_key_to_target_name = kwargs.get("account_key_to_target_name")
        account_id_for_code = kwargs.get("account_id_for_code")

        account_id = None
        used_target_code = False
        if (
            isinstance(account_key_to_target_code, dict)
            and callable(account_id_for_code)
        ):
            target_code = account_key_to_target_code.get(name) or (
                account_key_to_target_code.get(code) if code else None
            )
            if target_code:
                if isinstance(account_key_to_target_name, dict):
                    name = account_key_to_target_name.get(name) or account_key_to_target_name.get(code) or name
                code = target_code[:50] if len(target_code) > 50 else target_code
                account_id = account_id_for_code(code)
                used_target_code = True

        if not code and not name:
            return PromoteResult(success=False, error="Missing code (and no name to use as code)")
        if not code:
            code = name
        if not code:
            return PromoteResult(success=False, error="Missing code (and no name to use as code)")
        # Kernel Account.code is String(50); truncate when using long names as code
        if len(code) > 50:
            code = code[:50]
        name = name or code

        # Always derive type and normal_balance from code when it follows our COA (1xxx=Asset/Debit,
        # 2xxx=Liability/Credit, 3xxx=Equity/Credit, 4xxx=Revenue/Credit, 5-6xxx=Expense/Debit).
        # Only fall back to QBO/mapped_data when code is non-numeric (e.g. legacy or external).
        derived = _account_type_and_normal_balance_from_code(code)
        if derived is not None:
            account_type, normal_balance = derived
        else:
            account_type_str = _str(mapped_data, "account_type") or "asset"
            account_type = _parse_account_type(account_type_str)
            normal_balance_str = _str(mapped_data, "normal_balance")
            if normal_balance_str:
                try:
                    normal_balance = NormalBalance(normal_balance_str.lower())
                except ValueError:
                    normal_balance = _default_normal_balance(account_type)
            else:
                normal_balance = _default_normal_balance(account_type)

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

        account_kw: dict[str, Any] = dict(
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
        if account_id is not None:
            account_kw["id"] = account_id
        account = Account(**account_kw)
        session.add(account)
        session.flush()
        return PromoteResult(success=True, entity_id=account.id)

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        code = _str(mapped_data, "code")
        if not code:
            return False
        stmt = select(Account).where(Account.code == code)
        return session.scalars(stmt).first() is not None
