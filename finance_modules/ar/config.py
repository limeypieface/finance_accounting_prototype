"""
Accounts Receivable Configuration Schema.

Defines the structure and sensible defaults for AR settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.ar.profiles import AccountRole

logger = get_logger("modules.ar.config")


@dataclass
class DunningLevel:
    """Configuration for a dunning (collection) level."""
    days_past_due: int
    action: str  # "reminder", "warning", "final_notice", "collection"
    template_code: str

    def __post_init__(self):
        if self.days_past_due <= 0:
            raise ValueError("days_past_due must be positive")
        valid_actions = {"reminder", "warning", "final_notice", "collection"}
        if self.action not in valid_actions:
            raise ValueError(f"action must be one of {valid_actions}, got '{self.action}'")
        if not self.template_code or not self.template_code.strip():
            raise ValueError("template_code cannot be empty")
        logger.debug(
            "dunning_level_initialized",
            extra={
                "days_past_due": self.days_past_due,
                "action": self.action,
                "template_code": self.template_code,
            },
        )


@dataclass
class ARConfig:
    """
    Configuration schema for accounts receivable module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = ARConfig(
            enforce_credit_limits=True,
            default_payment_terms_days=45,
            **load_from_database("ar_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Credit management
    enforce_credit_limits: bool = True
    credit_limit_includes_pending: bool = True

    # Payment terms
    default_payment_terms_days: int = 30
    early_payment_discount_days: int = 10
    early_payment_discount_percent: Decimal = Decimal("2.0")

    # Receipt application
    auto_apply_receipts: bool = True
    apply_receipts_by: str = "due_date"  # "due_date", "invoice_date", "oldest_first"
    allow_overpayment: bool = True
    allow_partial_payment: bool = True

    # Aging buckets (in days)
    aging_buckets: tuple[int, ...] = (30, 60, 90, 120)

    # Dunning (collections)
    dunning_levels: tuple[DunningLevel, ...] = field(default_factory=tuple)
    auto_send_dunning: bool = False

    # Bad debt
    bad_debt_provision_method: str = "percentage"  # "percentage", "aging", "specific"
    bad_debt_provision_percent: Decimal = Decimal("2.0")
    write_off_approval_threshold: Decimal = Decimal("100.00")

    # Revenue recognition
    recognize_revenue_on: str = "invoice"  # "invoice", "delivery", "payment"

    def __post_init__(self):
        # Validate payment terms relationship
        if self.early_payment_discount_days > self.default_payment_terms_days:
            raise ValueError(
                f"early_payment_discount_days ({self.early_payment_discount_days}) "
                f"cannot exceed default_payment_terms_days ({self.default_payment_terms_days})"
            )

        # Validate discount percent
        if self.early_payment_discount_percent < 0:
            raise ValueError("early_payment_discount_percent cannot be negative")
        if self.early_payment_discount_percent > Decimal("100"):
            raise ValueError("early_payment_discount_percent cannot exceed 100%")

        # Validate aging buckets
        if self.aging_buckets:
            if list(self.aging_buckets) != sorted(self.aging_buckets):
                raise ValueError("aging_buckets must be sorted ascending")
            if len(self.aging_buckets) != len(set(self.aging_buckets)):
                raise ValueError("aging_buckets must be unique")
            if any(b <= 0 for b in self.aging_buckets):
                raise ValueError("aging_buckets must contain positive values")

        # Validate dunning levels are sorted by days_past_due
        if self.dunning_levels:
            days = [lvl.days_past_due for lvl in self.dunning_levels]
            if days != sorted(days):
                raise ValueError("dunning_levels must be sorted by days_past_due ascending")
            if len(days) != len(set(days)):
                raise ValueError("dunning_levels must have unique days_past_due values")

        # Validate bad debt settings
        if self.bad_debt_provision_percent < 0:
            raise ValueError("bad_debt_provision_percent cannot be negative")
        if self.bad_debt_provision_percent > Decimal("100"):
            raise ValueError("bad_debt_provision_percent cannot exceed 100%")
        if self.write_off_approval_threshold < 0:
            raise ValueError("write_off_approval_threshold cannot be negative")

        valid_provision_methods = {"percentage", "aging", "specific"}
        if self.bad_debt_provision_method not in valid_provision_methods:
            raise ValueError(
                f"bad_debt_provision_method must be one of {valid_provision_methods}, "
                f"got '{self.bad_debt_provision_method}'"
            )

        # Validate revenue recognition
        valid_recognition = {"invoice", "delivery", "payment"}
        if self.recognize_revenue_on not in valid_recognition:
            raise ValueError(
                f"recognize_revenue_on must be one of {valid_recognition}, "
                f"got '{self.recognize_revenue_on}'"
            )

        # Validate apply_receipts_by
        valid_apply_by = {"due_date", "invoice_date", "oldest_first"}
        if self.apply_receipts_by not in valid_apply_by:
            raise ValueError(
                f"apply_receipts_by must be one of {valid_apply_by}, "
                f"got '{self.apply_receipts_by}'"
            )

        logger.info(
            "ar_config_initialized",
            extra={
                "enforce_credit_limits": self.enforce_credit_limits,
                "default_payment_terms_days": self.default_payment_terms_days,
                "auto_apply_receipts": self.auto_apply_receipts,
                "apply_receipts_by": self.apply_receipts_by,
                "aging_buckets": list(self.aging_buckets),
                "bad_debt_provision_method": self.bad_debt_provision_method,
                "recognize_revenue_on": self.recognize_revenue_on,
                "dunning_levels_count": len(self.dunning_levels),
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("ar_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "ar_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        if "dunning_levels" in data:
            data["dunning_levels"] = tuple(
                DunningLevel(**level) if isinstance(level, dict) else level
                for level in data["dunning_levels"]
            )
        return cls(**data)
