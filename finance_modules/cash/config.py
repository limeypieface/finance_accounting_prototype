"""
finance_modules.cash.config
============================

Responsibility:
    Configuration schema for the cash management module.  Defines the
    structure, validation rules, and sensible defaults for cash settings.
    Actual values are loaded from company configuration at runtime via
    ``finance_config.get_active_config()``.

Architecture:
    Module layer (finance_modules).  Consumed by CashService and the
    configuration assembler.  MUST NOT be imported by finance_kernel.

Invariants enforced:
    - ``reconciliation_tolerance`` is non-negative (validated in ``__post_init__``).
    - ``transit_account_code`` required when ``use_transit_account_for_wires``
      is True.
    - All monetary thresholds are ``Decimal`` -- never ``float``.

Failure modes:
    - Invalid configuration values -> ``ValueError`` from ``__post_init__``.

Audit relevance:
    Reconciliation tolerance directly affects whether reconciliation
    adjustments are posted.  Changes to this value should be audited.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self
from uuid import UUID

from finance_kernel.logging_config import get_logger
from finance_modules.cash.profiles import AccountRole

logger = get_logger("modules.cash.config")


@dataclass
class CashConfig:
    """
    Configuration schema for cash management module.

    Contract:
        All fields have sensible defaults.  Override at instantiation
        with company-specific values.  ``__post_init__`` validates all
        constraints and raises ``ValueError`` on violation.

    Guarantees:
        - ``reconciliation_tolerance >= 0``.
        - ``require_dual_approval_above >= 0`` when set.
        - ``transit_account_code`` is present when wires use transit.

    Non-goals:
        - Does NOT enforce GL account existence (kernel responsibility).

    Example::

        config = CashConfig(
            reconciliation_tolerance=Decimal("0.05"),
            use_transit_account_for_wires=True,
            transit_account_code="1015",
            **load_from_database("cash_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Bank account registry
    bank_account_gl_mappings: dict[UUID, str] = field(default_factory=dict)

    # Reconciliation settings
    reconciliation_tolerance: Decimal = Decimal("0.01")
    require_dual_approval_above: Decimal | None = None

    # Transfer settings
    use_transit_account_for_wires: bool = False  # Requires transit_account_code when True
    transit_account_code: str | None = None

    def __post_init__(self):
        # Validate reconciliation tolerance
        if self.reconciliation_tolerance < 0:
            raise ValueError("reconciliation_tolerance cannot be negative")

        # Validate dual approval threshold if set
        if self.require_dual_approval_above is not None and self.require_dual_approval_above < 0:
            raise ValueError("require_dual_approval_above cannot be negative")

        # Validate transit_account_code is set when use_transit_account_for_wires is True
        if self.use_transit_account_for_wires and not self.transit_account_code:
            raise ValueError(
                "transit_account_code is required when use_transit_account_for_wires is True"
            )

        logger.info(
            "cash_config_initialized",
            extra={
                "reconciliation_tolerance": str(self.reconciliation_tolerance),
                "use_transit_account_for_wires": self.use_transit_account_for_wires,
                "bank_account_count": len(self.bank_account_gl_mappings),
                "has_dual_approval_threshold": self.require_dual_approval_above is not None,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("cash_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "cash_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        return cls(**data)
