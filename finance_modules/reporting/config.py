"""
Reporting Configuration Schema.

Defines classification rules and report formatting options.
Account classification uses code prefixes consistent with the
existing COA structure (1xxx=assets, 2xxx=liabilities, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

from finance_kernel.logging_config import get_logger

logger = get_logger("modules.reporting.config")


@dataclass
class AccountClassification:
    """
    Rules for classifying accounts into financial statement sections.

    Uses account code prefixes to determine current vs non-current
    and multi-step income statement categorization.

    Prefix matching: an account matches a section if its code
    starts with any of the configured prefixes.
    """

    # Balance sheet — assets
    current_asset_prefixes: tuple[str, ...] = ("10", "11", "12", "13", "14")
    non_current_asset_prefixes: tuple[str, ...] = ("15", "16", "17", "18", "19")

    # Balance sheet — liabilities
    current_liability_prefixes: tuple[str, ...] = ("20", "21", "22", "23", "24")
    non_current_liability_prefixes: tuple[str, ...] = ("25", "26", "27", "28", "29")

    # Balance sheet — equity
    equity_prefixes: tuple[str, ...] = ("30", "31", "32", "33")

    # Income statement — revenue
    revenue_prefixes: tuple[str, ...] = ("40", "41", "42", "43", "44", "45")

    # Income statement — multi-step breakdown
    cogs_prefixes: tuple[str, ...] = ("50",)
    operating_expense_prefixes: tuple[str, ...] = (
        "51", "52", "53", "54", "55", "56", "57", "58", "59",
    )
    other_income_prefixes: tuple[str, ...] = ("42", "43", "44")
    other_expense_prefixes: tuple[str, ...] = ("60", "61", "62")

    # Cash flow — accounts treated as cash and cash equivalents
    cash_account_prefixes: tuple[str, ...] = ("1000", "1020", "1030", "1040")

    def matches_prefix(self, code: str, prefixes: tuple[str, ...]) -> bool:
        """Check if an account code matches any of the given prefixes."""
        return any(code.startswith(p) for p in prefixes)


@dataclass
class ReportingConfig:
    """
    Configuration schema for the reporting module.

    Controls account classification, formatting, and report generation.
    """

    # Classification rules
    classification: AccountClassification = field(
        default_factory=AccountClassification,
    )

    # Default currency for reports
    default_currency: str = "USD"

    # Entity name shown on reports
    entity_name: str = "Company"

    # Rounding precision for display
    display_precision: int = 2

    # Whether to include accounts with zero balance in reports
    include_zero_balances: bool = False

    # Whether to include inactive accounts
    include_inactive: bool = False

    def __post_init__(self):
        if self.display_precision < 0:
            raise ValueError("display_precision cannot be negative")
        if len(self.default_currency) != 3:
            raise ValueError("default_currency must be a 3-letter ISO 4217 code")

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with standard defaults."""
        logger.info("reporting_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary."""
        if "classification" in data and isinstance(data["classification"], dict):
            data["classification"] = AccountClassification(**data["classification"])
        logger.info(
            "reporting_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
