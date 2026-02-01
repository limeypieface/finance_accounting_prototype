"""
Accounts Payable Configuration Schema (``finance_modules.ap.config``).

Responsibility
--------------
Defines the declarative configuration schema for the AP module: three-way
match tolerances, approval thresholds, payment terms, aging buckets,
1099 thresholds, and accrual settings.  Default values represent common
US industry practices.

Architecture position
---------------------
**Modules layer** -- configuration schema only.  Loaded at runtime via
``finance_config.get_active_config()``; no component reads config files or
environment variables directly.

Invariants enforced
-------------------
* All monetary thresholds use ``Decimal`` (never ``float``).
* ``__post_init__`` validates internal consistency: discount days <= payment
  terms, approval levels sorted, aging buckets sorted and unique, etc.

Failure modes
-------------
* ``ValueError`` at construction if any constraint is violated.

Audit relevance
---------------
Configuration state logged at initialization time with all key settings.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.ap.profiles import AccountRole

logger = get_logger("modules.ap.config")


@dataclass
class MatchTolerance:
    """Tolerance settings for three-way matching.

    Contract: all percentages are ``Decimal`` in the range [0, 100].
    Guarantees: validated at construction via ``__post_init__``.
    Non-goals: does not apply the tolerance -- ``MatchingEngine`` does.
    """
    price_variance_percent: Decimal = Decimal("0.05")  # 5%
    price_variance_absolute: Decimal = Decimal("10.00")
    quantity_variance_percent: Decimal = Decimal("0.02")  # 2%
    use_lesser_of: bool = True  # use lesser of percent or absolute

    def __post_init__(self):
        if self.price_variance_percent < 0:
            raise ValueError("price_variance_percent cannot be negative")
        if self.price_variance_percent > Decimal("100"):
            raise ValueError("price_variance_percent cannot exceed 100%")
        if self.price_variance_absolute < 0:
            raise ValueError("price_variance_absolute cannot be negative")
        if self.quantity_variance_percent < 0:
            raise ValueError("quantity_variance_percent cannot be negative")
        if self.quantity_variance_percent > Decimal("100"):
            raise ValueError("quantity_variance_percent cannot exceed 100%")
        logger.debug(
            "match_tolerance_initialized",
            extra={
                "price_variance_percent": str(self.price_variance_percent),
                "price_variance_absolute": str(self.price_variance_absolute),
                "quantity_variance_percent": str(self.quantity_variance_percent),
                "use_lesser_of": self.use_lesser_of,
            },
        )


@dataclass
class ApprovalLevel:
    """Approval threshold configuration.

    Contract: ``amount_threshold >= 0``, ``required_role`` non-empty.
    Guarantees: validated at construction.
    """
    amount_threshold: Decimal
    required_role: str
    requires_dual_approval: bool = False

    def __post_init__(self):
        if self.amount_threshold < 0:
            raise ValueError("amount_threshold cannot be negative")
        if not self.required_role or not self.required_role.strip():
            raise ValueError("required_role cannot be empty")
        logger.debug(
            "approval_level_initialized",
            extra={
                "amount_threshold": str(self.amount_threshold),
                "required_role": self.required_role,
                "requires_dual_approval": self.requires_dual_approval,
            },
        )


@dataclass
class APConfig:
    """
    Configuration schema for accounts payable module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = APConfig(
            require_po_match=False,
            default_payment_terms_days=45,
            **load_from_database("ap_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Three-way match settings
    match_tolerance: MatchTolerance = field(default_factory=MatchTolerance)
    require_po_match: bool = True
    require_receipt_match: bool = True
    allow_match_override: bool = True

    # Approval settings
    approval_levels: tuple[ApprovalLevel, ...] = field(default_factory=tuple)
    auto_approve_below: Decimal = Decimal("0")

    # Payment settings
    default_payment_terms_days: int = 30
    early_payment_discount_days: int = 10
    early_payment_discount_percent: Decimal = Decimal("2.0")

    # Aging buckets (in days)
    aging_buckets: tuple[int, ...] = (30, 60, 90, 120)

    # 1099 reporting threshold (US regulatory default)
    threshold_1099: Decimal = Decimal("600.00")

    # Accrual settings
    auto_accrue_uninvoiced_receipts: bool = True
    accrual_reversal_method: str = "first_day"

    def __post_init__(self):
        # Validate payment terms relationship
        if self.early_payment_discount_days > self.default_payment_terms_days:
            raise ValueError(
                f"early_payment_discount_days ({self.early_payment_discount_days}) "
                f"cannot exceed default_payment_terms_days ({self.default_payment_terms_days})"
            )

        # Validate approval levels are sorted by threshold
        if self.approval_levels:
            thresholds = [lvl.amount_threshold for lvl in self.approval_levels]
            if thresholds != sorted(thresholds):
                raise ValueError("approval_levels must be sorted by amount_threshold ascending")

        # Validate auto_approve_below is non-negative
        if self.auto_approve_below < 0:
            raise ValueError("auto_approve_below cannot be negative")

        # Validate aging buckets are sorted and unique
        if self.aging_buckets:
            if list(self.aging_buckets) != sorted(self.aging_buckets):
                raise ValueError("aging_buckets must be sorted ascending")
            if len(self.aging_buckets) != len(set(self.aging_buckets)):
                raise ValueError("aging_buckets must be unique")
            if any(b <= 0 for b in self.aging_buckets):
                raise ValueError("aging_buckets must contain positive values")

        # Validate discount percent is reasonable
        if self.early_payment_discount_percent < 0:
            raise ValueError("early_payment_discount_percent cannot be negative")
        if self.early_payment_discount_percent > Decimal("100"):
            raise ValueError("early_payment_discount_percent cannot exceed 100%")

        # Validate 1099 threshold
        if self.threshold_1099 < 0:
            raise ValueError("threshold_1099 cannot be negative")

        # Validate accrual_reversal_method
        valid_reversal_methods = {"first_day", "last_day", "manual"}
        if self.accrual_reversal_method not in valid_reversal_methods:
            raise ValueError(
                f"accrual_reversal_method must be one of {valid_reversal_methods}, "
                f"got '{self.accrual_reversal_method}'"
            )

        logger.info(
            "ap_config_initialized",
            extra={
                "require_po_match": self.require_po_match,
                "require_receipt_match": self.require_receipt_match,
                "default_payment_terms_days": self.default_payment_terms_days,
                "early_payment_discount_days": self.early_payment_discount_days,
                "aging_buckets": list(self.aging_buckets),
                "accrual_reversal_method": self.accrual_reversal_method,
                "approval_levels_count": len(self.approval_levels),
                "auto_approve_below": str(self.auto_approve_below),
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults.

        Postconditions:
            - Returns a valid ``APConfig`` with all defaults applied.
        """
        logger.info("ap_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file).

        Preconditions:
            - ``data`` keys match ``APConfig`` field names.
        Postconditions:
            - Nested objects (``MatchTolerance``, ``ApprovalLevel``) hydrated.
        Raises:
            ValueError: if validation fails in ``__post_init__``.
        """
        logger.info(
            "ap_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        # Handle nested objects
        if "match_tolerance" in data and isinstance(data["match_tolerance"], dict):
            data["match_tolerance"] = MatchTolerance(**data["match_tolerance"])
        if "approval_levels" in data:
            data["approval_levels"] = tuple(
                ApprovalLevel(**level) if isinstance(level, dict) else level
                for level in data["approval_levels"]
            )
        return cls(**data)
