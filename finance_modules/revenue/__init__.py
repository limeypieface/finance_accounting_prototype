"""
Module: finance_modules.revenue
Responsibility:
    Thin ERP glue for ASC 606 five-step revenue recognition model.
    Exposes domain models, economic profiles, and configuration for the
    revenue recognition lifecycle: contract identification, performance
    obligation identification, transaction price determination, price
    allocation (SSP), and revenue recognition.

Architecture:
    finance_modules layer -- declarative profiles, workflow definitions,
    configuration schemas, and frozen domain DTOs.  Contains ZERO business
    logic beyond what is expressed in AccountingPolicy declarations.
    All posting flows through ModulePostingService in the kernel.
    All calculation flows through AllocationEngine or local pure helpers.

    Dependency direction (strict):
        finance_modules/revenue  -->  finance_engines (AllocationEngine)
        finance_modules/revenue  -->  finance_kernel  (services, domain)
        finance_modules/revenue  -X-> finance_services (FORBIDDEN)
        finance_modules/revenue  -X-> finance_config   (FORBIDDEN)

Invariants:
    - R14/R15: Each event type maps to exactly one AccountingPolicy via
      profile registration; no central if/switch dispatch.
    - R4: All journal entries produced by this module satisfy
      DOUBLE_ENTRY_BALANCE (debits == credits per currency per entry).
    - L1: Account ROLES used in profiles resolve to COA codes at posting
      time -- module never references concrete COA codes.
    - P1: Exactly one EconomicProfile matches any revenue event.

Failure modes:
    - Import failure if kernel or engine packages are unavailable.
    - Profile registration failure if duplicate event_type is detected.

Audit relevance:
    - Revenue recognition is a material financial assertion under SOX.
    - ASC 606 compliance requires traceable five-step model execution.
    - Every recognition event produces an immutable journal entry with
      full audit trail (R1, R10, R11).
"""

from finance_modules.revenue.config import RevenueConfig
from finance_modules.revenue.models import (
    ContractModification,
    ContractStatus,
    ModificationType,
    PerformanceObligation,
    RecognitionMethod,
    RecognitionSchedule,
    RevenueContract,
    SSPAllocation,
    TransactionPrice,
)
from finance_modules.revenue.profiles import REVENUE_PROFILES

__all__ = [
    "ContractModification",
    "ContractStatus",
    "ModificationType",
    "PerformanceObligation",
    "RecognitionMethod",
    "RecognitionSchedule",
    "RevenueContract",
    "SSPAllocation",
    "TransactionPrice",
    "REVENUE_PROFILES",
    "RevenueConfig",
]
