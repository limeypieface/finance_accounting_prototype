"""
Work-in-Process Configuration Schema.

Defines the structure and sensible defaults for WIP/manufacturing settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.wip.profiles import AccountRole

logger = get_logger("modules.wip.config")


@dataclass
class WIPConfig:
    """
    Configuration schema for WIP module.

    Field defaults represent common manufacturing practices.
    Override at instantiation with company-specific values:

        config = WIPConfig(
            costing_method="actual",
            backflush_materials=True,
            **load_from_database("wip_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Costing method
    costing_method: str = "standard"  # "standard", "actual", "average"
    use_work_center_rates: bool = True

    # Labor
    labor_rate_source: str = "employee"  # "employee", "work_center", "operation"
    track_setup_separately: bool = True
    overtime_rate_multiplier: Decimal = Decimal("1.5")

    # Overhead
    overhead_application_basis: str = "labor_hours"  # "labor_hours", "machine_hours", "units"
    apply_overhead_frequency: str = "transaction"  # "transaction", "daily", "period_end"
    fixed_overhead_rate: Decimal = Decimal("0")
    variable_overhead_rate: Decimal = Decimal("0")

    # Variances
    calculate_variances_on: str = "close"  # "completion", "close"
    material_variance_method: str = "standard"  # "standard", "planned"
    labor_variance_method: str = "standard"

    # Scrap
    track_scrap_by_operation: bool = True
    scrap_includes_labor: bool = True

    # Backflushing
    backflush_materials: bool = False
    backflush_labor: bool = False

    def __post_init__(self):
        logger.info(
            "wip_config_initialized",
            extra={
                "costing_method": self.costing_method,
                "labor_rate_source": self.labor_rate_source,
                "overhead_application_basis": self.overhead_application_basis,
                "apply_overhead_frequency": self.apply_overhead_frequency,
                "calculate_variances_on": self.calculate_variances_on,
                "backflush_materials": self.backflush_materials,
                "backflush_labor": self.backflush_labor,
                "track_scrap_by_operation": self.track_scrap_by_operation,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("wip_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "wip_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
