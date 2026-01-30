"""
Config → Kernel Bridges.

Functions that convert CompiledPolicyPack artifacts into kernel-compatible
inputs. These live in finance_config (the producer) because the kernel
must NEVER import finance_config.

Usage:
    from finance_config.bridges import build_role_resolver

    config = get_active_config(...)
    role_resolver = build_role_resolver(config)
    service = ModulePostingService(session=session, role_resolver=role_resolver)
"""

from __future__ import annotations

from uuid import UUID, uuid5

from finance_config.compiler import CompiledPolicyPack
from finance_kernel.services.journal_writer import RoleResolver

# Fixed namespace for deterministic account UUID generation.
# In production, account IDs would come from the database.
_COA_UUID_NAMESPACE = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def build_role_resolver(config: CompiledPolicyPack) -> RoleResolver:
    """Build a RoleResolver from CompiledPolicyPack role_bindings.

    Generates deterministic UUIDs from account codes using uuid5 so that
    the same code always yields the same account ID.
    """
    resolver = RoleResolver()
    for binding in config.role_bindings:
        account_id = uuid5(_COA_UUID_NAMESPACE, binding.account_code)
        resolver.register_binding(binding.role, account_id, binding.account_code)
    return resolver
