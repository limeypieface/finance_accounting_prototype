"""
Module: finance_engines.contracts
Responsibility:
    Typed declarations (EngineContract) for each pure engine.  Each
    contract declares engine name, version, JSON Schema for configurable
    parameters, and fingerprint rules.  The CompiledPolicyPack compiler
    validates that every policy's ``required_engines`` exist and that
    ``engine_parameters_ref`` satisfies the engine's ``parameter_schema``.

Architecture position:
    Engines -- pure calculation layer, zero I/O.
    May only import finance_kernel/domain/values (no imports needed here).

Invariants enforced:
    - R14 (no central dispatch): engine registry is declarative; no
      if/switch on event_type.
    - R15 (open/closed compliance): adding a new engine requires only a
      new EngineContract and registration in ``ENGINE_CONTRACTS``.
    - R23 (strategy lifecycle): engine_version enables replay
      compatibility checks.

Failure modes:
    - KeyError when looking up an unregistered engine name in
      ``ENGINE_CONTRACTS``.

Audit relevance:
    Engine contracts are the authoritative schema for configuration
    validation.  Any mismatch between a policy's engine_parameters_ref
    and the contract's parameter_schema is a compile-time error,
    preventing misconfigured engine invocations from reaching production.

Usage:
    from finance_engines.contracts import ENGINE_CONTRACTS, EngineContract

    contract = ENGINE_CONTRACTS["variance"]
    assert contract.engine_version == "1.0"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EngineContract:
    """Declares the contract for a pure calculation engine.

    Contract:
        Frozen dataclass serving as the declarative specification for a
        single engine.  Used by the config compiler and engine dispatcher.
    Guarantees:
        - ``engine_name`` is unique within ``ENGINE_CONTRACTS``.
        - ``parameter_schema`` is a valid JSON Schema dict (validated at
          compile time by the policy compiler).
    Non-goals:
        - Does not contain engine implementation logic; it is metadata only.

    Attributes:
        engine_name: Unique engine identifier (matches config references).
        engine_version: Semantic version of the engine implementation.
        parameter_schema: JSON Schema for the engine's configurable parameters.
        input_fingerprint_rules: Fields used to compute a deterministic
            input fingerprint for replay tracing.
        description: Human-readable purpose of this engine.
    """

    engine_name: str
    engine_version: str
    parameter_schema: dict[str, Any]
    input_fingerprint_rules: tuple[str, ...] = ()
    description: str = ""


# ---------------------------------------------------------------------------
# Variance Calculator
# ---------------------------------------------------------------------------

VARIANCE_CONTRACT = EngineContract(
    engine_name="variance",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "tolerance_percent": {
                "type": "number",
                "minimum": 0,
                "description": "Percentage threshold for variance materiality",
            },
            "tolerance_amount": {
                "type": "number",
                "minimum": 0,
                "description": "Absolute amount threshold for variance materiality",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "expected_price", "actual_price", "quantity",
        "standard_cost", "actual_cost",
    ),
    description="Calculates price, quantity, FX, and standard cost variances.",
)


# ---------------------------------------------------------------------------
# Allocation Engine
# ---------------------------------------------------------------------------

ALLOCATION_CONTRACT = EngineContract(
    engine_name="allocation",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": [
                    "proportional", "prorata", "fifo", "lifo",
                    "specific", "weighted", "equal",
                ],
                "description": "Default allocation method",
            },
            "rounding_method": {
                "type": "string",
                "enum": ["largest_remainder", "first_target", "last_target"],
                "description": "How to handle penny rounding differences",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "amount", "method", "target_count",
    ),
    description="Allocates monetary amounts across targets using configurable methods.",
)


# ---------------------------------------------------------------------------
# Matching Engine
# ---------------------------------------------------------------------------

MATCHING_CONTRACT = EngineContract(
    engine_name="matching",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "tolerance_percent": {
                "type": "number",
                "minimum": 0,
                "description": "Percentage tolerance for amount matching",
            },
            "tolerance_amount": {
                "type": "number",
                "minimum": 0,
                "description": "Absolute tolerance for amount matching",
            },
            "match_strategy": {
                "type": "string",
                "enum": ["three_way", "two_way", "shipment", "bank"],
                "description": "Default matching strategy",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "target_amount", "candidate_count", "match_type",
    ),
    description="Matches documents (PO/receipt/invoice) with configurable tolerance.",
)


# ---------------------------------------------------------------------------
# Aging Calculator
# ---------------------------------------------------------------------------

AGING_CONTRACT = EngineContract(
    engine_name="aging",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "buckets": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Aging bucket boundaries in days (e.g., [30, 60, 90, 120])",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "document_date", "as_of_date", "due_date",
    ),
    description="Calculates document aging and classifies into configurable buckets.",
)


# ---------------------------------------------------------------------------
# Tax Calculator
# ---------------------------------------------------------------------------

TAX_CONTRACT = EngineContract(
    engine_name="tax",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "calculation_method": {
                "type": "string",
                "enum": ["destination", "origin", "inclusive", "exclusive"],
                "description": "Tax calculation nexus/method",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "amount", "tax_codes", "is_tax_inclusive",
    ),
    description="Calculates sales tax, VAT, withholding with compound rate support.",
)


# ---------------------------------------------------------------------------
# Allocation Cascade (DCAA)
# ---------------------------------------------------------------------------

ALLOCATION_CASCADE_CONTRACT = EngineContract(
    engine_name="allocation_cascade",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "cascade_type": {
                "type": "string",
                "enum": ["dcaa_standard", "custom"],
                "description": "Cascade template to use",
            },
            "fringe_rate": {
                "type": "number",
                "minimum": 0,
                "description": "Fringe benefit rate (decimal, e.g. 0.35)",
            },
            "overhead_rate": {
                "type": "number",
                "minimum": 0,
                "description": "Overhead rate (decimal, e.g. 0.45)",
            },
            "ga_rate": {
                "type": "number",
                "minimum": 0,
                "description": "G&A rate (decimal, e.g. 0.10)",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "pool_balances", "rates", "step_count",
    ),
    description="Executes multi-step DCAA indirect cost allocation cascades.",
)


# ---------------------------------------------------------------------------
# Billing Engine (pure)
# ---------------------------------------------------------------------------

BILLING_CONTRACT = EngineContract(
    engine_name="billing",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "default_withholding_pct": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Default withholding percentage (DCAA standard: 0.15)",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "contract_type", "cost_breakdown", "indirect_rates", "fee_rate",
    ),
    description="Calculates government contract billing (CPFF, T&M, FFP, etc.).",
)


# ---------------------------------------------------------------------------
# ICE Engine (DCAA Incurred Cost Electronically)
# ---------------------------------------------------------------------------

ICE_CONTRACT = EngineContract(
    engine_name="ice",
    engine_version="1.0",
    parameter_schema={
        "type": "object",
        "properties": {
            "fiscal_year_end_month": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12,
                "description": "Fiscal year end month for ICE schedule compilation",
            },
        },
        "additionalProperties": False,
    },
    input_fingerprint_rules=(
        "contractor_name", "fiscal_year", "schedule_types",
    ),
    description="Compiles DCAA Incurred Cost Electronically (ICE) submission schedules.",
)


# ---------------------------------------------------------------------------
# Registry of all engine contracts
# ---------------------------------------------------------------------------

ENGINE_CONTRACTS: dict[str, EngineContract] = {
    contract.engine_name: contract
    for contract in (
        VARIANCE_CONTRACT,
        ALLOCATION_CONTRACT,
        MATCHING_CONTRACT,
        AGING_CONTRACT,
        TAX_CONTRACT,
        ALLOCATION_CASCADE_CONTRACT,
        BILLING_CONTRACT,
        ICE_CONTRACT,
    )
}
