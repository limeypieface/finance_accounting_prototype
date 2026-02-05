"""
Contracts Configuration Schema.

Defines the structure and sensible defaults for contract accounting settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.contracts.profiles import AccountRole

logger = get_logger("modules.contracts.config")


VALID_CONTRACT_TYPES = {
    "CPFF",       # Cost-Plus-Fixed-Fee
    "CPIF",       # Cost-Plus-Incentive-Fee
    "CPAF",       # Cost-Plus-Award-Fee
    "FFP",        # Firm-Fixed-Price
    "FPI",        # Fixed-Price-Incentive
    "T_AND_M",    # Time and Materials
    "LABOR_HOUR", # Labor Hour
}

VALID_BILLING_FREQUENCIES = {"weekly", "biweekly", "monthly", "milestone"}

VALID_INDIRECT_RATE_BASES = {"direct_labor_dollars", "direct_labor_hours", "total_direct_cost"}


@dataclass
class ContractsConfig:
    """
    Configuration schema for contracts module.

    Field defaults represent common government contracting practices.
    Override at instantiation with company-specific values:

        config = ContractsConfig(
            default_contract_type="CPFF",
            require_dcaa_compliance=True,
            **load_from_database("contracts_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Contract defaults
    default_contract_type: str = "CPFF"
    require_dcaa_compliance: bool = True

    # Billing
    default_billing_frequency: str = "monthly"
    provisional_billing_enabled: bool = True

    # Indirect rates
    default_indirect_rate_basis: str = "direct_labor_dollars"
    allow_provisional_rates: bool = True
    rate_adjustment_threshold: Decimal = Decimal("0.01")

    # Fee limits
    fee_ceiling_enforcement: bool = True
    max_fee_percent: Decimal = Decimal("15.0")

    # DCAA compliance
    segregate_unallowable_costs: bool = True
    require_conditional_justification: bool = True
    audit_trail_retention_years: int = 7

    # Cost accumulation
    track_by_clin: bool = True
    track_by_labor_category: bool = True

    # DCAA Rate Controls (D8)
    enable_rate_verification: bool = True
    enable_rate_ceiling_enforcement: bool = True
    enable_provisional_rate_tracking: bool = True
    rate_verification_on_post: bool = True

    def __post_init__(self) -> None:
        if self.default_contract_type not in VALID_CONTRACT_TYPES:
            raise ValueError(
                f"default_contract_type must be one of {VALID_CONTRACT_TYPES}, "
                f"got '{self.default_contract_type}'"
            )

        if self.default_billing_frequency not in VALID_BILLING_FREQUENCIES:
            raise ValueError(
                f"default_billing_frequency must be one of {VALID_BILLING_FREQUENCIES}, "
                f"got '{self.default_billing_frequency}'"
            )

        if self.default_indirect_rate_basis not in VALID_INDIRECT_RATE_BASES:
            raise ValueError(
                f"default_indirect_rate_basis must be one of {VALID_INDIRECT_RATE_BASES}, "
                f"got '{self.default_indirect_rate_basis}'"
            )

        if self.rate_adjustment_threshold < 0:
            raise ValueError("rate_adjustment_threshold cannot be negative")

        if self.max_fee_percent < 0:
            raise ValueError("max_fee_percent cannot be negative")

        if self.audit_trail_retention_years <= 0:
            raise ValueError("audit_trail_retention_years must be positive")

        logger.info(
            "contracts_config_initialized",
            extra={
                "default_contract_type": self.default_contract_type,
                "require_dcaa_compliance": self.require_dcaa_compliance,
                "default_billing_frequency": self.default_billing_frequency,
                "default_indirect_rate_basis": self.default_indirect_rate_basis,
                "fee_ceiling_enforcement": self.fee_ceiling_enforcement,
                "segregate_unallowable_costs": self.segregate_unallowable_costs,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with government contracting defaults."""
        logger.info("contracts_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "contracts_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
