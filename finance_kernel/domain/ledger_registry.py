"""LedgerRegistry -- Required account roles per economic type per ledger."""

from dataclasses import dataclass
from typing import ClassVar

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.ledger_registry")


@dataclass(frozen=True)
class LedgerRequirements:
    """Requirements for a ledger by economic type."""

    required_roles: dict[str, tuple[str, ...]]  # economic_type -> (role1, role2, ...)
    dimension_requirements: tuple[str, ...] = ()


class LedgerRegistry:
    """Registry for ledger requirements (P7)."""

    # Class-level registry: ledger_id -> LedgerRequirements
    _ledgers: ClassVar[dict[str, LedgerRequirements]] = {}

    @classmethod
    def register(
        cls,
        ledger_id: str,
        required_roles: dict[str, tuple[str, ...]],
        dimension_requirements: tuple[str, ...] = (),
    ) -> None:
        """Register ledger requirements."""
        cls._ledgers[ledger_id] = LedgerRequirements(
            required_roles=required_roles,
            dimension_requirements=dimension_requirements,
        )
        logger.info(
            "ledger_registered",
            extra={
                "ledger_id": ledger_id,
                "economic_types": sorted(required_roles.keys()),
                "dimension_requirements": list(dimension_requirements),
            },
        )

    @classmethod
    def get_required_roles(
        cls,
        ledger_id: str,
        economic_type: str,
    ) -> tuple[str, ...]:
        """Get required account roles for an economic type on a ledger."""
        if ledger_id not in cls._ledgers:
            logger.debug(
                "ledger_not_found",
                extra={
                    "ledger_id": ledger_id,
                    "economic_type": economic_type,
                },
            )
            return ()

        requirements = cls._ledgers[ledger_id]
        roles = requirements.required_roles.get(economic_type, ())
        if roles:
            logger.debug(
                "ledger_resolved",
                extra={
                    "ledger_id": ledger_id,
                    "economic_type": economic_type,
                    "required_roles": list(roles),
                },
            )
        else:
            logger.debug(
                "ledger_no_roles_for_type",
                extra={
                    "ledger_id": ledger_id,
                    "economic_type": economic_type,
                },
            )
        return roles

    @classmethod
    def get_dimension_requirements(cls, ledger_id: str) -> tuple[str, ...]:
        """Get dimension requirements for a ledger."""
        if ledger_id not in cls._ledgers:
            return ()
        return cls._ledgers[ledger_id].dimension_requirements

    @classmethod
    def has_ledger(cls, ledger_id: str) -> bool:
        """Check if ledger is registered."""
        return ledger_id in cls._ledgers

    @classmethod
    def list_ledgers(cls) -> list[str]:
        """List all registered ledgers."""
        return sorted(cls._ledgers.keys())

    @classmethod
    def clear(cls) -> None:
        """Clear all registered ledgers. For testing only."""
        cls._ledgers.clear()


# Register default GL ledger requirements
def _register_default_gl():
    """Register default GL ledger with common economic types."""
    LedgerRegistry.register(
        ledger_id="GL",
        required_roles={
            "InventoryIncrease": ("InventoryAsset", "GRNI"),
            "InventoryDecrease": ("COGS", "InventoryAsset"),
            "Revenue": ("AccountsReceivable", "Revenue"),
            "Expense": ("Expense", "AccountsPayable"),
            "Payment": ("Cash", "AccountsPayable"),
            "Receipt": ("Cash", "AccountsReceivable"),
        },
        dimension_requirements=("cost_center",),
    )


# Auto-register default GL on module load
_register_default_gl()
