"""
Lease Accounting Configuration Schema.

Defines settings for ASC 842 compliance including
short-term exemption thresholds and discount rate defaults.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.lease.config")


@dataclass
class LeaseConfig:
    """Configuration schema for lease accounting module."""

    # Short-term lease exemption threshold (months)
    short_term_threshold_months: int = 12

    # Default incremental borrowing rate
    default_ibr: Decimal = Decimal("0.05")

    # Low-value asset exemption threshold
    low_value_threshold: Decimal = Decimal("5000.00")

    # Currency
    default_currency: str = "USD"

    def __post_init__(self):
        if self.short_term_threshold_months <= 0:
            raise ValueError("short_term_threshold_months must be positive")
        if self.default_ibr < 0:
            raise ValueError("default_ibr cannot be negative")
        if self.low_value_threshold < 0:
            raise ValueError("low_value_threshold cannot be negative")

        logger.info(
            "lease_config_initialized",
            extra={
                "short_term_threshold_months": self.short_term_threshold_months,
                "default_ibr": str(self.default_ibr),
                "low_value_threshold": str(self.low_value_threshold),
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with standard ASC 842 defaults."""
        return cls()
