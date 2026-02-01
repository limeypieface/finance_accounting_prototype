"""
Module: finance_modules.revenue.config
Responsibility:
    Configuration schema for the ASC 606 revenue recognition module.
    Defines settings for variable consideration constraint thresholds,
    default recognition methods, SSP estimation method preference,
    significant financing component thresholds, and currency defaults.

Architecture:
    finance_modules layer -- pure dataclass configuration schema.
    Consumed by RevenueRecognitionService at construction time.
    No I/O; no database access; no clock dependency.

    Configuration is resolved via the single entrypoint:
        finance_config.get_active_config(legal_entity, as_of_date)

Invariants:
    - Constraint threshold must be non-negative.
    - Financing threshold must be positive (> 0 days).
    - All monetary thresholds use Decimal (R16 precision).

Failure modes:
    - ValueError on invalid threshold values in __post_init__.

Audit relevance:
    - Variable consideration constraint threshold directly affects
      revenue timing per ASC 606-10-32-11 (constraint on estimates).
    - Financing threshold determines whether a significant financing
      component exists per ASC 606-10-32-15 (practical expedient at
      1 year).
    - Configuration values are logged at initialization for audit trail.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.revenue.config")


@dataclass
class RevenueConfig:
    """
    Configuration schema for revenue recognition module.

    Contract:
        Mutable dataclass (not frozen) so it can be loaded from YAML
        config.  Validated in ``__post_init__``.

    Guarantees:
        - ``variable_consideration_constraint_threshold`` >= 0.
        - ``financing_threshold_days`` > 0.
        - All Decimal fields use Decimal type (never float).

    Non-goals:
        - Does not perform I/O; config loading is done externally.
    """

    # Variable consideration constraint threshold
    variable_consideration_constraint_threshold: Decimal = Decimal("0.50")

    # Default recognition method
    default_recognition_method: str = "point_in_time"

    # SSP estimation method preference
    default_ssp_method: str = "observable"  # observable, adjusted_market, cost_plus, residual

    # Significant financing component threshold (days)
    financing_threshold_days: int = 365

    # Currency
    default_currency: str = "USD"

    def __post_init__(self):
        if self.variable_consideration_constraint_threshold < 0:
            raise ValueError("constraint threshold cannot be negative")
        if self.financing_threshold_days <= 0:
            raise ValueError("financing_threshold_days must be positive")

        logger.info(
            "revenue_config_initialized",
            extra={
                "default_recognition_method": self.default_recognition_method,
                "default_ssp_method": self.default_ssp_method,
                "financing_threshold_days": self.financing_threshold_days,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """
        Create config with standard ASC 606 defaults.

        Postconditions:
            - Returns a RevenueConfig with all default values.
            - Passes __post_init__ validation.
        """
        return cls()
