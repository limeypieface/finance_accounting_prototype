"""
Lease Accounting Module (``finance_modules.lease``).

Responsibility
--------------
Thin ERP glue for ASC 842 lease accounting: lease classification
(operating vs. finance), initial recognition (ROU asset and liability),
periodic payment recording, interest accrual, ROU amortization, lease
modification, and early termination.

Architecture position
---------------------
**Modules layer** -- declarative profiles, config schemas, and a service
facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``LeaseService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New lease event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Lease classification is determined at inception; reclassification requires
  a modification event.

Audit relevance
---------------
ASC 842 compliance requires recognition of all lease liabilities and ROU
assets on the balance sheet.  All lease transactions produce immutable
journal entries with full provenance through the kernel audit chain (R11).
"""

from finance_modules.lease.config import LeaseConfig
from finance_modules.lease.models import (
    AmortizationScheduleLine,
    Lease,
    LeaseClassification,
    LeaseLiability,
    LeaseModification,
    LeasePayment,
    ROUAsset,
)
from finance_modules.lease.profiles import LEASE_PROFILES

__all__ = [
    "AmortizationScheduleLine",
    "Lease",
    "LeaseClassification",
    "LeaseLiability",
    "LeaseModification",
    "LeasePayment",
    "ROUAsset",
    "LEASE_PROFILES",
    "LeaseConfig",
]
