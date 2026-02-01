"""
Fixed Assets Module (``finance_modules.assets``).

Responsibility
--------------
Thin ERP glue for fixed-asset lifecycle: acquisition, depreciation
(straight-line, DDB, SYD, units-of-production), disposal, impairment
testing, transfers, revaluation, and component depreciation (ASC 360).

Architecture position
---------------------
**Modules layer** -- declarative profiles, workflows, config schemas, and a
service facade that delegates depreciation calculations to pure helpers and
all journal posting to ``finance_kernel`` via ``ModulePostingService``.

Invariants enforced
-------------------
* R4  -- Double-entry balance guaranteed by kernel posting pipeline.
* R7  -- Transaction boundary owned by ``FixedAssetService``.
* R14 -- No ``if/switch`` on event_type; profile dispatch via where-clauses.
* R15 -- New asset event types require only a new profile + registration.
* L1  -- Account ROLES used in profiles; COA resolution at posting time.

Failure modes
-------------
* ``ModulePostingResult.is_success == False`` -- guard rejection, missing
  profile, or kernel validation error.
* Depreciation helper returns ``Decimal("0")`` for zero/negative useful life.

Audit relevance
---------------
Depreciation methodology must be documented and consistently applied per
ASC 360.  All asset transactions produce immutable journal entries with
full provenance through the kernel audit chain (R11).

Total: ~180 lines of module-specific code.
Depreciation calculations come from pure helpers in ``helpers.py``.
"""

from finance_modules.assets.config import AssetConfig
from finance_modules.assets.models import (
    Asset,
    AssetCategory,
    AssetDisposal,
    AssetRevaluation,
    AssetTransfer,
    DepreciationComponent,
    DepreciationSchedule,
)
from finance_modules.assets.profiles import ASSET_PROFILES
from finance_modules.assets.workflows import ASSET_WORKFLOW

__all__ = [
    "Asset",
    "AssetCategory",
    "DepreciationSchedule",
    "AssetDisposal",
    "AssetTransfer",
    "AssetRevaluation",
    "DepreciationComponent",
    "ASSET_PROFILES",
    "ASSET_WORKFLOW",
    "AssetConfig",
]
