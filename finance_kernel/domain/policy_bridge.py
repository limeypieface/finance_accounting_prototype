"""PolicyBridge -- Connects module profiles to kernel posting pipeline."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, ClassVar
from uuid import UUID, uuid4

from finance_kernel.domain.accounting_intent import (
    AccountingIntent,
    AccountingIntentSnapshot,
    IntentLine,
    LedgerIntent,
)
from finance_kernel.domain.accounting_policy import AccountingPolicy
from finance_kernel.domain.policy_selector import (
    PolicyAlreadyRegisteredError,
    PolicySelector,
)
from finance_kernel.logging_config import get_logger

logger = get_logger("domain.policy_bridge")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleLineMapping:
    """Canonical line mapping for AccountingIntent construction."""

    role: str
    side: str
    ledger: str = "GL"
    from_context: str | None = None
    foreach: str | None = None


@dataclass(frozen=True)
class ModulePolicyEntry:
    """Complete module profile data stored in the registry."""

    module_name: str
    profile_key: str
    kernel_profile: AccountingPolicy
    line_mappings: tuple[ModuleLineMapping, ...]
    event_type: str


# ---------------------------------------------------------------------------
# Module Profile Registry
# ---------------------------------------------------------------------------


class ModulePolicyAlreadyRegisteredError(Exception):
    """Profile already registered in ModulePolicyRegistry (same profile name)."""

    code: str = "MODULE_POLICY_ALREADY_REGISTERED"

    def __init__(self, profile_name: str, existing_module: str):
        self.profile_name = profile_name
        self.existing_module = existing_module
        super().__init__(
            f"Profile '{profile_name}' already registered by module '{existing_module}'; "
            "duplicate registration is not allowed (use policy_registry_reset fixture or clear())."
        )


class ModulePolicyRegistry:
    """Registry for module profile data including line mappings."""

    _entries: ClassVar[dict[str, ModulePolicyEntry]] = {}

    @classmethod
    def register(cls, entry: ModulePolicyEntry) -> None:
        """Register a module profile entry. Fails hard on duplicate (name) to avoid shadowing."""
        name = entry.kernel_profile.name
        if name in cls._entries:
            existing = cls._entries[name]
            raise ModulePolicyAlreadyRegisteredError(name, existing.module_name)
        cls._entries[name] = entry
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
    """Register a pre-built kernel AccountingPolicy with line mappings.

    Registration order: PolicySelector first, then ModulePolicyRegistry.
    Duplicate (name, version) raises PolicyAlreadyRegisteredError or
    ModulePolicyAlreadyRegisteredError — no shadowing or last-writer-wins.
    """
    entry = ModulePolicyEntry(
        module_name=module_name,
        profile_key=profile.name,
        kernel_profile=profile,
        line_mappings=line_mappings,
        event_type=profile.trigger.event_type,
    )

    # Register in kernel PolicySelector first (lookup). Fails hard on duplicate.
    PolicySelector.register(profile)

    # Register in ModulePolicyRegistry (line mappings). Fails hard on duplicate.
    try:
        ModulePolicyRegistry.register(entry)
    except ModulePolicyAlreadyRegisteredError:
        # Roll back PolicySelector so registries stay in sync
        PolicySelector.unregister(profile.name, profile.version)
        raise

    return profile


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
    """Build an AccountingIntent from registered module profile line mappings."""
    entry = ModulePolicyRegistry.get(profile_name)
    if entry is None:
        raise ValueError(f"No module profile registered: {profile_name}")

    profile = entry.kernel_profile
    safe_payload = payload or {}

    logger.info(
        "intent_construction_started",
        extra={
            "profile_name": profile_name,
            "source_event_id": str(source_event_id),
            "amount": str(amount),
            "currency": currency,
            "ledger_effect_count": len(profile.ledger_effects),
            "mapping_count": len(entry.line_mappings),
        },
    )

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
            logger.debug(
                "ledger_intent_built",
                extra={
                    "profile_name": profile_name,
                    "ledger_id": effect.ledger,
                    "line_count": len(lines),
                    "source": "mappings" if effect.ledger in mappings_by_ledger else "auto",
                },
            )

    if not ledger_intents:
        raise ValueError(
            f"No ledger intents could be built for profile: {profile_name}"
        )

    total_lines = sum(len(li.lines) for li in ledger_intents)
    logger.info(
        "intent_construction_completed",
        extra={
            "profile_name": profile_name,
            "source_event_id": str(source_event_id),
            "ledger_count": len(ledger_intents),
            "total_lines": total_lines,
        },
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
    """Build IntentLine objects from module line mappings."""
    lines: list[IntentLine] = []

    for mapping in line_mappings:
        if mapping.foreach:
            collection = payload.get(mapping.foreach, [])
            logger.debug(
                "intent_line_foreach",
                extra={
                    "role": mapping.role,
                    "field": mapping.foreach,
                    "item_count": len(collection),
                },
            )
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
                    logger.debug(
                        "intent_line_from_context",
                        extra={
                            "role": mapping.role,
                            "field": mapping.from_context,
                            "amount": str(ctx_amount),
                            "side": mapping.side,
                        },
                    )
                    lines.append(
                        _create_intent_line(
                            mapping.role, mapping.side, ctx_amount, currency
                        )
                    )
                elif ctx_amount < 0:
                    # Negative context amount: flip side (e.g., favorable variance)
                    flipped_side = "credit" if mapping.side == "debit" else "debit"
                    logger.debug(
                        "intent_line_side_flip",
                        extra={
                            "role": mapping.role,
                            "field": mapping.from_context,
                            "amount": str(abs(ctx_amount)),
                            "original_side": mapping.side,
                            "flipped_side": flipped_side,
                        },
                    )
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


# ---------------------------------------------------------------------------
# Intent from payload lines (import.historical_journal)
# ---------------------------------------------------------------------------

def build_accounting_intent_from_payload_lines(
    profile: AccountingPolicy,
    source_event_id: UUID,
    effective_date: date,
    payload: dict[str, Any],
    account_key_to_role: Callable[[str], str | None],
    currency: str = "USD",
    description: str | None = None,
    coa_version: int = 1,
    dimension_schema_version: int = 1,
) -> AccountingIntent:
    """Build AccountingIntent from payload.lines (account_key→role per line). Used for import.historical_journal."""
    lines_data = payload.get("lines")
    if not isinstance(lines_data, list) or len(lines_data) == 0:
        raise ValueError("payload.lines must be a non-empty list")

    intent_lines: list[IntentLine] = []
    for line in lines_data:
        if not isinstance(line, dict):
            continue
        account_key = line.get("account")
        if account_key is None or (isinstance(account_key, str) and not account_key.strip()):
            continue
        key = (account_key if isinstance(account_key, str) else str(account_key)).strip()
        role = account_key_to_role(key)
        if not role:
            raise ValueError(f"Unresolvable account for import line: {key!r}")

        debit_val = line.get("debit")
        credit_val = line.get("credit")
        try:
            d = Decimal(str(debit_val)) if debit_val is not None else Decimal("0")
            c = Decimal(str(credit_val)) if credit_val is not None else Decimal("0")
        except Exception:
            raise ValueError(f"Invalid amount for account {key!r} (debit={debit_val!r}, credit={credit_val!r})")
        if d > 0 and c > 0:
            raise ValueError(f"Line cannot have both debit and credit for account {key!r}")
        if d > 0:
            intent_lines.append(IntentLine.debit(role=role, amount=d, currency=currency))
        elif c > 0:
            intent_lines.append(IntentLine.credit(role=role, amount=c, currency=currency))
        # else skip zero line

    if not intent_lines:
        raise ValueError("No valid lines with amount in payload.lines")

    ledger_intent = LedgerIntent(ledger_id="GL", lines=tuple(intent_lines))
    snapshot = AccountingIntentSnapshot(
        coa_version=coa_version,
        dimension_schema_version=dimension_schema_version,
    )
    return AccountingIntent(
        econ_event_id=uuid4(),
        source_event_id=source_event_id,
        profile_id=profile.name,
        profile_version=profile.version,
        effective_date=effective_date,
        ledger_intents=(ledger_intent,),
        snapshot=snapshot,
        description=description or profile.description,
    )
