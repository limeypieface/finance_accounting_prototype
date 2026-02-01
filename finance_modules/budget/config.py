"""
Budgeting Configuration Schema.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.budget.config")


@dataclass
class BudgetConfig:
    """Configuration schema for budgeting module."""

    default_currency: str = "USD"
    variance_threshold_percentage: Decimal = Decimal("10.0")
    allow_over_budget: bool = False
    encumbrance_enabled: bool = True

    def __post_init__(self):
        if self.variance_threshold_percentage < 0:
            raise ValueError("variance_threshold_percentage cannot be negative")
        logger.info("budget_config_initialized", extra={
            "allow_over_budget": self.allow_over_budget,
            "encumbrance_enabled": self.encumbrance_enabled,
        })

    @classmethod
    def with_defaults(cls) -> Self:
        return cls()
