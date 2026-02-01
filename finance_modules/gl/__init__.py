"""
General Ledger Module (``finance_modules.gl``).

Responsibility
--------------
Thin ERP glue for the general ledger: chart of accounts management,
manual journal entries, period close, recurring entries, journal
batches, foreign-currency revaluation, translation, account
reconciliation, and intercompany eliminations.

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``GLService``.
* R12 -- Closed-period enforcement via ``PeriodService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New GL event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Period close operations fail if period is already CLOSED (R12).

Audit relevance
---------------
GL is the book of record.  Every posting method emits structured log events
for the audit trail.  Period close is serialized and logged.  All journal
entries carry full provenance through the kernel audit chain (R11).

Total: ~180 lines of module-specific code.
This module orchestrates the kernel's journal posting and provides GL-specific features.
"""

from finance_modules.gl.models import (
    Account,
    AccountHierarchy,
    AccountReconciliation,
    FiscalPeriod,
    JournalBatch,
    PeriodCloseTask,
    RecurringEntry,
    RevaluationResult,
    TranslationMethod,
    TranslationResult,
)
from finance_modules.gl.profiles import GL_PROFILES
from finance_modules.gl.workflows import PERIOD_CLOSE_WORKFLOW
from finance_modules.gl.config import GLConfig

__all__ = [
    "Account",
    "AccountHierarchy",
    "AccountReconciliation",
    "FiscalPeriod",
    "JournalBatch",
    "PeriodCloseTask",
    "RecurringEntry",
    "RevaluationResult",
    "TranslationMethod",
    "TranslationResult",
    "GL_PROFILES",
    "PERIOD_CLOSE_WORKFLOW",
    "GLConfig",
]
