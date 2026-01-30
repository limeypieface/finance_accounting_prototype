"""
Inventory Configuration Schema.

Defines the structure and sensible defaults for inventory settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.inventory.profiles import AccountRole

logger = get_logger("modules.inventory.config")


VALID_COSTING_METHODS = {"fifo", "lifo", "weighted_avg", "standard", "specific"}
VALID_ISSUE_METHODS = {"fifo", "lifo", "fefo"}
VALID_LCM_METHODS = {"item", "category", "total"}
VALID_UPDATE_FREQUENCIES = {"transaction", "daily", "monthly"}


@dataclass
class CostingMethod:
    """Costing method configuration."""
    method: str  # "fifo", "lifo", "weighted_avg", "standard", "specific"
    update_frequency: str = "transaction"  # "transaction", "daily", "monthly"

    def __post_init__(self):
        if self.method not in VALID_COSTING_METHODS:
            raise ValueError(f"method must be one of {VALID_COSTING_METHODS}, got '{self.method}'")
        if self.update_frequency not in VALID_UPDATE_FREQUENCIES:
            raise ValueError(f"update_frequency must be one of {VALID_UPDATE_FREQUENCIES}")


@dataclass
class InventoryConfig:
    """
    Configuration schema for inventory module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = InventoryConfig(
            default_costing_method="fifo",
            allow_negative_inventory=False,
            **load_from_database("inventory_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Costing
    default_costing_method: str = "weighted_avg"
    allow_negative_inventory: bool = False
    use_standard_costing: bool = False

    # Valuation
    valuation_currency: str = "USD"
    lcm_adjustment_method: str = "item"  # "item", "category", "total"

    # Receipts
    require_inspection: bool = False
    auto_putaway: bool = True
    receipt_tolerance_percent: Decimal = Decimal("5.0")

    # Issues
    issue_method: str = "fifo"  # "fifo", "lifo", "fefo" (first expired)
    reserve_on_order: bool = True

    # Transfers
    track_in_transit: bool = True
    require_receipt_confirmation: bool = True

    # Cycle counting
    enable_cycle_counting: bool = True
    cycle_count_frequency_days: int = 90
    adjustment_approval_threshold: Decimal = Decimal("100.00")

    # ABC classification thresholds (by value)
    abc_class_a_percent: Decimal = Decimal("80.0")  # top 80% of value
    abc_class_b_percent: Decimal = Decimal("15.0")  # next 15%
    # Class C is remainder

    def __post_init__(self):
        # Validate costing method
        if self.default_costing_method not in VALID_COSTING_METHODS:
            raise ValueError(
                f"default_costing_method must be one of {VALID_COSTING_METHODS}, "
                f"got '{self.default_costing_method}'"
            )

        # Validate issue method
        if self.issue_method not in VALID_ISSUE_METHODS:
            raise ValueError(
                f"issue_method must be one of {VALID_ISSUE_METHODS}, "
                f"got '{self.issue_method}'"
            )

        # Validate LCM method
        if self.lcm_adjustment_method not in VALID_LCM_METHODS:
            raise ValueError(
                f"lcm_adjustment_method must be one of {VALID_LCM_METHODS}, "
                f"got '{self.lcm_adjustment_method}'"
            )

        # Validate receipt tolerance
        if self.receipt_tolerance_percent < 0:
            raise ValueError("receipt_tolerance_percent cannot be negative")
        if self.receipt_tolerance_percent > Decimal("100"):
            raise ValueError("receipt_tolerance_percent cannot exceed 100%")

        # Validate ABC percentages
        if self.abc_class_a_percent < 0:
            raise ValueError("abc_class_a_percent cannot be negative")
        if self.abc_class_b_percent < 0:
            raise ValueError("abc_class_b_percent cannot be negative")
        abc_total = self.abc_class_a_percent + self.abc_class_b_percent
        if abc_total > Decimal("100"):
            raise ValueError(
                f"abc_class_a_percent + abc_class_b_percent cannot exceed 100%, "
                f"got {abc_total}%"
            )

        # Validate cycle count settings
        if self.cycle_count_frequency_days <= 0:
            raise ValueError("cycle_count_frequency_days must be positive")
        if self.adjustment_approval_threshold < 0:
            raise ValueError("adjustment_approval_threshold cannot be negative")

        logger.info(
            "inventory_config_initialized",
            extra={
                "default_costing_method": self.default_costing_method,
                "allow_negative_inventory": self.allow_negative_inventory,
                "valuation_currency": self.valuation_currency,
                "lcm_adjustment_method": self.lcm_adjustment_method,
                "issue_method": self.issue_method,
                "enable_cycle_counting": self.enable_cycle_counting,
                "cycle_count_frequency_days": self.cycle_count_frequency_days,
                "track_in_transit": self.track_in_transit,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("inventory_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "inventory_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
