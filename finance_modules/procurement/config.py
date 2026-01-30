"""
Procurement Configuration Schema.

Defines the structure and sensible defaults for procurement settings.
Actual values are loaded from company configuration at runtime.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from finance_kernel.logging_config import get_logger
from finance_modules.procurement.profiles import AccountRole

logger = get_logger("modules.procurement.config")


@dataclass
class ApprovalLevel:
    """Approval threshold by amount."""
    max_amount: Decimal
    approver_role: str


@dataclass
class ProcurementConfig:
    """
    Configuration schema for procurement module.

    Field defaults represent common industry practices.
    Override at instantiation with company-specific values:

        config = ProcurementConfig(
            require_requisition=True,
            enable_three_way_match=True,
            **load_from_database("procurement_settings"),
        )
    """

    # Account role mappings (required - no sensible default)
    account_mappings: dict[AccountRole, str] = field(default_factory=dict)

    # Requisitions
    require_requisition: bool = True
    auto_create_po: bool = False
    allow_non_catalog_items: bool = True

    # Approval routing
    approval_levels: tuple[ApprovalLevel, ...] = field(default_factory=tuple)
    require_budget_check: bool = True
    allow_over_budget: bool = False
    parallel_approval: bool = False

    # Vendors
    require_approved_vendors: bool = True
    vendor_approval_categories: tuple[str, ...] = field(default_factory=tuple)

    # Purchase orders
    default_payment_terms_days: int = 30
    require_po_for_invoice: bool = True
    po_change_order_threshold: Decimal = Decimal("500.00")

    # Receiving
    require_receipt_for_payment: bool = True
    allow_over_receipt_percent: Decimal = Decimal("10.0")
    blind_receiving: bool = False

    # Three-way match
    enable_three_way_match: bool = True
    match_tolerance_percent: Decimal = Decimal("5.0")
    match_tolerance_amount: Decimal = Decimal("10.00")

    # Encumbrance
    use_encumbrance_accounting: bool = False
    relieve_encumbrance_on: str = "receipt"  # "receipt", "invoice", "payment"

    # Blanket orders
    allow_blanket_orders: bool = True
    blanket_order_expiry_warning_days: int = 30

    def __post_init__(self):
        logger.info(
            "procurement_config_initialized",
            extra={
                "require_requisition": self.require_requisition,
                "require_budget_check": self.require_budget_check,
                "require_approved_vendors": self.require_approved_vendors,
                "require_po_for_invoice": self.require_po_for_invoice,
                "enable_three_way_match": self.enable_three_way_match,
                "use_encumbrance_accounting": self.use_encumbrance_accounting,
                "approval_levels_count": len(self.approval_levels),
                "allow_blanket_orders": self.allow_blanket_orders,
            },
        )

    @classmethod
    def with_defaults(cls) -> Self:
        """Create config with industry-standard defaults."""
        logger.info("procurement_config_created_with_defaults")
        return cls()

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create config from dictionary (e.g., loaded from database/file)."""
        logger.info(
            "procurement_config_loading_from_dict",
            extra={"keys": sorted(data.keys())},
        )
        if "approval_levels" in data:
            data["approval_levels"] = tuple(
                ApprovalLevel(**level) if isinstance(level, dict) else level
                for level in data["approval_levels"]
            )
        return cls(**data)
