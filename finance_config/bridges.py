"""
finance_config.bridges -- Config-to-Kernel translation bridges.

Responsibility:
    Converts ``CompiledPolicyPack`` artifacts into kernel-compatible
    inputs (``RoleResolver``, ``SubledgerControlRegistry``).  These
    functions live in ``finance_config`` (the producer) because the
    kernel MUST NEVER import ``finance_config`` (architectural boundary).

Architecture position:
    Configuration -- YAML-driven policy pipeline, build-time validation.
    Bridges are invoked by ``finance_services.PostingOrchestrator`` after
    ``get_active_config()`` returns a pack.  They translate compiled
    configuration into kernel domain objects that the posting pipeline
    consumes.

Invariants enforced:
    - L1: Every account role resolves to exactly one COA account code.
      ``build_role_resolver`` registers one binding per role; the kernel
      ``RoleResolver`` raises if a role is unresolvable at post time.
    - Deterministic UUIDs: account IDs are generated via ``uuid5`` from
      a fixed namespace + account code, so the same code always yields
      the same UUID.
    - Subledger type validation: ``build_subledger_registry_from_defs``
      rejects unknown ``subledger_id`` values at config time, not at
      post time.

Failure modes:
    - ``FinanceKernelError`` -- a ``control_account_role`` in a
      subledger contract cannot be resolved through the role resolver.
    - ``ValueError`` (propagated) -- unknown ``SubledgerType`` enum
      value in a contract definition.

Audit relevance:
    Role resolution provenance (effective dates, config_id,
    config_version) is recorded in each ``RoleResolver`` binding so that
    the audit trail can trace every COA code back to its configuration
    source.

Usage::

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
    """Derive (account_type, normal_balance) from COA code prefix.

    Preconditions:
        - ``code`` is a non-empty string representing a COA account code.

    Postconditions:
        - Returns a two-tuple of (account_type, normal_balance) where
          account_type is one of {asset, liability, equity, revenue,
          expense} and normal_balance is one of {debit, credit}.
    """
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

    Generates deterministic UUIDs from account codes using ``uuid5`` so
    that the same code always yields the same account ID.  Includes full
    binding provenance (effective dates, config identity) for audit
    logging.

    Preconditions:
        - ``config.role_bindings`` is a non-empty tuple of
          ``RoleBinding`` instances with valid account codes.

    Postconditions:
        - Every role in ``config.role_bindings`` is registered in the
          returned ``RoleResolver`` with deterministic account UUIDs
          and full provenance metadata.

    Raises:
        No exceptions under normal operation; downstream
        ``RoleResolver.resolve()`` raises if a role is missing.
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
    """Convert config tolerance fields into a ReconciliationTolerance.

    Preconditions:
        - ``contract_def.tolerance_type`` is one of {"none", "absolute",
          "percentage"} (case-insensitive).

    Postconditions:
        - Returns a ``ReconciliationTolerance`` matching the contract's
          declared tolerance.  Falls back to ``zero()`` for unrecognised
          types.
    """
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

    Resolves each contract's ``control_account_role`` to a concrete COA
    account code at config compilation time (not dynamically at G9
    time).

    Preconditions:
        - ``config`` is a valid ``CompiledPolicyPack``.
        - ``role_resolver`` was built from the same config's role
          bindings.

    Postconditions:
        - Returns an empty ``SubledgerControlRegistry``.  (Contracts
          are currently resolved via
          ``build_subledger_registry_from_defs``.)

    Args:
        config: Compiled policy pack containing subledger_contracts.
        role_resolver: Role resolver built from the same config's role
            bindings.

    Returns:
        Empty ``SubledgerControlRegistry`` (placeholder for future
        compilation integration).
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

    Resolves each contract's ``control_account_role`` to a concrete COA
    account code at config time.  This ensures G9 enforcement uses
    pre-resolved accounts -- no dynamic role lookup at post time.

    Preconditions:
        - Every ``control_account_role`` in ``contract_defs`` has a
          corresponding binding in ``role_resolver``.
        - Every ``subledger_id`` is a valid ``SubledgerType`` enum
          member.

    Postconditions:
        - Returns a ``SubledgerControlRegistry`` containing one
          ``SubledgerControlContract`` per input definition with
          pre-resolved COA codes.

    Args:
        contract_defs: Subledger contract definitions from config.
        role_resolver: Role resolver for control_account_role to COA
            code resolution.
        default_currency: Default currency for control account bindings.

    Returns:
        Fully populated ``SubledgerControlRegistry``.

    Raises:
        FinanceKernelError: If a ``control_account_role`` cannot be
            resolved or a ``subledger_id`` is unknown.
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
