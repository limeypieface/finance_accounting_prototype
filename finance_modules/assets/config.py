"""
Fixed Assets Configuration Schema.

Defines the structure and sensible defaults for asset accounting settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.assets.profiles import AccountRole

logger = get_logger("modules.assets.config")


@dataclass
class AssetConfig:
    """
    Configuration schema for fixed assets module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = AssetConfig(
            capitalization_threshold=Decimal("2500.00"),
            tax_depreciation_method="macrs",
            **load_from_database("asset_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Capitalization
    capitalization_threshold: Decimal = Decimal("5000.00")
    capitalize_interest: bool = False

    # Depreciation
    default_depreciation_method: str = "straight_line"
    depreciation_convention: str = "mid_month"  # "mid_month", "full_month", "half_year"
    calculate_tax_depreciation: bool = True
    tax_depreciation_method: str = "macrs"

    # Books
    maintain_tax_book: bool = True
    maintain_amr_book: bool = False  # alternative minimum tax (legacy)

    # Period processing
    run_depreciation_frequency: str = "monthly"  # "monthly", "quarterly", "annually"
    allow_backdated_depreciation: bool = False
    auto_close_fully_depreciated: bool = False

    # Disposals
    require_disposal_approval: bool = True
    disposal_approval_threshold: Decimal = Decimal("10000.00")
    allow_partial_disposal: bool = True

    # Physical inventory
    require_annual_physical: bool = True
    physical_inventory_month: int = 12  # December

    # Integration
    create_ap_on_acquisition: bool = True
    link_to_purchase_orders: bool = True

    def __post_init__(self):
        logger.info(
            "asset_config_initialized",
            extra={
                "capitalization_threshold": str(self.capitalization_threshold),
                "default_depreciation_method": self.default_depreciation_method,
                "depreciation_convention": self.depreciation_convention,
                "maintain_tax_book": self.maintain_tax_book,
                "run_depreciation_frequency": self.run_depreciation_frequency,
                "require_disposal_approval": self.require_disposal_approval,
                "create_ap_on_acquisition": self.create_ap_on_acquisition,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("asset_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "asset_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
