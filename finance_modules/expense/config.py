"""
Travel & Expense Configuration Schema.

Defines the structure and sensible defaults for expense settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.expense.profiles import AccountRole

logger = get_logger("modules.expense.config")

VALID_APPROVAL_HIERARCHIES = {"manager", "cost_center", "project"}
VALID_PAYMENT_METHODS = {"ach", "check", "payroll"}
VALID_PAYMENT_FREQUENCIES = {"weekly", "biweekly", "on_demand"}


@dataclass
class CategoryLimit:
    """Per-category expense limits."""
    category: str
    daily_limit: Decimal | None = None
    per_transaction_limit: Decimal | None = None
    requires_receipt_above: Decimal | None = None

    def __post_init__(self):
        if not self.category or not self.category.strip():
            raise ValueError("category cannot be empty")
        if self.daily_limit is not None and self.daily_limit < 0:
            raise ValueError("daily_limit cannot be negative")
        if self.per_transaction_limit is not None and self.per_transaction_limit < 0:
            raise ValueError("per_transaction_limit cannot be negative")
        if self.requires_receipt_above is not None and self.requires_receipt_above < 0:
            raise ValueError("requires_receipt_above cannot be negative")

        # Validate daily_limit >= per_transaction_limit (if both are set)
        if (self.daily_limit is not None and
            self.per_transaction_limit is not None and
            self.daily_limit < self.per_transaction_limit):
            raise ValueError(
                f"daily_limit ({self.daily_limit}) cannot be less than "
                f"per_transaction_limit ({self.per_transaction_limit})"
            )
        logger.debug(
            "category_limit_initialized",
            extra={
                "category": self.category,
                "daily_limit": str(self.daily_limit) if self.daily_limit else None,
                "per_transaction_limit": str(self.per_transaction_limit) if self.per_transaction_limit else None,
            },
        )


@dataclass
class ExpenseConfig:
    """
    Configuration schema for expense module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = ExpenseConfig(
            receipt_required_above=Decimal("50.00"),
            mileage_rate_per_mile=Decimal("0.67"),
            **load_from_database("expense_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Receipt requirements
    receipt_required_above: Decimal = Decimal("25.00")
    allow_missing_receipts: bool = True
    missing_receipt_affidavit: bool = True

    # Per diem
    use_per_diem: bool = False
    meals_per_diem_rate: Decimal = Decimal("75.00")
    lodging_per_diem_rate: Decimal = Decimal("150.00")

    # Limits
    category_limits: tuple[CategoryLimit, ...] = field(default_factory=tuple)
    single_expense_approval_threshold: Decimal = Decimal("500.00")
    report_approval_threshold: Decimal = Decimal("2500.00")

    # Mileage (IRS standard rate as default)
    mileage_rate_per_mile: Decimal = Decimal("0.67")
    mileage_requires_destination: bool = True

    # Corporate cards
    auto_import_card_transactions: bool = True
    require_card_reconciliation: bool = True
    days_to_reconcile: int = 30

    # Approval routing
    approval_hierarchy: str = "manager"  # "manager", "cost_center", "project"
    allow_self_approval_below: Decimal = Decimal("0")

    # Payment
    payment_method: str = "ach"  # "ach", "check", "payroll"
    payment_frequency: str = "weekly"  # "weekly", "biweekly", "on_demand"

    # Policy violations
    allow_policy_exceptions: bool = True
    require_exception_justification: bool = True

    def __post_init__(self):
        # Validate receipt threshold
        if self.receipt_required_above < 0:
            raise ValueError("receipt_required_above cannot be negative")

        # Validate per diem rates
        if self.meals_per_diem_rate < 0:
            raise ValueError("meals_per_diem_rate cannot be negative")
        if self.lodging_per_diem_rate < 0:
            raise ValueError("lodging_per_diem_rate cannot be negative")

        # Validate approval thresholds
        if self.single_expense_approval_threshold < 0:
            raise ValueError("single_expense_approval_threshold cannot be negative")
        if self.report_approval_threshold < 0:
            raise ValueError("report_approval_threshold cannot be negative")

        # Validate mileage rate
        if self.mileage_rate_per_mile < 0:
            raise ValueError("mileage_rate_per_mile cannot be negative")

        # Validate days to reconcile
        if self.days_to_reconcile <= 0:
            raise ValueError("days_to_reconcile must be positive")

        # Validate approval hierarchy
        if self.approval_hierarchy not in VALID_APPROVAL_HIERARCHIES:
            raise ValueError(
                f"approval_hierarchy must be one of {VALID_APPROVAL_HIERARCHIES}, "
                f"got '{self.approval_hierarchy}'"
            )

        # Validate self-approval threshold
        if self.allow_self_approval_below < 0:
            raise ValueError("allow_self_approval_below cannot be negative")

        # Validate payment method
        if self.payment_method not in VALID_PAYMENT_METHODS:
            raise ValueError(
                f"payment_method must be one of {VALID_PAYMENT_METHODS}, "
                f"got '{self.payment_method}'"
            )

        # Validate payment frequency
        if self.payment_frequency not in VALID_PAYMENT_FREQUENCIES:
            raise ValueError(
                f"payment_frequency must be one of {VALID_PAYMENT_FREQUENCIES}, "
                f"got '{self.payment_frequency}'"
            )

        # Validate self-approval below threshold is <= approval threshold
        if self.allow_self_approval_below > self.single_expense_approval_threshold:
            raise ValueError(
                f"allow_self_approval_below ({self.allow_self_approval_below}) "
                f"cannot exceed single_expense_approval_threshold "
                f"({self.single_expense_approval_threshold})"
            )

        logger.info(
            "expense_config_initialized",
            extra={
                "receipt_required_above": str(self.receipt_required_above),
                "use_per_diem": self.use_per_diem,
                "mileage_rate_per_mile": str(self.mileage_rate_per_mile),
                "approval_hierarchy": self.approval_hierarchy,
                "payment_method": self.payment_method,
                "payment_frequency": self.payment_frequency,
                "category_limits_count": len(self.category_limits),
                "auto_import_card_transactions": self.auto_import_card_transactions,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("expense_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "expense_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        if "category_limits" in data:
            data["category_limits"] = tuple(
                CategoryLimit(**limit) if isinstance(limit, dict) else limit
                for limit in data["category_limits"]
            )
        return cls(**data)
