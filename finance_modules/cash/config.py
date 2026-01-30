"""
Cash Management Configuration Schema.

Defines the structure and sensible defaults for cash settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self
from uuid import UUID

from finance_kernel.logging_config import get_logger
from finance_modules.cash.profiles import AccountRole

logger = get_logger("modules.cash.config")


@dataclass
class CashConfig:
    """
    Configuration schema for cash management module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = CashConfig(
            reconciliation_tolerance=Decimal("0.05"),
            use_transit_account_for_wires=True,
            **load_from_database("cash_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Bank account registry
    bank_account_gl_mappings: dict[UUID, str] = field(default_factory=dict)

    # Reconciliation settings
    reconciliation_tolerance: Decimal = Decimal("0.01")
    require_dual_approval_above: Decimal | None = None

    # Transfer settings
    use_transit_account_for_wires: bool = False  # Requires transit_account_code when True
    transit_account_code: str | None = None

    def __post_init__(self):
        # Validate reconciliation tolerance
        if self.reconciliation_tolerance < 0:
            raise ValueError("reconciliation_tolerance cannot be negative")

        # Validate dual approval threshold if set
        if self.require_dual_approval_above is not None and self.require_dual_approval_above < 0:
            raise ValueError("require_dual_approval_above cannot be negative")

        # Validate transit_account_code is set when use_transit_account_for_wires is True
        if self.use_transit_account_for_wires and not self.transit_account_code:
            raise ValueError(
                "transit_account_code is required when use_transit_account_for_wires is True"
            )

        logger.info(
            "cash_config_initialized",
            extra={
                "reconciliation_tolerance": str(self.reconciliation_tolerance),
                "use_transit_account_for_wires": self.use_transit_account_for_wires,
                "bank_account_count": len(self.bank_account_gl_mappings),
                "has_dual_approval_threshold": self.require_dual_approval_above is not None,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("cash_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "cash_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
