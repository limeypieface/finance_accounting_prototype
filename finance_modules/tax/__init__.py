"""
Tax Module.

Responsibility:
    Thin ERP glue for sales/use tax, VAT, income tax, and tax reporting.
    Delegates all computation to ``finance_engines.tax.TaxCalculator`` and
    all journal posting to ``finance_kernel.services.ModulePostingService``.

Architecture:
    finance_modules -- Thin ERP glue (this layer).
    The module owns domain models, economic profiles, configuration, and
    workflows.  Actual tax arithmetic lives in ``finance_engines.tax``;
    actual ledger writes live in ``finance_kernel``.

Invariants:
    - All monetary amounts use ``Decimal`` -- NEVER ``float`` (R16, R17).
    - Profile-to-event dispatch uses account ROLES resolved at posting time
      (L1).  No hard-coded COA codes in this module.
    - Every journal-posting method follows the single-transaction boundary
      contract (R7): commit on success, rollback on failure.

Failure modes:
    - Invalid ``tax_type`` in ``record_tax_obligation`` silently
      dispatches to the profile registry, which may reject (BLOCKED) if
      no profile matches.
    - ``TaxConfig.__post_init__`` raises ``ValueError`` for out-of-range
      or invalid configuration values.

Audit relevance:
    - Tax provisions (ASC 740) and VAT settlements produce journal entries
      whose audit trail is guaranteed by the kernel (R11 hash chain).
    - Multi-jurisdiction aggregation is logged but not individually posted.

Total: ~180 lines of module-specific code.
Tax calculation engines (jurisdiction lookup, rate determination) come from shared engines.
"""

from finance_modules.tax.config import TaxConfig
from finance_modules.tax.models import (
    TaxExemption,
    TaxJurisdiction,
    TaxRate,
    TaxReturn,
    TaxTransaction,
)
from finance_modules.tax.profiles import TAX_PROFILES
from finance_modules.tax.workflows import TAX_RETURN_WORKFLOW

__all__ = [
    "TaxJurisdiction",
    "TaxRate",
    "TaxExemption",
    "TaxTransaction",
    "TaxReturn",
    "TAX_PROFILES",
    "TAX_RETURN_WORKFLOW",
    "TaxConfig",
]
