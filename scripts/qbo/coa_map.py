"""
CoA mapping with recommendations: suggest target account code per QBO account.

Given input CoA (from QBO) and a chosen config COA (e.g. US-GAAP-2026-v1),
recommends for each input account a target account code (and role) based on
QBO account_type → role → config's role_bindings. When there is no match,
suggests a new account code in a logical numbering scheme. User can edit
target_code and target_name to either map to an existing account or create
a new one. Upload flow (Phase 3): create all relevant accounts first, then
upload journals once those accounts exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from scripts.qbo.coa_recommend import QBO_ACCOUNT_TYPE_TO_ROLES

# Logical numbering: QBO account_type → 2-digit prefix for suggested new codes.
# US-GAAP style: 10xx cash/bank, 11xx AR, 12xx inventory, 13xx prepaid, 15xx fixed,
# 20xx AP/liability, 31xx equity, 40xx revenue, 50xx COGS, 60xx expense.
QBO_ACCOUNT_TYPE_TO_PREFIX: dict[str, str] = {
    "Bank": "10",
    "Credit Card": "24",
    "Equity": "31",
    "Cost of Goods Sold": "50",
    "Expenses": "60",
    "Income": "40",
    "Other Income": "40",
    "Accounts Receivable": "11",
    "Accounts Payable": "20",
    "Other Current Asset": "13",
    "Fixed Asset": "15",
    "Other Current Liability": "22",
    "Long Term Liability": "22",
    "Fixed Assets": "15",
    "Long Term Liabilities": "22",
    "Other Current Assets": "13",
    "Other Assets": "19",
    "Other Current Liabilities": "22",
    "Other Expense": "66",
}


@dataclass(frozen=True)
class AccountMappingRecommendation:
    """One QBO account: recommended/mapped target or new account (code + name)."""

    input_name: str
    input_code: str | None
    input_type: str
    import_row: int | None
    recommended_role: str | None
    recommended_code: str | None
    suggested_new_code: str | None  # when no match: suggested code in numbering scheme
    target_code: str  # final: existing code (map) or new code (create)
    target_name: str  # display name for new account; user can adjust


def _role_to_code_dict(role_to_code: frozenset[tuple[str, str]]) -> dict[str, str]:
    """Build role → account_code dict; first occurrence per role wins."""
    return dict(role_to_code)


def _existing_codes_from_config(config_option: object) -> set[str]:
    """Collect all account codes from config's role_to_code."""
    role_to_code = getattr(config_option, "role_to_code", frozenset())
    return {code for _, code in role_to_code}


def suggest_new_code(
    account_type: str,
    used_codes: set[str],
    type_to_prefix: dict[str, str] | None = None,
    config_codes: set[str] | None = None,
) -> str:
    """
    Suggest next account code in standard accounting range for this QBO type.

    Uses QBO_ACCOUNT_TYPE_TO_PREFIX: 10xx Bank, 11xx AR, 20xx AP, 31xx Equity,
    40xx Revenue, 50xx COGS, 60xx Expense. Stays within [base, base+99] so we
    never assign 1100 (AR) to Bank or 6100 (Scrap) to a generic expense. Picks
    next multiple of 10 in range, or first gap; fallback base+91, base+92, ...
    """
    mapping = type_to_prefix or QBO_ACCOUNT_TYPE_TO_PREFIX
    prefix = mapping.get(account_type) or mapping.get(account_type.strip()) or "69"
    if not prefix.isdigit():
        prefix = "69"
    base = int(prefix) * 100
    high = base + 99
    config = config_codes or set()
    # Only consider codes in this type's range [base, base+99]
    in_range: set[int] = set()
    for c in used_codes:
        if not c or not c.isdigit():
            continue
        try:
            n = int(c)
            if base <= n <= high:
                in_range.add(n)
        except ValueError:
            pass
    # Standard step is 10 (1000, 1010, 1020, ...); find first free slot
    for code in range(base, high + 1, 10):
        if code not in in_range:
            return str(code)
    # Exhausted 10-step slots; use 91, 92, ... (e.g. 1091, 1092)
    for code in range(base + 91, high + 1):
        if code not in in_range:
            return str(code)
    # Range [base, base+99] full; overflow into next block (base+100 to base+199), step 10, skip config
    config = config_codes or set()
    used_str = {str(c) for c in used_codes if c}
    for code in range(base + 100, base + 200, 10):
        s = str(code)
        if s not in used_str and s not in config:
            return s
    for code in range(base + 101, base + 110):
        s = str(code)
        if s not in used_str and s not in config:
            return s
    return str(high)


def recommend_account_mapping(
    input_coa_records: Sequence[object],
    config_option: object,
    type_to_roles: dict[str, tuple[str, ...]] | None = None,
    existing_codes: set[str] | None = None,
    named_accounts: dict[str, str] | None = None,
) -> list[AccountMappingRecommendation]:
    """
    For each input account, assign a target code: match by name if named_accounts
    provided (1:1 map), else use standard numbering (distinct code per type).

    When named_accounts (name → code) is provided (e.g. from accounts_ironflow.yaml),
    each QBO account that matches a name gets that code (map to original). Otherwise
    uses standard ranges (10xx Bank, 60xx Expense, etc.) and suggest_new_code.
    """
    mapping = type_to_roles or QBO_ACCOUNT_TYPE_TO_ROLES
    role_to_code = _role_to_code_dict(getattr(config_option, "role_to_code", frozenset()))
    existing = existing_codes if existing_codes is not None else _existing_codes_from_config(config_option)
    used_new_codes: set[str] = set(existing)
    name_to_code = named_accounts or {}
    results: list[AccountMappingRecommendation] = []
    for r in input_coa_records:
        name = getattr(r, "name", "") or ""
        code = getattr(r, "code", None)
        account_type = (getattr(r, "account_type", None) or "").strip()
        import_row = getattr(r, "import_row", None)
        recommended_role: str | None = None
        recommended_code: str | None = None
        if account_type:
            roles_for_type = mapping.get(account_type) or mapping.get(account_type.strip())
            if roles_for_type:
                for role in roles_for_type:
                    if role in role_to_code:
                        recommended_role = role
                        recommended_code = role_to_code[role]
                        break
        suggested_new_code: str | None = None
        if name and name in name_to_code:
            target_code = name_to_code[name]
            target_name = name
        else:
            suggested_new_code = suggest_new_code(
                account_type, used_new_codes, config_codes=existing
            )
            used_new_codes.add(suggested_new_code)
            target_code = suggested_new_code
            target_name = name
        results.append(
            AccountMappingRecommendation(
                input_name=name,
                input_code=code,
                input_type=account_type,
                import_row=import_row,
                recommended_role=recommended_role,
                recommended_code=recommended_code,
                suggested_new_code=suggested_new_code,
                target_code=target_code,
                target_name=target_name,
            )
        )
    return results


def mapping_to_yaml(recommendations: list[AccountMappingRecommendation], config_id: str) -> str:
    """Serialize mapping to YAML. Edit target_code and target_name to map to existing or create new."""
    lines = [
        "# QBO → System COA mapping. Edit target_code and target_name per row.",
        "# - Map to existing: set target_code to an existing config account code.",
        "# - Create new: set target_code to a new code (or keep suggested) and set target_name.",
        "# Upload flow: first create all accounts (new codes + names), then upload journals.",
        f"config_id: {config_id}",
        "mappings:",
    ]
    for rec in recommendations:
        lines.append(f"  - input_name: {_yaml_str(rec.input_name)}")
        lines.append(f"    input_code: {_yaml_str(rec.input_code or '')}")
        lines.append(f"    input_type: {_yaml_str(rec.input_type)}")
        if rec.import_row is not None:
            lines.append(f"    import_row: {rec.import_row}")
        lines.append(f"    recommended_role: {_yaml_str(rec.recommended_role or '')}")
        lines.append(f"    recommended_code: {_yaml_str(rec.recommended_code or '')}")
        if rec.suggested_new_code:
            lines.append(f"    suggested_new_code: {_yaml_str(rec.suggested_new_code)}")
        lines.append(f"    target_code: {_yaml_str(rec.target_code)}")
        lines.append(f"    target_name: {_yaml_str(rec.target_name)}")
    return "\n".join(lines)


def _yaml_str(s: str) -> str:
    """Quote string for YAML if it contains special chars."""
    if not s:
        return "''"
    if any(c in s for c in ":[]{}#&*!|>\"'%\n"):
        return repr(s)
    return f"'{s}'"
