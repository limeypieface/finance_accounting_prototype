"""
Module: finance_modules.revenue.helpers
Responsibility:
    Pure calculation functions for ASC 606 revenue recognition.
    Domain math that engines do not cover:
    - Variable consideration estimation (expected value, most likely amount)
    - Standalone selling price (SSP) determination hierarchy
    - Progress measurement (input method / output method)
    - Contract modification type assessment per ASC 606-10-25-12

Architecture:
    finance_modules layer -- pure functions with ZERO I/O, ZERO side
    effects, ZERO database access.  Called by RevenueRecognitionService
    for domain-specific calculations that do not warrant a dedicated
    engine.

    Dependency direction: helpers.py imports only stdlib (decimal).

Invariants:
    - All functions are pure: same inputs always produce same outputs.
    - All monetary arithmetic uses Decimal -- NEVER float.
    - Progress returns are clamped to [0, 1] range.

Failure modes:
    - Division by zero guarded: total <= 0 returns Decimal("0").
    - Empty scenario list returns Decimal("0") for variable consideration.

Audit relevance:
    - Variable consideration estimates feed directly into transaction
      price determination (ASC 606-10-32-8); calculation must be
      deterministic and reproducible for audit trail purposes.
    - SSP hierarchy (observable > adjusted market > cost plus > residual)
      follows ASC 606-10-32-33 priority order.
    - Modification type assessment is the basis for journal entry
      classification (separate contract vs. cumulative catch-up vs.
      prospective treatment).
"""

from decimal import Decimal


def estimate_variable_consideration(
    base_amount: Decimal,
    scenarios: list[dict],
    method: str = "expected_value",
) -> Decimal:
    """
    Estimate variable consideration per ASC 606-10-32-8.

    Preconditions:
        - ``base_amount`` is Decimal.
        - ``scenarios`` is a list of dicts with "probability" and "amount" keys.
        - ``method`` is "expected_value" or "most_likely_amount".

    Postconditions:
        - Returns Decimal representing the estimated variable consideration.
        - For "expected_value": sum of probability-weighted amounts.
        - For "most_likely_amount": amount of the highest-probability scenario.
        - Returns Decimal("0") if scenarios is empty.

    Raises:
        KeyError: If a scenario dict is missing "probability" or "amount".
    """
    # INVARIANT: Method must be one of the two ASC 606 approaches.
    assert method in ("expected_value", "most_likely_amount"), \
        f"method must be 'expected_value' or 'most_likely_amount', got '{method}'"
    if method == "most_likely_amount":
        if not scenarios:
            return Decimal("0")
        best = max(scenarios, key=lambda s: Decimal(str(s["probability"])))
        return Decimal(str(best["amount"]))

    # Expected value: sum of probability-weighted amounts
    total = Decimal("0")
    for scenario in scenarios:
        prob = Decimal(str(scenario["probability"]))
        amt = Decimal(str(scenario["amount"]))
        total += prob * amt

    return total


def calculate_ssp(
    observable_price: Decimal | None = None,
    adjusted_market_price: Decimal | None = None,
    expected_cost_plus_margin: tuple[Decimal, Decimal] | None = None,
    residual_total: Decimal | None = None,
    residual_other_ssp_sum: Decimal | None = None,
) -> Decimal:
    """
    Determine standalone selling price per ASC 606-10-32-33.

    Priority (ASC 606 hierarchy):
        1. Observable price (if available)
        2. Adjusted market assessment
        3. Expected cost plus margin
        4. Residual approach (total - sum of other SSPs)

    Preconditions:
        - All provided amounts are Decimal (never float).
        - ``expected_cost_plus_margin`` is (cost, margin_pct) if provided.

    Postconditions:
        - Returns Decimal >= 0 representing SSP.
        - Returns Decimal("0") if no inputs are provided.

    Raises:
        No exceptions under normal conditions.
    """
    if observable_price is not None:
        return observable_price

    if adjusted_market_price is not None:
        return adjusted_market_price

    if expected_cost_plus_margin is not None:
        cost, margin_pct = expected_cost_plus_margin
        return cost * (Decimal("1") + margin_pct)

    if residual_total is not None and residual_other_ssp_sum is not None:
        return residual_total - residual_other_ssp_sum

    return Decimal("0")


def measure_progress_input(
    costs_incurred: Decimal,
    total_estimated_costs: Decimal,
) -> Decimal:
    """
    Measure progress toward completion using input method (costs incurred).

    Preconditions:
        - Both arguments are Decimal >= 0.

    Postconditions:
        - Returns Decimal in [0, 1] representing percentage complete.
        - Returns Decimal("0") if total_estimated_costs <= 0.

    Raises:
        No exceptions under normal conditions.
    """
    if total_estimated_costs <= 0:
        return Decimal("0")
    progress = costs_incurred / total_estimated_costs
    return min(progress, Decimal("1"))


def measure_progress_output(
    units_delivered: Decimal,
    total_units: Decimal,
) -> Decimal:
    """
    Measure progress toward completion using output method (units delivered).

    Preconditions:
        - Both arguments are Decimal >= 0.

    Postconditions:
        - Returns Decimal in [0, 1] representing percentage complete.
        - Returns Decimal("0") if total_units <= 0.

    Raises:
        No exceptions under normal conditions.
    """
    if total_units <= 0:
        return Decimal("0")
    progress = units_delivered / total_units
    return min(progress, Decimal("1"))


def assess_modification_type(
    adds_distinct_goods: bool,
    price_reflects_ssp: bool,
    remaining_goods_distinct: bool,
) -> str:
    """
    Assess contract modification treatment per ASC 606-10-25-12.

    Preconditions:
        - All arguments are boolean.

    Postconditions:
        - Returns one of: "separate_contract", "prospective",
          "cumulative_catch_up".
        - Decision tree:
          * adds_distinct_goods AND price_reflects_ssp -> "separate_contract"
          * remaining_goods_distinct -> "prospective"
          * otherwise -> "cumulative_catch_up"

    Raises:
        No exceptions under normal conditions.
    """
    if adds_distinct_goods and price_reflects_ssp:
        return "separate_contract"
    if remaining_goods_distinct:
        return "prospective"
    return "cumulative_catch_up"
