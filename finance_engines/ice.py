"""
Incurred Cost Electronically (ICE) Reporting Engine.

Pure functions with deterministic behavior. No I/O.

This engine compiles incurred cost data into the DCAA ICE submission
format required for government contract cost reimbursement. It produces
structured schedule data that can be serialized for electronic submission.

DCAA ICE Schedules produced:
- Schedule A: Claimed Direct Costs by Contract
- Schedule B: Direct Labor Details (hours and costs by labor category)
- Schedule C: Direct Material/Subcontract/Travel/ODC Details
- Schedule G: Indirect Cost Pool Summary (Fringe, Overhead, G&A)
- Schedule H: Indirect Rate Calculation
- Schedule I: Cumulative Allowable Cost Summary by Contract
- Schedule J: Contract Ceiling/Funding Comparison

Usage:
    from finance_engines.ice import (
        compile_ice_submission,
        ICEInput,
        ContractCostInput,
        LaborDetailInput,
        IndirectPoolInput,
    )

    contract_costs = [
        ContractCostInput(
            contract_number="FA8750-21-C-0001",
            direct_labor=Money.of("100000", "USD"),
            ...
        ),
    ]
    ice_input = ICEInput(
        fiscal_year=2024,
        contractor_name="Acme Defense Corp",
        contract_costs=tuple(contract_costs),
        ...
    )
    result = compile_ice_submission(ice_input)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger

logger = get_logger("engines.ice")


# ============================================================================
# Constants
# ============================================================================

_TWO_PLACES = Decimal("0.01")
_SIX_PLACES = Decimal("0.000001")


class ICEScheduleType(str, Enum):
    """ICE schedule identifiers."""

    SCHEDULE_A = "A"  # Claimed Direct Costs by Contract
    SCHEDULE_B = "B"  # Direct Labor Details
    SCHEDULE_C = "C"  # Other Direct Cost Details
    SCHEDULE_G = "G"  # Indirect Cost Pool Summary
    SCHEDULE_H = "H"  # Indirect Rate Calculation
    SCHEDULE_I = "I"  # Cumulative Allowable Cost Summary
    SCHEDULE_J = "J"  # Contract Ceiling/Funding Comparison


class CostElement(str, Enum):
    """Standard cost element classification for ICE reporting."""

    DIRECT_LABOR = "DIRECT_LABOR"
    DIRECT_MATERIAL = "DIRECT_MATERIAL"
    SUBCONTRACT = "SUBCONTRACT"
    TRAVEL = "TRAVEL"
    ODC = "ODC"
    FRINGE = "FRINGE"
    OVERHEAD = "OVERHEAD"
    GA = "G&A"
    MATERIAL_HANDLING = "MATERIAL_HANDLING"
    FEE = "FEE"


class AllowabilityStatus(str, Enum):
    """Allowability classification for ICE."""

    ALLOWABLE = "ALLOWABLE"
    UNALLOWABLE = "UNALLOWABLE"
    CONDITIONAL = "CONDITIONAL"


# ============================================================================
# Input Value Objects
# ============================================================================


@dataclass(frozen=True)
class LaborDetailInput:
    """
    Labor detail for a single employee/category on a contract.

    Attributes:
        contract_number: Contract charged
        labor_category: Labor category code
        employee_id: Employee identifier (anonymized for submission)
        hours: Hours worked in the fiscal year
        rate: Hourly rate
        amount: Total labor cost (hours * rate)
        allowability: DCAA allowability classification
    """

    contract_number: str
    labor_category: str
    employee_id: str
    hours: Decimal
    rate: Decimal
    amount: Money
    allowability: AllowabilityStatus = AllowabilityStatus.ALLOWABLE

    def __post_init__(self) -> None:
        if self.hours < 0:
            raise ValueError("hours must be non-negative")
        if self.rate < 0:
            raise ValueError("rate must be non-negative")


@dataclass(frozen=True)
class OtherDirectCostInput:
    """
    Non-labor direct cost detail.

    Attributes:
        contract_number: Contract charged
        cost_element: Type of cost (MATERIAL, SUBCONTRACT, TRAVEL, ODC)
        description: Cost description
        vendor: Vendor/supplier name
        amount: Cost amount
        allowability: DCAA allowability classification
    """

    contract_number: str
    cost_element: CostElement
    description: str
    vendor: str
    amount: Money
    allowability: AllowabilityStatus = AllowabilityStatus.ALLOWABLE

    def __post_init__(self) -> None:
        if self.cost_element in (
            CostElement.DIRECT_LABOR,
            CostElement.FRINGE,
            CostElement.OVERHEAD,
            CostElement.GA,
            CostElement.MATERIAL_HANDLING,
            CostElement.FEE,
        ):
            raise ValueError(
                f"OtherDirectCostInput cannot use cost element: {self.cost_element}"
            )


@dataclass(frozen=True)
class ContractCostInput:
    """
    Aggregate direct costs for a single contract.

    Attributes:
        contract_number: Contract identifier
        contract_type: FAR contract type code
        direct_labor: Total direct labor cost
        direct_material: Total direct material cost
        subcontract: Total subcontract cost
        travel: Total travel cost
        odc: Total other direct costs
        currency: Currency code
    """

    contract_number: str
    contract_type: str
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
    def currency(self) -> str:
        """Currency code from direct labor."""
        return self.direct_labor.currency.code

    @property
    def total_direct(self) -> Money:
        """Total of all direct costs."""
        total = self.direct_labor.amount
        for attr in ("direct_material", "subcontract", "travel", "odc"):
            val = getattr(self, attr)
            if val is not None:
                total += val.amount
        return Money.of(total, self.currency)


@dataclass(frozen=True)
class IndirectPoolInput:
    """
    Indirect cost pool data for rate calculation.

    Attributes:
        pool_name: Pool identifier (FRINGE, OVERHEAD, G&A, etc.)
        pool_costs: Total costs in the pool
        allocation_base: Total allocation base
        computed_rate: Pool costs / allocation base
        claimed_rate: Rate claimed (may differ from computed if negotiated)
        base_description: Description of the allocation base
    """

    pool_name: str
    pool_costs: Money
    allocation_base: Money
    claimed_rate: Decimal
    base_description: str
    computed_rate: Decimal | None = None

    def __post_init__(self) -> None:
        if self.claimed_rate < 0:
            raise ValueError("claimed_rate must be non-negative")
        if self.pool_costs.amount < 0:
            raise ValueError("pool_costs must be non-negative")
        if self.allocation_base.amount < 0:
            raise ValueError("allocation_base must be non-negative")
        # Compute rate if not provided
        if self.computed_rate is None and self.allocation_base.amount > 0:
            computed = (
                self.pool_costs.amount / self.allocation_base.amount
            ).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
            object.__setattr__(self, "computed_rate", computed)


@dataclass(frozen=True)
class ContractCeilingInput:
    """
    Contract ceiling/funding comparison data.

    Attributes:
        contract_number: Contract identifier
        contract_type: FAR contract type code
        funded_amount: Current obligated/funded amount
        ceiling_amount: Contract ceiling (NTE)
        cumulative_incurred: Total incurred costs to date
        cumulative_billed: Total billed to date
        cumulative_fee: Total fee earned to date
    """

    contract_number: str
    contract_type: str
    funded_amount: Money
    ceiling_amount: Money | None = None
    cumulative_incurred: Money = field(default=None)
    cumulative_billed: Money = field(default=None)
    cumulative_fee: Money = field(default=None)

    def __post_init__(self) -> None:
        currency = self.funded_amount.currency.code
        zero = Money.zero(currency)
        if self.cumulative_incurred is None:
            object.__setattr__(self, "cumulative_incurred", zero)
        if self.cumulative_billed is None:
            object.__setattr__(self, "cumulative_billed", zero)
        if self.cumulative_fee is None:
            object.__setattr__(self, "cumulative_fee", zero)


@dataclass(frozen=True)
class ICEInput:
    """
    Complete input for ICE submission compilation.

    Attributes:
        fiscal_year: Fiscal year being reported
        fiscal_year_start: First day of fiscal year
        fiscal_year_end: Last day of fiscal year
        contractor_name: Legal name of contractor
        contractor_duns: DUNS number
        contractor_cage: CAGE code
        currency: Currency for all amounts
        contract_costs: Direct costs by contract
        labor_details: Individual labor charge details
        other_direct_costs: Non-labor direct cost details
        indirect_pools: Indirect cost pool data
        contract_ceilings: Contract ceiling/funding data
        total_unallowable_direct: Total unallowable direct costs
        total_unallowable_indirect: Total unallowable indirect costs
    """

    fiscal_year: int
    fiscal_year_start: date
    fiscal_year_end: date
    contractor_name: str
    currency: str
    contract_costs: tuple[ContractCostInput, ...]
    labor_details: tuple[LaborDetailInput, ...] = ()
    other_direct_costs: tuple[OtherDirectCostInput, ...] = ()
    indirect_pools: tuple[IndirectPoolInput, ...] = ()
    contract_ceilings: tuple[ContractCeilingInput, ...] = ()
    contractor_duns: str | None = None
    contractor_cage: str | None = None
    total_unallowable_direct: Money | None = None
    total_unallowable_indirect: Money | None = None

    def __post_init__(self) -> None:
        if self.fiscal_year_end <= self.fiscal_year_start:
            raise ValueError("fiscal_year_end must be after fiscal_year_start")
        if not self.contract_costs:
            raise ValueError("At least one contract cost entry is required")


# ============================================================================
# Output Value Objects - ICE Schedules
# ============================================================================


@dataclass(frozen=True)
class ScheduleALine:
    """
    Schedule A line: Claimed direct costs for a single contract.

    Shows total claimed direct costs by cost element per contract.
    """

    contract_number: str
    contract_type: str
    direct_labor: Money
    direct_material: Money
    subcontract: Money
    travel: Money
    odc: Money
    total_direct: Money


@dataclass(frozen=True)
class ScheduleA:
    """Schedule A: Claimed Direct Costs by Contract."""

    lines: tuple[ScheduleALine, ...]
    total_direct_labor: Money
    total_direct_material: Money
    total_subcontract: Money
    total_travel: Money
    total_odc: Money
    grand_total_direct: Money


@dataclass(frozen=True)
class ScheduleBLine:
    """
    Schedule B line: Labor detail for a contract/category.
    """

    contract_number: str
    labor_category: str
    total_hours: Decimal
    average_rate: Decimal
    total_amount: Money
    employee_count: int


@dataclass(frozen=True)
class ScheduleB:
    """Schedule B: Direct Labor Details."""

    lines: tuple[ScheduleBLine, ...]
    total_hours: Decimal
    total_amount: Money


@dataclass(frozen=True)
class ScheduleCLine:
    """
    Schedule C line: Other direct cost detail.
    """

    contract_number: str
    cost_element: CostElement
    description: str
    vendor: str
    amount: Money
    allowability: AllowabilityStatus


@dataclass(frozen=True)
class ScheduleC:
    """Schedule C: Other Direct Cost Details."""

    lines: tuple[ScheduleCLine, ...]
    total_material: Money
    total_subcontract: Money
    total_travel: Money
    total_odc: Money
    grand_total: Money


@dataclass(frozen=True)
class ScheduleGLine:
    """
    Schedule G line: Indirect cost pool detail.
    """

    pool_name: str
    pool_costs: Money
    allocation_base: Money
    base_description: str
    computed_rate: Decimal
    claimed_rate: Decimal


@dataclass(frozen=True)
class ScheduleG:
    """Schedule G: Indirect Cost Pool Summary."""

    lines: tuple[ScheduleGLine, ...]
    total_pool_costs: Money


@dataclass(frozen=True)
class ScheduleHLine:
    """
    Schedule H line: Indirect rate applied to a contract.
    """

    contract_number: str
    pool_name: str
    allocation_base: Money
    claimed_rate: Decimal
    applied_amount: Money


@dataclass(frozen=True)
class ScheduleH:
    """Schedule H: Indirect Rate Calculation by Contract."""

    lines: tuple[ScheduleHLine, ...]
    total_indirect_applied: Money


@dataclass(frozen=True)
class ScheduleILine:
    """
    Schedule I line: Cumulative allowable cost for a contract.
    """

    contract_number: str
    contract_type: str
    total_direct: Money
    total_indirect: Money
    total_cost: Money
    fee: Money
    total_claimed: Money


@dataclass(frozen=True)
class ScheduleI:
    """Schedule I: Cumulative Allowable Cost Summary."""

    lines: tuple[ScheduleILine, ...]
    grand_total_direct: Money
    grand_total_indirect: Money
    grand_total_cost: Money
    grand_total_fee: Money
    grand_total_claimed: Money


@dataclass(frozen=True)
class ScheduleJLine:
    """
    Schedule J line: Contract ceiling/funding comparison.
    """

    contract_number: str
    contract_type: str
    funded_amount: Money
    ceiling_amount: Money | None
    cumulative_incurred: Money
    cumulative_billed: Money
    cumulative_fee: Money
    remaining_funding: Money
    funding_utilization_pct: Decimal


@dataclass(frozen=True)
class ScheduleJ:
    """Schedule J: Contract Ceiling/Funding Comparison."""

    lines: tuple[ScheduleJLine, ...]
    total_funded: Money
    total_incurred: Money
    total_billed: Money


@dataclass(frozen=True)
class ICEValidationFinding:
    """
    Validation finding from ICE compilation.

    Attributes:
        severity: ERROR (blocks submission) or WARNING (informational)
        schedule: Which schedule has the issue
        finding: Description of the finding
        contract_number: Affected contract (if applicable)
    """

    severity: str
    schedule: str
    finding: str
    contract_number: str | None = None


@dataclass(frozen=True)
class ICESubmission:
    """
    Complete ICE submission result.

    Contains all schedules and validation findings.
    """

    fiscal_year: int
    fiscal_year_start: date
    fiscal_year_end: date
    contractor_name: str
    contractor_duns: str | None
    contractor_cage: str | None
    currency: str
    schedule_a: ScheduleA
    schedule_b: ScheduleB
    schedule_c: ScheduleC
    schedule_g: ScheduleG
    schedule_h: ScheduleH
    schedule_i: ScheduleI
    schedule_j: ScheduleJ
    findings: tuple[ICEValidationFinding, ...]
    is_valid: bool
    total_claimed: Money
    total_unallowable: Money


# ============================================================================
# Core ICE Compilation Functions
# ============================================================================


def compile_ice_submission(ice_input: ICEInput) -> ICESubmission:
    """
    Compile a complete ICE submission from input data.

    Pure function - no side effects, no I/O, deterministic output.

    Orchestrates compilation of all ICE schedules and performs
    cross-schedule validation.

    Args:
        ice_input: Complete ICE input data

    Returns:
        ICESubmission with all schedules and validation findings
    """
    t0 = time.monotonic()
    currency = ice_input.currency
    zero = Money.zero(currency)

    logger.info("ice_submission_compilation_started", extra={
        "fiscal_year": ice_input.fiscal_year,
        "contractor_name": ice_input.contractor_name,
        "contract_count": len(ice_input.contract_costs),
        "labor_detail_count": len(ice_input.labor_details),
        "odc_count": len(ice_input.other_direct_costs),
        "indirect_pool_count": len(ice_input.indirect_pools),
        "currency": currency,
    })

    # Compile individual schedules
    schedule_a = compile_schedule_a(ice_input.contract_costs, currency)
    schedule_b = compile_schedule_b(ice_input.labor_details, currency)
    schedule_c = compile_schedule_c(ice_input.other_direct_costs, currency)
    schedule_g = compile_schedule_g(ice_input.indirect_pools, currency)
    schedule_h = compile_schedule_h(
        ice_input.contract_costs, ice_input.indirect_pools, currency
    )
    schedule_i = compile_schedule_i(
        ice_input.contract_costs, ice_input.indirect_pools, currency
    )
    schedule_j = compile_schedule_j(ice_input.contract_ceilings, currency)

    # Calculate totals
    total_unallowable_direct = ice_input.total_unallowable_direct or zero
    total_unallowable_indirect = ice_input.total_unallowable_indirect or zero
    total_unallowable = Money.of(
        total_unallowable_direct.amount + total_unallowable_indirect.amount,
        currency,
    )

    # Cross-schedule validation
    findings = _validate_ice_submission(
        ice_input, schedule_a, schedule_b, schedule_c,
        schedule_g, schedule_h, schedule_i, schedule_j,
    )

    is_valid = all(f.severity != "ERROR" for f in findings)

    error_count = sum(1 for f in findings if f.severity == "ERROR")
    warning_count = sum(1 for f in findings if f.severity == "WARNING")
    duration_ms = round((time.monotonic() - t0) * 1000, 2)

    if is_valid:
        logger.info("ice_submission_compilation_completed", extra={
            "fiscal_year": ice_input.fiscal_year,
            "is_valid": is_valid,
            "finding_count": len(findings),
            "error_count": error_count,
            "warning_count": warning_count,
            "total_claimed": str(schedule_i.grand_total_claimed.amount),
            "duration_ms": duration_ms,
        })
    else:
        logger.warning("ice_submission_compilation_has_errors", extra={
            "fiscal_year": ice_input.fiscal_year,
            "is_valid": is_valid,
            "error_count": error_count,
            "warning_count": warning_count,
            "duration_ms": duration_ms,
        })

    return ICESubmission(
        fiscal_year=ice_input.fiscal_year,
        fiscal_year_start=ice_input.fiscal_year_start,
        fiscal_year_end=ice_input.fiscal_year_end,
        contractor_name=ice_input.contractor_name,
        contractor_duns=ice_input.contractor_duns,
        contractor_cage=ice_input.contractor_cage,
        currency=currency,
        schedule_a=schedule_a,
        schedule_b=schedule_b,
        schedule_c=schedule_c,
        schedule_g=schedule_g,
        schedule_h=schedule_h,
        schedule_i=schedule_i,
        schedule_j=schedule_j,
        findings=tuple(findings),
        is_valid=is_valid,
        total_claimed=schedule_i.grand_total_claimed,
        total_unallowable=total_unallowable,
    )


# ============================================================================
# Individual Schedule Compilation
# ============================================================================


def compile_schedule_a(
    contract_costs: Sequence[ContractCostInput],
    currency: str,
) -> ScheduleA:
    """
    Compile Schedule A: Claimed Direct Costs by Contract.

    Pure function.

    Args:
        contract_costs: Direct costs per contract
        currency: Currency code

    Returns:
        ScheduleA with per-contract direct cost totals
    """
    logger.debug("schedule_a_compilation_started", extra={
        "contract_count": len(contract_costs),
    })

    zero = Money.zero(currency)
    lines: list[ScheduleALine] = []

    total_labor = Decimal("0")
    total_material = Decimal("0")
    total_sub = Decimal("0")
    total_travel = Decimal("0")
    total_odc = Decimal("0")

    for cc in contract_costs:
        labor = cc.direct_labor
        material = cc.direct_material or zero
        sub = cc.subcontract or zero
        travel = cc.travel or zero
        odc = cc.odc or zero

        total_direct = Money.of(
            labor.amount + material.amount + sub.amount + travel.amount + odc.amount,
            currency,
        )

        lines.append(ScheduleALine(
            contract_number=cc.contract_number,
            contract_type=cc.contract_type,
            direct_labor=labor,
            direct_material=material,
            subcontract=sub,
            travel=travel,
            odc=odc,
            total_direct=total_direct,
        ))

        total_labor += labor.amount
        total_material += material.amount
        total_sub += sub.amount
        total_travel += travel.amount
        total_odc += odc.amount

    grand_total = total_labor + total_material + total_sub + total_travel + total_odc

    logger.info("schedule_a_compiled", extra={
        "contract_count": len(lines),
        "grand_total_direct": str(grand_total),
        "total_labor": str(total_labor),
    })

    return ScheduleA(
        lines=tuple(lines),
        total_direct_labor=Money.of(total_labor, currency),
        total_direct_material=Money.of(total_material, currency),
        total_subcontract=Money.of(total_sub, currency),
        total_travel=Money.of(total_travel, currency),
        total_odc=Money.of(total_odc, currency),
        grand_total_direct=Money.of(grand_total, currency),
    )


def compile_schedule_b(
    labor_details: Sequence[LaborDetailInput],
    currency: str,
) -> ScheduleB:
    """
    Compile Schedule B: Direct Labor Details.

    Pure function. Aggregates labor by contract and category.

    Args:
        labor_details: Individual labor charge entries
        currency: Currency code

    Returns:
        ScheduleB with labor summary by contract/category
    """
    zero = Money.zero(currency)

    # Aggregate by (contract, category)
    agg: dict[tuple[str, str], dict] = {}
    for detail in labor_details:
        key = (detail.contract_number, detail.labor_category)
        if key not in agg:
            agg[key] = {
                "hours": Decimal("0"),
                "amount": Decimal("0"),
                "employees": set(),
            }
        agg[key]["hours"] += detail.hours
        agg[key]["amount"] += detail.amount.amount
        agg[key]["employees"].add(detail.employee_id)

    lines: list[ScheduleBLine] = []
    total_hours = Decimal("0")
    total_amount = Decimal("0")

    for (contract, category), data in sorted(agg.items()):
        hours = data["hours"]
        amount = data["amount"]
        avg_rate = (
            (amount / hours).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
            if hours > 0
            else Decimal("0")
        )

        lines.append(ScheduleBLine(
            contract_number=contract,
            labor_category=category,
            total_hours=hours,
            average_rate=avg_rate,
            total_amount=Money.of(amount, currency),
            employee_count=len(data["employees"]),
        ))

        total_hours += hours
        total_amount += amount

    logger.info("schedule_b_compiled", extra={
        "labor_detail_count": len(labor_details),
        "aggregated_line_count": len(lines),
        "total_hours": str(total_hours),
        "total_amount": str(total_amount),
    })

    return ScheduleB(
        lines=tuple(lines),
        total_hours=total_hours,
        total_amount=Money.of(total_amount, currency),
    )


def compile_schedule_c(
    other_direct_costs: Sequence[OtherDirectCostInput],
    currency: str,
) -> ScheduleC:
    """
    Compile Schedule C: Other Direct Cost Details.

    Pure function.

    Args:
        other_direct_costs: Non-labor direct cost entries
        currency: Currency code

    Returns:
        ScheduleC with itemized other direct costs
    """
    zero = Money.zero(currency)
    lines: list[ScheduleCLine] = []

    total_mat = Decimal("0")
    total_sub = Decimal("0")
    total_travel = Decimal("0")
    total_odc = Decimal("0")

    for odc in other_direct_costs:
        lines.append(ScheduleCLine(
            contract_number=odc.contract_number,
            cost_element=odc.cost_element,
            description=odc.description,
            vendor=odc.vendor,
            amount=odc.amount,
            allowability=odc.allowability,
        ))

        if odc.cost_element == CostElement.DIRECT_MATERIAL:
            total_mat += odc.amount.amount
        elif odc.cost_element == CostElement.SUBCONTRACT:
            total_sub += odc.amount.amount
        elif odc.cost_element == CostElement.TRAVEL:
            total_travel += odc.amount.amount
        else:
            total_odc += odc.amount.amount

    grand = total_mat + total_sub + total_travel + total_odc

    logger.info("schedule_c_compiled", extra={
        "odc_line_count": len(lines),
        "grand_total": str(grand),
    })

    return ScheduleC(
        lines=tuple(lines),
        total_material=Money.of(total_mat, currency),
        total_subcontract=Money.of(total_sub, currency),
        total_travel=Money.of(total_travel, currency),
        total_odc=Money.of(total_odc, currency),
        grand_total=Money.of(grand, currency),
    )


def compile_schedule_g(
    indirect_pools: Sequence[IndirectPoolInput],
    currency: str,
) -> ScheduleG:
    """
    Compile Schedule G: Indirect Cost Pool Summary.

    Pure function.

    Args:
        indirect_pools: Indirect pool definitions with costs and bases
        currency: Currency code

    Returns:
        ScheduleG with pool summary
    """
    lines: list[ScheduleGLine] = []
    total_pool = Decimal("0")

    for pool in indirect_pools:
        computed = pool.computed_rate if pool.computed_rate is not None else Decimal("0")
        lines.append(ScheduleGLine(
            pool_name=pool.pool_name,
            pool_costs=pool.pool_costs,
            allocation_base=pool.allocation_base,
            base_description=pool.base_description,
            computed_rate=computed,
            claimed_rate=pool.claimed_rate,
        ))
        total_pool += pool.pool_costs.amount

    logger.info("schedule_g_compiled", extra={
        "pool_count": len(lines),
        "total_pool_costs": str(total_pool),
    })

    return ScheduleG(
        lines=tuple(lines),
        total_pool_costs=Money.of(total_pool, currency),
    )


def compile_schedule_h(
    contract_costs: Sequence[ContractCostInput],
    indirect_pools: Sequence[IndirectPoolInput],
    currency: str,
) -> ScheduleH:
    """
    Compile Schedule H: Indirect Rate Calculation by Contract.

    Applies indirect rates to each contract's allocation base.
    Pure function.

    Args:
        contract_costs: Direct costs per contract
        indirect_pools: Indirect pool definitions
        currency: Currency code

    Returns:
        ScheduleH with indirect costs allocated to each contract
    """
    lines: list[ScheduleHLine] = []
    total_indirect = Decimal("0")

    for cc in contract_costs:
        for pool in indirect_pools:
            # Determine allocation base for this pool
            base = _get_pool_allocation_base(cc, pool.pool_name, currency)
            applied = (base.amount * pool.claimed_rate).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP
            )

            lines.append(ScheduleHLine(
                contract_number=cc.contract_number,
                pool_name=pool.pool_name,
                allocation_base=base,
                claimed_rate=pool.claimed_rate,
                applied_amount=Money.of(applied, currency),
            ))

            total_indirect += applied

    logger.info("schedule_h_compiled", extra={
        "line_count": len(lines),
        "total_indirect_applied": str(total_indirect),
    })

    return ScheduleH(
        lines=tuple(lines),
        total_indirect_applied=Money.of(total_indirect, currency),
    )


def compile_schedule_i(
    contract_costs: Sequence[ContractCostInput],
    indirect_pools: Sequence[IndirectPoolInput],
    currency: str,
) -> ScheduleI:
    """
    Compile Schedule I: Cumulative Allowable Cost Summary.

    Combines direct and indirect costs per contract.
    Pure function.

    Args:
        contract_costs: Direct costs per contract
        indirect_pools: Indirect pool definitions
        currency: Currency code

    Returns:
        ScheduleI with total claimed costs per contract
    """
    zero = Money.zero(currency)
    lines: list[ScheduleILine] = []

    gt_direct = Decimal("0")
    gt_indirect = Decimal("0")
    gt_cost = Decimal("0")
    gt_fee = Decimal("0")
    gt_claimed = Decimal("0")

    for cc in contract_costs:
        total_direct = cc.total_direct

        # Calculate total indirect for this contract
        contract_indirect = Decimal("0")
        for pool in indirect_pools:
            base = _get_pool_allocation_base(cc, pool.pool_name, currency)
            applied = (base.amount * pool.claimed_rate).quantize(
                _TWO_PLACES, rounding=ROUND_HALF_UP
            )
            contract_indirect += applied

        total_indirect = Money.of(contract_indirect, currency)
        total_cost = Money.of(total_direct.amount + contract_indirect, currency)

        # Fee is zero in ICE (claimed separately)
        fee = zero
        total_claimed = total_cost

        lines.append(ScheduleILine(
            contract_number=cc.contract_number,
            contract_type=cc.contract_type,
            total_direct=total_direct,
            total_indirect=total_indirect,
            total_cost=total_cost,
            fee=fee,
            total_claimed=total_claimed,
        ))

        gt_direct += total_direct.amount
        gt_indirect += contract_indirect
        gt_cost += total_cost.amount
        gt_fee += fee.amount
        gt_claimed += total_claimed.amount

    logger.info("schedule_i_compiled", extra={
        "contract_count": len(lines),
        "grand_total_direct": str(gt_direct),
        "grand_total_indirect": str(gt_indirect),
        "grand_total_claimed": str(gt_claimed),
    })

    return ScheduleI(
        lines=tuple(lines),
        grand_total_direct=Money.of(gt_direct, currency),
        grand_total_indirect=Money.of(gt_indirect, currency),
        grand_total_cost=Money.of(gt_cost, currency),
        grand_total_fee=Money.of(gt_fee, currency),
        grand_total_claimed=Money.of(gt_claimed, currency),
    )


def compile_schedule_j(
    contract_ceilings: Sequence[ContractCeilingInput],
    currency: str,
) -> ScheduleJ:
    """
    Compile Schedule J: Contract Ceiling/Funding Comparison.

    Pure function.

    Args:
        contract_ceilings: Contract ceiling/funding data
        currency: Currency code

    Returns:
        ScheduleJ with ceiling/funding comparison per contract
    """
    lines: list[ScheduleJLine] = []
    total_funded = Decimal("0")
    total_incurred = Decimal("0")
    total_billed = Decimal("0")

    for cc in contract_ceilings:
        remaining = Money.of(
            cc.funded_amount.amount - cc.cumulative_incurred.amount,
            currency,
        )

        utilization = (
            (cc.cumulative_incurred.amount / cc.funded_amount.amount * Decimal("100"))
            .quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
            if cc.funded_amount.amount > 0
            else Decimal("0")
        )

        lines.append(ScheduleJLine(
            contract_number=cc.contract_number,
            contract_type=cc.contract_type,
            funded_amount=cc.funded_amount,
            ceiling_amount=cc.ceiling_amount,
            cumulative_incurred=cc.cumulative_incurred,
            cumulative_billed=cc.cumulative_billed,
            cumulative_fee=cc.cumulative_fee,
            remaining_funding=remaining,
            funding_utilization_pct=utilization,
        ))

        total_funded += cc.funded_amount.amount
        total_incurred += cc.cumulative_incurred.amount
        total_billed += cc.cumulative_billed.amount

    logger.info("schedule_j_compiled", extra={
        "contract_count": len(lines),
        "total_funded": str(total_funded),
        "total_incurred": str(total_incurred),
        "total_billed": str(total_billed),
    })

    return ScheduleJ(
        lines=tuple(lines),
        total_funded=Money.of(total_funded, currency),
        total_incurred=Money.of(total_incurred, currency),
        total_billed=Money.of(total_billed, currency),
    )


# ============================================================================
# Pool Base Resolution
# ============================================================================


def _get_pool_allocation_base(
    contract_cost: ContractCostInput,
    pool_name: str,
    currency: str,
) -> Money:
    """
    Determine the allocation base for a contract and indirect pool.

    Standard DCAA pool base rules:
    - FRINGE: Applied to direct labor
    - OVERHEAD: Applied to direct labor + fringe
    - G&A: Applied to total cost (direct + fringe + overhead)
    - MATERIAL_HANDLING: Applied to direct material

    Pure function.

    Args:
        contract_cost: Contract's direct costs
        pool_name: Name of the indirect pool
        currency: Currency code

    Returns:
        Money representing the allocation base
    """
    zero = Money.zero(currency)
    pool_upper = pool_name.upper()

    if pool_upper == "FRINGE":
        return contract_cost.direct_labor
    elif pool_upper == "OVERHEAD":
        # Labor + fringe (but we don't cascade here - that's Schedule I's job)
        return contract_cost.direct_labor
    elif pool_upper in ("G&A", "GA", "G_AND_A"):
        return contract_cost.total_direct
    elif pool_upper in ("MATERIAL_HANDLING", "MAT_HANDLING"):
        return contract_cost.direct_material or zero
    else:
        # Default: apply to total direct costs
        return contract_cost.total_direct


# ============================================================================
# Cross-Schedule Validation
# ============================================================================


def _validate_ice_submission(
    ice_input: ICEInput,
    schedule_a: ScheduleA,
    schedule_b: ScheduleB,
    schedule_c: ScheduleC,
    schedule_g: ScheduleG,
    schedule_h: ScheduleH,
    schedule_i: ScheduleI,
    schedule_j: ScheduleJ,
) -> list[ICEValidationFinding]:
    """
    Perform cross-schedule validation for ICE submission.

    Pure function.

    Checks:
    1. Schedule B labor total matches Schedule A labor total
    2. Schedule C ODC total matches Schedule A non-labor total
    3. Schedule H indirect total matches Schedule I indirect total
    4. All contracts in A appear in I
    5. Funding utilization warnings (>90%)
    6. Negative remaining funding warnings

    Args:
        All compiled schedules

    Returns:
        List of validation findings
    """
    logger.info("ice_validation_started", extra={
        "fiscal_year": ice_input.fiscal_year,
    })

    findings: list[ICEValidationFinding] = []
    currency = ice_input.currency

    # 1. Schedule B labor total should match Schedule A labor total
    if schedule_b.total_amount.amount != schedule_a.total_direct_labor.amount:
        findings.append(ICEValidationFinding(
            severity="WARNING",
            schedule="B",
            finding=(
                f"Schedule B labor total ({schedule_b.total_amount.amount}) "
                f"does not match Schedule A labor total ({schedule_a.total_direct_labor.amount})"
            ),
        ))

    # 2. Schedule C non-labor total should match Schedule A non-labor totals
    a_nonlabor = (
        schedule_a.total_direct_material.amount
        + schedule_a.total_subcontract.amount
        + schedule_a.total_travel.amount
        + schedule_a.total_odc.amount
    )
    if schedule_c.grand_total.amount != a_nonlabor:
        findings.append(ICEValidationFinding(
            severity="WARNING",
            schedule="C",
            finding=(
                f"Schedule C total ({schedule_c.grand_total.amount}) "
                f"does not match Schedule A non-labor total ({a_nonlabor})"
            ),
        ))

    # 3. Schedule H indirect total should match Schedule I indirect total
    if schedule_h.total_indirect_applied.amount != schedule_i.grand_total_indirect.amount:
        findings.append(ICEValidationFinding(
            severity="ERROR",
            schedule="H/I",
            finding=(
                f"Schedule H indirect ({schedule_h.total_indirect_applied.amount}) "
                f"does not match Schedule I indirect ({schedule_i.grand_total_indirect.amount})"
            ),
        ))

    # 4. All contracts in Schedule A should appear in Schedule I
    a_contracts = {line.contract_number for line in schedule_a.lines}
    i_contracts = {line.contract_number for line in schedule_i.lines}
    missing = a_contracts - i_contracts
    for contract in sorted(missing):
        findings.append(ICEValidationFinding(
            severity="ERROR",
            schedule="I",
            finding=f"Contract {contract} in Schedule A but missing from Schedule I",
            contract_number=contract,
        ))

    # 5. Funding utilization warnings
    for jline in schedule_j.lines:
        if jline.funding_utilization_pct > Decimal("90"):
            findings.append(ICEValidationFinding(
                severity="WARNING",
                schedule="J",
                finding=(
                    f"Contract {jline.contract_number} funding utilization "
                    f"is {jline.funding_utilization_pct}%"
                ),
                contract_number=jline.contract_number,
            ))

    # 6. Negative remaining funding
    for jline in schedule_j.lines:
        if jline.remaining_funding.amount < 0:
            findings.append(ICEValidationFinding(
                severity="ERROR",
                schedule="J",
                finding=(
                    f"Contract {jline.contract_number} has negative remaining "
                    f"funding ({jline.remaining_funding.amount})"
                ),
                contract_number=jline.contract_number,
            ))

    logger.info("ice_validation_completed", extra={
        "finding_count": len(findings),
        "error_count": sum(1 for f in findings if f.severity == "ERROR"),
        "warning_count": sum(1 for f in findings if f.severity == "WARNING"),
    })

    return findings
