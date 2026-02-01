"""
Credit Loss Module (``finance_modules.credit_loss``).

Responsibility
--------------
Thin ERP glue for ASC 326 / CECL (Current Expected Credit Loss)
methodology: credit portfolio management, ECL estimation, vintage
analysis, loss rate computation, forward-looking macroeconomic
adjustments, and allowance postings.

Architecture position
---------------------
**Modules layer** -- declarative profiles, config schemas, and a service
facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``CreditLossService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New credit-loss event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* ECL estimation requires historical loss data; missing data returns zero.

Audit relevance
---------------
CECL compliance is a material accounting estimate subject to auditor
scrutiny.  All allowance postings produce immutable journal entries with
full provenance through the kernel audit chain (R11).  Forward-looking
adjustments must be documented with methodology and assumptions.
"""

from finance_modules.credit_loss.config import CreditLossConfig
from finance_modules.credit_loss.models import (
    CreditPortfolio,
    ECLEstimate,
    ForwardLookingAdjustment,
    LossRate,
    VintageAnalysis,
)

__all__ = [
    "CreditLossConfig",
    "CreditPortfolio",
    "ECLEstimate",
    "ForwardLookingAdjustment",
    "LossRate",
    "VintageAnalysis",
]
