"""
Tax Configuration Schema.

Defines the structure and sensible defaults for tax settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.tax.profiles import AccountRole

logger = get_logger("modules.tax.config")

VALID_TAX_TYPES = {"sales", "vat", "gst"}
VALID_TAX_CALCULATION_METHODS = {"address", "origin", "destination"}
VALID_FILING_FREQUENCIES = {"monthly", "quarterly", "annually"}


@dataclass
class NexusState:
    """States where company has tax nexus."""
    state_code: str
    effective_date: str
    has_economic_nexus: bool = False
    has_physical_nexus: bool = False

    def __post_init__(self):
        if not self.state_code or not self.state_code.strip():
            raise ValueError("state_code cannot be empty")
        if not self.effective_date or not self.effective_date.strip():
            raise ValueError("effective_date cannot be empty")
        logger.debug(
            "nexus_state_initialized",
            extra={
                "state_code": self.state_code,
                "effective_date": self.effective_date,
                "has_economic_nexus": self.has_economic_nexus,
                "has_physical_nexus": self.has_physical_nexus,
            },
        )


@dataclass
class TaxConfig:
    """
    Configuration schema for tax module.

    Field defaults represent common US practices.
    Override at instantiation with company-specific values:

        config = TaxConfig(
            primary_tax_type="vat",
            is_tax_inclusive_pricing=True,
            **load_from_database("tax_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Tax type
    primary_tax_type: str = "sales"  # "sales", "vat", "gst"
    is_tax_inclusive_pricing: bool = False

    # Nexus (US specific)
    nexus_states: tuple[NexusState, ...] = field(default_factory=tuple)
    economic_nexus_threshold: Decimal = Decimal("100000.00")
    transaction_count_threshold: int = 200

    # Calculation
    tax_calculation_method: str = "address"  # "address", "origin", "destination"
    use_external_tax_service: bool = False
    round_at_line: bool = True

    # Exemptions
    validate_exemption_certificates: bool = True
    exemption_expiry_warning_days: int = 30

    # Reporting
    filing_frequency: str = "monthly"  # "monthly", "quarterly", "annually"
    consolidate_local_taxes: bool = True
    auto_calculate_returns: bool = True

    # Use tax
    accrue_use_tax: bool = True
    use_tax_threshold: Decimal = Decimal("0")

    # VAT specific
    vat_registration_number: str | None = None
    reverse_charge_threshold: Decimal | None = None
    intrastat_reporting: bool = False

    def __post_init__(self):
        # Validate tax type
        if self.primary_tax_type not in VALID_TAX_TYPES:
            raise ValueError(
                f"primary_tax_type must be one of {VALID_TAX_TYPES}, "
                f"got '{self.primary_tax_type}'"
            )

        # Validate economic nexus threshold
        if self.economic_nexus_threshold < 0:
            raise ValueError("economic_nexus_threshold cannot be negative")

        # Validate transaction count threshold
        if self.transaction_count_threshold < 0:
            raise ValueError("transaction_count_threshold cannot be negative")

        # Validate tax calculation method
        if self.tax_calculation_method not in VALID_TAX_CALCULATION_METHODS:
            raise ValueError(
                f"tax_calculation_method must be one of {VALID_TAX_CALCULATION_METHODS}, "
                f"got '{self.tax_calculation_method}'"
            )

        # Validate exemption expiry warning days
        if self.exemption_expiry_warning_days < 0:
            raise ValueError("exemption_expiry_warning_days cannot be negative")

        # Validate filing frequency
        if self.filing_frequency not in VALID_FILING_FREQUENCIES:
            raise ValueError(
                f"filing_frequency must be one of {VALID_FILING_FREQUENCIES}, "
                f"got '{self.filing_frequency}'"
            )

        # Validate use tax threshold
        if self.use_tax_threshold < 0:
            raise ValueError("use_tax_threshold cannot be negative")

        # Validate reverse charge threshold if set
        if self.reverse_charge_threshold is not None and self.reverse_charge_threshold < 0:
            raise ValueError("reverse_charge_threshold cannot be negative")

        # Validate nexus_states have unique state codes
        if self.nexus_states:
            state_codes = [s.state_code for s in self.nexus_states]
            if len(state_codes) != len(set(state_codes)):
                duplicates = [code for code in state_codes if state_codes.count(code) > 1]
                logger.warning(
                    "tax_config_duplicate_nexus_states",
                    extra={"duplicate_state_codes": list(set(duplicates))},
                )
                raise ValueError(
                    f"nexus_states contains duplicate state codes: {set(duplicates)}"
                )

        logger.info(
            "tax_config_initialized",
            extra={
                "primary_tax_type": self.primary_tax_type,
                "is_tax_inclusive_pricing": self.is_tax_inclusive_pricing,
                "tax_calculation_method": self.tax_calculation_method,
                "filing_frequency": self.filing_frequency,
                "use_external_tax_service": self.use_external_tax_service,
                "accrue_use_tax": self.accrue_use_tax,
                "nexus_states_count": len(self.nexus_states),
                "intrastat_reporting": self.intrastat_reporting,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("tax_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "tax_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        if "nexus_states" in data:
            data["nexus_states"] = tuple(
                NexusState(**state) if isinstance(state, dict) else state
                for state in data["nexus_states"]
            )
        return cls(**data)
