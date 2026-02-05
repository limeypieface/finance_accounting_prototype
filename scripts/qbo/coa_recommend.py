"""
Pure CoA recommendation engine: score input CoA against config COA options.

Given a list of input account types (from QBO) and a config's set of roles,
returns a score in [0, 1] indicating how well the config "covers" the input
account types. Used to recommend which config set best matches a QBO export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# QBO account_type (from QBO exports) -> primary system role(s) we use for coverage.
# If the config has any of these roles, we count that input type as covered.
QBO_ACCOUNT_TYPE_TO_ROLES: dict[str, tuple[str, ...]] = {
    "Bank": ("CASH", "BANK", "BANK_DESTINATION", "BANK_SOURCE", "UNDEPOSITED_FUNDS"),
    "Credit Card": ("CORPORATE_CARD_LIABILITY", "ACCRUED_LIABILITY", "EXPENSE"),
    "Equity": ("RETAINED_EARNINGS", "EQUITY", "INCOME_SUMMARY", "RoundingExpense"),
    "Cost of Goods Sold": ("COGS", "EXPENSE"),
    "Expenses": ("EXPENSE", "SALARY_EXPENSE", "WAGE_EXPENSE", "PAYROLL_TAX_EXPENSE", "DEPRECIATION_EXPENSE"),
    "Income": ("REVENUE", "INTEREST_INCOME", "FEE_EARNED"),
    "Other Income": ("REVENUE", "INTEREST_INCOME"),
    "Accounts Receivable": ("ACCOUNTS_RECEIVABLE", "AR_CONTROL"),
    "Accounts Payable": ("ACCOUNTS_PAYABLE", "AP_CONTROL"),
    "Other Current Asset": ("PREPAID_EXPENSE", "INVENTORY", "INVENTORY_IN_TRANSIT"),
    "Fixed Asset": ("FIXED_ASSET", "ACCUMULATED_DEPRECIATION", "CIP"),
    "Other Current Liability": ("ACCRUED_LIABILITY", "TAX_PAYABLE", "ACCRUED_PAYROLL"),
    "Long Term Liability": ("ACCRUED_LIABILITY",),
    # QBO export variants (plural / alternate labels)
    "Fixed Assets": ("FIXED_ASSET", "ACCUMULATED_DEPRECIATION", "CIP"),
    "Long Term Liabilities": ("ACCRUED_LIABILITY",),
    "Other Current Assets": ("PREPAID_EXPENSE", "INVENTORY", "INVENTORY_IN_TRANSIT"),
    "Other Assets": ("PREPAID_EXPENSE", "FIXED_ASSET"),
    "Other Current Liabilities": ("ACCRUED_LIABILITY", "TAX_PAYABLE", "ACCRUED_PAYROLL"),
    "Other Expense": ("EXPENSE", "BAD_DEBT_EXPENSE"),
}


def _unique_account_types(records: Sequence[object]) -> set[str]:
    """Extract unique account_type values from InputCoARecord-like objects (have .account_type)."""
    out: set[str] = set()
    for r in records:
        t = getattr(r, "account_type", None)
        if t and isinstance(t, str):
            out.add(t.strip())
    return out


def score_config_coa(
    input_account_types: set[str],
    config_roles: frozenset[str],
    type_to_roles: dict[str, tuple[str, ...]] | None = None,
) -> float:
    """
    Pure: score how well a config COA covers the input account types.

    For each distinct input account_type, we check if the config has at least
    one of the mapped system roles. Score = (covered types) / max(1, total unique types).
    Returns a value in [0.0, 1.0].
    """
    mapping = type_to_roles or QBO_ACCOUNT_TYPE_TO_ROLES
    if not input_account_types:
        return 0.0
    covered = 0
    for qbo_type in input_account_types:
        roles_for_type = mapping.get(qbo_type) or mapping.get(qbo_type.strip())
        if not roles_for_type:
            # Unknown QBO type: count as covered if config has generic EXPENSE/REVENUE
            if "EXPENSE" in config_roles or "REVENUE" in config_roles:
                covered += 1
            continue
        if any(r in config_roles for r in roles_for_type):
            covered += 1
    return covered / max(1, len(input_account_types))


@dataclass(frozen=True)
class RecommendationEntry:
    """A single recommendation: config_id and score."""

    config_id: str
    score: float


def recommend_coa(
    input_coa_records: Sequence[object],
    config_options: Sequence[object],
    type_to_roles: dict[str, tuple[str, ...]] | None = None,
) -> list[RecommendationEntry]:
    """
    Pure: rank config COA options by coverage of input CoA.

    input_coa_records: objects with .account_type (e.g. InputCoARecord).
    config_options: objects with .config_id and .roles (e.g. ConfigCoAOption).
    Returns list of RecommendationEntry (config_id, score), sorted by score descending.
    """
    input_types = _unique_account_types(input_coa_records)
    mapping = type_to_roles or QBO_ACCOUNT_TYPE_TO_ROLES
    results: list[RecommendationEntry] = []
    for opt in config_options:
        config_id = getattr(opt, "config_id", None)
        roles = getattr(opt, "roles", None)
        if config_id is None or roles is None:
            continue
        if not isinstance(roles, (set, frozenset)):
            continue
        score = score_config_coa(input_types, frozenset(roles), mapping)
        results.append(RecommendationEntry(config_id=config_id, score=score))
    results.sort(key=lambda e: (e.score, e.config_id), reverse=True)
    return results
