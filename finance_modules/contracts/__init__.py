"""
Contracts Module.

Handles government and commercial contract accounting including cost
incurrence, billing, fee accruals, indirect allocations, rate adjustments,
and DCAA compliance (allowability segregation per FAR 31.205).

Total: 29 profiles (18 contract + 11 DCAA compliance).
"""

from finance_modules.contracts.profiles import CONTRACT_PROFILES
from finance_modules.contracts.config import ContractsConfig
from finance_modules.contracts.workflows import ContractLifecycleState

__all__ = [
    "CONTRACT_PROFILES",
    "ContractsConfig",
    "ContractLifecycleState",
]
