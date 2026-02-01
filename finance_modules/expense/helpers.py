"""
Expense Helpers (``finance_modules.expense.helpers``).

Responsibility
--------------
Pure calculation functions for travel & expense processing: mileage
reimbursement, per diem allowance, and expense-line policy validation.
These are stateless functions with no side effects.

Architecture position
---------------------
**Modules layer** -- pure helper functions.  No I/O, no session, no
clock, no database access.  Called by ``ExpenseService`` or from tests.

Invariants enforced
-------------------
* All numeric inputs and outputs use ``Decimal`` -- NEVER ``float``.
* Negative miles or rates raise ``ValueError`` (explicit failure, R18).
* Policy violations are returned as a list, never silently suppressed.

Failure modes
-------------
* Negative miles or rate -> ``ValueError`` raised.
* Zero or negative days -> ``ValueError`` raised.
* Missing policy for a category -> line is silently skipped (no violation).

Audit relevance
---------------
These functions produce intermediate values and compliance artifacts
consumed by ``ExpenseService``.  Mileage rates must match IRS standard
mileage rates for tax-deductible reimbursements.  Per diem rates must
match GSA/CONUS rates for government contracts (FAR 31.205-46).  Policy
violation records support internal controls over T&E spending.
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

    Preconditions:
        - ``miles`` is ``Decimal`` >= 0.
        - ``rate_per_mile`` is ``Decimal`` >= 0.
    Postconditions:
        - Returns ``miles * rate_per_mile`` as ``Decimal``.
    Raises:
        ValueError: If ``miles`` or ``rate_per_mile`` is negative.
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

    Preconditions:
        - ``days`` is a positive integer (> 0).
        - ``rates`` is a ``PerDiemRate`` with ``meals_rate``,
          ``lodging_rate``, and ``incidentals_rate`` fields.
    Postconditions:
        - Returns total per diem as ``Decimal`` (daily rate * days).
        - Only included components contribute to the daily rate.
    Raises:
        ValueError: If ``days`` is not positive.
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

    Preconditions:
        - ``lines`` is a sequence of ``ExpenseLine`` objects.
        - ``policies`` maps category value strings to ``ExpensePolicy``.
    Postconditions:
        - Returns a list of ``PolicyViolation`` for any violations found.
        - Returns an empty list if all lines comply with their policies.
        - Lines whose category has no matching policy are silently skipped.
        - Checks: per-transaction limit, receipt requirement threshold,
          and justification requirement.
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
