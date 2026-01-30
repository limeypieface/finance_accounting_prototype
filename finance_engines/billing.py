"""
Government Contract Billing Engine.

Pure functions with deterministic behavior. No I/O.

This engine calculates provisional and final billing amounts for
government contracts. It composes with AllocationCascade for
indirect cost calculations and produces billing line items
suitable for invoice generation.

Supported contract types:
- Cost Plus Fixed Fee (CPFF)
- Cost Plus Incentive Fee (CPIF)
- Cost Plus Award Fee (CPAF)
- Time & Materials (T&M)
- Labor Hour (LH)
- Firm Fixed Price (FFP) - milestone billing
- Fixed Price Incentive (FPI)

Usage:
    from finance_engines.billing import (
        calculate_billing,
        BillingInput,
        CostBreakdown,
        BillingLineItem,
    )

    costs = CostBreakdown(
        direct_labor=Money.of("100000", "USD"),
        direct_material=Money.of("50000", "USD"),
    )
    rates = IndirectRates(
        fringe=Decimal("0.35"),
        overhead=Decimal("0.45"),
        ga=Decimal("0.10"),
    )
    billing_input = BillingInput(
        contract_type="CPFF",
        cost_breakdown=costs,
        indirect_rates=rates,
        fee_rate=Decimal("0.08"),
        currency="USD",
    )
    result = calculate_billing(billing_input)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.billing")


# ============================================================================
# Constants
# ============================================================================

_TWO_PLACES = Decimal("0.01")


class BillingContractType(str, Enum):
    """Contract types for billing calculation."""

    CPFF = "CPFF"  # Cost Plus Fixed Fee
    CPIF = "CPIF"  # Cost Plus Incentive Fee
    CPAF = "CPAF"  # Cost Plus Award Fee
    TM = "T&M"  # Time & Materials
    LH = "LH"  # Labor Hour
    FFP = "FFP"  # Firm Fixed Price
    FPI = "FPI"  # Fixed Price Incentive


class BillingLineType(str, Enum):
    """Types of billing line items."""

    DIRECT_LABOR = "DIRECT_LABOR"
    DIRECT_MATERIAL = "DIRECT_MATERIAL"
    SUBCONTRACT = "SUBCONTRACT"
    TRAVEL = "TRAVEL"
    ODC = "ODC"
    FRINGE = "FRINGE"
    OVERHEAD = "OVERHEAD"
    GA = "G&A"
    FEE = "FEE"
    MILESTONE = "MILESTONE"
    RATE_ADJUSTMENT = "RATE_ADJUSTMENT"
    WITHHOLDING = "WITHHOLDING"


# ============================================================================
# Value Objects
# ============================================================================


@dataclass(frozen=True)
class CostBreakdown:
    """
    Direct cost breakdown for a billing period.

    All amounts must be non-negative.
    """

    direct_labor: Money
    direct_material: Money = field(default=None)
    subcontract: Money = field(default=None)
    travel: Money = field(default=None)
    odc: Money = field(default=None)

    def __post_init__(self) -> None:
        if self.direct_labor.amount < 0:
            raise ValueError("direct_labor must be non-negative")
        for attr in ("direct_material", "subcontract", "travel", "odc"):
            val = getattr(self, attr)
            if val is not None and val.amount < 0:
                raise ValueError(f"{attr} must be non-negative")

    @property
    def total_direct(self) -> Money:
        """Total of all direct costs."""
        currency = self.direct_labor.currency
        total = self.direct_labor.amount
        for attr in ("direct_material", "subcontract", "travel", "odc"):
            val = getattr(self, attr)
            if val is not None:
                total += val.amount
        return Money.of(total, currency)


@dataclass(frozen=True)
class IndirectRates:
    """
    Indirect cost rates for billing calculation.

    All rates are expressed as decimals (e.g., 0.35 for 35%).
    """

    fringe: Decimal = Decimal("0")
    overhead: Decimal = Decimal("0")
    ga: Decimal = Decimal("0")
    material_handling: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for attr in ("fringe", "overhead", "ga", "material_handling"):
            val = getattr(self, attr)
            if val < 0:
                raise ValueError(f"{attr} rate must be non-negative")


@dataclass(frozen=True)
class LaborRateEntry:
    """
    A single labor category billing rate for T&M/LH contracts.

    Attributes:
        labor_category: Labor category name
        hours: Hours worked in the billing period
        billing_rate: Billing rate per hour (fully-burdened)
    """

    labor_category: str
    hours: Decimal
    billing_rate: Decimal

    def __post_init__(self) -> None:
        if self.hours < 0:
            raise ValueError("hours must be non-negative")
        if self.billing_rate < 0:
            raise ValueError("billing_rate must be non-negative")

    @property
    def amount(self) -> Decimal:
        """Total amount for this labor entry."""
        return (self.hours * self.billing_rate).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class MilestoneEntry:
    """
    A milestone billing entry for FFP contracts.

    Attributes:
        milestone_id: Milestone identifier
        description: Milestone description
        amount: Milestone payment amount
        completion_pct: Completion percentage (0-100)
    """

    milestone_id: str
    description: str
    amount: Decimal
    completion_pct: Decimal

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("amount must be non-negative")
        if not (Decimal("0") <= self.completion_pct <= Decimal("100")):
            raise ValueError("completion_pct must be between 0 and 100")


@dataclass(frozen=True)
class BillingInput:
    """
    Complete input for billing calculation.

    Attributes:
        contract_type: Type of contract for billing rules
        currency: Currency code for all monetary amounts
        cost_breakdown: Direct cost breakdown (for cost-type contracts)
        indirect_rates: Indirect rates to apply (for cost-type contracts)
        fee_rate: Fee percentage (for cost-plus contracts)
        fee_ceiling: Maximum fee amount (optional)
        labor_entries: Labor rate entries (for T&M/LH contracts)
        material_passthrough: Material costs at cost for T&M contracts
        milestones: Milestone entries (for FFP contracts)
        withholding_pct: Withholding percentage (DCAA standard 15%)
        cumulative_billed: Total already billed to date
        funding_limit: Maximum billable (funded amount)
        ceiling_amount: Contract ceiling (for cost-type contracts)
    """

    contract_type: BillingContractType
    currency: str
    cost_breakdown: CostBreakdown | None = None
    indirect_rates: IndirectRates | None = None
    fee_rate: Decimal = Decimal("0")
    fee_ceiling: Decimal | None = None
    labor_entries: tuple[LaborRateEntry, ...] = ()
    material_passthrough: Money | None = None
    milestones: tuple[MilestoneEntry, ...] = ()
    withholding_pct: Decimal = Decimal("0")
    cumulative_billed: Decimal = Decimal("0")
    funding_limit: Decimal | None = None
    ceiling_amount: Decimal | None = None


@dataclass(frozen=True)
class BillingLineItem:
    """
    A single line on the billing invoice.

    Immutable value object representing one component of the bill.
    """

    line_type: BillingLineType
    description: str
    amount: Money
    rate: Decimal | None = None
    base_amount: Money | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class BillingResult:
    """
    Complete billing calculation result.

    Immutable value object containing all billing details.

    Attributes:
        contract_type: Type of contract billed
        line_items: Individual billing line items
        total_direct_cost: Sum of direct cost line items
        total_indirect_cost: Sum of indirect cost line items
        total_cost: Total costs (direct + indirect)
        fee_amount: Fee amount
        gross_billing: Total before withholding
        withholding_amount: Amount withheld
        net_billing: Amount to invoice (gross - withholding)
        funding_limited: True if billing was capped by funding
        ceiling_limited: True if billing was capped by ceiling
    """

    contract_type: BillingContractType
    line_items: tuple[BillingLineItem, ...]
    total_direct_cost: Money
    total_indirect_cost: Money
    total_cost: Money
    fee_amount: Money
    gross_billing: Money
    withholding_amount: Money
    net_billing: Money
    funding_limited: bool = False
    ceiling_limited: bool = False


# ============================================================================
# Core Billing Functions
# ============================================================================


def calculate_billing(billing_input: BillingInput) -> BillingResult:
    """
    Calculate billing for a government contract period.

    Pure function - no side effects, no I/O, deterministic output.

    Routes to the appropriate billing calculator based on contract type.

    Args:
        billing_input: Complete billing input parameters

    Returns:
        BillingResult with all line items and totals

    Raises:
        ValueError: If required inputs are missing for the contract type
    """
    t0 = time.monotonic()
    contract_type = billing_input.contract_type

    logger.info("billing_calculation_started", extra={
        "contract_type": contract_type.value,
        "currency": billing_input.currency,
        "fee_rate": str(billing_input.fee_rate),
        "withholding_pct": str(billing_input.withholding_pct),
    })

    if contract_type in (
        BillingContractType.CPFF,
        BillingContractType.CPIF,
        BillingContractType.CPAF,
    ):
        result = _calculate_cost_plus_billing(billing_input)
    elif contract_type == BillingContractType.TM:
        result = _calculate_tm_billing(billing_input)
    elif contract_type == BillingContractType.LH:
        result = _calculate_lh_billing(billing_input)
    elif contract_type == BillingContractType.FFP:
        result = _calculate_ffp_billing(billing_input)
    elif contract_type == BillingContractType.FPI:
        result = _calculate_fpi_billing(billing_input)
    else:
        logger.error("billing_unsupported_contract_type", extra={
            "contract_type": str(contract_type),
        })
        raise ValueError(f"Unsupported contract type: {contract_type}")

    duration_ms = round((time.monotonic() - t0) * 1000, 2)
    logger.info("billing_calculation_completed", extra={
        "contract_type": contract_type.value,
        "net_billing": str(result.net_billing.amount),
        "gross_billing": str(result.gross_billing.amount),
        "total_cost": str(result.total_cost.amount),
        "fee_amount": str(result.fee_amount.amount),
        "line_item_count": len(result.line_items),
        "funding_limited": result.funding_limited,
        "ceiling_limited": result.ceiling_limited,
        "duration_ms": duration_ms,
    })

    return result


def calculate_indirect_costs(
    cost_breakdown: CostBreakdown,
    indirect_rates: IndirectRates,
    currency: str,
) -> tuple[Money, Money, Money, Money]:
    """
    Calculate indirect cost amounts from direct costs and rates.

    Pure function.

    Args:
        cost_breakdown: Direct cost breakdown
        indirect_rates: Indirect rates to apply
        currency: Currency code

    Returns:
        Tuple of (fringe, overhead, ga, material_handling) amounts
    """
    logger.debug("indirect_cost_calculation_started", extra={
        "direct_labor": str(cost_breakdown.direct_labor.amount),
        "fringe_rate": str(indirect_rates.fringe),
        "overhead_rate": str(indirect_rates.overhead),
        "ga_rate": str(indirect_rates.ga),
    })

    # Fringe is applied to direct labor only
    fringe = _apply_rate(cost_breakdown.direct_labor.amount, indirect_rates.fringe, currency)

    # Overhead is applied to direct labor + fringe
    overhead_base = cost_breakdown.direct_labor.amount + fringe.amount
    overhead = _apply_rate(overhead_base, indirect_rates.overhead, currency)

    # G&A is applied to total cost including overhead
    ga_base = cost_breakdown.total_direct.amount + fringe.amount + overhead.amount
    ga = _apply_rate(ga_base, indirect_rates.ga, currency)

    # Material handling is applied to direct material only
    mat_handling_base = (
        cost_breakdown.direct_material.amount
        if cost_breakdown.direct_material
        else Decimal("0")
    )
    mat_handling = _apply_rate(mat_handling_base, indirect_rates.material_handling, currency)

    logger.debug("indirect_cost_calculation_completed", extra={
        "fringe": str(fringe.amount),
        "overhead": str(overhead.amount),
        "ga": str(ga.amount),
        "material_handling": str(mat_handling.amount),
    })

    return fringe, overhead, ga, mat_handling


def calculate_fee(
    total_cost: Decimal,
    fee_rate: Decimal,
    fee_ceiling: Decimal | None,
    cumulative_fee: Decimal,
    currency: str,
) -> tuple[Money, bool]:
    """
    Calculate fee amount with ceiling enforcement.

    Pure function.

    Args:
        total_cost: Total allowable cost base for fee
        fee_rate: Fee rate (e.g., 0.08 for 8%)
        fee_ceiling: Maximum total fee (optional)
        cumulative_fee: Fee already earned to date
        currency: Currency code

    Returns:
        Tuple of (fee_amount, ceiling_hit)
    """
    raw_fee = (total_cost * fee_rate).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)

    if fee_ceiling is not None:
        remaining_fee = fee_ceiling - cumulative_fee
        if remaining_fee < Decimal("0"):
            remaining_fee = Decimal("0")
        if raw_fee > remaining_fee:
            logger.warning("fee_ceiling_applied", extra={
                "raw_fee": str(raw_fee),
                "fee_ceiling": str(fee_ceiling),
                "cumulative_fee": str(cumulative_fee),
                "capped_fee": str(remaining_fee),
            })
            return Money.of(remaining_fee, currency), True

    logger.debug("fee_calculated", extra={
        "total_cost": str(total_cost),
        "fee_rate": str(fee_rate),
        "fee_amount": str(raw_fee),
    })

    return Money.of(raw_fee, currency), False


def apply_withholding(
    gross_amount: Decimal,
    withholding_pct: Decimal,
    currency: str,
) -> tuple[Money, Money]:
    """
    Apply withholding to gross billing amount.

    DCAA standard: 15% withholding on cost-reimbursement contracts
    until incurred costs are verified.

    Pure function.

    Args:
        gross_amount: Gross billing before withholding
        withholding_pct: Withholding percentage (e.g., 0.15 for 15%)
        currency: Currency code

    Returns:
        Tuple of (withholding_amount, net_amount)
    """
    withholding = (gross_amount * withholding_pct).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    net = gross_amount - withholding
    return Money.of(withholding, currency), Money.of(net, currency)


def apply_funding_limit(
    billing_amount: Decimal,
    cumulative_billed: Decimal,
    funding_limit: Decimal | None,
    currency: str,
) -> tuple[Money, bool]:
    """
    Cap billing at funded amount.

    Pure function.

    Args:
        billing_amount: Proposed billing amount
        cumulative_billed: Amount already billed
        funding_limit: Maximum funded amount (optional)
        currency: Currency code

    Returns:
        Tuple of (capped_amount, was_limited)
    """
    if funding_limit is None:
        return Money.of(billing_amount, currency), False

    remaining_funding = funding_limit - cumulative_billed
    if remaining_funding < Decimal("0"):
        remaining_funding = Decimal("0")

    if billing_amount > remaining_funding:
        logger.warning("funding_limit_applied", extra={
            "billing_amount": str(billing_amount),
            "funding_limit": str(funding_limit),
            "cumulative_billed": str(cumulative_billed),
            "remaining_funding": str(remaining_funding),
        })
        return Money.of(remaining_funding, currency), True

    return Money.of(billing_amount, currency), False


# ============================================================================
# Contract-Type-Specific Billing Calculators
# ============================================================================


def _calculate_cost_plus_billing(bi: BillingInput) -> BillingResult:
    """Calculate billing for cost-plus contracts (CPFF, CPIF, CPAF)."""
    if bi.cost_breakdown is None:
        raise ValueError("cost_breakdown required for cost-plus contracts")
    if bi.indirect_rates is None:
        raise ValueError("indirect_rates required for cost-plus contracts")

    currency = bi.currency
    costs = bi.cost_breakdown
    rates = bi.indirect_rates
    zero = Money.zero(currency)

    # Build direct cost lines
    lines: list[BillingLineItem] = []

    lines.append(BillingLineItem(
        line_type=BillingLineType.DIRECT_LABOR,
        description="Direct Labor",
        amount=costs.direct_labor,
    ))

    if costs.direct_material and costs.direct_material.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.DIRECT_MATERIAL,
            description="Direct Material",
            amount=costs.direct_material,
        ))

    if costs.subcontract and costs.subcontract.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.SUBCONTRACT,
            description="Subcontract",
            amount=costs.subcontract,
        ))

    if costs.travel and costs.travel.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.TRAVEL,
            description="Travel",
            amount=costs.travel,
        ))

    if costs.odc and costs.odc.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.ODC,
            description="Other Direct Costs",
            amount=costs.odc,
        ))

    total_direct = costs.total_direct

    # Calculate indirect costs
    fringe, overhead, ga, mat_handling = calculate_indirect_costs(
        costs, rates, currency
    )

    if fringe.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.FRINGE,
            description="Fringe Benefits",
            amount=fringe,
            rate=rates.fringe,
            base_amount=costs.direct_labor,
        ))

    if overhead.amount > 0:
        overhead_base_val = costs.direct_labor.amount + fringe.amount
        lines.append(BillingLineItem(
            line_type=BillingLineType.OVERHEAD,
            description="Overhead",
            amount=overhead,
            rate=rates.overhead,
            base_amount=Money.of(overhead_base_val, currency),
        ))

    if ga.amount > 0:
        ga_base_val = total_direct.amount + fringe.amount + overhead.amount
        lines.append(BillingLineItem(
            line_type=BillingLineType.GA,
            description="General & Administrative",
            amount=ga,
            rate=rates.ga,
            base_amount=Money.of(ga_base_val, currency),
        ))

    total_indirect = Money.of(
        fringe.amount + overhead.amount + ga.amount + mat_handling.amount,
        currency,
    )
    total_cost = Money.of(total_direct.amount + total_indirect.amount, currency)

    # Calculate fee
    fee, _ceiling_hit = calculate_fee(
        total_cost.amount, bi.fee_rate, bi.fee_ceiling, Decimal("0"), currency
    )

    if fee.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.FEE,
            description=f"Fee ({bi.contract_type.value})",
            amount=fee,
            rate=bi.fee_rate,
            base_amount=total_cost,
        ))

    gross = Money.of(total_cost.amount + fee.amount, currency)

    # Apply funding limit
    gross_capped, funding_limited = apply_funding_limit(
        gross.amount, bi.cumulative_billed, bi.funding_limit, currency
    )
    if funding_limited:
        gross = gross_capped

    # Check ceiling
    ceiling_limited = False
    if bi.ceiling_amount is not None:
        remaining = bi.ceiling_amount - bi.cumulative_billed
        if remaining < Decimal("0"):
            remaining = Decimal("0")
        if gross.amount > remaining:
            gross = Money.of(remaining, currency)
            ceiling_limited = True

    # Apply withholding
    withholding, net = apply_withholding(
        gross.amount, bi.withholding_pct, currency
    )

    if withholding.amount > 0:
        lines.append(BillingLineItem(
            line_type=BillingLineType.WITHHOLDING,
            description=f"Withholding ({bi.withholding_pct * 100}%)",
            amount=Money.of(-withholding.amount, currency),
            rate=bi.withholding_pct,
        ))

    return BillingResult(
        contract_type=bi.contract_type,
        line_items=tuple(lines),
        total_direct_cost=total_direct,
        total_indirect_cost=total_indirect,
        total_cost=total_cost,
        fee_amount=fee,
        gross_billing=gross,
        withholding_amount=withholding,
        net_billing=net,
        funding_limited=funding_limited,
        ceiling_limited=ceiling_limited,
    )


def _calculate_tm_billing(bi: BillingInput) -> BillingResult:
    """Calculate billing for Time & Materials contracts."""
    currency = bi.currency
    zero = Money.zero(currency)
    lines: list[BillingLineItem] = []

    # Labor lines - each category billed at its rate
    total_labor = Decimal("0")
    for entry in bi.labor_entries:
        entry_amount = entry.amount
        total_labor += entry_amount
        lines.append(BillingLineItem(
            line_type=BillingLineType.DIRECT_LABOR,
            description=f"Labor: {entry.labor_category}",
            amount=Money.of(entry_amount, currency),
            rate=entry.billing_rate,
            quantity=entry.hours,
        ))

    # Material passthrough
    total_material = Decimal("0")
    if bi.material_passthrough and bi.material_passthrough.amount > 0:
        total_material = bi.material_passthrough.amount
        lines.append(BillingLineItem(
            line_type=BillingLineType.DIRECT_MATERIAL,
            description="Materials at Cost",
            amount=bi.material_passthrough,
        ))

    # T&M billing rates are fully burdened - no separate indirect lines
    total_direct = Money.of(total_labor + total_material, currency)
    total_indirect = zero
    total_cost = total_direct
    fee = zero  # Fee is embedded in billing rates for T&M

    gross = total_cost

    # Apply funding limit
    gross_capped, funding_limited = apply_funding_limit(
        gross.amount, bi.cumulative_billed, bi.funding_limit, currency
    )
    if funding_limited:
        gross = gross_capped

    # Check ceiling
    ceiling_limited = False
    if bi.ceiling_amount is not None:
        remaining = bi.ceiling_amount - bi.cumulative_billed
        if remaining < Decimal("0"):
            remaining = Decimal("0")
        if gross.amount > remaining:
            gross = Money.of(remaining, currency)
            ceiling_limited = True

    # Withholding (typically no withholding on T&M)
    withholding, net = apply_withholding(
        gross.amount, bi.withholding_pct, currency
    )

    return BillingResult(
        contract_type=bi.contract_type,
        line_items=tuple(lines),
        total_direct_cost=total_direct,
        total_indirect_cost=total_indirect,
        total_cost=total_cost,
        fee_amount=fee,
        gross_billing=gross,
        withholding_amount=withholding,
        net_billing=net,
        funding_limited=funding_limited,
        ceiling_limited=ceiling_limited,
    )


def _calculate_lh_billing(bi: BillingInput) -> BillingResult:
    """Calculate billing for Labor Hour contracts."""
    currency = bi.currency
    zero = Money.zero(currency)
    lines: list[BillingLineItem] = []

    # LH is like T&M but labor-only (no materials passthrough)
    total_labor = Decimal("0")
    for entry in bi.labor_entries:
        entry_amount = entry.amount
        total_labor += entry_amount
        lines.append(BillingLineItem(
            line_type=BillingLineType.DIRECT_LABOR,
            description=f"Labor: {entry.labor_category}",
            amount=Money.of(entry_amount, currency),
            rate=entry.billing_rate,
            quantity=entry.hours,
        ))

    total_direct = Money.of(total_labor, currency)
    total_indirect = zero
    total_cost = total_direct
    fee = zero

    gross = total_cost

    # Apply funding limit
    gross_capped, funding_limited = apply_funding_limit(
        gross.amount, bi.cumulative_billed, bi.funding_limit, currency
    )
    if funding_limited:
        gross = gross_capped

    # Check ceiling
    ceiling_limited = False
    if bi.ceiling_amount is not None:
        remaining = bi.ceiling_amount - bi.cumulative_billed
        if remaining < Decimal("0"):
            remaining = Decimal("0")
        if gross.amount > remaining:
            gross = Money.of(remaining, currency)
            ceiling_limited = True

    withholding, net = apply_withholding(
        gross.amount, bi.withholding_pct, currency
    )

    return BillingResult(
        contract_type=bi.contract_type,
        line_items=tuple(lines),
        total_direct_cost=total_direct,
        total_indirect_cost=total_indirect,
        total_cost=total_cost,
        fee_amount=fee,
        gross_billing=gross,
        withholding_amount=withholding,
        net_billing=net,
        funding_limited=funding_limited,
        ceiling_limited=ceiling_limited,
    )


def _calculate_ffp_billing(bi: BillingInput) -> BillingResult:
    """Calculate billing for Firm Fixed Price contracts (milestone-based)."""
    currency = bi.currency
    zero = Money.zero(currency)
    lines: list[BillingLineItem] = []

    total_milestone = Decimal("0")
    for milestone in bi.milestones:
        if milestone.completion_pct >= 100:
            # Fully completed milestone - bill full amount
            total_milestone += milestone.amount
            lines.append(BillingLineItem(
                line_type=BillingLineType.MILESTONE,
                description=f"Milestone: {milestone.description}",
                amount=Money.of(milestone.amount, currency),
            ))

    total_direct = Money.of(total_milestone, currency)
    total_indirect = zero
    total_cost = total_direct
    fee = zero  # Fee is embedded in FFP price

    gross = total_cost

    # Apply funding limit
    gross_capped, funding_limited = apply_funding_limit(
        gross.amount, bi.cumulative_billed, bi.funding_limit, currency
    )
    if funding_limited:
        gross = gross_capped

    withholding, net = apply_withholding(
        gross.amount, bi.withholding_pct, currency
    )

    return BillingResult(
        contract_type=bi.contract_type,
        line_items=tuple(lines),
        total_direct_cost=total_direct,
        total_indirect_cost=total_indirect,
        total_cost=total_cost,
        fee_amount=fee,
        gross_billing=gross,
        withholding_amount=withholding,
        net_billing=net,
        funding_limited=funding_limited,
        ceiling_limited=False,
    )


def _calculate_fpi_billing(bi: BillingInput) -> BillingResult:
    """Calculate billing for Fixed Price Incentive contracts."""
    # FPI is like FFP for billing purposes but with incentive adjustments
    # The incentive/adjustment calculation happens at contract completion
    return _calculate_ffp_billing(
        BillingInput(
            contract_type=BillingContractType.FPI,
            currency=bi.currency,
            milestones=bi.milestones,
            withholding_pct=bi.withholding_pct,
            cumulative_billed=bi.cumulative_billed,
            funding_limit=bi.funding_limit,
            ceiling_amount=bi.ceiling_amount,
        )
    )


# ============================================================================
# Rate Adjustment Functions
# ============================================================================


@dataclass(frozen=True)
class RateAdjustmentInput:
    """
    Input for calculating rate adjustments (final vs provisional).

    Attributes:
        indirect_type: Type of indirect rate being adjusted
        provisional_rate: Rate used for provisional billing
        final_rate: Final negotiated rate
        base_amount: Cost base to which rates were applied
        currency: Currency code
    """

    indirect_type: str
    provisional_rate: Decimal
    final_rate: Decimal
    base_amount: Decimal
    currency: str


@dataclass(frozen=True)
class RateAdjustmentResult:
    """
    Result of a rate adjustment calculation.

    Attributes:
        indirect_type: Type of indirect rate adjusted
        provisional_amount: Amount billed at provisional rate
        final_amount: Amount at final rate
        adjustment_amount: Difference (final - provisional)
        is_underbilled: True if final > provisional (gov owes contractor)
    """

    indirect_type: str
    provisional_amount: Money
    final_amount: Money
    adjustment_amount: Money
    is_underbilled: bool


def calculate_rate_adjustment(
    adjustment: RateAdjustmentInput,
) -> RateAdjustmentResult:
    """
    Calculate rate adjustment between provisional and final rates.

    Pure function.

    This handles the year-end reconciliation between provisional billing
    rates and final negotiated rates per DCAA requirements.

    Args:
        adjustment: Rate adjustment input parameters

    Returns:
        RateAdjustmentResult with calculated amounts
    """
    currency = adjustment.currency

    logger.info("rate_adjustment_calculation_started", extra={
        "indirect_type": adjustment.indirect_type,
        "provisional_rate": str(adjustment.provisional_rate),
        "final_rate": str(adjustment.final_rate),
        "base_amount": str(adjustment.base_amount),
    })

    provisional_amt = (adjustment.base_amount * adjustment.provisional_rate).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
    final_amt = (adjustment.base_amount * adjustment.final_rate).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )

    diff = final_amt - provisional_amt

    logger.info("rate_adjustment_calculation_completed", extra={
        "indirect_type": adjustment.indirect_type,
        "provisional_amount": str(provisional_amt),
        "final_amount": str(final_amt),
        "adjustment_amount": str(diff),
        "is_underbilled": diff > 0,
    })

    return RateAdjustmentResult(
        indirect_type=adjustment.indirect_type,
        provisional_amount=Money.of(provisional_amt, currency),
        final_amount=Money.of(final_amt, currency),
        adjustment_amount=Money.of(diff, currency),
        is_underbilled=diff > 0,
    )


def _apply_rate(base: Decimal, rate: Decimal, currency: str) -> Money:
    """Apply rate to base with deterministic rounding."""
    result = (base * rate).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return Money.of(result, currency)
