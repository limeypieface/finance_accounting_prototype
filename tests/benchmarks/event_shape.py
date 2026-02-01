"""
Event shape introspection — derives the "shape" of a valid test event
from a CompiledPolicy's metadata.

The EventShape describes what payload fields are required, what constraints
they must satisfy, and how to balance debit/credit amounts across ledgers.

Usage:
    shape = introspect_policy(compiled_policy)
    # shape.where_clauses  -> discriminator fields
    # shape.ledger_specs   -> per-ledger balance requirements
    # shape.constraints    -> guard-derived field constraints
    # shape.required_engines -> engines that need payload fragments
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finance_config.compiler import CompiledPolicy

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldConstraint:
    """A constraint on a payload field derived from a guard expression.

    The operator describes what values the field must have to AVOID rejection.
    E.g., guard `payload.amount <= 0` (reject) -> FieldConstraint("amount", "gt", 0)
    means the field must be > 0.
    """

    field_name: str
    operator: str  # "gt", "gte", "lt", "lte", "eq", "neq", "is_none", "is_not_none"
    threshold: Any = None


@dataclass(frozen=True)
class ContextField:
    """A from_context field — its amount comes from the payload, not the primary amount."""

    field_name: str
    side: str  # "debit" or "credit"
    role: str
    ledger: str = "GL"


@dataclass(frozen=True)
class ForeachField:
    """A foreach collection field — iterates a payload array to produce lines."""

    collection_name: str
    side: str
    role: str
    ledger: str = "GL"


@dataclass(frozen=True)
class WhereClause:
    """A discriminator field that must be set to a specific value for policy match."""

    field_path: str  # e.g., "payload.cost_type"
    value: Any


@dataclass(frozen=True)
class LedgerSpec:
    """Balance specification for one ledger in a policy.

    simple_debit_count / simple_credit_count: lines that use the primary amount.
    context_debits / context_credits: lines whose amounts come from payload fields.
    foreach_fields: lines that iterate a payload collection.
    """

    ledger_id: str
    simple_debit_count: int = 0
    simple_credit_count: int = 0
    context_debits: tuple[ContextField, ...] = ()
    context_credits: tuple[ContextField, ...] = ()
    foreach_fields: tuple[ForeachField, ...] = ()


@dataclass(frozen=True)
class EventShape:
    """Complete shape of a valid test event, derived from CompiledPolicy metadata."""

    policy_name: str
    module: str
    event_type: str

    where_clauses: tuple[WhereClause, ...] = ()
    ledger_specs: tuple[LedgerSpec, ...] = ()
    constraints: tuple[FieldConstraint, ...] = ()
    required_engines: tuple[str, ...] = ()
    engine_parameters_ref: str | None = None

    has_context_fields: bool = False
    has_foreach_fields: bool = False
    all_context_field_names: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def introspect_policy(policy: CompiledPolicy) -> EventShape:
    """Derive the EventShape from a CompiledPolicy.

    Pure function: reads only frozen dataclass fields, no I/O.
    """
    from tests.benchmarks.guard_parser import parse_guard_constraints

    # 1. Where clauses
    where_clauses = tuple(
        WhereClause(field_path=field, value=value)
        for field, value in policy.trigger.where
    )

    # 2. Group line_mappings by ledger
    ledger_groups: dict[str, dict] = {}
    all_context_names: list[str] = []
    has_context = False
    has_foreach = False

    for mapping in policy.line_mappings:
        ledger = mapping.ledger if hasattr(mapping, "ledger") else "GL"
        if ledger not in ledger_groups:
            ledger_groups[ledger] = {
                "simple_debits": 0,
                "simple_credits": 0,
                "context_debits": [],
                "context_credits": [],
                "foreach_fields": [],
            }
        g = ledger_groups[ledger]

        if mapping.foreach:
            has_foreach = True
            g["foreach_fields"].append(
                ForeachField(
                    collection_name=mapping.foreach,
                    side=mapping.side,
                    role=mapping.role,
                    ledger=ledger,
                )
            )
        elif mapping.from_context:
            has_context = True
            all_context_names.append(mapping.from_context)
            ctx = ContextField(
                field_name=mapping.from_context,
                side=mapping.side,
                role=mapping.role,
                ledger=ledger,
            )
            if mapping.side == "debit":
                g["context_debits"].append(ctx)
            else:
                g["context_credits"].append(ctx)
        else:
            if mapping.side == "debit":
                g["simple_debits"] += 1
            else:
                g["simple_credits"] += 1

    ledger_specs = tuple(
        LedgerSpec(
            ledger_id=lid,
            simple_debit_count=g["simple_debits"],
            simple_credit_count=g["simple_credits"],
            context_debits=tuple(g["context_debits"]),
            context_credits=tuple(g["context_credits"]),
            foreach_fields=tuple(g["foreach_fields"]),
        )
        for lid, g in ledger_groups.items()
    )

    # 3. Guard constraints
    constraints: list[FieldConstraint] = []
    for guard in policy.guards:
        if guard.guard_type in ("reject",):
            constraints.extend(parse_guard_constraints(guard.expression))

    # 4. Required engines
    req_engines = policy.required_engines if policy.required_engines else ()
    engine_ref = policy.engine_parameters_ref if hasattr(policy, "engine_parameters_ref") else None

    return EventShape(
        policy_name=policy.name,
        module=policy.module,
        event_type=policy.trigger.event_type,
        where_clauses=where_clauses,
        ledger_specs=ledger_specs,
        constraints=tuple(constraints),
        required_engines=req_engines,
        engine_parameters_ref=engine_ref,
        has_context_fields=has_context,
        has_foreach_fields=has_foreach,
        all_context_field_names=tuple(all_context_names),
    )
