"""
finance_services.rbac_authority -- Runtime RBAC enforcement at workflow boundary.

Responsibility:
    Check that an actor (with assigned roles and optional authority role) is
    allowed to perform an action (required permission). Enforces config-driven
    RBAC: authority role requirement, permission grant, and SoD (role and
    permission conflicts).

Architecture position:
    Services layer. Consumes CompiledRbacConfig from finance_config (pack).
    Called by WorkflowExecutor before allowing a transition when pack has
    compiled_rbac set.

Invariants:
    - Kernel remains actor-agnostic; this module does not resolve actor
      identity (caller supplies assigned_roles and authority_role).
    - Permissions are economic verbs only (taxonomy in config schema).
"""

from __future__ import annotations

# (workflow_name, action) -> permission string (must be in PERMISSION_TAXONOMY)
WORKFLOW_ACTION_TO_PERMISSION: dict[tuple[str, str], str] = {
    # AP
    ("ap_invoice", "submit"): "ap.invoice.enter",
    ("ap_invoice", "approve"): "ap.invoice.approve",
    ("ap_invoice", "post"): "ap.invoice.enter",
    ("ap_payment", "submit"): "ap.payment.enter",
    ("ap_payment", "approve"): "ap.payment.approve",
    ("ap_payment", "release"): "ap.payment.release",
    ("ap_inventory_invoice", "post"): "ap.invoice.enter",
    # AR
    ("ar_invoice", "submit"): "ar.invoice.enter",
    ("ar_invoice", "post"): "ar.invoice.enter",
    ("ar_receipt", "post"): "ar.payment.apply",
    ("ar_receipt_application", "post"): "ar.payment.apply",
    ("ar_credit_memo", "post"): "ar.credit_memo.issue",
    ("ar_write_off", "post"): "ar.invoice.enter",
    ("ar_deferred_revenue", "post"): "ar.invoice.enter",
    ("ar_refund", "post"): "ar.payment.apply",
    ("ar_finance_charge", "post"): "ar.invoice.enter",
}


def get_permission_for_transition(workflow_name: str, action: str) -> str | None:
    """Return the permission required for this workflow transition, or None if not in scope."""
    return WORKFLOW_ACTION_TO_PERMISSION.get((workflow_name, action))


def check_rbac(
    compiled_rbac: "CompiledRbacConfig",
    assigned_roles: tuple[str, ...],
    authority_role: str | None,
    required_permission: str,
) -> tuple[bool, str]:
    """Check whether the actor is allowed to perform the action (required permission).

    Args:
        compiled_rbac: Validated RBAC config from CompiledPolicyPack.
        assigned_roles: All roles the actor possesses (from IdP or role provider).
        authority_role: The role under which the actor is acting (when authority_role_required).
        required_permission: Permission string (e.g. ap.invoice.enter) from taxonomy.

    Returns:
        (allowed, reason). allowed is True iff the actor may perform the action;
        reason is empty when allowed, or a short message when denied.
    """
    # Build role -> permissions map
    role_permissions_map: dict[str, frozenset[str]] = {
        name: perms for name, perms in compiled_rbac.role_permissions
    }

    # Authority role requirement
    if compiled_rbac.authority_role_required:
        if not authority_role or not authority_role.strip():
            return (False, "RBAC: authority role required but not provided")
        if authority_role not in assigned_roles:
            return (False, f"RBAC: actor does not have authority role '{authority_role}'")
        acting_roles = (authority_role,)
    else:
        acting_roles = assigned_roles

    # No roles -> nothing to check; design: fail-open when no role provider configured
    if not acting_roles:
        return (True, "")

    # Gather permissions from acting role(s)
    permissions: set[str] = set()
    for r in acting_roles:
        permissions |= role_permissions_map.get(r, frozenset())

    if required_permission not in permissions:
        return (False, f"RBAC: permission '{required_permission}' not granted to actor")

    # SoD: role conflicts (same actor cannot hold conflicting roles for this action)
    for pair in compiled_rbac.role_conflicts:
        roles_in_pair = set(pair)
        if roles_in_pair.issubset(set(assigned_roles)):
            return (False, f"RBAC: SoD role conflict: actor has conflicting roles {roles_in_pair}")

    # SoD: hard permission conflicts (actor cannot have both permissions)
    for pair in compiled_rbac.permission_conflicts_hard:
        perms_in_pair = set(pair)
        if required_permission in perms_in_pair:
            other = perms_in_pair - {required_permission}
            if other and permissions & other:
                return (
                    False,
                    f"RBAC: SoD permission conflict: '{required_permission}' conflicts with {other}",
                )

    return (True, "")


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finance_config.compiler import CompiledRbacConfig
