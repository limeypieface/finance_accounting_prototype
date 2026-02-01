"""PolicyAuthority -- Governance layer controlling economic capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import FrozenSet
from uuid import UUID

from finance_kernel.logging_config import get_logger

logger = get_logger("domain.policy_authority")


class EconomicCapability(str, Enum):
    """Economic actions that can be authorized."""

    # Balance operations
    CREATE_BALANCE = "create_balance"  # Create new account balance
    CLEAR_BALANCE = "clear_balance"  # Zero out/clear balance

    # Value recognition
    RECOGNIZE_REVENUE = "recognize_revenue"  # Book revenue
    RECOGNIZE_EXPENSE = "recognize_expense"  # Book expense
    RECOGNIZE_GAIN = "recognize_gain"  # Book gain
    RECOGNIZE_LOSS = "recognize_loss"  # Book loss

    # Asset operations
    CAPITALIZE = "capitalize"  # Add to asset value
    DEPRECIATE = "depreciate"  # Reduce asset value over time
    IMPAIR = "impair"  # Write down asset value
    DISPOSE = "dispose"  # Remove asset from books

    # Liability operations
    ACCRUE = "accrue"  # Create accrued liability
    SETTLE = "settle"  # Settle/pay liability

    # Write-off operations
    WRITE_OFF = "write_off"  # Write off uncollectable/unusable
    WRITE_DOWN = "write_down"  # Partial value reduction

    # Adjustment operations
    ADJUST = "adjust"  # General adjustment
    CORRECT = "correct"  # Error correction
    RECLASSIFY = "reclassify"  # Move between accounts

    # Intercompany
    INTERCOMPANY_TRANSFER = "intercompany_transfer"  # Between entities


class ModuleType(str, Enum):
    """Module types that can hold economic authority."""

    GL = "gl"  # General Ledger
    AP = "ap"  # Accounts Payable
    AR = "ar"  # Accounts Receivable
    INVENTORY = "inventory"  # Inventory Management
    FIXED_ASSETS = "fixed_assets"  # Fixed Asset Management
    PAYROLL = "payroll"  # Payroll
    BANK = "bank"  # Bank/Cash Management
    TAX = "tax"  # Tax Module
    MANUFACTURING = "manufacturing"  # Manufacturing/WIP
    PROJECTS = "projects"  # Project Accounting
    INTERCOMPANY = "intercompany"  # Intercompany Transactions


@dataclass(frozen=True, slots=True)
class ModuleAuthorization:
    """Authorization for a module to perform economic actions."""

    module_type: ModuleType
    capabilities: frozenset[EconomicCapability]
    allowed_ledgers: frozenset[str]  # Ledger IDs this module can post to
    restricted_account_roles: frozenset[str] = frozenset()  # Roles module CANNOT use
    effective_from: datetime | None = None
    effective_to: datetime | None = None  # None = current

    def has_capability(self, capability: EconomicCapability) -> bool:
        """Check if module has a specific capability."""
        return capability in self.capabilities

    def can_post_to_ledger(self, ledger_id: str) -> bool:
        """Check if module can post to a specific ledger."""
        return ledger_id in self.allowed_ledgers

    def can_use_role(self, account_role: str) -> bool:
        """Check if module can use a specific account role."""
        return account_role not in self.restricted_account_roles

    def is_effective(self, as_of: datetime) -> bool:
        """Check if authorization is effective at a given time."""
        if self.effective_from and as_of < self.effective_from:
            return False
        if self.effective_to and as_of > self.effective_to:
            return False
        return True


@dataclass(frozen=True, slots=True)
class LedgerRoleMapping:
    """Maps a ledger role to its control account."""

    ledger_id: str
    role_code: str
    control_account_code: str
    is_debit_normal: bool  # Normal balance side
    effective_from: datetime | None = None
    effective_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class EconomicTypeConstraint:
    """Constraint on which ledgers an economic type can affect."""

    economic_type: str  # e.g., "inventory.receipt", "ap.invoice"
    required_ledgers: frozenset[str]  # Must post to these
    optional_ledgers: frozenset[str]  # May post to these
    forbidden_ledgers: frozenset[str]  # Never post to these

    def validate_ledgers(self, target_ledgers: frozenset[str]) -> list[str]:
        """Validate that target ledgers satisfy constraints."""
        errors: list[str] = []

        # Check required ledgers are present
        missing_required = self.required_ledgers - target_ledgers
        if missing_required:
            errors.append(
                f"Missing required ledgers for {self.economic_type}: "
                f"{', '.join(sorted(missing_required))}"
            )

        # Check no forbidden ledgers are present
        forbidden_present = self.forbidden_ledgers & target_ledgers
        if forbidden_present:
            errors.append(
                f"Forbidden ledgers for {self.economic_type}: "
                f"{', '.join(sorted(forbidden_present))}"
            )

        # Check unknown ledgers (not required, not optional, not forbidden)
        known_ledgers = (
            self.required_ledgers | self.optional_ledgers | self.forbidden_ledgers
        )
        unknown = target_ledgers - known_ledgers
        if unknown:
            errors.append(
                f"Unknown ledgers for {self.economic_type}: "
                f"{', '.join(sorted(unknown))}"
            )

        return errors


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """A policy violation with details."""

    policy_type: str
    module: ModuleType | None
    capability: EconomicCapability | None
    ledger_id: str | None
    economic_type: str | None
    message: str
    severity: str = "error"  # "error" or "warning"


@dataclass(frozen=True)
class PolicyAuthority:
    """Central registry of all economic policies."""

    version: int
    effective_from: datetime
    effective_to: datetime | None  # None = current

    # Module authorizations
    module_authorizations: tuple[ModuleAuthorization, ...]

    # Ledger role mappings
    ledger_role_mappings: tuple[LedgerRoleMapping, ...]

    # Economic type constraints
    economic_type_constraints: tuple[EconomicTypeConstraint, ...]

    def get_module_authorization(
        self,
        module_type: ModuleType,
        as_of: datetime | None = None,
    ) -> ModuleAuthorization | None:
        """Get authorization for a module type."""
        for auth in self.module_authorizations:
            if auth.module_type == module_type:
                if as_of is None or auth.is_effective(as_of):
                    logger.debug(
                        "policy_resolved",
                        extra={
                            "module_type": module_type.value,
                            "policy_version": self.version,
                        },
                    )
                    return auth
        logger.warning(
            "policy_missing",
            extra={
                "module_type": module_type.value,
                "policy_version": self.version,
                "lookup_type": "module_authorization",
            },
        )
        return None

    def get_ledger_role_mapping(
        self,
        ledger_id: str,
        role_code: str,
    ) -> LedgerRoleMapping | None:
        """Get control account mapping for a ledger role."""
        for mapping in self.ledger_role_mappings:
            if mapping.ledger_id == ledger_id and mapping.role_code == role_code:
                return mapping
        return None

    def get_economic_type_constraint(
        self,
        economic_type: str,
    ) -> EconomicTypeConstraint | None:
        """Get constraints for an economic type."""
        for constraint in self.economic_type_constraints:
            if constraint.economic_type == economic_type:
                return constraint
        return None

    def validate_module_action(
        self,
        module_type: ModuleType,
        capability: EconomicCapability,
        ledger_id: str,
        as_of: datetime | None = None,
    ) -> list[PolicyViolation]:
        """Validate that a module can perform an action on a ledger."""
        violations: list[PolicyViolation] = []

        auth = self.get_module_authorization(module_type, as_of)

        if auth is None:
            violations.append(
                PolicyViolation(
                    policy_type="module_authorization",
                    module=module_type,
                    capability=capability,
                    ledger_id=ledger_id,
                    economic_type=None,
                    message=f"No authorization found for module {module_type.value}",
                )
            )
            return violations

        if not auth.has_capability(capability):
            violations.append(
                PolicyViolation(
                    policy_type="capability",
                    module=module_type,
                    capability=capability,
                    ledger_id=ledger_id,
                    economic_type=None,
                    message=(
                        f"Module {module_type.value} does not have "
                        f"capability {capability.value}"
                    ),
                )
            )

        if not auth.can_post_to_ledger(ledger_id):
            violations.append(
                PolicyViolation(
                    policy_type="ledger_access",
                    module=module_type,
                    capability=capability,
                    ledger_id=ledger_id,
                    economic_type=None,
                    message=(
                        f"Module {module_type.value} cannot post to "
                        f"ledger {ledger_id}"
                    ),
                )
            )

        if violations:
            logger.warning(
                "module_action_policy_violation",
                extra={
                    "module_type": module_type.value,
                    "capability": capability.value,
                    "ledger_id": ledger_id,
                    "violation_count": len(violations),
                    "violation_types": [v.policy_type for v in violations],
                },
            )
        else:
            logger.info(
                "module_action_policy_validated",
                extra={
                    "module_type": module_type.value,
                    "capability": capability.value,
                    "ledger_id": ledger_id,
                },
            )

        return violations

    def validate_economic_type_posting(
        self,
        economic_type: str,
        target_ledgers: frozenset[str],
    ) -> list[PolicyViolation]:
        """Validate that an economic type can post to target ledgers."""
        violations: list[PolicyViolation] = []

        constraint = self.get_economic_type_constraint(economic_type)

        if constraint is None:
            # No constraint = allow any ledger (permissive default)
            logger.debug(
                "economic_type_no_constraint",
                extra={
                    "economic_type": economic_type,
                    "target_ledgers": sorted(target_ledgers),
                },
            )
            return violations

        error_messages = constraint.validate_ledgers(target_ledgers)
        for msg in error_messages:
            violations.append(
                PolicyViolation(
                    policy_type="economic_type_constraint",
                    module=None,
                    capability=None,
                    ledger_id=None,
                    economic_type=economic_type,
                    message=msg,
                )
            )

        if violations:
            logger.warning(
                "economic_type_posting_violation",
                extra={
                    "economic_type": economic_type,
                    "target_ledgers": sorted(target_ledgers),
                    "violation_count": len(violations),
                },
            )
        else:
            logger.info(
                "economic_type_posting_validated",
                extra={
                    "economic_type": economic_type,
                    "target_ledgers": sorted(target_ledgers),
                },
            )

        return violations


class PolicyAuthorityBuilder:
    """Builder for constructing PolicyAuthority instances."""

    def __init__(self, version: int = 1):
        self._version = version
        self._effective_from = datetime.now()
        self._effective_to: datetime | None = None
        self._module_authorizations: list[ModuleAuthorization] = []
        self._ledger_role_mappings: list[LedgerRoleMapping] = []
        self._economic_type_constraints: list[EconomicTypeConstraint] = []

    def effective_from(self, dt: datetime) -> PolicyAuthorityBuilder:
        self._effective_from = dt
        return self

    def effective_to(self, dt: datetime | None) -> PolicyAuthorityBuilder:
        self._effective_to = dt
        return self

    def authorize_module(
        self,
        module_type: ModuleType,
        capabilities: frozenset[EconomicCapability],
        allowed_ledgers: frozenset[str],
        restricted_account_roles: frozenset[str] = frozenset(),
    ) -> PolicyAuthorityBuilder:
        """Add module authorization."""
        self._module_authorizations.append(
            ModuleAuthorization(
                module_type=module_type,
                capabilities=capabilities,
                allowed_ledgers=allowed_ledgers,
                restricted_account_roles=restricted_account_roles,
                effective_from=self._effective_from,
                effective_to=self._effective_to,
            )
        )
        return self

    def map_ledger_role(
        self,
        ledger_id: str,
        role_code: str,
        control_account_code: str,
        is_debit_normal: bool = True,
    ) -> PolicyAuthorityBuilder:
        """Add ledger role mapping."""
        self._ledger_role_mappings.append(
            LedgerRoleMapping(
                ledger_id=ledger_id,
                role_code=role_code,
                control_account_code=control_account_code,
                is_debit_normal=is_debit_normal,
                effective_from=self._effective_from,
                effective_to=self._effective_to,
            )
        )
        return self

    def constrain_economic_type(
        self,
        economic_type: str,
        required_ledgers: frozenset[str] = frozenset(),
        optional_ledgers: frozenset[str] = frozenset(),
        forbidden_ledgers: frozenset[str] = frozenset(),
    ) -> PolicyAuthorityBuilder:
        """Add economic type constraint."""
        self._economic_type_constraints.append(
            EconomicTypeConstraint(
                economic_type=economic_type,
                required_ledgers=required_ledgers,
                optional_ledgers=optional_ledgers,
                forbidden_ledgers=forbidden_ledgers,
            )
        )
        return self

    def build(self) -> PolicyAuthority:
        """Build the PolicyAuthority."""
        return PolicyAuthority(
            version=self._version,
            effective_from=self._effective_from,
            effective_to=self._effective_to,
            module_authorizations=tuple(self._module_authorizations),
            ledger_role_mappings=tuple(self._ledger_role_mappings),
            economic_type_constraints=tuple(self._economic_type_constraints),
        )


# =============================================================================
# Default Policy Configurations
# =============================================================================


def create_standard_ap_authorization() -> ModuleAuthorization:
    """Create standard AP module authorization."""
    return ModuleAuthorization(
        module_type=ModuleType.AP,
        capabilities=frozenset({
            EconomicCapability.CREATE_BALANCE,
            EconomicCapability.CLEAR_BALANCE,
            EconomicCapability.RECOGNIZE_EXPENSE,
            EconomicCapability.ACCRUE,
            EconomicCapability.SETTLE,
            EconomicCapability.ADJUST,
            EconomicCapability.CORRECT,
        }),
        allowed_ledgers=frozenset({"AP", "GL"}),
        restricted_account_roles=frozenset({
            "REVENUE",  # AP cannot book revenue
            "EQUITY",  # AP cannot touch equity directly
        }),
    )


def create_standard_ar_authorization() -> ModuleAuthorization:
    """Create standard AR module authorization."""
    return ModuleAuthorization(
        module_type=ModuleType.AR,
        capabilities=frozenset({
            EconomicCapability.CREATE_BALANCE,
            EconomicCapability.CLEAR_BALANCE,
            EconomicCapability.RECOGNIZE_REVENUE,
            EconomicCapability.WRITE_OFF,
            EconomicCapability.ADJUST,
            EconomicCapability.CORRECT,
        }),
        allowed_ledgers=frozenset({"AR", "GL"}),
        restricted_account_roles=frozenset({
            "INVENTORY",  # AR cannot touch inventory
            "FIXED_ASSETS",  # AR cannot touch fixed assets
        }),
    )


def create_standard_inventory_authorization() -> ModuleAuthorization:
    """Create standard Inventory module authorization."""
    return ModuleAuthorization(
        module_type=ModuleType.INVENTORY,
        capabilities=frozenset({
            EconomicCapability.CREATE_BALANCE,
            EconomicCapability.CLEAR_BALANCE,
            EconomicCapability.CAPITALIZE,
            EconomicCapability.RECOGNIZE_EXPENSE,  # COGS
            EconomicCapability.WRITE_OFF,
            EconomicCapability.WRITE_DOWN,
            EconomicCapability.ADJUST,
            EconomicCapability.CORRECT,
            EconomicCapability.RECLASSIFY,
        }),
        allowed_ledgers=frozenset({"INVENTORY", "GL"}),
        restricted_account_roles=frozenset({
            "REVENUE",  # Inventory cannot book revenue directly
            "AR",  # Inventory cannot touch AR
        }),
    )


def create_default_policy_registry() -> PolicyAuthority:
    """Create a default policy registry with standard configurations."""
    builder = PolicyAuthorityBuilder(version=1)

    # Add standard module authorizations
    builder._module_authorizations.append(create_standard_ap_authorization())
    builder._module_authorizations.append(create_standard_ar_authorization())
    builder._module_authorizations.append(create_standard_inventory_authorization())

    # Add GL authorization (full access)
    builder.authorize_module(
        module_type=ModuleType.GL,
        capabilities=frozenset(EconomicCapability),  # All capabilities
        allowed_ledgers=frozenset({"GL", "AP", "AR", "INVENTORY", "BANK"}),
        restricted_account_roles=frozenset(),  # No restrictions
    )

    # Add standard ledger role mappings
    builder.map_ledger_role("AP", "AP_CONTROL", "2100", is_debit_normal=False)
    builder.map_ledger_role("AR", "AR_CONTROL", "1200", is_debit_normal=True)
    builder.map_ledger_role("INVENTORY", "INV_CONTROL", "1400", is_debit_normal=True)
    builder.map_ledger_role("BANK", "CASH_CONTROL", "1000", is_debit_normal=True)

    # Add economic type constraints
    builder.constrain_economic_type(
        economic_type="ap.invoice",
        required_ledgers=frozenset({"AP"}),
        optional_ledgers=frozenset({"GL"}),
        forbidden_ledgers=frozenset({"AR", "INVENTORY"}),
    )

    builder.constrain_economic_type(
        economic_type="ar.invoice",
        required_ledgers=frozenset({"AR"}),
        optional_ledgers=frozenset({"GL"}),
        forbidden_ledgers=frozenset({"AP", "INVENTORY"}),
    )

    builder.constrain_economic_type(
        economic_type="inventory.receipt",
        required_ledgers=frozenset({"INVENTORY"}),
        optional_ledgers=frozenset({"GL", "AP"}),
        forbidden_ledgers=frozenset({"AR"}),
    )

    return builder.build()
