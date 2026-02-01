"""
Payroll Helpers (``finance_modules.payroll.helpers``).

Responsibility
--------------
Pure calculation functions for US payroll tax withholding (federal,
state, FICA) and NACHA/ACH batch generation.  These are textbook
payroll-tax formulas and a structured format builder with no side
effects.

Architecture position
---------------------
**Modules layer** -- pure helper functions.  No I/O, no session, no
clock, no database access.  Called by ``PayrollService`` or from tests.

Invariants enforced
-------------------
* All numeric inputs and outputs use ``Decimal`` -- NEVER ``float``.
* Results are quantized to 2 decimal places.
* Social Security wage-base cap is enforced per pay period.

Failure modes
-------------
* Zero or negative gross pay -> returns ``Decimal("0")`` tax.
* YTD earnings already above SS wage base -> SS tax is ``Decimal("0")``.
* Empty payment list -> batch contains only header and control lines.

Audit relevance
---------------
These functions produce intermediate values consumed by
``PayrollService`` for journal entry amounts.  Withholding methodology
must be documented and consistently applied.  NACHA batch format
supports direct-deposit audit trail requirements.
"""

from __future__ import annotations

from decimal import Decimal


def calculate_federal_withholding(
    gross_pay: Decimal,
    filing_status: str = "single",
    allowances: int = 0,
) -> Decimal:
    """
    Calculate federal income tax withholding (simplified bracket model).

    Uses simplified 2024 brackets. Filing status: 'single' or 'married'.
    Each allowance reduces taxable by ~$4,300 annually (~$358.33/pay period).

    Preconditions:
        - ``gross_pay`` is ``Decimal``.
        - ``filing_status`` is ``"single"`` or ``"married"``.
        - ``allowances`` is a non-negative integer.
    Postconditions:
        - Returns federal withholding as ``Decimal`` quantized to 0.01.
        - Returns ``Decimal("0")`` if taxable income (after allowances) <= 0.
    """
    allowance_amount = Decimal(str(allowances)) * Decimal("358.33")
    taxable = max(Decimal("0"), gross_pay - allowance_amount)

    if filing_status == "married":
        brackets = [
            (Decimal("958"), Decimal("0.10")),
            (Decimal("2883"), Decimal("0.12")),
            (Decimal("6958"), Decimal("0.22")),
            (Decimal("12708"), Decimal("0.24")),
            (Decimal("18375"), Decimal("0.32")),
            (Decimal("22542"), Decimal("0.35")),
            (Decimal("999999"), Decimal("0.37")),
        ]
    else:
        brackets = [
            (Decimal("479"), Decimal("0.10")),
            (Decimal("1923"), Decimal("0.12")),
            (Decimal("3960"), Decimal("0.22")),
            (Decimal("7356"), Decimal("0.24")),
            (Decimal("9189"), Decimal("0.32")),
            (Decimal("11271"), Decimal("0.35")),
            (Decimal("999999"), Decimal("0.37")),
        ]

    tax = Decimal("0")
    remaining = taxable
    prev_limit = Decimal("0")

    for limit, rate in brackets:
        bracket_income = min(remaining, limit - prev_limit)
        if bracket_income <= 0:
            break
        tax += (bracket_income * rate).quantize(Decimal("0.01"))
        remaining -= bracket_income
        prev_limit = limit

    return tax


def calculate_state_withholding(
    gross_pay: Decimal,
    state_rate: Decimal = Decimal("0.05"),
) -> Decimal:
    """
    Calculate state income tax withholding (flat rate model).

    Most states use a simplified flat rate or progressive brackets.
    This uses a configurable flat rate for simplicity.

    Preconditions:
        - ``gross_pay`` and ``state_rate`` are ``Decimal``.
        - ``state_rate`` is between 0 and 1 (inclusive).
    Postconditions:
        - Returns state withholding as ``Decimal`` quantized to 0.01.
    """
    return (gross_pay * state_rate).quantize(Decimal("0.01"))


def calculate_fica(
    gross_pay: Decimal,
    ytd_earnings: Decimal = Decimal("0"),
    ss_wage_base: Decimal = Decimal("168600"),
    ss_rate: Decimal = Decimal("0.062"),
    medicare_rate: Decimal = Decimal("0.0145"),
    additional_medicare_threshold: Decimal = Decimal("200000"),
    additional_medicare_rate: Decimal = Decimal("0.009"),
) -> tuple[Decimal, Decimal]:
    """
    Calculate FICA taxes (Social Security + Medicare).

    Returns tuple of (social_security_tax, medicare_tax).
    Handles SS wage base limit and additional Medicare tax.

    Preconditions:
        - All monetary arguments are ``Decimal``.
        - ``gross_pay`` >= 0.
        - ``ytd_earnings`` >= 0 (cumulative prior period earnings).
    Postconditions:
        - Returns ``(ss_tax, medicare_tax)`` both quantized to 0.01.
        - SS tax is zero when YTD earnings already exceed ``ss_wage_base``.
        - Additional Medicare tax applies only when ``ytd_earnings + gross_pay``
          exceeds ``additional_medicare_threshold``.
    """
    # Social Security
    remaining_ss_wages = max(Decimal("0"), ss_wage_base - ytd_earnings)
    ss_taxable = min(gross_pay, remaining_ss_wages)
    ss_tax = (ss_taxable * ss_rate).quantize(Decimal("0.01"))

    # Medicare
    medicare_tax = (gross_pay * medicare_rate).quantize(Decimal("0.01"))

    # Additional Medicare on amounts over threshold
    ytd_plus = ytd_earnings + gross_pay
    if ytd_plus > additional_medicare_threshold:
        additional_wages = min(gross_pay, ytd_plus - additional_medicare_threshold)
        additional_wages = max(Decimal("0"), additional_wages)
        medicare_tax += (additional_wages * additional_medicare_rate).quantize(
            Decimal("0.01")
        )

    return ss_tax, medicare_tax


def generate_nacha_batch(
    payments: list[dict],
    company_name: str,
    company_id: str,
    effective_date: str,
) -> str:
    """
    Generate a NACHA/ACH batch for payroll direct deposits.

    Each payment dict should have: name, account, routing, amount.
    Returns pipe-delimited representation of ACH batch.

    Preconditions:
        - ``payments`` is a list of dicts each containing ``name``,
          ``account``, ``routing``, and ``amount`` keys.
        - ``company_name`` and ``company_id`` are non-empty strings.
        - ``effective_date`` is a date string (e.g. ``"2024-01-15"``).
    Postconditions:
        - Returns a newline-delimited string with header, entry, and
          control lines.
        - Control line total equals sum of all payment amounts.
    """
    lines: list[str] = []
    lines.append(
        f"PAYROLL_BATCH_HEADER|PPD|{company_name}|{company_id}|{effective_date}"
    )

    total = Decimal("0")
    for i, payment in enumerate(payments, 1):
        amount = Decimal(str(payment.get("amount", "0")))
        total += amount
        lines.append(
            f"PAYROLL_ENTRY|{i}|{payment.get('routing', '')}|"
            f"{payment.get('account', '')}|{amount}|{payment.get('name', '')}"
        )

    lines.append(f"PAYROLL_BATCH_CONTROL|{len(payments)}|{total}")
    return "\n".join(lines)
