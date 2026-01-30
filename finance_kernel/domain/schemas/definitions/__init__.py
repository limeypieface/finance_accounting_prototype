"""
Event schema definitions.

Import this module to auto-register all defined schemas.
"""

# Import all schema definition modules to trigger registration
from finance_kernel.domain.schemas.definitions import (
    ap,
    ar,
    asset,
    bank,
    contract,
    dcaa,
    deferred,
    fx,
    generic,
    inventory,
    payroll,
)

__all__ = [
    "ap",
    "ar",
    "asset",
    "bank",
    "contract",
    "dcaa",
    "deferred",
    "fx",
    "generic",
    "inventory",
    "payroll",
]
