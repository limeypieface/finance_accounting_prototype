"""
Fixed Assets Domain Models.

The nouns of fixed assets: assets, categories, depreciation, disposals.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.assets.models")


class AssetStatus(Enum):
    """Asset lifecycle states."""
    PENDING = "pending"  # not yet in service
    IN_SERVICE = "in_service"
    FULLY_DEPRECIATED = "fully_depreciated"
    DISPOSED = "disposed"
    IMPAIRED = "impaired"


class DepreciationMethod(Enum):
    """Supported depreciation methods."""
    STRAIGHT_LINE = "straight_line"
    DECLINING_BALANCE = "declining_balance"
    DOUBLE_DECLINING = "double_declining"
    SUM_OF_YEARS = "sum_of_years"
    UNITS_OF_PRODUCTION = "units_of_production"
    MACRS = "macrs"  # US tax depreciation


class DisposalType(Enum):
    """Types of asset disposal."""
    SALE = "sale"
    SCRAP = "scrap"
    TRADE_IN = "trade_in"
    THEFT = "theft"
    CASUALTY = "casualty"
    ABANDONMENT = "abandonment"


@dataclass(frozen=True)
class AssetCategory:
    """A category for grouping assets with common attributes."""
    id: UUID
    code: str
    name: str
    useful_life_years: int
    depreciation_method: DepreciationMethod
    salvage_value_percent: Decimal = Decimal("0")
    gl_asset_account: str | None = None
    gl_depreciation_account: str | None = None
    gl_accumulated_depreciation_account: str | None = None


@dataclass(frozen=True)
class Asset:
    """A fixed asset."""
    id: UUID
    asset_number: str
    description: str
    category_id: UUID
    acquisition_date: date
    in_service_date: date | None = None
    acquisition_cost: Decimal = Decimal("0")
    salvage_value: Decimal = Decimal("0")
    useful_life_months: int = 0
    accumulated_depreciation: Decimal = Decimal("0")
    net_book_value: Decimal = Decimal("0")
    status: AssetStatus = AssetStatus.PENDING
    location_id: UUID | None = None
    department_id: UUID | None = None
    custodian_id: UUID | None = None
    serial_number: str | None = None
    purchase_order_id: UUID | None = None
    vendor_id: UUID | None = None


@dataclass(frozen=True)
class DepreciationSchedule:
    """Monthly depreciation record for an asset."""
    id: UUID
    asset_id: UUID
    period_date: date
    depreciation_amount: Decimal
    accumulated_depreciation: Decimal
    net_book_value: Decimal
    is_posted: bool = False


@dataclass(frozen=True)
class AssetDisposal:
    """Record of asset disposal."""
    id: UUID
    asset_id: UUID
    disposal_date: date
    disposal_type: DisposalType
    proceeds: Decimal = Decimal("0")
    accumulated_depreciation_at_disposal: Decimal = Decimal("0")
    net_book_value_at_disposal: Decimal = Decimal("0")
    gain_loss: Decimal = Decimal("0")
