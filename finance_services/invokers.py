"""
finance_services.invokers -- Standard engine invoker registrations for the EngineDispatcher.

Responsibility:
    Each invoker bridges the EngineDispatcher's ``(payload, FrozenEngineParams)``
    contract to a specific pure engine's API.  Invokers read relevant fields
    from the event payload, merge with frozen configuration parameters, coerce
    raw dict/JSON values into proper domain types (Money, Decimal, frozen
    dataclasses), and call the pure engine function.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    This module is the single location that couples the dispatcher to
    concrete engine implementations.  Adding a new engine requires only
    a new ``_invoke_*`` function and a ``dispatcher.register()`` call
    inside ``register_standard_engines`` (R15 open/closed compliance).

Invariants enforced:
    - R14 (No central dispatch): engine selection is data-driven via
      ``CompiledPolicy.required_engines``, not via if/switch on event_type.
    - R15 (Open/closed compliance): adding a new engine is additive --
      one new invoker function + one register call.
    - R16 (ISO 4217): currency strings are propagated from payload/params;
      Money.of validates currency at construction.
    - Decimal safety: ``_money`` and ``_decimal`` helpers ensure no float
      values leak into engine calls.

Failure modes:
    - ValueError from ``_invoke_*`` functions when required payload keys
      are missing or have incompatible types.
    - Engine-level exceptions propagate to the dispatcher, which records
      them in the EngineTraceRecord.

Audit relevance:
    - Invoker fingerprint_fields are declared per-engine so that the
      dispatcher can compute a deterministic input_fingerprint for the
      trace record.

Usage:
    from finance_services.invokers import register_standard_engines
    register_standard_engines(dispatcher)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from finance_config.compiler import FrozenEngineParams
from finance_engines.allocation import (
    AllocationEngine,
    AllocationMethod,
    AllocationTarget,
)
from finance_engines.allocation_cascade import AllocationStep, execute_cascade
from finance_engines.billing import BillingInput, calculate_billing
from finance_engines.ice import ICEInput, compile_ice_submission
from finance_engines.matching import (
    MatchCandidate,
    MatchingEngine,
    MatchTolerance,
    MatchType,
)
from finance_engines.tax import TaxCalculator, TaxRate
from finance_engines.variance import VarianceCalculator
from finance_services.engine_dispatcher import EngineDispatcher, EngineInvoker
from finance_services.observability import log_match_accepted, log_match_suggested

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _money(value: Any) -> Any:
    """Coerce a numeric value to Money if available, else Decimal.

    Preconditions:
        value is None, a Money instance, a dict with 'amount' key, or a
        numeric/string convertible to Decimal.

    Postconditions:
        Returns Money (never float).  Currency defaults to 'USD' when
        not specified.

    Raises:
        decimal.InvalidOperation: If value cannot be converted to Decimal.
    """
    if value is None:
        return None
    from finance_kernel.domain.values import Money
    if isinstance(value, Money):
        return value
    if isinstance(value, dict) and "amount" in value:
        # INVARIANT [R16]: currency propagated; defaults to 'USD' when not specified.
        return Money(amount=Decimal(str(value["amount"])), currency=value.get("currency", "USD"))
    return Money(amount=Decimal(str(value)), currency="USD")


def _decimal(value: Any) -> Decimal:
    """Coerce to Decimal.

    Preconditions:
        value is None, a Decimal, or a numeric/string convertible to Decimal.

    Postconditions:
        Returns Decimal (never float).  None maps to Decimal('0').

    Raises:
        decimal.InvalidOperation: If value cannot be converted to Decimal.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value)) if value is not None else Decimal("0")


# ---------------------------------------------------------------------------
# Invoker implementations
# ---------------------------------------------------------------------------


def _invoke_variance(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke VarianceCalculator based on payload variance_type."""
    calc = VarianceCalculator()
    variance_type = payload.get("variance_type", "price")

    if variance_type == "price":
        return calc.price_variance(
            expected_price=_money(payload.get("expected_price")),
            actual_price=_money(payload.get("actual_price")),
            quantity=_decimal(payload.get("quantity", 1)),
        )
    elif variance_type == "quantity":
        return calc.quantity_variance(
            expected_quantity=_decimal(payload.get("expected_quantity")),
            actual_quantity=_decimal(payload.get("actual_quantity")),
            standard_price=_money(payload.get("standard_price")),
        )
    elif variance_type == "fx":
        return calc.fx_variance(
            original_amount=_money(payload.get("original_amount")),
            original_rate=_decimal(payload.get("original_rate")),
            current_rate=_decimal(payload.get("current_rate")),
            functional_currency=payload.get("functional_currency", "USD"),
        )
    elif variance_type == "standard_cost":
        return calc.standard_cost_variance(
            standard_cost=_money(payload.get("standard_cost")),
            actual_cost=_money(payload.get("actual_cost")),
            quantity=_decimal(payload.get("quantity", 1)),
        )
    else:
        raise ValueError(f"Unknown variance_type: {variance_type}")


def _invoke_allocation(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke AllocationEngine.allocate."""
    engine = AllocationEngine()
    method_str = params.parameters.get("method", payload.get("allocation_method", "prorata"))
    method = AllocationMethod(method_str) if isinstance(method_str, str) else method_str

    targets_raw = payload.get("allocation_targets", [])
    targets = []
    for t in targets_raw:
        if isinstance(t, AllocationTarget):
            targets.append(t)
        elif isinstance(t, dict):
            targets.append(AllocationTarget(
                target_id=t["target_id"],
                target_type=t.get("target_type", "account"),
                eligible_amount=_money(t.get("eligible_amount")),
                weight=_decimal(t.get("weight", 0)),
                priority=t.get("priority", 0),
                date=t.get("date"),
            ))

    return engine.allocate(
        amount=_money(payload.get("amount")),
        targets=targets,
        method=method,
        rounding_target_index=payload.get("rounding_target_index"),
    )


def _coerce_match_candidate(raw: dict) -> MatchCandidate:
    """Coerce a dict to MatchCandidate with proper Money/Decimal types."""
    coerced = dict(raw)
    if "amount" in coerced and coerced["amount"] is not None:
        coerced["amount"] = _money(coerced["amount"])
    if "quantity" in coerced and coerced["quantity"] is not None:
        coerced["quantity"] = _decimal(coerced["quantity"])
    return MatchCandidate(**coerced)


def _invoke_matching(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke MatchingEngine.find_matches or create_match."""
    from finance_engines.matching import ToleranceType

    engine = MatchingEngine()

    tolerance_pct = _decimal(params.parameters.get(
        "tolerance_percent", payload.get("tolerance_percent", "0.01"),
    ))
    tolerance_amt = _decimal(params.parameters.get(
        "tolerance_amount", payload.get("tolerance_amount", "100"),
    ))
    match_strategy = params.parameters.get(
        "match_strategy", payload.get("match_strategy", "three_way"),
    )

    tolerance = MatchTolerance(
        amount_tolerance=tolerance_amt,
        amount_tolerance_type=ToleranceType.PERCENT if tolerance_pct else ToleranceType.ABSOLUTE,
    )

    # Determine operation from payload
    operation = payload.get("match_operation", "find_matches")
    if operation == "create_match":
        documents = payload.get("match_documents", [])
        candidates = [
            c if isinstance(c, MatchCandidate) else _coerce_match_candidate(c)
            for c in documents
        ]
        match_type_str = payload.get("match_type", match_strategy)
        match_type = MatchType(match_type_str) if isinstance(match_type_str, str) else match_type_str
        match_date_str = payload.get("as_of_date") or payload.get("match_date")
        if not match_date_str:
            raise ValueError(
                "create_match requires an explicit as_of_date or match_date in the payload "
                "for determinism; do not rely on server date"
            )
        as_of_date = date.fromisoformat(match_date_str)
        result = engine.create_match(
            documents=candidates,
            match_type=match_type,
            as_of_date=as_of_date,
            tolerance=tolerance,
        )
        log_match_accepted(
            context="engine",
            match_type=match_type.value if hasattr(match_type, "value") else str(match_type),
        )
        return result
    else:
        target_raw = payload.get("match_target")
        target = target_raw if isinstance(target_raw, MatchCandidate) else _coerce_match_candidate(target_raw)
        candidates_raw = payload.get("match_candidates", [])
        candidates = [
            c if isinstance(c, MatchCandidate) else _coerce_match_candidate(c)
            for c in candidates_raw
        ]
        result = engine.find_matches(
            target=target,
            candidates=candidates,
            tolerance=tolerance,
        )
        log_match_suggested(
            context="engine",
            suggestion_count=len(result),
            target_id=str(target.document_id) if getattr(target, "document_id", None) is not None else None,
            candidate_count=len(candidates),
        )
        return result


def _invoke_tax(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke TaxCalculator.calculate."""
    calc = TaxCalculator()

    tax_codes = payload.get("tax_codes", [])
    rates_raw = payload.get("tax_rates", {})
    rates = {}
    for code, rate_data in rates_raw.items():
        if isinstance(rate_data, TaxRate):
            rates[code] = rate_data
        elif isinstance(rate_data, dict):
            # Coerce rate to Decimal since JSON-decoded payloads produce strings
            coerced = dict(rate_data)
            if "rate" in coerced and not isinstance(coerced["rate"], Decimal):
                coerced["rate"] = _decimal(coerced["rate"])
            rates[code] = TaxRate(**coerced)

    return calc.calculate(
        amount=_money(payload.get("amount")),
        tax_codes=tax_codes,
        rates=rates,
        is_tax_inclusive=payload.get("is_tax_inclusive", False),
        calculation_date=payload.get("calculation_date"),
    )


def _invoke_allocation_cascade(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke execute_cascade."""
    steps_raw = payload.get("cascade_steps", [])
    steps = []
    for s in steps_raw:
        if isinstance(s, AllocationStep):
            steps.append(s)
        elif isinstance(s, dict):
            steps.append(AllocationStep(**s))

    pool_balances = payload.get("pool_balances", {})
    rates_raw = payload.get("cascade_rates", {})
    rates = {k: _decimal(v) for k, v in rates_raw.items()}

    # Override rates from config params if available
    for rate_key in ("fringe_rate", "overhead_rate", "ga_rate"):
        if rate_key in params.parameters:
            rates[rate_key] = _decimal(params.parameters[rate_key])

    currency = payload.get("currency", "USD")
    return execute_cascade(steps, pool_balances, rates, currency)


def _coerce_billing_input(raw: dict) -> BillingInput:
    """Coerce a dict into BillingInput with proper nested types."""
    from finance_engines.billing import (
        BillingContractType,
        CostBreakdown,
        IndirectRates,
        LaborRateEntry,
        MilestoneEntry,
    )

    kwargs = dict(raw)

    # contract_type: str → BillingContractType
    if "contract_type" in kwargs and isinstance(kwargs["contract_type"], str):
        kwargs["contract_type"] = BillingContractType(kwargs["contract_type"])

    # cost_breakdown: dict → CostBreakdown (needs Money fields)
    if "cost_breakdown" in kwargs and isinstance(kwargs["cost_breakdown"], dict):
        cb = dict(kwargs["cost_breakdown"])
        for fld in ("direct_labor", "direct_material", "subcontract", "travel", "odc"):
            if fld in cb and cb[fld] is not None:
                cb[fld] = _money(cb[fld])
        kwargs["cost_breakdown"] = CostBreakdown(**cb)

    # indirect_rates: dict → IndirectRates (needs Decimal fields)
    if "indirect_rates" in kwargs and isinstance(kwargs["indirect_rates"], dict):
        ir = {k: _decimal(v) for k, v in kwargs["indirect_rates"].items()}
        kwargs["indirect_rates"] = IndirectRates(**ir)

    # Scalar Decimal fields
    for fld in ("fee_rate", "withholding_pct", "cumulative_billed",
                "fee_ceiling", "funding_limit", "ceiling_amount"):
        if fld in kwargs and kwargs[fld] is not None:
            kwargs[fld] = _decimal(kwargs[fld])

    # material_passthrough: value → Money
    if "material_passthrough" in kwargs and kwargs["material_passthrough"] is not None:
        kwargs["material_passthrough"] = _money(kwargs["material_passthrough"])

    # labor_entries: list[dict] → tuple[LaborRateEntry, ...]
    if "labor_entries" in kwargs:
        entries = []
        for entry in kwargs["labor_entries"]:
            if isinstance(entry, dict):
                entries.append(LaborRateEntry(
                    labor_category=entry["labor_category"],
                    hours=_decimal(entry["hours"]),
                    billing_rate=_decimal(entry["billing_rate"]),
                ))
            else:
                entries.append(entry)
        kwargs["labor_entries"] = tuple(entries)

    # milestones: list[dict] → tuple[MilestoneEntry, ...]
    if "milestones" in kwargs:
        ms = []
        for m in kwargs["milestones"]:
            if isinstance(m, dict):
                ms.append(MilestoneEntry(
                    milestone_id=m["milestone_id"],
                    description=m["description"],
                    amount=_decimal(m["amount"]),
                    completion_pct=_decimal(m["completion_pct"]),
                ))
            else:
                ms.append(m)
        kwargs["milestones"] = tuple(ms)

    return BillingInput(**kwargs)


def _invoke_billing(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke calculate_billing."""
    billing_input_raw = payload.get("billing_input")
    if isinstance(billing_input_raw, BillingInput):
        billing_input = billing_input_raw
    elif isinstance(billing_input_raw, dict):
        billing_input = _coerce_billing_input(billing_input_raw)
    else:
        raise ValueError("payload must contain 'billing_input' dict or BillingInput")
    return calculate_billing(billing_input)


def _invoke_ice(payload: dict, params: FrozenEngineParams) -> Any:
    """Invoke compile_ice_submission."""
    ice_input_raw = payload.get("ice_input")
    if isinstance(ice_input_raw, ICEInput):
        ice_input = ice_input_raw
    elif isinstance(ice_input_raw, dict):
        ice_input = ICEInput(**ice_input_raw)
    else:
        raise ValueError("payload must contain 'ice_input' dict or ICEInput")
    return compile_ice_submission(ice_input)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_standard_engines(dispatcher: EngineDispatcher) -> None:
    """Register all standard engine invokers with the dispatcher.

    Preconditions:
        dispatcher is a freshly-constructed EngineDispatcher (or one where
        re-registration of the same names is acceptable).

    Postconditions:
        Seven engines are registered: variance, allocation, matching, tax,
        allocation_cascade, billing, ice.

    Raises:
        ValueError: If an EngineInvoker's engine_name is inconsistent with
        the registration key (caught by EngineDispatcher.register).
    """
    # INVARIANT [R15]: Open/closed compliance -- adding a new engine requires
    # only a new _invoke_* function and a dispatcher.register() call here.
    dispatcher.register("variance", EngineInvoker(
        engine_name="variance",
        engine_version="1.0",
        invoke=_invoke_variance,
        fingerprint_fields=("expected_price", "actual_price", "quantity", "standard_cost", "actual_cost"),
    ))

    dispatcher.register("allocation", EngineInvoker(
        engine_name="allocation",
        engine_version="1.0",
        invoke=_invoke_allocation,
        fingerprint_fields=("amount", "allocation_method", "allocation_targets"),
    ))

    dispatcher.register("matching", EngineInvoker(
        engine_name="matching",
        engine_version="1.0",
        invoke=_invoke_matching,
        fingerprint_fields=("match_target", "match_candidates", "match_type"),
    ))

    dispatcher.register("tax", EngineInvoker(
        engine_name="tax",
        engine_version="1.0",
        invoke=_invoke_tax,
        fingerprint_fields=("amount", "tax_codes", "is_tax_inclusive"),
    ))

    dispatcher.register("allocation_cascade", EngineInvoker(
        engine_name="allocation_cascade",
        engine_version="1.0",
        invoke=_invoke_allocation_cascade,
        fingerprint_fields=("pool_balances", "cascade_rates", "cascade_steps"),
    ))

    dispatcher.register("billing", EngineInvoker(
        engine_name="billing",
        engine_version="1.0",
        invoke=_invoke_billing,
        fingerprint_fields=("billing_input",),
    ))

    dispatcher.register("ice", EngineInvoker(
        engine_name="ice",
        engine_version="1.0",
        invoke=_invoke_ice,
        fingerprint_fields=("ice_input",),
    ))
