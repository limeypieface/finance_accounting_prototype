"""
LedgerRegistry -- Required account roles per economic type per ledger.

Responsibility:
    Defines what account roles are required for each economic type on each
    ledger.  Used by PolicyCompiler for P7 validation (semantic completeness).

Architecture position:
    Kernel > Domain -- pure functional core, zero I/O.

Invariants enforced:
    P7 -- Compiler rejects profiles that don't map to all required account
          roles for the target ledger.

Failure modes:
    (none -- returns empty tuples for unknown ledgers or economic types)

Audit relevance:
    Auditors verify that every profile provides mappings for all roles
    required by the LedgerRegistry (P7).  Missing roles would mean the
    posting pipeline cannot resolve accounts at L1.
"""

from dataclasses import dataclass
from typing import ClassVar

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.ledger_registry")


@dataclass(frozen=True)
class LedgerRequirements:
    """
    Requirements for a ledger by economic type.

    Contract:
        Maps each economic type to the account roles it must provide.

    Guarantees:
        Frozen dataclass -- immutable after construction.

    Attributes:
        required_roles: Account roles required for each economic type
        dimension_requirements: Dimensions that must be provided
    """

    required_roles: dict[str, tuple[str, ...]]  # economic_type -> (role1, role2, ...)
    dimension_requirements: tuple[str, ...] = ()


class LedgerRegistry:
    """
    Registry for ledger requirements.

    Contract:
        Class-level singleton registry.  ``register()`` adds a ledger,
        ``get_required_roles()`` queries it.

    Guarantees:
        - ``get_required_roles()`` returns an empty tuple (never None) for
          unknown ledgers or economic types.
        - ``list_ledgers()`` returns a sorted list of all registered ledger IDs.

    Non-goals:
        - Does NOT persist to database (in-memory registry, populated at
          module load and by config assembly).
        - Does NOT resolve roles to COA accounts (that is L1 / JournalWriter).

    Invariants enforced:
        P7 -- Compiler rejects profiles that don't map to required accounts.
    """

    # Class-level registry: ledger_id -> LedgerRequirements
    _ledgers: ClassVar[dict[str, LedgerRequirements]] = {}

    @classmethod
    def register(
        cls,
        ledger_id: str,
        required_roles: dict[str, tuple[str, ...]],
        dimension_requirements: tuple[str, ...] = (),
    ) -> None:
        """
        Register ledger requirements.

        Args:
            ledger_id: Ledger identifier (e.g., "GL")
            required_roles: Map of economic_type -> required account roles
            dimension_requirements: Required dimensions for this ledger
        """
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
        """
        Get required account roles for an economic type on a ledger.

        Args:
            ledger_id: The ledger identifier.
            economic_type: The economic event type.

        Returns:
            Tuple of required account role names.
        """
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
