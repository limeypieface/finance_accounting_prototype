"""
Profile Bridge — Connects module profiles to kernel posting pipeline.

Each finance module defines AccountingPolicy instances (kernel format) and
companion ModuleLineMapping tuples. This bridge:

1. Stores line mapping data alongside the kernel profile (ModulePolicyRegistry)
2. Builds AccountingIntent from line mappings + event data at posting time
3. Supports multi-ledger intents derived from profile LedgerEffects

Modules call register_rich_profile() at startup.
ModulePostingService calls build_accounting_intent() at runtime.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID, uuid4

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.accounting_policy import (
    AccountingPolicy,
    GuardCondition,
    LedgerEffect,
    PolicyMeaning,
    PolicyTrigger,
)
from finance_kernel.domain.policy_selector import PolicySelector
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.policy_bridge")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleLineMapping:
    """
    Canonical line mapping for AccountingIntent construction.

    Attributes:
        role: Account role as string (e.g., "INVENTORY", "GRNI")
        side: "debit" or "credit"
        ledger: Target ledger (default: "GL")
        from_context: Payload field name to use for this line's amount
        foreach: Payload collection field to iterate over
    """

    role: str
    side: str
    ledger: str = "GL"
    from_context: str | None = None
    foreach: str | None = None


@dataclass(frozen=True)
class ModulePolicyEntry:
    """
    Complete module profile data stored in the registry.

    Includes both the kernel AccountingPolicy (for MeaningBuilder/PolicySelector)
    and the line mapping data (for AccountingIntent construction).
    """

    module_name: str
    profile_key: str
    kernel_profile: AccountingPolicy
    line_mappings: tuple[ModuleLineMapping, ...]
    event_type: str


# ---------------------------------------------------------------------------
# Module Profile Registry
# ---------------------------------------------------------------------------


class ModulePolicyRegistry:
    """
    Registry for module profile data including line mappings.

    Complements the kernel's PolicySelector by also storing
    line mapping data needed for AccountingIntent construction.
    """

    _entries: ClassVar[dict[str, ModulePolicyEntry]] = {}

    @classmethod
    def register(cls, entry: ModulePolicyEntry) -> None:
        """Register a module profile entry."""
        cls._entries[entry.kernel_profile.name] = entry
        logger.info(
            "module_profile_registered",
            extra={
                "module_name": entry.module_name,
                "profile": entry.kernel_profile.name,
                "event_type": entry.event_type,
                "mapping_count": len(entry.line_mappings),
            },
        )

    @classmethod
    def get(cls, profile_name: str) -> ModulePolicyEntry | None:
        """Get a module profile entry by name."""
        return cls._entries.get(profile_name)

    @classmethod
    def get_line_mappings(
        cls, profile_name: str
    ) -> tuple[ModuleLineMapping, ...] | None:
        """Get line mappings for a profile."""
        entry = cls._entries.get(profile_name)
        return entry.line_mappings if entry else None

    @classmethod
    def list_by_module(cls, module_name: str) -> list[ModulePolicyEntry]:
        """List all profiles registered by a module."""
        return [e for e in cls._entries.values() if e.module_name == module_name]

    @classmethod
    def list_all(cls) -> list[ModulePolicyEntry]:
        """List all registered module profiles."""
        return list(cls._entries.values())

    @classmethod
    def clear(cls) -> None:
        """Clear all entries. For testing only."""
        cls._entries.clear()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_rich_profile(
    module_name: str,
    profile: AccountingPolicy,
    line_mappings: tuple[ModuleLineMapping, ...],
) -> AccountingPolicy:
    """
    Register a pre-built kernel AccountingPolicy with line mappings.

    This is the primary registration path. Modules create their own
    AccountingPolicy instances with full control over guards, triggers,
    effective dates, scope, and precedence. This function registers
    the profile in both PolicySelector (for lookup) and
    ModulePolicyRegistry (for intent construction).

    Args:
        module_name: Module identifier (e.g., "inventory", "ap").
        profile: Pre-built kernel AccountingPolicy.
        line_mappings: Line mappings for AccountingIntent construction.

    Returns:
        The registered AccountingPolicy (same instance passed in).
    """
    entry = ModulePolicyEntry(
        module_name=module_name,
        profile_key=profile.name,
        kernel_profile=profile,
        line_mappings=line_mappings,
        event_type=profile.trigger.event_type,
    )

    # Register in ModulePolicyRegistry (line mappings)
    ModulePolicyRegistry.register(entry)

    # Register in kernel PolicySelector (lookup)
    try:
        PolicySelector.register(profile)
    except Exception:
        logger.debug(
            "profile_already_in_kernel_registry",
            extra={"profile": profile.name},
        )

    return profile


# ---------------------------------------------------------------------------
# Legacy registration (deprecated — use register_rich_profile instead)
# ---------------------------------------------------------------------------


def register_module_profile(
    module_name: str,
    profile_key: str,
    event_type: str,
    description: str,
    line_mappings: tuple[ModuleLineMapping, ...],
    economic_type: str,
    quantity_field: str | None = None,
    dimensions: tuple[str, ...] = (),
    guards: tuple[GuardCondition, ...] = (),
    effective_from: date = date(2024, 1, 1),
    ledger_id: str = "GL",
) -> AccountingPolicy:
    """
    Register a module profile from flat parameters.

    .. deprecated::
        Use ``register_rich_profile`` with a pre-built AccountingPolicy
        for full control over guards, scope, precedence, and multi-ledger effects.
    """
    debit_roles = [m for m in line_mappings if m.side == "debit"]
    credit_roles = [m for m in line_mappings if m.side == "credit"]

    ledger_effects: tuple[LedgerEffect, ...] = ()
    if debit_roles and credit_roles:
        ledger_effects = (
            LedgerEffect(
                ledger=ledger_id,
                debit_role=debit_roles[0].role,
                credit_role=credit_roles[0].role,
            ),
        )

    kernel_profile = AccountingPolicy(
        name=f"{module_name}.{profile_key}",
        version=1,
        trigger=PolicyTrigger(event_type=event_type),
        meaning=PolicyMeaning(
            economic_type=economic_type,
            quantity_field=quantity_field,
            dimensions=dimensions,
        ),
        ledger_effects=ledger_effects,
        effective_from=effective_from,
        guards=guards,
        description=description,
    )

    return register_rich_profile(module_name, kernel_profile, line_mappings)


# ---------------------------------------------------------------------------
# Conversion helpers (for migrating old-format module profiles)
# ---------------------------------------------------------------------------


def convert_line_mapping(local_mapping: Any, role_value: str) -> ModuleLineMapping:
    """Convert a module-local LineMapping to a canonical ModuleLineMapping."""
    return ModuleLineMapping(
        role=role_value,
        side=local_mapping.side,
        from_context=getattr(local_mapping, "from_context", None),
        foreach=getattr(local_mapping, "foreach", None),
    )


def convert_local_profile(
    local_profile: Any,
) -> tuple[ModuleLineMapping, ...]:
    """Convert a module-local AccountingPolicy's line_mappings to canonical format."""
    return tuple(
        convert_line_mapping(m, m.role.value) for m in local_profile.line_mappings
    )


# ---------------------------------------------------------------------------
# Intent building
# ---------------------------------------------------------------------------


def build_accounting_intent(
    profile_name: str,
    source_event_id: UUID,
    effective_date: date,
    amount: Decimal,
    currency: str,
    payload: dict[str, Any] | None = None,
    description: str | None = None,
    coa_version: int = 1,
    dimension_schema_version: int = 1,
) -> AccountingIntent:
    """
    Build an AccountingIntent from registered module profile line mappings.

    Creates one LedgerIntent per ledger. For ledgers with explicit line
    mappings, uses the mappings (supporting from_context, foreach).
    For ledger effects without explicit mappings, auto-generates
    simple debit/credit lines from the LedgerEffect.

    Args:
        profile_name: Registered profile name (e.g., "InventoryReceipt").
        source_event_id: The source event ID.
        effective_date: Accounting effective date.
        amount: Primary monetary amount for the entry.
        currency: Currency code (e.g., "USD").
        payload: Event payload for from_context/foreach lookups.
        description: Optional entry description.
        coa_version: COA version for snapshot.
        dimension_schema_version: Dimension schema version for snapshot.

    Returns:
        AccountingIntent ready for posting.

    Raises:
        ValueError: If profile not found or no lines could be built.
    """
    entry = ModulePolicyRegistry.get(profile_name)
    if entry is None:
        raise ValueError(f"No module profile registered: {profile_name}")

    profile = entry.kernel_profile
    safe_payload = payload or {}

    # Group line mappings by ledger
    mappings_by_ledger: dict[str, list[ModuleLineMapping]] = defaultdict(list)
    for m in entry.line_mappings:
        mappings_by_ledger[m.ledger].append(m)

    # Build LedgerIntents
    ledger_intents: list[LedgerIntent] = []

    for effect in profile.ledger_effects:
        if effect.ledger in mappings_by_ledger:
            # Use explicit line mappings for this ledger
            lines = _build_intent_lines(
                line_mappings=tuple(mappings_by_ledger[effect.ledger]),
                amount=amount,
                currency=currency,
                payload=safe_payload,
            )
        else:
            # Auto-generate from LedgerEffect (simple debit/credit)
            lines = [
                IntentLine.debit(
                    role=effect.debit_role, amount=amount, currency=currency
                ),
                IntentLine.credit(
                    role=effect.credit_role, amount=amount, currency=currency
                ),
            ]

        if lines:
            ledger_intents.append(
                LedgerIntent(ledger_id=effect.ledger, lines=tuple(lines))
            )

    if not ledger_intents:
        raise ValueError(
            f"No ledger intents could be built for profile: {profile_name}"
        )

    econ_event_id = uuid4()

    return AccountingIntent(
        econ_event_id=econ_event_id,
        source_event_id=source_event_id,
        profile_id=profile.name,
        profile_version=profile.version,
        effective_date=effective_date,
        ledger_intents=tuple(ledger_intents),
        snapshot=AccountingIntentSnapshot(
            coa_version=coa_version,
            dimension_schema_version=dimension_schema_version,
        ),
        description=description or profile.description,
    )


# ---------------------------------------------------------------------------
# Intent line building (internal)
# ---------------------------------------------------------------------------


def _build_intent_lines(
    line_mappings: tuple[ModuleLineMapping, ...],
    amount: Decimal,
    currency: str,
    payload: dict[str, Any],
) -> list[IntentLine]:
    """
    Build IntentLine objects from module line mappings.

    Handles three patterns:
    - Simple lines: use the provided primary amount
    - Context-based amounts: from_context reads amount from payload field
    - Collection iteration: foreach creates one line per item in collection
    """
    lines: list[IntentLine] = []

    for mapping in line_mappings:
        if mapping.foreach:
            collection = payload.get(mapping.foreach, [])
            for item in collection:
                item_amount = _extract_amount(item, amount)
                lines.append(
                    _create_intent_line(
                        mapping.role, mapping.side, item_amount, currency
                    )
                )
            if not collection:
                lines.append(
                    _create_intent_line(
                        mapping.role, mapping.side, amount, currency
                    )
                )
        elif mapping.from_context:
            ctx_value = payload.get(mapping.from_context)
            if ctx_value is not None:
                ctx_amount = Decimal(str(ctx_value))
                if ctx_amount > 0:
                    lines.append(
                        _create_intent_line(
                            mapping.role, mapping.side, ctx_amount, currency
                        )
                    )
                elif ctx_amount < 0:
                    # Negative context amount: flip side (e.g., favorable variance)
                    flipped_side = "credit" if mapping.side == "debit" else "debit"
                    lines.append(
                        _create_intent_line(
                            mapping.role, flipped_side, abs(ctx_amount), currency
                        )
                    )
        else:
            lines.append(
                _create_intent_line(mapping.role, mapping.side, amount, currency)
            )

    return lines


def _extract_amount(item: Any, default: Decimal) -> Decimal:
    """Extract amount from a collection item."""
    if isinstance(item, dict):
        for key in ("amount", "total", "line_amount", "value", "extended_cost"):
            if key in item:
                return Decimal(str(item[key]))
    if isinstance(item, (int, float, Decimal)):
        return Decimal(str(item))
    return default


def _create_intent_line(
    role: str, side: str, amount: Decimal, currency: str
) -> IntentLine:
    """Create an IntentLine for the given role and side."""
    if side == "debit":
        return IntentLine.debit(role=role, amount=amount, currency=currency)
    else:
        return IntentLine.credit(role=role, amount=amount, currency=currency)
