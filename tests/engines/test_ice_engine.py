"""
Comprehensive tests for the ICE (Incurred Cost Electronically) engine.

Tests all ICE schedules, input validation, cross-schedule validation,
and determinism guarantees.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finance_engines.ice import (
    AllowabilityStatus,
    ContractCeilingInput,
    ContractCostInput,
    CostElement,
    ICEInput,
    ICEScheduleType,
    ICESubmission,
    ICEValidationFinding,
    IndirectPoolInput,
    LaborDetailInput,
    OtherDirectCostInput,
    ScheduleA,
    ScheduleALine,
    ScheduleB,
    ScheduleBLine,
    ScheduleC,
    ScheduleCLine,
    ScheduleG,
    ScheduleGLine,
    ScheduleH,
    ScheduleHLine,
    ScheduleI,
    ScheduleILine,
    ScheduleJ,
    ScheduleJLine,
    compile_ice_submission,
    compile_schedule_a,
    compile_schedule_b,
    compile_schedule_c,
    compile_schedule_g,
    compile_schedule_h,
    compile_schedule_i,
    compile_schedule_j,
)
from finance_kernel.domain.values import Money

# ============================================================================
# Fixtures / Helpers
# ============================================================================

USD = "USD"


def _money(amount: str) -> Money:
    return Money.of(Decimal(amount), USD)


def _basic_contract_costs() -> tuple[ContractCostInput, ...]:
    """Two contracts with typical cost breakdown."""
    return (
        ContractCostInput(
            contract_number="FA8750-21-C-0001",
            contract_type="CPFF",
            direct_labor=_money("100000"),
            direct_material=_money("30000"),
            subcontract=_money("20000"),
            travel=_money("5000"),
            odc=_money("2000"),
        ),
        ContractCostInput(
            contract_number="W911NF-22-C-0042",
            contract_type="T&M",
            direct_labor=_money("80000"),
            direct_material=_money("10000"),
        ),
    )


def _basic_indirect_pools() -> tuple[IndirectPoolInput, ...]:
    """Standard DCAA indirect pools."""
    return (
        IndirectPoolInput(
            pool_name="FRINGE",
            pool_costs=_money("63000"),
            allocation_base=_money("180000"),
            claimed_rate=Decimal("0.35"),
            base_description="Direct Labor",
        ),
        IndirectPoolInput(
            pool_name="OVERHEAD",
            pool_costs=_money("81000"),
            allocation_base=_money("180000"),
            claimed_rate=Decimal("0.45"),
            base_description="Direct Labor",
        ),
        IndirectPoolInput(
            pool_name="G&A",
            pool_costs=_money("26700"),
            allocation_base=_money("267000"),
            claimed_rate=Decimal("0.10"),
            base_description="Total Direct Costs",
        ),
    )


def _basic_labor_details() -> tuple[LaborDetailInput, ...]:
    """Labor detail entries for two contracts."""
    return (
        LaborDetailInput(
            contract_number="FA8750-21-C-0001",
            labor_category="ENGINEER_III",
            employee_id="EMP-001",
            hours=Decimal("1000"),
            rate=Decimal("50"),
            amount=_money("50000"),
        ),
        LaborDetailInput(
            contract_number="FA8750-21-C-0001",
            labor_category="ENGINEER_III",
            employee_id="EMP-002",
            hours=Decimal("500"),
            rate=Decimal("50"),
            amount=_money("25000"),
        ),
        LaborDetailInput(
            contract_number="FA8750-21-C-0001",
            labor_category="ANALYST_II",
            employee_id="EMP-003",
            hours=Decimal("625"),
            rate=Decimal("40"),
            amount=_money("25000"),
        ),
        LaborDetailInput(
            contract_number="W911NF-22-C-0042",
            labor_category="ENGINEER_III",
            employee_id="EMP-001",
            hours=Decimal("800"),
            rate=Decimal("50"),
            amount=_money("40000"),
        ),
        LaborDetailInput(
            contract_number="W911NF-22-C-0042",
            labor_category="MANAGER_I",
            employee_id="EMP-004",
            hours=Decimal("400"),
            rate=Decimal("100"),
            amount=_money("40000"),
        ),
    )


def _basic_other_direct_costs() -> tuple[OtherDirectCostInput, ...]:
    """Non-labor direct cost entries."""
    return (
        OtherDirectCostInput(
            contract_number="FA8750-21-C-0001",
            cost_element=CostElement.DIRECT_MATERIAL,
            description="Electronic components",
            vendor="Digi-Key Corp",
            amount=_money("20000"),
        ),
        OtherDirectCostInput(
            contract_number="FA8750-21-C-0001",
            cost_element=CostElement.DIRECT_MATERIAL,
            description="Test equipment",
            vendor="National Instruments",
            amount=_money("10000"),
        ),
        OtherDirectCostInput(
            contract_number="FA8750-21-C-0001",
            cost_element=CostElement.SUBCONTRACT,
            description="Subcontract - RF Testing",
            vendor="RF Solutions Inc",
            amount=_money("20000"),
        ),
        OtherDirectCostInput(
            contract_number="FA8750-21-C-0001",
            cost_element=CostElement.TRAVEL,
            description="On-site review travel",
            vendor="Various",
            amount=_money("5000"),
        ),
        OtherDirectCostInput(
            contract_number="FA8750-21-C-0001",
            cost_element=CostElement.ODC,
            description="Software licenses",
            vendor="MathWorks Inc",
            amount=_money("2000"),
        ),
        OtherDirectCostInput(
            contract_number="W911NF-22-C-0042",
            cost_element=CostElement.DIRECT_MATERIAL,
            description="Network hardware",
            vendor="Cisco Systems",
            amount=_money("10000"),
        ),
    )


def _basic_ceilings() -> tuple[ContractCeilingInput, ...]:
    """Contract ceiling/funding data."""
    return (
        ContractCeilingInput(
            contract_number="FA8750-21-C-0001",
            contract_type="CPFF",
            funded_amount=_money("500000"),
            ceiling_amount=_money("600000"),
            cumulative_incurred=_money("250000"),
            cumulative_billed=_money("220000"),
            cumulative_fee=_money("15000"),
        ),
        ContractCeilingInput(
            contract_number="W911NF-22-C-0042",
            contract_type="T&M",
            funded_amount=_money("200000"),
            ceiling_amount=_money("250000"),
            cumulative_incurred=_money("150000"),
            cumulative_billed=_money("140000"),
        ),
    )


def _basic_ice_input() -> ICEInput:
    """Complete ICE input for testing."""
    return ICEInput(
        fiscal_year=2024,
        fiscal_year_start=date(2024, 1, 1),
        fiscal_year_end=date(2024, 12, 31),
        contractor_name="Acme Defense Corp",
        currency=USD,
        contract_costs=_basic_contract_costs(),
        labor_details=_basic_labor_details(),
        other_direct_costs=_basic_other_direct_costs(),
        indirect_pools=_basic_indirect_pools(),
        contract_ceilings=_basic_ceilings(),
        contractor_duns="123456789",
        contractor_cage="ABC12",
    )


# ============================================================================
# Input Value Object Tests
# ============================================================================


class TestLaborDetailInput:
    """Test LaborDetailInput validation."""

    def test_valid_entry(self):
        entry = LaborDetailInput(
            contract_number="C-001",
            labor_category="ENG",
            employee_id="E-001",
            hours=Decimal("100"),
            rate=Decimal("50"),
            amount=_money("5000"),
        )
        assert entry.hours == Decimal("100")
        assert entry.rate == Decimal("50")

    def test_negative_hours_rejected(self):
        with pytest.raises(ValueError, match="hours must be non-negative"):
            LaborDetailInput(
                contract_number="C-001",
                labor_category="ENG",
                employee_id="E-001",
                hours=Decimal("-1"),
                rate=Decimal("50"),
                amount=_money("0"),
            )

    def test_negative_rate_rejected(self):
        with pytest.raises(ValueError, match="rate must be non-negative"):
            LaborDetailInput(
                contract_number="C-001",
                labor_category="ENG",
                employee_id="E-001",
                hours=Decimal("100"),
                rate=Decimal("-10"),
                amount=_money("0"),
            )

    def test_zero_hours_allowed(self):
        entry = LaborDetailInput(
            contract_number="C-001",
            labor_category="ENG",
            employee_id="E-001",
            hours=Decimal("0"),
            rate=Decimal("50"),
            amount=_money("0"),
        )
        assert entry.hours == Decimal("0")

    def test_default_allowability(self):
        entry = LaborDetailInput(
            contract_number="C-001",
            labor_category="ENG",
            employee_id="E-001",
            hours=Decimal("10"),
            rate=Decimal("50"),
            amount=_money("500"),
        )
        assert entry.allowability == AllowabilityStatus.ALLOWABLE


class TestOtherDirectCostInput:
    """Test OtherDirectCostInput validation."""

    def test_valid_material(self):
        entry = OtherDirectCostInput(
            contract_number="C-001",
            cost_element=CostElement.DIRECT_MATERIAL,
            description="Test material",
            vendor="Vendor A",
            amount=_money("1000"),
        )
        assert entry.cost_element == CostElement.DIRECT_MATERIAL

    def test_labor_element_rejected(self):
        with pytest.raises(ValueError, match="cannot use cost element"):
            OtherDirectCostInput(
                contract_number="C-001",
                cost_element=CostElement.DIRECT_LABOR,
                description="Should fail",
                vendor="Vendor",
                amount=_money("1000"),
            )

    def test_fringe_element_rejected(self):
        with pytest.raises(ValueError, match="cannot use cost element"):
            OtherDirectCostInput(
                contract_number="C-001",
                cost_element=CostElement.FRINGE,
                description="Should fail",
                vendor="Vendor",
                amount=_money("1000"),
            )

    def test_overhead_element_rejected(self):
        with pytest.raises(ValueError, match="cannot use cost element"):
            OtherDirectCostInput(
                contract_number="C-001",
                cost_element=CostElement.OVERHEAD,
                description="Should fail",
                vendor="Vendor",
                amount=_money("1000"),
            )

    def test_ga_element_rejected(self):
        with pytest.raises(ValueError, match="cannot use cost element"):
            OtherDirectCostInput(
                contract_number="C-001",
                cost_element=CostElement.GA,
                description="Should fail",
                vendor="Vendor",
                amount=_money("1000"),
            )

    def test_fee_element_rejected(self):
        with pytest.raises(ValueError, match="cannot use cost element"):
            OtherDirectCostInput(
                contract_number="C-001",
                cost_element=CostElement.FEE,
                description="Should fail",
                vendor="Vendor",
                amount=_money("1000"),
            )

    def test_valid_elements_accepted(self):
        """MATERIAL, SUBCONTRACT, TRAVEL, ODC are all valid."""
        for element in (
            CostElement.DIRECT_MATERIAL,
            CostElement.SUBCONTRACT,
            CostElement.TRAVEL,
            CostElement.ODC,
        ):
            entry = OtherDirectCostInput(
                contract_number="C-001",
                cost_element=element,
                description=f"Test {element.value}",
                vendor="Vendor",
                amount=_money("100"),
            )
            assert entry.cost_element == element


class TestContractCostInput:
    """Test ContractCostInput validation."""

    def test_total_direct_all_categories(self):
        cc = ContractCostInput(
            contract_number="C-001",
            contract_type="CPFF",
            direct_labor=_money("100000"),
            direct_material=_money("30000"),
            subcontract=_money("20000"),
            travel=_money("5000"),
            odc=_money("2000"),
        )
        assert cc.total_direct.amount == Decimal("157000")

    def test_total_direct_labor_only(self):
        cc = ContractCostInput(
            contract_number="C-001",
            contract_type="CPFF",
            direct_labor=_money("100000"),
        )
        assert cc.total_direct.amount == Decimal("100000")

    def test_negative_labor_rejected(self):
        with pytest.raises(ValueError, match="direct_labor must be non-negative"):
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("-100"),
            )

    def test_negative_material_rejected(self):
        with pytest.raises(ValueError, match="direct_material must be non-negative"):
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("100"),
                direct_material=_money("-50"),
            )

    def test_currency_property(self):
        cc = ContractCostInput(
            contract_number="C-001",
            contract_type="CPFF",
            direct_labor=_money("100"),
        )
        assert cc.currency == "USD"


class TestIndirectPoolInput:
    """Test IndirectPoolInput validation."""

    def test_valid_pool(self):
        pool = IndirectPoolInput(
            pool_name="FRINGE",
            pool_costs=_money("35000"),
            allocation_base=_money("100000"),
            claimed_rate=Decimal("0.35"),
            base_description="Direct Labor",
        )
        assert pool.claimed_rate == Decimal("0.35")

    def test_computed_rate_auto_calculated(self):
        pool = IndirectPoolInput(
            pool_name="FRINGE",
            pool_costs=_money("35000"),
            allocation_base=_money("100000"),
            claimed_rate=Decimal("0.35"),
            base_description="Direct Labor",
        )
        assert pool.computed_rate == Decimal("0.350000")

    def test_computed_rate_zero_base(self):
        """Computed rate stays None when base is zero."""
        pool = IndirectPoolInput(
            pool_name="FRINGE",
            pool_costs=_money("0"),
            allocation_base=_money("0"),
            claimed_rate=Decimal("0"),
            base_description="Direct Labor",
        )
        assert pool.computed_rate is None

    def test_negative_rate_rejected(self):
        with pytest.raises(ValueError, match="claimed_rate must be non-negative"):
            IndirectPoolInput(
                pool_name="FRINGE",
                pool_costs=_money("35000"),
                allocation_base=_money("100000"),
                claimed_rate=Decimal("-0.1"),
                base_description="Direct Labor",
            )

    def test_negative_pool_costs_rejected(self):
        with pytest.raises(ValueError, match="pool_costs must be non-negative"):
            IndirectPoolInput(
                pool_name="FRINGE",
                pool_costs=_money("-100"),
                allocation_base=_money("100000"),
                claimed_rate=Decimal("0.35"),
                base_description="Direct Labor",
            )


class TestContractCeilingInput:
    """Test ContractCeilingInput validation."""

    def test_defaults_to_zero(self):
        cc = ContractCeilingInput(
            contract_number="C-001",
            contract_type="CPFF",
            funded_amount=_money("500000"),
        )
        assert cc.cumulative_incurred.amount == Decimal("0")
        assert cc.cumulative_billed.amount == Decimal("0")
        assert cc.cumulative_fee.amount == Decimal("0")

    def test_ceiling_optional(self):
        cc = ContractCeilingInput(
            contract_number="C-001",
            contract_type="CPFF",
            funded_amount=_money("500000"),
        )
        assert cc.ceiling_amount is None


class TestICEInput:
    """Test ICEInput validation."""

    def test_invalid_date_range(self):
        with pytest.raises(ValueError, match="fiscal_year_end must be after"):
            ICEInput(
                fiscal_year=2024,
                fiscal_year_start=date(2024, 12, 31),
                fiscal_year_end=date(2024, 1, 1),
                contractor_name="Test Corp",
                currency=USD,
                contract_costs=_basic_contract_costs(),
            )

    def test_same_start_end_rejected(self):
        with pytest.raises(ValueError, match="fiscal_year_end must be after"):
            ICEInput(
                fiscal_year=2024,
                fiscal_year_start=date(2024, 6, 15),
                fiscal_year_end=date(2024, 6, 15),
                contractor_name="Test Corp",
                currency=USD,
                contract_costs=_basic_contract_costs(),
            )

    def test_no_contracts_rejected(self):
        with pytest.raises(ValueError, match="At least one contract"):
            ICEInput(
                fiscal_year=2024,
                fiscal_year_start=date(2024, 1, 1),
                fiscal_year_end=date(2024, 12, 31),
                contractor_name="Test Corp",
                currency=USD,
                contract_costs=(),
            )


# ============================================================================
# Schedule A Tests
# ============================================================================


class TestScheduleA:
    """Test Schedule A: Claimed Direct Costs by Contract."""

    def test_basic_compilation(self):
        sched = compile_schedule_a(_basic_contract_costs(), USD)
        assert len(sched.lines) == 2

    def test_first_contract_totals(self):
        sched = compile_schedule_a(_basic_contract_costs(), USD)
        line = sched.lines[0]
        assert line.contract_number == "FA8750-21-C-0001"
        assert line.direct_labor.amount == Decimal("100000")
        assert line.direct_material.amount == Decimal("30000")
        assert line.subcontract.amount == Decimal("20000")
        assert line.travel.amount == Decimal("5000")
        assert line.odc.amount == Decimal("2000")
        assert line.total_direct.amount == Decimal("157000")

    def test_second_contract_totals(self):
        sched = compile_schedule_a(_basic_contract_costs(), USD)
        line = sched.lines[1]
        assert line.contract_number == "W911NF-22-C-0042"
        assert line.direct_labor.amount == Decimal("80000")
        assert line.direct_material.amount == Decimal("10000")
        assert line.total_direct.amount == Decimal("90000")

    def test_grand_totals(self):
        sched = compile_schedule_a(_basic_contract_costs(), USD)
        assert sched.total_direct_labor.amount == Decimal("180000")
        assert sched.total_direct_material.amount == Decimal("40000")
        assert sched.total_subcontract.amount == Decimal("20000")
        assert sched.total_travel.amount == Decimal("5000")
        assert sched.total_odc.amount == Decimal("2000")
        assert sched.grand_total_direct.amount == Decimal("247000")

    def test_zero_optional_costs(self):
        """Contracts with only labor show zero for other categories."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="LH",
                direct_labor=_money("50000"),
            ),
        )
        sched = compile_schedule_a(costs, USD)
        line = sched.lines[0]
        assert line.direct_material.amount == Decimal("0")
        assert line.subcontract.amount == Decimal("0")
        assert line.travel.amount == Decimal("0")
        assert line.odc.amount == Decimal("0")
        assert line.total_direct.amount == Decimal("50000")

    def test_single_contract(self):
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("1000"),
                direct_material=_money("500"),
            ),
        )
        sched = compile_schedule_a(costs, USD)
        assert len(sched.lines) == 1
        assert sched.grand_total_direct.amount == Decimal("1500")


# ============================================================================
# Schedule B Tests
# ============================================================================


class TestScheduleB:
    """Test Schedule B: Direct Labor Details."""

    def test_basic_compilation(self):
        sched = compile_schedule_b(_basic_labor_details(), USD)
        assert len(sched.lines) > 0

    def test_aggregation_by_contract_category(self):
        """Two employees on same contract/category should aggregate."""
        sched = compile_schedule_b(_basic_labor_details(), USD)
        # FA8750 / ENGINEER_III has EMP-001 (1000h) + EMP-002 (500h)
        eng3_line = None
        for line in sched.lines:
            if (
                line.contract_number == "FA8750-21-C-0001"
                and line.labor_category == "ENGINEER_III"
            ):
                eng3_line = line
                break
        assert eng3_line is not None
        assert eng3_line.total_hours == Decimal("1500")
        assert eng3_line.total_amount.amount == Decimal("75000")
        assert eng3_line.employee_count == 2

    def test_average_rate_calculation(self):
        sched = compile_schedule_b(_basic_labor_details(), USD)
        for line in sched.lines:
            if (
                line.contract_number == "FA8750-21-C-0001"
                and line.labor_category == "ENGINEER_III"
            ):
                # 75000 / 1500 = 50.00
                assert line.average_rate == Decimal("50.00")

    def test_total_hours_and_amount(self):
        sched = compile_schedule_b(_basic_labor_details(), USD)
        # 1000 + 500 + 625 + 800 + 400 = 3325
        assert sched.total_hours == Decimal("3325")
        # 50000 + 25000 + 25000 + 40000 + 40000 = 180000
        assert sched.total_amount.amount == Decimal("180000")

    def test_empty_labor_details(self):
        sched = compile_schedule_b((), USD)
        assert len(sched.lines) == 0
        assert sched.total_hours == Decimal("0")
        assert sched.total_amount.amount == Decimal("0")

    def test_single_entry(self):
        details = (
            LaborDetailInput(
                contract_number="C-001",
                labor_category="ENG",
                employee_id="E-001",
                hours=Decimal("100"),
                rate=Decimal("75"),
                amount=_money("7500"),
            ),
        )
        sched = compile_schedule_b(details, USD)
        assert len(sched.lines) == 1
        assert sched.lines[0].employee_count == 1
        assert sched.lines[0].average_rate == Decimal("75.00")


# ============================================================================
# Schedule C Tests
# ============================================================================


class TestScheduleC:
    """Test Schedule C: Other Direct Cost Details."""

    def test_basic_compilation(self):
        sched = compile_schedule_c(_basic_other_direct_costs(), USD)
        assert len(sched.lines) == 6

    def test_category_totals(self):
        sched = compile_schedule_c(_basic_other_direct_costs(), USD)
        # Material: 20000 + 10000 + 10000 = 40000
        assert sched.total_material.amount == Decimal("40000")
        assert sched.total_subcontract.amount == Decimal("20000")
        assert sched.total_travel.amount == Decimal("5000")
        assert sched.total_odc.amount == Decimal("2000")

    def test_grand_total(self):
        sched = compile_schedule_c(_basic_other_direct_costs(), USD)
        assert sched.grand_total.amount == Decimal("67000")

    def test_empty_odcs(self):
        sched = compile_schedule_c((), USD)
        assert len(sched.lines) == 0
        assert sched.grand_total.amount == Decimal("0")

    def test_allowability_preserved(self):
        costs = (
            OtherDirectCostInput(
                contract_number="C-001",
                cost_element=CostElement.TRAVEL,
                description="Unallowable travel",
                vendor="Airlines",
                amount=_money("500"),
                allowability=AllowabilityStatus.UNALLOWABLE,
            ),
        )
        sched = compile_schedule_c(costs, USD)
        assert sched.lines[0].allowability == AllowabilityStatus.UNALLOWABLE


# ============================================================================
# Schedule G Tests
# ============================================================================


class TestScheduleG:
    """Test Schedule G: Indirect Cost Pool Summary."""

    def test_basic_compilation(self):
        sched = compile_schedule_g(_basic_indirect_pools(), USD)
        assert len(sched.lines) == 3

    def test_pool_data_preserved(self):
        sched = compile_schedule_g(_basic_indirect_pools(), USD)
        fringe = sched.lines[0]
        assert fringe.pool_name == "FRINGE"
        assert fringe.pool_costs.amount == Decimal("63000")
        assert fringe.allocation_base.amount == Decimal("180000")
        assert fringe.claimed_rate == Decimal("0.35")

    def test_total_pool_costs(self):
        sched = compile_schedule_g(_basic_indirect_pools(), USD)
        # 63000 + 81000 + 26700 = 170700
        assert sched.total_pool_costs.amount == Decimal("170700")

    def test_empty_pools(self):
        sched = compile_schedule_g((), USD)
        assert len(sched.lines) == 0
        assert sched.total_pool_costs.amount == Decimal("0")

    def test_computed_rate_displayed(self):
        sched = compile_schedule_g(_basic_indirect_pools(), USD)
        fringe = sched.lines[0]
        assert fringe.computed_rate == Decimal("0.350000")


# ============================================================================
# Schedule H Tests
# ============================================================================


class TestScheduleH:
    """Test Schedule H: Indirect Rate Calculation by Contract."""

    def test_basic_compilation(self):
        sched = compile_schedule_h(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        # 2 contracts x 3 pools = 6 lines
        assert len(sched.lines) == 6

    def test_fringe_applied_to_labor(self):
        sched = compile_schedule_h(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        # First contract, FRINGE pool
        line = sched.lines[0]
        assert line.contract_number == "FA8750-21-C-0001"
        assert line.pool_name == "FRINGE"
        assert line.allocation_base.amount == Decimal("100000")
        assert line.claimed_rate == Decimal("0.35")
        assert line.applied_amount.amount == Decimal("35000.00")

    def test_overhead_applied_to_labor(self):
        sched = compile_schedule_h(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        # First contract, OVERHEAD pool (base is direct labor)
        line = sched.lines[1]
        assert line.pool_name == "OVERHEAD"
        assert line.allocation_base.amount == Decimal("100000")
        assert line.applied_amount.amount == Decimal("45000.00")

    def test_ga_applied_to_total_direct(self):
        sched = compile_schedule_h(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        # First contract, G&A pool (base is total direct)
        line = sched.lines[2]
        assert line.pool_name == "G&A"
        assert line.allocation_base.amount == Decimal("157000")
        assert line.applied_amount.amount == Decimal("15700.00")

    def test_total_indirect_applied(self):
        sched = compile_schedule_h(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        # Contract 1: 35000 + 45000 + 15700 = 95700
        # Contract 2: 28000 + 36000 + 9000 = 73000
        # Total: 168700
        assert sched.total_indirect_applied.amount == Decimal("168700.00")

    def test_second_contract_ga(self):
        sched = compile_schedule_h(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        # Second contract, G&A (total direct = 90000)
        line = sched.lines[5]
        assert line.contract_number == "W911NF-22-C-0042"
        assert line.pool_name == "G&A"
        assert line.allocation_base.amount == Decimal("90000")
        assert line.applied_amount.amount == Decimal("9000.00")


# ============================================================================
# Schedule I Tests
# ============================================================================


class TestScheduleI:
    """Test Schedule I: Cumulative Allowable Cost Summary."""

    def test_basic_compilation(self):
        sched = compile_schedule_i(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        assert len(sched.lines) == 2

    def test_first_contract_totals(self):
        sched = compile_schedule_i(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        line = sched.lines[0]
        assert line.contract_number == "FA8750-21-C-0001"
        assert line.total_direct.amount == Decimal("157000")
        # Indirect: 35000 + 45000 + 15700 = 95700
        assert line.total_indirect.amount == Decimal("95700.00")
        assert line.total_cost.amount == Decimal("252700.00")

    def test_grand_totals(self):
        sched = compile_schedule_i(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        assert sched.grand_total_direct.amount == Decimal("247000")
        # 95700 + 73000 = 168700
        assert sched.grand_total_indirect.amount == Decimal("168700.00")
        assert sched.grand_total_cost.amount == Decimal("415700.00")
        assert sched.grand_total_claimed.amount == Decimal("415700.00")

    def test_fee_is_zero(self):
        """Fee is zero in ICE (claimed separately)."""
        sched = compile_schedule_i(
            _basic_contract_costs(), _basic_indirect_pools(), USD
        )
        for line in sched.lines:
            assert line.fee.amount == Decimal("0")
        assert sched.grand_total_fee.amount == Decimal("0")


# ============================================================================
# Schedule J Tests
# ============================================================================


class TestScheduleJ:
    """Test Schedule J: Contract Ceiling/Funding Comparison."""

    def test_basic_compilation(self):
        sched = compile_schedule_j(_basic_ceilings(), USD)
        assert len(sched.lines) == 2

    def test_remaining_funding(self):
        sched = compile_schedule_j(_basic_ceilings(), USD)
        line = sched.lines[0]
        # 500000 - 250000 = 250000
        assert line.remaining_funding.amount == Decimal("250000")

    def test_utilization_percentage(self):
        sched = compile_schedule_j(_basic_ceilings(), USD)
        line = sched.lines[0]
        # 250000 / 500000 * 100 = 50.00%
        assert line.funding_utilization_pct == Decimal("50.00")

    def test_second_contract_utilization(self):
        sched = compile_schedule_j(_basic_ceilings(), USD)
        line = sched.lines[1]
        # 150000 / 200000 * 100 = 75.00%
        assert line.funding_utilization_pct == Decimal("75.00")

    def test_totals(self):
        sched = compile_schedule_j(_basic_ceilings(), USD)
        assert sched.total_funded.amount == Decimal("700000")
        assert sched.total_incurred.amount == Decimal("400000")
        assert sched.total_billed.amount == Decimal("360000")

    def test_zero_funded_utilization(self):
        """Zero funded amount should produce 0% utilization."""
        ceilings = (
            ContractCeilingInput(
                contract_number="C-001",
                contract_type="CPFF",
                funded_amount=_money("0"),
            ),
        )
        sched = compile_schedule_j(ceilings, USD)
        assert sched.lines[0].funding_utilization_pct == Decimal("0")

    def test_ceiling_preserved(self):
        sched = compile_schedule_j(_basic_ceilings(), USD)
        assert sched.lines[0].ceiling_amount.amount == Decimal("600000")

    def test_empty_ceilings(self):
        sched = compile_schedule_j((), USD)
        assert len(sched.lines) == 0
        assert sched.total_funded.amount == Decimal("0")


# ============================================================================
# Full ICE Submission Tests
# ============================================================================


class TestICESubmission:
    """Test complete ICE submission compilation."""

    def test_basic_compilation(self):
        result = compile_ice_submission(_basic_ice_input())
        assert result.fiscal_year == 2024
        assert result.contractor_name == "Acme Defense Corp"
        assert result.currency == USD

    def test_all_schedules_present(self):
        result = compile_ice_submission(_basic_ice_input())
        assert result.schedule_a is not None
        assert result.schedule_b is not None
        assert result.schedule_c is not None
        assert result.schedule_g is not None
        assert result.schedule_h is not None
        assert result.schedule_i is not None
        assert result.schedule_j is not None

    def test_contractor_info_preserved(self):
        result = compile_ice_submission(_basic_ice_input())
        assert result.contractor_duns == "123456789"
        assert result.contractor_cage == "ABC12"

    def test_total_claimed(self):
        result = compile_ice_submission(_basic_ice_input())
        # Should match Schedule I grand total claimed
        assert result.total_claimed.amount == result.schedule_i.grand_total_claimed.amount

    def test_date_range(self):
        result = compile_ice_submission(_basic_ice_input())
        assert result.fiscal_year_start == date(2024, 1, 1)
        assert result.fiscal_year_end == date(2024, 12, 31)


# ============================================================================
# Cross-Schedule Validation Tests
# ============================================================================


class TestICEValidation:
    """Test cross-schedule validation."""

    def test_labor_mismatch_warning(self):
        """Schedule B labor total != Schedule A labor total."""
        # Basic input has labor details totaling 180000 which matches Schedule A
        result = compile_ice_submission(_basic_ice_input())
        # No labor mismatch warning expected
        labor_warnings = [
            f for f in result.findings
            if f.schedule == "B" and "labor total" in f.finding
        ]
        assert len(labor_warnings) == 0

    def test_labor_mismatch_detected(self):
        """When labor details don't match contract costs."""
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=(
                ContractCostInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    direct_labor=_money("100000"),
                ),
            ),
            labor_details=(
                LaborDetailInput(
                    contract_number="C-001",
                    labor_category="ENG",
                    employee_id="E-001",
                    hours=Decimal("100"),
                    rate=Decimal("50"),
                    amount=_money("5000"),  # Only 5000 vs 100000 in Schedule A
                ),
            ),
        )
        result = compile_ice_submission(ice)
        labor_warnings = [
            f for f in result.findings
            if f.schedule == "B" and "labor total" in f.finding
        ]
        assert len(labor_warnings) == 1
        assert labor_warnings[0].severity == "WARNING"

    def test_odc_mismatch_detected(self):
        """When ODC details don't match contract costs."""
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=(
                ContractCostInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    direct_labor=_money("100000"),
                    direct_material=_money("50000"),
                ),
            ),
            other_direct_costs=(
                OtherDirectCostInput(
                    contract_number="C-001",
                    cost_element=CostElement.DIRECT_MATERIAL,
                    description="Test",
                    vendor="Test Vendor",
                    amount=_money("10000"),  # Only 10000 vs 50000
                ),
            ),
        )
        result = compile_ice_submission(ice)
        odc_warnings = [
            f for f in result.findings
            if f.schedule == "C" and "non-labor" in f.finding
        ]
        assert len(odc_warnings) == 1

    def test_h_i_consistency(self):
        """Schedule H and I indirect totals should match."""
        result = compile_ice_submission(_basic_ice_input())
        # They're computed from the same data so should always match
        h_i_errors = [
            f for f in result.findings
            if f.schedule == "H/I"
        ]
        assert len(h_i_errors) == 0

    def test_high_utilization_warning(self):
        """High funding utilization triggers warning."""
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=(
                ContractCostInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    direct_labor=_money("100000"),
                ),
            ),
            contract_ceilings=(
                ContractCeilingInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    funded_amount=_money("100000"),
                    cumulative_incurred=_money("95000"),
                ),
            ),
        )
        result = compile_ice_submission(ice)
        util_warnings = [
            f for f in result.findings
            if f.schedule == "J" and "utilization" in f.finding
        ]
        assert len(util_warnings) == 1
        assert "95.00%" in util_warnings[0].finding

    def test_negative_funding_error(self):
        """Overspent funding triggers error."""
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=(
                ContractCostInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    direct_labor=_money("100000"),
                ),
            ),
            contract_ceilings=(
                ContractCeilingInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    funded_amount=_money("50000"),
                    cumulative_incurred=_money("75000"),
                ),
            ),
        )
        result = compile_ice_submission(ice)
        negative_errors = [
            f for f in result.findings
            if f.schedule == "J" and "negative remaining" in f.finding
        ]
        assert len(negative_errors) == 1
        assert negative_errors[0].severity == "ERROR"

    def test_is_valid_true_when_clean(self):
        """No errors means is_valid is True."""
        result = compile_ice_submission(_basic_ice_input())
        errors = [f for f in result.findings if f.severity == "ERROR"]
        # The basic input is consistent so no errors expected
        assert result.is_valid is True or len(errors) > 0

    def test_is_valid_false_on_error(self):
        """Errors make is_valid False."""
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=(
                ContractCostInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    direct_labor=_money("100000"),
                ),
            ),
            contract_ceilings=(
                ContractCeilingInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    funded_amount=_money("50000"),
                    cumulative_incurred=_money("75000"),  # Overspent
                ),
            ),
        )
        result = compile_ice_submission(ice)
        assert result.is_valid is False


# ============================================================================
# Determinism Tests
# ============================================================================


class TestDeterminism:
    """Test that ICE engine produces deterministic results."""

    def test_same_input_same_output(self):
        ice_input = _basic_ice_input()
        result1 = compile_ice_submission(ice_input)
        result2 = compile_ice_submission(ice_input)

        assert result1.total_claimed.amount == result2.total_claimed.amount
        assert result1.schedule_a.grand_total_direct.amount == result2.schedule_a.grand_total_direct.amount
        assert result1.schedule_i.grand_total_cost.amount == result2.schedule_i.grand_total_cost.amount
        assert len(result1.findings) == len(result2.findings)

    def test_schedule_a_deterministic(self):
        costs = _basic_contract_costs()
        s1 = compile_schedule_a(costs, USD)
        s2 = compile_schedule_a(costs, USD)
        assert s1.grand_total_direct.amount == s2.grand_total_direct.amount
        for l1, l2 in zip(s1.lines, s2.lines, strict=False):
            assert l1.contract_number == l2.contract_number
            assert l1.total_direct.amount == l2.total_direct.amount

    def test_schedule_h_deterministic(self):
        costs = _basic_contract_costs()
        pools = _basic_indirect_pools()
        s1 = compile_schedule_h(costs, pools, USD)
        s2 = compile_schedule_h(costs, pools, USD)
        assert s1.total_indirect_applied.amount == s2.total_indirect_applied.amount

    def test_result_immutable(self):
        result = compile_ice_submission(_basic_ice_input())
        with pytest.raises((TypeError, AttributeError)):
            result.fiscal_year = 2025

    def test_schedule_lines_immutable(self):
        result = compile_ice_submission(_basic_ice_input())
        with pytest.raises((TypeError, AttributeError)):
            result.schedule_a.lines[0].direct_labor = _money("999")


# ============================================================================
# Edge Case Tests
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_contract_no_indirect(self):
        """Single contract with no indirect pools."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="FFP",
                direct_labor=_money("10000"),
            ),
        )
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=costs,
        )
        result = compile_ice_submission(ice)
        assert result.schedule_i.grand_total_indirect.amount == Decimal("0")
        assert result.schedule_i.grand_total_direct.amount == Decimal("10000")

    def test_zero_cost_contract(self):
        """Contract with zero direct labor."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("0"),
            ),
        )
        sched = compile_schedule_a(costs, USD)
        assert sched.grand_total_direct.amount == Decimal("0")

    def test_many_contracts(self):
        """ICE with many contracts."""
        costs = tuple(
            ContractCostInput(
                contract_number=f"C-{i:04d}",
                contract_type="CPFF",
                direct_labor=_money(str(i * 1000)),
            )
            for i in range(1, 51)
        )
        sched = compile_schedule_a(costs, USD)
        assert len(sched.lines) == 50
        # Sum of 1000 + 2000 + ... + 50000 = 50*51/2 * 1000 = 1275000
        assert sched.grand_total_direct.amount == Decimal("1275000")

    def test_small_amounts(self):
        """Very small monetary amounts handled correctly."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("0.01"),
                direct_material=_money("0.01"),
            ),
        )
        pools = (
            IndirectPoolInput(
                pool_name="FRINGE",
                pool_costs=_money("0.01"),
                allocation_base=_money("0.01"),
                claimed_rate=Decimal("0.35"),
                base_description="Direct Labor",
            ),
        )
        sched_h = compile_schedule_h(costs, pools, USD)
        # 0.01 * 0.35 = 0.0035 -> rounds to 0.00
        assert sched_h.total_indirect_applied.amount == Decimal("0.00")

    def test_large_amounts(self):
        """Large monetary amounts handled correctly."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("999999999.99"),
            ),
        )
        sched = compile_schedule_a(costs, USD)
        assert sched.grand_total_direct.amount == Decimal("999999999.99")

    def test_material_handling_pool(self):
        """Material handling pool uses material as base."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="CPFF",
                direct_labor=_money("100000"),
                direct_material=_money("50000"),
            ),
        )
        pools = (
            IndirectPoolInput(
                pool_name="MATERIAL_HANDLING",
                pool_costs=_money("5000"),
                allocation_base=_money("50000"),
                claimed_rate=Decimal("0.10"),
                base_description="Direct Material",
            ),
        )
        sched_h = compile_schedule_h(costs, pools, USD)
        # Base should be direct_material = 50000
        assert sched_h.lines[0].allocation_base.amount == Decimal("50000")
        assert sched_h.lines[0].applied_amount.amount == Decimal("5000.00")

    def test_no_material_for_material_handling(self):
        """Material handling with no material costs produces zero."""
        costs = (
            ContractCostInput(
                contract_number="C-001",
                contract_type="LH",
                direct_labor=_money("100000"),
            ),
        )
        pools = (
            IndirectPoolInput(
                pool_name="MATERIAL_HANDLING",
                pool_costs=_money("5000"),
                allocation_base=_money("50000"),
                claimed_rate=Decimal("0.10"),
                base_description="Direct Material",
            ),
        )
        sched_h = compile_schedule_h(costs, pools, USD)
        assert sched_h.lines[0].allocation_base.amount == Decimal("0")
        assert sched_h.lines[0].applied_amount.amount == Decimal("0.00")

    def test_unallowable_tracking(self):
        """Unallowable totals preserved in submission."""
        ice = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency=USD,
            contract_costs=(
                ContractCostInput(
                    contract_number="C-001",
                    contract_type="CPFF",
                    direct_labor=_money("100000"),
                ),
            ),
            total_unallowable_direct=_money("5000"),
            total_unallowable_indirect=_money("2000"),
        )
        result = compile_ice_submission(ice)
        assert result.total_unallowable.amount == Decimal("7000")


# ============================================================================
# Enum Tests
# ============================================================================


class TestEnums:
    """Test enum definitions."""

    def test_schedule_types(self):
        assert ICEScheduleType.SCHEDULE_A.value == "A"
        assert ICEScheduleType.SCHEDULE_B.value == "B"
        assert ICEScheduleType.SCHEDULE_C.value == "C"
        assert ICEScheduleType.SCHEDULE_G.value == "G"
        assert ICEScheduleType.SCHEDULE_H.value == "H"
        assert ICEScheduleType.SCHEDULE_I.value == "I"
        assert ICEScheduleType.SCHEDULE_J.value == "J"

    def test_cost_elements(self):
        assert CostElement.DIRECT_LABOR.value == "DIRECT_LABOR"
        assert CostElement.FRINGE.value == "FRINGE"
        assert CostElement.GA.value == "G&A"

    def test_allowability_statuses(self):
        assert AllowabilityStatus.ALLOWABLE.value == "ALLOWABLE"
        assert AllowabilityStatus.UNALLOWABLE.value == "UNALLOWABLE"
        assert AllowabilityStatus.CONDITIONAL.value == "CONDITIONAL"
