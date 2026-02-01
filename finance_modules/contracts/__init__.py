"""
Contracts Module (``finance_modules.contracts``).

Responsibility
--------------
Thin ERP glue for government and commercial contract accounting: cost
incurrence, billing (CPFF, T&M, FFP), fee accruals, indirect cost
allocation, rate adjustments, and DCAA compliance including allowability
segregation per FAR 31.205.

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``ContractsService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New contract event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* DCAA allowability checks may reject costs to unallowable pools.

Audit relevance
---------------
Government contract accounting is subject to DCAA audit.  All cost
incurrence, billing, and fee postings produce immutable journal entries
with full provenance through the kernel audit chain (R11).  FAR 31.205
compliance requires traceable allowability determination.

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
