"""
Travel & Expense Pure Functions.

Stateless calculations for mileage, per diem, and policy validation.
No I/O, no session, no clock — pure input/output.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Sequence
from uuid import UUID

if TYPE_CHECKING:
    from finance_modules.expense.models import (
        ExpenseLine,
        ExpensePolicy,
        MileageRate,
        PerDiemRate,
        PolicyViolation,
    )


def calculate_mileage(miles: Decimal, rate_per_mile: Decimal) -> Decimal:
    """
    Calculate mileage reimbursement.

    Args:
        miles: Number of miles driven (must be non-negative).
        rate_per_mile: Rate per mile (e.g. Decimal("0.67")).

    Returns:
        Total mileage reimbursement amount.

    Raises:
        ValueError: If miles or rate_per_mile is negative.
    """
    if miles < 0:
        raise ValueError(f"Miles must be non-negative, got {miles}")
    if rate_per_mile < 0:
        raise ValueError(f"Rate per mile must be non-negative, got {rate_per_mile}")
    return miles * rate_per_mile


def calculate_per_diem(
    days: int,
    rates: PerDiemRate,
    include_meals: bool = True,
    include_lodging: bool = True,
    include_incidentals: bool = True,
) -> Decimal:
    """
    Calculate per diem allowance for a trip.

    Args:
        days: Number of travel days (must be positive).
        rates: Per diem rates for the location.
        include_meals: Whether to include meals allowance.
        include_lodging: Whether to include lodging allowance.
        include_incidentals: Whether to include incidentals allowance.

    Returns:
        Total per diem amount.

    Raises:
        ValueError: If days is not positive.
    """
    if days <= 0:
        raise ValueError(f"Days must be positive, got {days}")

    daily_total = Decimal("0")
    if include_meals:
        daily_total += rates.meals_rate
    if include_lodging:
        daily_total += rates.lodging_rate
    if include_incidentals:
        daily_total += rates.incidentals_rate

    return daily_total * days


def validate_expense_against_policy(
    lines: Sequence[ExpenseLine],
    policies: dict[str, ExpensePolicy],
) -> list[PolicyViolation]:
    """
    Validate expense lines against category policies.

    Args:
        lines: Expense lines to validate.
        policies: Mapping of category value -> ExpensePolicy.

    Returns:
        List of PolicyViolation for any violations found.
        Empty list if all lines comply.
    """
    from finance_modules.expense.models import PolicyViolation

    violations: list[PolicyViolation] = []

    for line in lines:
        category_key = line.category.value
        policy = policies.get(category_key)
        if policy is None:
            continue

        # Check per-transaction limit
        if policy.per_transaction_limit is not None:
            if line.amount > policy.per_transaction_limit:
                violations.append(PolicyViolation(
                    line_id=line.id,
                    violation_type="OVER_LIMIT",
                    category=category_key,
                    amount=line.amount,
                    limit=policy.per_transaction_limit,
                    message=(
                        f"{category_key} expense {line.amount} exceeds "
                        f"per-transaction limit of {policy.per_transaction_limit}"
                    ),
                ))

        # Check receipt requirement
        if policy.requires_receipt_above is not None:
            if line.amount > policy.requires_receipt_above and not line.receipt_attached:
                violations.append(PolicyViolation(
                    line_id=line.id,
                    violation_type="MISSING_RECEIPT",
                    category=category_key,
                    amount=line.amount,
                    limit=policy.requires_receipt_above,
                    message=(
                        f"{category_key} expense {line.amount} exceeds "
                        f"receipt threshold of {policy.requires_receipt_above} "
                        f"but no receipt attached"
                    ),
                ))

        # Check justification requirement
        if policy.requires_justification:
            if not line.description or line.description.strip() == "":
                violations.append(PolicyViolation(
                    line_id=line.id,
                    violation_type="MISSING_JUSTIFICATION",
                    category=category_key,
                    amount=line.amount,
                    limit=None,
                    message=(
                        f"{category_key} expense requires justification "
                        f"but description is empty"
                    ),
                ))

    return violations
