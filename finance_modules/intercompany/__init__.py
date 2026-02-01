"""
Intercompany Module (``finance_modules.intercompany``).

Responsibility
--------------
Thin ERP glue for intercompany accounting: IC transfers (always posted
as matching debit/credit pairs across entities), elimination rules for
consolidation, IC reconciliation, consolidation result generation, and
transfer pricing adjustments.

Architecture position
---------------------
**Modules layer** -- declarative profiles, config schemas, and a service
facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``IntercompanyService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New IC event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* IC reconciliation may report mismatches between entity pairs.

Audit relevance
---------------
Intercompany eliminations are material for consolidated financial
statements.  All IC transactions produce immutable journal entries with
full provenance through the kernel audit chain (R11).  Transfer pricing
adjustments support tax compliance across jurisdictions.
"""

from finance_modules.intercompany.models import (
    ConsolidationResult,
    EliminationRule,
    ICReconciliationResult,
    ICTransaction,
    IntercompanyAgreement,
)
from finance_modules.intercompany.profiles import INTERCOMPANY_PROFILES
from finance_modules.intercompany.config import IntercompanyConfig

__all__ = [
    "ConsolidationResult",
    "EliminationRule",
    "ICReconciliationResult",
    "ICTransaction",
    "IntercompanyAgreement",
    "INTERCOMPANY_PROFILES",
    "IntercompanyConfig",
]
