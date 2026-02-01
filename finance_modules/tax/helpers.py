"""
Tax Helpers -- Pure calculation functions for deferred tax.

Responsibility:
    Pure arithmetic for ASC 740 deferred-tax concepts that the shared
    ``TaxCalculator`` engine does not cover: temporary differences, valuation
    allowance, effective tax rate, and multi-jurisdiction aggregation.

Architecture:
    finance_modules -- Thin ERP glue (this layer).
    Every function is pure: no I/O, no side effects, no database.

Invariants:
    - All inputs and outputs are ``Decimal`` -- NEVER ``float``.
    - Division-by-zero cases return ``Decimal("0")`` instead of raising.

Failure modes:
    - ``aggregate_multi_jurisdiction`` raises ``KeyError`` if a jurisdiction
      dict lacks ``taxable_amount`` or ``tax_amount``.

Audit relevance:
    - These functions produce intermediate values consumed by ``TaxService``
      for journal entry amounts.  Results are not persisted directly but
      flow into event payloads that become part of the audit trail.
"""

from __future__ import annotations

from decimal import Decimal


def calculate_temporary_differences(
    book_basis: Decimal,
    tax_basis: Decimal,
) -> tuple[Decimal, str]:
    """
    Calculate temporary difference between book and tax basis.

    Preconditions:
        - ``book_basis`` and ``tax_basis`` are ``Decimal``.
    Postconditions:
        - Returns ``(difference_amount, difference_type)`` where type is
          ``'deductible'`` (creates DTA), ``'taxable'`` (creates DTL),
          or ``'none'`` (zero difference).
        - ``difference_amount`` >= 0.
    """
    assert isinstance(book_basis, Decimal), "book_basis must be Decimal"
    assert isinstance(tax_basis, Decimal), "tax_basis must be Decimal"
    diff = book_basis - tax_basis
    if diff > 0:
        return diff, "taxable"
    elif diff < 0:
        return abs(diff), "deductible"
    return Decimal("0"), "none"


def calculate_dta_valuation_allowance(
    dta_amount: Decimal,
    realizability_percentage: Decimal,
) -> Decimal:
    """
    Calculate valuation allowance for deferred tax asset.

    Preconditions:
        - ``dta_amount`` >= 0.
        - ``realizability_percentage`` in [0, 1].
    Postconditions:
        - Returns valuation allowance in [0, dta_amount].

    If not all DTA is expected to be realized, a valuation allowance
    reduces the net DTA. realizability_percentage is 0-1 (e.g. 0.70 = 70% realizable).
    """
    if realizability_percentage >= Decimal("1"):
        return Decimal("0")
    if realizability_percentage <= Decimal("0"):
        return dta_amount
    unrealizable = Decimal("1") - realizability_percentage
    return (dta_amount * unrealizable).quantize(Decimal("0.01"))


def calculate_effective_tax_rate(
    total_tax_expense: Decimal,
    pre_tax_income: Decimal,
) -> Decimal:
    """
    Calculate effective tax rate.

    Preconditions:
        - Both arguments are ``Decimal``.
    Postconditions:
        - Returns rate as decimal (e.g. 0.25 for 25%).
        - Returns ``Decimal("0")`` if ``pre_tax_income`` is zero (avoids
          division-by-zero).
    """
    if pre_tax_income == 0:
        return Decimal("0")
    return (total_tax_expense / pre_tax_income).quantize(Decimal("0.0001"))


def aggregate_multi_jurisdiction(
    jurisdictions: list[dict],
) -> dict:
    """
    Aggregate tax amounts across multiple jurisdictions.

    Preconditions:
        - Each dict must contain ``taxable_amount`` and ``tax_amount`` keys.
    Postconditions:
        - Returns summary dict with ``jurisdiction_count``,
          ``total_taxable``, ``total_tax``, ``weighted_average_rate``.
    Raises:
        ``KeyError`` if a jurisdiction dict lacks required keys.

    Each dict has: jurisdiction, taxable_amount, tax_rate, tax_amount.
    Returns summary dict with totals and weighted average rate.
    """
    total_taxable = Decimal("0")
    total_tax = Decimal("0")

    for j in jurisdictions:
        total_taxable += Decimal(str(j["taxable_amount"]))
        total_tax += Decimal(str(j["tax_amount"]))

    weighted_rate = Decimal("0")
    if total_taxable > 0:
        weighted_rate = (total_tax / total_taxable).quantize(Decimal("0.0001"))

    return {
        "jurisdiction_count": len(jurisdictions),
        "total_taxable": total_taxable,
        "total_tax": total_tax,
        "weighted_average_rate": weighted_rate,
    }
