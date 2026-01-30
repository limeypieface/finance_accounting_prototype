"""
Fixed Assets Module.

Handles asset acquisition, depreciation, disposal, and impairment.

Total: ~180 lines of module-specific code.
Depreciation engines come from shared engines.
"""

from finance_modules.assets.models import (
    Asset,
    AssetCategory,
    DepreciationSchedule,
    AssetDisposal,
)
from finance_modules.assets.profiles import ASSET_PROFILES
from finance_modules.assets.workflows import ASSET_WORKFLOW
from finance_modules.assets.config import AssetConfig

__all__ = [
    "Asset",
    "AssetCategory",
    "DepreciationSchedule",
    "AssetDisposal",
    "ASSET_PROFILES",
    "ASSET_WORKFLOW",
    "AssetConfig",
]
