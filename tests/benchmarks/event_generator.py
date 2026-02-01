"""
Event generator — produces balanced, guard-satisfying, engine-aware test payloads.

Given an EventShape (derived from a CompiledPolicy), generates a TestEvent
with a payload that:
  1. Sets where-clause discriminator fields
  2. Satisfies guard constraints (amounts > 0, hours <= 24, etc.)
  3. Balances debits = credits per ledger per currency
  4. Includes engine-specific payload fragments when engines are required
  5. Generates foreach collections when needed

The balance solver mirrors _build_intent_lines() in policy_bridge.py:
  - Simple mappings use the primary `amount`
  - from_context mappings use named payload fields
  - foreach mappings iterate a collection
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from tests.benchmarks.event_shape import (
    ContextField,
    EventShape,
    FieldConstraint,
    ForeachField,
    LedgerSpec,
)
from tests.benchmarks.engine_templates import get_engine_payload


@dataclass
class TestEvent:
    """A complete test event ready for post_event()."""

    label: str
    policy_name: str
    module: str
    event_type: str
    amount: Decimal
    currency: str
    producer: str
    payload: dict[str, Any]


def generate_event(
    shape: EventShape,
    *,
    amount: Decimal = Decimal("5000.00"),
    currency: str = "USD",
    overrides: dict[str, Any] | None = None,
) -> TestEvent:
    """Generate a valid, balanced test event from an EventShape.

    Args:
        shape: The introspected event shape.
        amount: Primary posting amount (default 5000.00).
        currency: ISO 4217 currency code.
        overrides: Optional dict of payload field overrides applied last.

    Returns:
        A TestEvent with a balanced payload.
    """
    payload: dict[str, Any] = {}

    # 1. Set where-clause discriminator fields
    for wc in shape.where_clauses:
        # Compiled config serializes None as string "None"; restore Python None
        # so the policy selector's `is None` check works correctly.
        value = wc.value
        if value == "None":
            value = None
        _set_field(payload, wc.field_path, value)

    # 2. Apply guard constraints
    _apply_constraints(payload, shape.constraints, amount)

    # 3. Solve balance for from_context and foreach fields
    if shape.has_context_fields or shape.has_foreach_fields:
        amount = _solve_balance(payload, shape.ledger_specs, amount)
    # else: simple policies auto-balance (same amount on both sides)

    # 4. Merge engine templates
    for engine_name in shape.required_engines:
        engine_payload = get_engine_payload(engine_name, amount)
        for k, v in engine_payload.items():
            if k not in payload:
                payload[k] = v

    # 4b. Engine invokers read amount from payload (e.g., _invoke_allocation
    # reads payload.get("amount")).  Ensure it's present.
    if shape.required_engines and "amount" not in payload:
        payload["amount"] = str(amount)

    # 5. Apply overrides
    if overrides:
        payload.update(overrides)

    return TestEvent(
        label=shape.policy_name,
        policy_name=shape.policy_name,
        module=shape.module,
        event_type=shape.event_type,
        amount=amount,
        currency=currency,
        producer=shape.module,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Constraint application
# ---------------------------------------------------------------------------


def _apply_constraints(
    payload: dict[str, Any],
    constraints: tuple[FieldConstraint, ...],
    amount: Decimal,
) -> None:
    """Set payload fields to values that satisfy guard constraints."""
    for c in constraints:
        if c.field_name in payload:
            continue  # Already set by where-clause or earlier constraint

        if c.operator == "gt":
            # Must be > threshold
            threshold = Decimal(str(c.threshold)) if c.threshold is not None else Decimal("0")
            if "quantity" in c.field_name or "hours" in c.field_name:
                payload[c.field_name] = int(threshold) + 1
            else:
                payload[c.field_name] = str((threshold + Decimal("1")).quantize(Decimal("0.01")))

        elif c.operator == "gte":
            threshold = Decimal(str(c.threshold)) if c.threshold is not None else Decimal("0")
            if "quantity" in c.field_name or "hours" in c.field_name:
                payload[c.field_name] = max(int(threshold), 1)
            else:
                payload[c.field_name] = str(max(threshold, Decimal("1")).quantize(Decimal("0.01")))

        elif c.operator == "lte":
            # Must be <= threshold
            threshold = Decimal(str(c.threshold)) if c.threshold is not None else Decimal("100")
            if "quantity" in c.field_name or "hours" in c.field_name:
                # Set to a reasonable value within limit
                payload[c.field_name] = min(int(threshold), 8)
            else:
                payload[c.field_name] = str(min(threshold, amount).quantize(Decimal("0.01")))

        elif c.operator == "lt":
            threshold = Decimal(str(c.threshold)) if c.threshold is not None else Decimal("100")
            if "quantity" in c.field_name or "hours" in c.field_name:
                payload[c.field_name] = max(int(threshold) - 1, 1)
            else:
                payload[c.field_name] = str((threshold - Decimal("0.01")).quantize(Decimal("0.01")))

        elif c.operator == "is_none":
            payload[c.field_name] = None

        elif c.operator == "is_not_none":
            payload[c.field_name] = "TEST-VALUE"

        elif c.operator == "eq":
            if c.threshold is False:
                payload[c.field_name] = False
            elif c.threshold is not None:
                payload[c.field_name] = c.threshold

        elif c.operator == "neq":
            # Must not equal threshold -- set to something else
            if c.threshold == 0:
                payload[c.field_name] = str(amount)
            else:
                payload[c.field_name] = "OTHER"


# ---------------------------------------------------------------------------
# Balance solver
# ---------------------------------------------------------------------------


def _solve_balance(
    payload: dict[str, Any],
    ledger_specs: tuple[LedgerSpec, ...],
    amount: Decimal,
) -> Decimal:
    """Compute from_context and foreach field values that balance each ledger.

    Returns the (possibly adjusted) primary amount.

    Strategy per ledger:
      debit_total = (simple_debit_count * amount) + sum(context_debit_values)
      credit_total = (simple_credit_count * amount) + sum(context_credit_values)
      We need debit_total == credit_total.
    """
    for spec in ledger_specs:
        _solve_ledger(payload, spec, amount)

    return amount


def _solve_ledger(
    payload: dict[str, Any],
    spec: LedgerSpec,
    amount: Decimal,
) -> None:
    """Solve the balance equation for one ledger.

    Key insight: foreach and from_context fields on the same side must share
    the total.  E.g., AP invoice: foreach(invoice_lines) + from_context(tax_amount)
    on debit side must equal the simple credit (AP liability).

    If both exist on the same side, the foreach amount is reduced to leave room
    for context fields (typically 10% per field, capped at 50%).
    """
    sd = spec.simple_debit_count
    sc = spec.simple_credit_count
    cd = spec.context_debits
    cc = spec.context_credits
    fe = spec.foreach_fields

    # Determine if foreach and context share a side
    fe_debits = [ff for ff in fe if ff.side == "debit"]
    fe_credits = [ff for ff in fe if ff.side == "credit"]
    ctx_share_debit = len(fe_debits) > 0 and len(cd) > 0
    ctx_share_credit = len(fe_credits) > 0 and len(cc) > 0

    # Compute foreach item amounts, reserving space for same-side context fields
    foreach_debit_amt = amount
    foreach_credit_amt = amount
    if ctx_share_debit:
        # Reserve 10% per context field (capped at 50%) for tax/adjustment amounts
        ctx_share_pct = min(Decimal("0.10") * len(cd), Decimal("0.50"))
        foreach_debit_amt = (amount * (1 - ctx_share_pct)).quantize(Decimal("0.01"))
    if ctx_share_credit:
        ctx_share_pct = min(Decimal("0.10") * len(cc), Decimal("0.50"))
        foreach_credit_amt = (amount * (1 - ctx_share_pct)).quantize(Decimal("0.01"))

    # Handle foreach fields by creating collections
    for ff in fe:
        if ff.collection_name not in payload:
            fe_amt = foreach_debit_amt if ff.side == "debit" else foreach_credit_amt
            payload[ff.collection_name] = [{"amount": str(fe_amt), "item": "GEN-001"}]

    # Count foreach contributions
    fe_debit_total = Decimal("0")
    fe_credit_total = Decimal("0")
    for ff in fe:
        collection = payload.get(ff.collection_name, [])
        for item in collection:
            item_amt = Decimal(str(item.get("amount", amount))) if isinstance(item, dict) else amount
            if ff.side == "debit":
                fe_debit_total += item_amt
            else:
                fe_credit_total += item_amt

    # If no context fields, nothing to solve
    if not cd and not cc:
        return

    # Compute what simple + foreach lines contribute
    simple_debit_total = sd * amount + fe_debit_total
    simple_credit_total = sc * amount + fe_credit_total

    # Case: ALL lines are from_context (no simple lines, no foreach)
    if sd == 0 and sc == 0 and fe_debit_total == 0 and fe_credit_total == 0:
        _solve_all_context(payload, cd, cc, amount)
        return

    # Case: Context fields on one side only
    if cd and not cc:
        # Debits have context fields, credits are all simple
        # Need: simple_debit_total + sum(cd_values) = simple_credit_total
        # sum(cd_values) = simple_credit_total - simple_debit_total
        needed = simple_credit_total - simple_debit_total
        if needed <= 0:
            # Simple debits already >= credits; context debits would worsen
            # imbalance. Set to zero so no lines are produced.
            for f in cd:
                if f.field_name not in payload:
                    payload[f.field_name] = "0.00"
            return
        _distribute_amounts(payload, cd, needed)
        return

    if cc and not cd:
        # Credits have context fields, debits are all simple
        # Need: simple_debit_total = simple_credit_total + sum(cc_values)
        # sum(cc_values) = simple_debit_total - simple_credit_total
        needed = simple_debit_total - simple_credit_total
        if needed <= 0:
            for f in cc:
                if f.field_name not in payload:
                    payload[f.field_name] = "0.00"
            return
        _distribute_amounts(payload, cc, needed)
        return

    # Case: Context fields on BOTH sides
    # Strategy: set debit context fields proportionally, then adjust credits to balance
    debit_ctx_total = amount * len(cd)
    _distribute_amounts(payload, cd, debit_ctx_total)

    # Total debits now = simple_debit_total + debit_ctx_total
    total_debits = simple_debit_total + debit_ctx_total
    # Credits need: simple_credit_total + credit_ctx_total = total_debits
    credit_ctx_needed = total_debits - simple_credit_total
    if credit_ctx_needed <= 0:
        for f in cc:
            if f.field_name not in payload:
                payload[f.field_name] = "0.00"
        return
    _distribute_amounts(payload, cc, credit_ctx_needed)


def _solve_all_context(
    payload: dict[str, Any],
    debits: tuple[ContextField, ...],
    credits: tuple[ContextField, ...],
    amount: Decimal,
) -> None:
    """Solve when ALL lines are from_context (no simple lines).

    Strategy: distribute `amount` across debit fields, then set credit fields
    so they sum to the same total.
    """
    if not debits and not credits:
        return

    if debits and credits:
        # Give each debit field an equal share
        total = amount
        _distribute_amounts(payload, debits, total)
        # Credits must match
        _distribute_amounts(payload, credits, total)
    elif debits:
        _distribute_amounts(payload, debits, amount)
    elif credits:
        _distribute_amounts(payload, credits, amount)


def _distribute_amounts(
    payload: dict[str, Any],
    fields: tuple[ContextField, ...],
    total: Decimal,
) -> None:
    """Distribute a total amount evenly across context fields.

    Any remainder from rounding goes to the last field.
    Only sets fields that aren't already in the payload.
    """
    if not fields:
        return

    # Check which fields are already set
    unset = [f for f in fields if f.field_name not in payload]
    already_set_total = Decimal("0")
    for f in fields:
        if f.field_name in payload:
            try:
                already_set_total += Decimal(str(payload[f.field_name]))
            except Exception:
                pass

    remaining = total - already_set_total
    if remaining <= 0 or not unset:
        # All already set or nothing to distribute
        if not unset:
            return
        remaining = total  # Override: distribute total ignoring existing

    per_field = (remaining / len(unset)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    distributed = Decimal("0")

    for i, f in enumerate(unset):
        if i == len(unset) - 1:
            # Last field gets remainder to avoid rounding drift
            val = remaining - distributed
        else:
            val = per_field
            distributed += per_field

        if val <= 0:
            val = Decimal("0.01")

        payload[f.field_name] = str(val.quantize(Decimal("0.01")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_field(payload: dict[str, Any], field_path: str, value: Any) -> None:
    """Set a payload field from a dot-path like 'payload.cost_type'.

    Also handles comparison expressions used as where-clause discriminators:
      field_path="payload.quantity_change > 0", value=True
      -> sets payload["quantity_change"] = 100 (satisfies > 0)
    """
    path = field_path
    if path.startswith("payload."):
        path = path[len("payload."):]

    # Handle comparison expression where-clauses
    for op_str, op_fn in [(" > ", "gt"), (" >= ", "gte"), (" < ", "lt"), (" <= ", "lte")]:
        if op_str in path:
            field_name = path.split(op_str)[0].strip()
            threshold_str = path.split(op_str)[1].strip()
            try:
                threshold = Decimal(threshold_str)
            except Exception:
                threshold = Decimal("0")

            if value is True or value == "true":
                # Need expression to be true
                if op_fn == "gt":
                    payload[field_name] = int(threshold) + 100
                elif op_fn == "gte":
                    payload[field_name] = int(threshold) + 100
                elif op_fn == "lt":
                    payload[field_name] = int(threshold) - 100
                elif op_fn == "lte":
                    payload[field_name] = int(threshold) - 100
            else:
                # Need expression to be false (inverse)
                if op_fn in ("gt", "gte"):
                    payload[field_name] = int(threshold) - 100
                else:
                    payload[field_name] = int(threshold) + 100
            return

    parts = path.split(".")
    target = payload
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value
