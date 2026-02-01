"""
Earned Value Management (EVM) Calculations -- Pure Functions.

All functions are pure: no I/O, no side effects, no database.
They compute EVM metrics from input parameters.
"""

from __future__ import annotations

from decimal import Decimal


def calculate_bcws(
    total_budget: Decimal,
    planned_pct_complete: Decimal,
) -> Decimal:
    """Budgeted Cost of Work Scheduled (Planned Value)."""
    return (total_budget * planned_pct_complete).quantize(Decimal("0.01"))


def calculate_bcwp(
    total_budget: Decimal,
    actual_pct_complete: Decimal,
) -> Decimal:
    """Budgeted Cost of Work Performed (Earned Value)."""
    return (total_budget * actual_pct_complete).quantize(Decimal("0.01"))


def calculate_acwp(
    actual_costs: Decimal,
) -> Decimal:
    """Actual Cost of Work Performed (just a passthrough for clarity)."""
    return actual_costs


def calculate_cpi(
    bcwp: Decimal,
    acwp: Decimal,
) -> Decimal:
    """Cost Performance Index = EV / AC. >1 = under budget."""
    if acwp == 0:
        return Decimal("0")
    return (bcwp / acwp).quantize(Decimal("0.0001"))


def calculate_spi(
    bcwp: Decimal,
    bcws: Decimal,
) -> Decimal:
    """Schedule Performance Index = EV / PV. >1 = ahead of schedule."""
    if bcws == 0:
        return Decimal("0")
    return (bcwp / bcws).quantize(Decimal("0.0001"))


def calculate_eac(
    bac: Decimal,
    cpi: Decimal,
) -> Decimal:
    """Estimate at Completion = BAC / CPI."""
    if cpi == 0:
        return Decimal("0")
    return (bac / cpi).quantize(Decimal("0.01"))


def calculate_etc(
    eac: Decimal,
    acwp: Decimal,
) -> Decimal:
    """Estimate to Complete = EAC - AC."""
    return (eac - acwp).quantize(Decimal("0.01"))


def calculate_vac(
    bac: Decimal,
    eac: Decimal,
) -> Decimal:
    """Variance at Completion = BAC - EAC."""
    return (bac - eac).quantize(Decimal("0.01"))
