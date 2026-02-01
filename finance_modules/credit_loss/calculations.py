"""
Credit Loss Calculations — Pure Functions.

CECL methodology calculations: ECL loss rate, PD/LGD,
vintage loss curves, forward-looking adjustments, and provision changes.
"""

from __future__ import annotations

from decimal import Decimal


def calculate_ecl_loss_rate(
    gross_balance: Decimal,
    historical_loss_rate: Decimal,
) -> Decimal:
    """
    Calculate Expected Credit Loss using loss rate method.

    ECL = Gross Balance * Historical Loss Rate
    """
    return (gross_balance * historical_loss_rate).quantize(Decimal("0.01"))


def calculate_ecl_pd_lgd(
    gross_balance: Decimal,
    probability_of_default: Decimal,
    loss_given_default: Decimal,
    exposure_at_default: Decimal | None = None,
) -> Decimal:
    """
    Calculate ECL using PD * LGD * EAD method.

    If EAD is not provided, uses gross_balance as EAD.
    """
    ead = exposure_at_default if exposure_at_default is not None else gross_balance
    return (ead * probability_of_default * loss_given_default).quantize(Decimal("0.01"))


def calculate_vintage_loss_curve(
    original_balance: Decimal,
    cumulative_losses: Decimal,
) -> Decimal:
    """
    Calculate cumulative loss rate for a vintage cohort.

    Returns loss rate as decimal (e.g. 0.035 for 3.5%).
    """
    if original_balance == 0:
        return Decimal("0")
    return (cumulative_losses / original_balance).quantize(Decimal("0.0001"))


def apply_forward_looking_adjustment(
    base_rate: Decimal,
    adjustment_pct: Decimal,
) -> Decimal:
    """
    Apply forward-looking adjustment to a base loss rate.

    adjustment_pct is the percentage change (e.g., 0.10 for 10% increase).
    Returns adjusted rate.
    """
    adjustment = base_rate * adjustment_pct
    return (base_rate + adjustment).quantize(Decimal("0.0001"))


def calculate_provision_change(
    new_ecl: Decimal,
    existing_allowance: Decimal,
) -> Decimal:
    """
    Calculate the provision change needed.

    Positive = need to increase allowance (expense).
    Negative = can release allowance (income).
    """
    return new_ecl - existing_allowance
