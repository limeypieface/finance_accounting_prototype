"""
Engine payload templates — minimal valid payload fragments for each engine.

Each engine invoker (finance_services/invokers.py) expects specific payload
fields. This module provides templates that satisfy those expectations.

The 7 engines are a finite, rarely-changing set. Static templates are simpler
and more debuggable than introspecting engine contracts at runtime.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Templates keyed by engine name
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, Any]] = {
    "variance": {
        # _invoke_variance reads: variance_type, expected_price, actual_price, quantity
        # For "price" variance type (default)
        "variance_type": "price",
        "expected_price": "10.00",
        "actual_price": "10.50",
        "quantity": 100,
        "standard_price": "10.00",
    },
    "allocation": {
        # _invoke_allocation reads: amount (from event), allocation_targets, allocation_method
        # Amount comes from the primary event amount, not the template.
        # prorata method requires eligible_amount on targets.
        "allocation_method": "prorata",
        "allocation_targets": [
            {
                "target_id": "T1",
                "target_type": "account",
                "eligible_amount": "5000.00",
                "weight": "1",
            },
        ],
    },
    "matching": {
        # _invoke_matching reads: match_operation, match_documents or match_target/candidates
        "match_operation": "create_match",
        "match_documents": [
            {
                "document_id": "DOC-001",
                "document_type": "PO",
                "amount": "5000.00",
                "currency": "USD",
            },
            {
                "document_id": "DOC-002",
                "document_type": "RECEIPT",
                "amount": "5000.00",
                "currency": "USD",
            },
        ],
        "match_type": "two_way",
        "as_of_date": "2026-06-15",
    },
    "tax": {
        # _invoke_tax reads: tax_codes, tax_rates, is_tax_inclusive
        # rate is a string here for JSON serialization; the invoker coerces to Decimal
        "tax_codes": ["SALES_TAX"],
        "tax_rates": {
            "SALES_TAX": {
                "tax_code": "SALES_TAX",
                "tax_name": "Sales Tax",
                "rate": "0.08",
            },
        },
        "is_tax_inclusive": False,
    },
    "allocation_cascade": {
        # _invoke_allocation_cascade reads: cascade_steps, pool_balances, cascade_rates
        "cascade_steps": [
            {
                "step_name": "fringe",
                "source_pool": "fringe_pool",
                "allocation_base": "direct_labor",
                "rate_key": "fringe_rate",
            },
        ],
        "pool_balances": {
            "fringe_pool": "10000.00",
            "direct_labor": "100000.00",
        },
        "cascade_rates": {
            "fringe_rate": "0.35",
        },
    },
    "billing": {
        # _invoke_billing reads: billing_input (BillingInput-shaped dict)
        "billing_input": {
            "contract_type": "CPFF",
            "cost_breakdown": {
                "direct_labor": "3000.00",
                "direct_material": "1000.00",
            },
            "indirect_rates": {
                "fringe": "0.35",
                "overhead": "0.45",
                "ga": "0.10",
            },
            "fee_rate": "0.08",
            "currency": "USD",
        },
    },
    "ice": {
        # _invoke_ice reads: ice_input (ICEInput-shaped dict)
        "ice_input": {
            "fiscal_year": 2026,
            "contractor_name": "Test Corp",
            "contract_costs": [],
            "labor_details": [],
            "indirect_pools": [],
        },
    },
}


def get_engine_payload(engine_name: str, amount: Decimal) -> dict[str, Any]:
    """Get a deep copy of the engine template, scaled to the given amount.

    Monetary values in the template are adjusted proportionally based on
    the ratio of the requested amount to the template's default amount.
    """
    template = _TEMPLATES.get(engine_name)
    if template is None:
        return {}

    result = copy.deepcopy(template)
    _scale_amounts(result, amount)
    return result


def _scale_amounts(d: dict[str, Any], amount: Decimal) -> None:
    """Scale monetary string values in a dict based on the target amount.

    Applies to string values that look like decimal numbers at the top level
    and in match_documents / billing_input.cost_breakdown.
    """
    amt_str = str(amount.quantize(Decimal("0.01")))

    # Scale allocation target amounts
    if "allocation_targets" in d:
        for target in d["allocation_targets"]:
            if "eligible_amount" in target:
                target["eligible_amount"] = amt_str

    # Scale match document amounts
    if "match_documents" in d:
        for doc in d["match_documents"]:
            if "amount" in doc:
                doc["amount"] = amt_str

    # Scale billing input cost breakdown
    if "billing_input" in d and "cost_breakdown" in d["billing_input"]:
        cb = d["billing_input"]["cost_breakdown"]
        total_parts = len(cb)
        if total_parts > 0:
            per_part = (amount / total_parts).quantize(Decimal("0.01"))
            for key in list(cb.keys()):
                cb[key] = str(per_part)

    # Scale pool balances
    if "pool_balances" in d:
        for key in list(d["pool_balances"].keys()):
            d["pool_balances"][key] = amt_str

    # Scale variance prices proportionally
    if "expected_price" in d and "quantity" in d:
        qty = Decimal(str(d["quantity"]))
        if qty > 0:
            unit_price = (amount / qty).quantize(Decimal("0.0001"))
            d["expected_price"] = str(unit_price)
            # Actual price slightly higher for variance
            d["actual_price"] = str((unit_price * Decimal("1.05")).quantize(Decimal("0.0001")))
            d["standard_price"] = str(unit_price)
