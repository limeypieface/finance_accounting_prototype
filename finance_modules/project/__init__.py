"""
Project Accounting Module (``finance_modules.project``).

Responsibility
--------------
Thin ERP glue for project accounting: cost incurrence tracking, WBS-based
budgeting, milestone and T&M billing, revenue recognition, budget revisions,
phase completion, and Earned Value Management (EVM) metrics (CPI, SPI,
EAC, ETC, TCPI).

Architecture position
---------------------
**Modules layer** -- declarative profiles, config schemas, and a service
facade that delegates all journal posting to ``finance_kernel`` via
``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``ProjectService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New project event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* EVM calculations require budget and actual cost data; missing data returns
  zero metrics.

Audit relevance
---------------
Project cost accounting supports government contract compliance and EVM
reporting (ANSI/EIA-748).  All project postings produce immutable journal
entries with full provenance through the kernel audit chain (R11).
"""

from finance_modules.project.models import (
    EVMSnapshot,
    Milestone,
    Project,
    ProjectBudget,
    WBSElement,
)
from finance_modules.project.config import ProjectConfig

__all__ = [
    "EVMSnapshot",
    "Milestone",
    "Project",
    "ProjectBudget",
    "ProjectConfig",
    "WBSElement",
]
