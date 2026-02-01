"""
Lease Accounting Pure Calculation Functions — ASC 842.

Domain math that engines don't cover:
- Present value of lease payments
- Amortization schedule building
- Lease classification test
- Liability remeasurement
- ROU asset adjustment
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence


def present_value(
    payment: Decimal,
    rate_per_period: Decimal,
    num_periods: int,
) -> Decimal:
    """
    Calculate present value of an annuity (level payments).

    PV = payment * [(1 - (1 + r)^-n) / r]
    """
    if rate_per_period <= 0 or num_periods <= 0:
        return payment * Decimal(str(num_periods))

    one_plus_r = Decimal("1") + rate_per_period
    # (1 + r)^-n
    discount_factor = Decimal("1")
    for _ in range(num_periods):
        discount_factor /= one_plus_r

    pv_factor = (Decimal("1") - discount_factor) / rate_per_period
    return (payment * pv_factor).quantize(Decimal("0.01"))


def build_amortization_schedule(
    principal: Decimal,
    rate_per_period: Decimal,
    payment: Decimal,
    num_periods: int,
    start_date: date,
) -> list[dict]:
    """
    Build an amortization schedule for a lease liability.

    Returns list of dicts with: period, payment_date, payment, interest, principal, balance.
    """
    schedule = []
    balance = principal

    for period in range(1, num_periods + 1):
        interest = (balance * rate_per_period).quantize(Decimal("0.01"))
        principal_portion = payment - interest
        balance = balance - principal_portion

        # Ensure last period clears balance
        if period == num_periods and balance != Decimal("0"):
            principal_portion += balance
            balance = Decimal("0")

        payment_date = start_date + timedelta(days=30 * period)

        schedule.append({
            "period": period,
            "payment_date": payment_date,
            "payment": payment,
            "interest": interest,
            "principal": principal_portion,
            "balance": max(balance, Decimal("0")),
        })

    return schedule


def classify_lease_type(
    lease_term_months: int,
    economic_life_months: int,
    pv_payments: Decimal,
    fair_value: Decimal,
    transfer_ownership: bool = False,
    purchase_option: bool = False,
    specialized_asset: bool = False,
) -> str:
    """
    Classify a lease as finance or operating per ASC 842-10-25-2.

    Finance lease if ANY of:
    1. Transfer of ownership
    2. Purchase option reasonably certain to exercise
    3. Lease term >= 75% of economic life
    4. PV of payments >= 90% of fair value
    5. Specialized nature with no alternative use
    """
    if transfer_ownership:
        return "finance"
    if purchase_option:
        return "finance"
    if economic_life_months > 0:
        term_ratio = Decimal(str(lease_term_months)) / Decimal(str(economic_life_months))
        if term_ratio >= Decimal("0.75"):
            return "finance"
    if fair_value > 0:
        pv_ratio = pv_payments / fair_value
        if pv_ratio >= Decimal("0.90"):
            return "finance"
    if specialized_asset:
        return "finance"

    return "operating"


def remeasure_liability(
    current_balance: Decimal,
    new_payment: Decimal,
    new_rate: Decimal,
    remaining_periods: int,
) -> Decimal:
    """
    Remeasure lease liability after modification.

    Returns new liability value as PV of remaining payments.
    """
    return present_value(new_payment, new_rate, remaining_periods)


def calculate_rou_adjustment(
    current_rou: Decimal,
    old_liability: Decimal,
    new_liability: Decimal,
) -> Decimal:
    """
    Calculate ROU asset adjustment after lease modification.

    Adjustment = change in liability (proportional to remaining ROU).
    """
    return new_liability - old_liability
