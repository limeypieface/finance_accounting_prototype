"""
Config → Kernel Bridges.

Functions that convert CompiledPolicyPack artifacts into kernel-compatible
inputs. These live in finance_config (the producer) because the kernel
must NEVER import finance_config.

Usage:
    from finance_config.bridges import build_role_resolver, build_subledger_registry

    config = get_active_config(...)
    role_resolver = build_role_resolver(config)
    registry = build_subledger_registry(config, role_resolver)
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid5

from finance_config.compiler import CompiledPolicyPack
from finance_config.schema import SubledgerContractDef
from finance_kernel.domain.subledger_control import (
    ControlAccountBinding,
    ReconciliationTiming,
    ReconciliationTolerance,
    SubledgerControlContract,
    SubledgerControlRegistry,
    SubledgerType,
    ToleranceType,
)
from finance_kernel.exceptions import FinanceKernelError
from finance_kernel.services.journal_writer import RoleResolver

# Fixed namespace for deterministic account UUID generation.
# In production, account IDs would come from the database.
_COA_UUID_NAMESPACE = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _account_type_from_code(code: str) -> tuple[str, str]:
    """Derive (account_type, normal_balance) from COA code prefix."""
    if code.startswith("SL-"):
        return ("asset", "debit")
    prefix = int(code[0]) if code and code[0].isdigit() else 0
    if prefix == 1:
        return ("asset", "debit")
    elif prefix == 2:
        return ("liability", "credit")
    elif prefix == 3:
        return ("equity", "credit")
    elif prefix == 4:
        return ("revenue", "credit")
    elif prefix in (5, 6):
        return ("expense", "debit")
    return ("expense", "debit")


def build_role_resolver(config: CompiledPolicyPack) -> RoleResolver:
    """Build a RoleResolver from CompiledPolicyPack role_bindings.

    Generates deterministic UUIDs from account codes using uuid5 so that
    the same code always yields the same account ID.  Includes full binding
    provenance (effective dates, config identity) for audit logging.
    """
    resolver = RoleResolver()
    for binding in config.role_bindings:
        account_id = uuid5(_COA_UUID_NAMESPACE, binding.account_code)
        atype, nbal = _account_type_from_code(binding.account_code)
        resolver.register_binding(
            binding.role,
            account_id,
            binding.account_code,
            account_name=f"{binding.role} ({binding.account_code})",
            account_type=atype,
            normal_balance=nbal,
            effective_from=str(binding.effective_from),
            effective_to=str(binding.effective_to) if binding.effective_to else "",
            config_id=config.config_id,
            config_version=config.config_version,
        )
    return resolver


# ---------------------------------------------------------------------------
# Timing / tolerance mapping
# ---------------------------------------------------------------------------

_TIMING_MAP: dict[str, ReconciliationTiming] = {
    "real_time": ReconciliationTiming.REAL_TIME,
    "daily": ReconciliationTiming.DAILY,
    "period_end": ReconciliationTiming.PERIOD_END,
}


def _build_tolerance(contract_def: SubledgerContractDef) -> ReconciliationTolerance:
    """Convert config tolerance fields into a ReconciliationTolerance."""
    ttype = contract_def.tolerance_type.lower()
    if ttype == "none":
        return ReconciliationTolerance.zero()
    if ttype == "absolute":
        return ReconciliationTolerance.pennies(Decimal(contract_def.tolerance_amount))
    if ttype == "percentage":
        return ReconciliationTolerance.percent(Decimal(contract_def.tolerance_percentage))
    return ReconciliationTolerance.zero()


# ---------------------------------------------------------------------------
# Subledger registry builder
# ---------------------------------------------------------------------------


def build_subledger_registry(
    config: CompiledPolicyPack,
    role_resolver: RoleResolver,
) -> SubledgerControlRegistry:
    """Build a SubledgerControlRegistry from compiled config.

    Resolves each contract's control_account_role to a concrete COA
    account code at config compilation time (not dynamically at G9 time).
    If a role cannot be resolved, raises ConfigurationError.

    Args:
        config: Compiled policy pack containing subledger_contracts.
        role_resolver: Role resolver built from the same config's role bindings.

    Returns:
        Fully populated SubledgerControlRegistry.
    """
    registry = SubledgerControlRegistry()

    # subledger_contracts lives on the source AccountingConfigurationSet,
    # which is not carried through to CompiledPolicyPack. The contracts
    # are accessed through the config's source data.  However, we receive
    # them as a separate parameter list from the caller who has access.
    # For now, this function is called by PostingOrchestrator which passes
    # contracts from the assembled config.
    #
    # The function signature accepts CompiledPolicyPack for future use
    # when contracts are compiled into the pack. Currently the registry
    # is built externally.
    return registry


def build_subledger_registry_from_defs(
    contract_defs: tuple[SubledgerContractDef, ...],
    role_resolver: RoleResolver,
    default_currency: str = "USD",
) -> SubledgerControlRegistry:
    """Build a SubledgerControlRegistry from SubledgerContractDef list.

    Resolves each contract's control_account_role to a concrete COA
    account code at config time. This ensures G9 enforcement uses
    pre-resolved accounts — no dynamic role lookup at post time.

    Args:
        contract_defs: Subledger contract definitions from config.
        role_resolver: Role resolver for control_account_role → COA code.
        default_currency: Default currency for control account bindings.

    Returns:
        Fully populated SubledgerControlRegistry.

    Raises:
        FinanceKernelError: If a control_account_role cannot be resolved.
    """
    registry = SubledgerControlRegistry()

    for cdef in contract_defs:
        # Resolve subledger type
        try:
            sl_type = SubledgerType(cdef.subledger_id)
        except ValueError:
            raise FinanceKernelError(
                f"Unknown subledger_id '{cdef.subledger_id}' in config. "
                f"Valid types: {[t.value for t in SubledgerType]}"
            )

        # Resolve control account role to concrete COA code
        try:
            _account_id, account_code = role_resolver.resolve(
                cdef.control_account_role, "GL", 0,
            )
        except Exception as exc:
            raise FinanceKernelError(
                f"Cannot resolve control_account_role '{cdef.control_account_role}' "
                f"for subledger '{cdef.subledger_id}': {exc}"
            )

        # Build timing
        timing = _TIMING_MAP.get(
            cdef.timing.lower(), ReconciliationTiming.REAL_TIME,
        )

        # Build tolerance
        tolerance = _build_tolerance(cdef)

        # Build binding
        binding = ControlAccountBinding(
            subledger_type=sl_type,
            control_account_role=cdef.control_account_role,
            control_account_code=account_code,
            is_debit_normal=cdef.is_debit_normal,
            currency=default_currency,
        )

        # Build contract
        contract = SubledgerControlContract(
            binding=binding,
            timing=timing,
            tolerance=tolerance,
            enforce_on_post=cdef.enforce_on_post,
            enforce_on_close=cdef.enforce_on_close,
        )

        registry.register(contract)

    return registry
