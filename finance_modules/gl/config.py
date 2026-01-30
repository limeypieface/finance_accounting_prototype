"""
General Ledger Configuration Schema.

Defines the structure and sensible defaults for GL settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.gl.profiles import AccountRole

logger = get_logger("modules.gl.config")

VALID_PAY_FREQUENCIES = {"weekly", "biweekly", "semimonthly", "monthly"}
VALID_CLOSE_ORDER_MODULES = {"inventory", "wip", "ar", "ap", "assets", "payroll", "gl"}


@dataclass
class GLConfig:
    """
    Configuration schema for general ledger module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = GLConfig(
            fiscal_year_end_month=6,  # June fiscal year
            functional_currency="EUR",
            **load_from_database("gl_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Chart of accounts
    account_code_format: str = "####-###"
    segment_separator: str = "-"
    allow_account_creation: bool = True
    require_account_approval: bool = True

    # Fiscal calendar
    fiscal_year_end_month: int = 12
    fiscal_year_end_day: int = 31
    num_periods: int = 12
    use_4_4_5_calendar: bool = False

    # Journal entry
    require_journal_approval: bool = True
    approval_threshold_amount: float = 10000.00
    allow_unbalanced_entries: bool = False  # NEVER set to True
    require_journal_reference: bool = True

    # Period close
    auto_close_subledgers: bool = True
    close_order: tuple[str, ...] = ("inventory", "wip", "ar", "ap", "assets", "payroll", "gl")
    allow_posting_to_closed_period: bool = False
    adjusting_entries_period: int = 13

    # Multi-currency
    functional_currency: str = "USD"
    revalue_foreign_balances: bool = True
    fx_gain_loss_account_code: str | None = None

    # Intercompany
    enable_intercompany: bool = False
    auto_create_intercompany_entries: bool = False  # Must be False when enable_intercompany is False

    # Consolidation
    consolidation_currency: str = "USD"
    eliminate_intercompany: bool = True

    # Reporting
    retained_earnings_account_code: str | None = None
    income_summary_account_code: str | None = None

    def __post_init__(self):
        # Validate fiscal year end month
        if not 1 <= self.fiscal_year_end_month <= 12:
            raise ValueError(
                f"fiscal_year_end_month must be between 1 and 12, "
                f"got {self.fiscal_year_end_month}"
            )

        # Validate fiscal year end day
        if not 1 <= self.fiscal_year_end_day <= 31:
            raise ValueError(
                f"fiscal_year_end_day must be between 1 and 31, "
                f"got {self.fiscal_year_end_day}"
            )

        # Validate num_periods
        if not 1 <= self.num_periods <= 13:
            raise ValueError(
                f"num_periods must be between 1 and 13, got {self.num_periods}"
            )

        # Validate adjusting_entries_period
        if self.adjusting_entries_period < 1:
            raise ValueError("adjusting_entries_period must be at least 1")

        # Validate approval_threshold_amount
        if self.approval_threshold_amount < 0:
            raise ValueError("approval_threshold_amount cannot be negative")

        # Validate close_order contains only valid modules
        if self.close_order:
            invalid_modules = set(self.close_order) - VALID_CLOSE_ORDER_MODULES
            if invalid_modules:
                raise ValueError(
                    f"close_order contains invalid modules: {invalid_modules}. "
                    f"Valid modules are: {VALID_CLOSE_ORDER_MODULES}"
                )

        # Validate intercompany settings consistency
        if not self.enable_intercompany and self.auto_create_intercompany_entries:
            raise ValueError(
                "auto_create_intercompany_entries cannot be True when "
                "enable_intercompany is False"
            )

        if self.allow_unbalanced_entries:
            logger.warning(
                "gl_config_unbalanced_entries_allowed",
                extra={"allow_unbalanced_entries": True},
            )

        logger.info(
            "gl_config_initialized",
            extra={
                "fiscal_year_end_month": self.fiscal_year_end_month,
                "num_periods": self.num_periods,
                "functional_currency": self.functional_currency,
                "require_journal_approval": self.require_journal_approval,
                "auto_close_subledgers": self.auto_close_subledgers,
                "close_order": list(self.close_order),
                "enable_intercompany": self.enable_intercompany,
                "allow_posting_to_closed_period": self.allow_posting_to_closed_period,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("gl_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "gl_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
