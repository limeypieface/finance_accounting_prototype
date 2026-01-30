"""
Comprehensive tests for the BillingEngine.

Tests Phase D: Government contract billing calculations.
Covers CPFF, CPIF, CPAF, T&M, LH, FFP, FPI contract types.
"""

from decimal import Decimal

import pytest

from finance_kernel.domain.values import Money
from finance_engines.billing import (
    BillingContractType,
    BillingInput,
    BillingLineItem,
    BillingLineType,
    BillingResult,
    CostBreakdown,
    IndirectRates,
    LaborRateEntry,
    MilestoneEntry,
    RateAdjustmentInput,
    RateAdjustmentResult,
    calculate_billing,
    calculate_fee,
    calculate_indirect_costs,
    calculate_rate_adjustment,
    apply_funding_limit,
    apply_withholding,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def standard_costs():
    """Standard direct cost breakdown for testing."""
    return CostBreakdown(
        direct_labor=Money.of("100000.00", "USD"),
        direct_material=Money.of("50000.00", "USD"),
        subcontract=Money.of("30000.00", "USD"),
        travel=Money.of("5000.00", "USD"),
        odc=Money.of("3000.00", "USD"),
    )


@pytest.fixture
def standard_rates():
    """Standard DCAA indirect rates."""
    return IndirectRates(
        fringe=Decimal("0.35"),
        overhead=Decimal("0.45"),
        ga=Decimal("0.10"),
    )


@pytest.fixture
def cpff_input(standard_costs, standard_rates):
    """Standard CPFF billing input."""
    return BillingInput(
        contract_type=BillingContractType.CPFF,
        currency="USD",
        cost_breakdown=standard_costs,
        indirect_rates=standard_rates,
        fee_rate=Decimal("0.08"),
    )


# ============================================================================
# Value Object Tests
# ============================================================================


class TestCostBreakdown:
    """Tests for CostBreakdown value object."""

    def test_total_direct_all_categories(self, standard_costs):
        """Total direct should sum all categories."""
        expected = Decimal("188000.00")  # 100K + 50K + 30K + 5K + 3K
        assert standard_costs.total_direct.amount == expected

    def test_total_direct_labor_only(self):
        """Total direct with only labor should equal labor."""
        costs = CostBreakdown(direct_labor=Money.of("100000.00", "USD"))
        assert costs.total_direct.amount == Decimal("100000.00")

    def test_negative_labor_rejected(self):
        """Negative direct labor should raise ValueError."""
        with pytest.raises(ValueError, match="direct_labor"):
            CostBreakdown(direct_labor=Money.of("-100.00", "USD"))

    def test_negative_material_rejected(self):
        """Negative material should raise ValueError."""
        with pytest.raises(ValueError, match="direct_material"):
            CostBreakdown(
                direct_labor=Money.of("100.00", "USD"),
                direct_material=Money.of("-50.00", "USD"),
            )

    def test_zero_labor_allowed(self):
        """Zero labor should be allowed."""
        costs = CostBreakdown(direct_labor=Money.of("0.00", "USD"))
        assert costs.total_direct.amount == Decimal("0.00")


class TestIndirectRates:
    """Tests for IndirectRates value object."""

    def test_default_rates_zero(self):
        """Default rates should be zero."""
        rates = IndirectRates()
        assert rates.fringe == Decimal("0")
        assert rates.overhead == Decimal("0")
        assert rates.ga == Decimal("0")

    def test_negative_rate_rejected(self):
        """Negative rates should raise ValueError."""
        with pytest.raises(ValueError, match="fringe"):
            IndirectRates(fringe=Decimal("-0.10"))

    def test_valid_rates_accepted(self, standard_rates):
        """Standard rates should be accepted."""
        assert standard_rates.fringe == Decimal("0.35")
        assert standard_rates.overhead == Decimal("0.45")
        assert standard_rates.ga == Decimal("0.10")


class TestLaborRateEntry:
    """Tests for LaborRateEntry value object."""

    def test_amount_calculation(self):
        """Amount should be hours * billing_rate."""
        entry = LaborRateEntry(
            labor_category="Engineer III",
            hours=Decimal("160"),
            billing_rate=Decimal("125.00"),
        )
        assert entry.amount == Decimal("20000.00")

    def test_fractional_hours(self):
        """Fractional hours should be handled with rounding."""
        entry = LaborRateEntry(
            labor_category="Analyst",
            hours=Decimal("7.5"),
            billing_rate=Decimal("95.33"),
        )
        # 7.5 * 95.33 = 714.975 -> rounded to 714.98
        assert entry.amount == Decimal("714.98")

    def test_negative_hours_rejected(self):
        """Negative hours should raise ValueError."""
        with pytest.raises(ValueError, match="hours"):
            LaborRateEntry(
                labor_category="Test",
                hours=Decimal("-8"),
                billing_rate=Decimal("100"),
            )

    def test_negative_rate_rejected(self):
        """Negative billing rate should raise ValueError."""
        with pytest.raises(ValueError, match="billing_rate"):
            LaborRateEntry(
                labor_category="Test",
                hours=Decimal("8"),
                billing_rate=Decimal("-100"),
            )


class TestMilestoneEntry:
    """Tests for MilestoneEntry value object."""

    def test_valid_milestone(self):
        """Valid milestone should be created."""
        ms = MilestoneEntry(
            milestone_id="MS-001",
            description="Design Review",
            amount=Decimal("50000.00"),
            completion_pct=Decimal("100"),
        )
        assert ms.amount == Decimal("50000.00")
        assert ms.completion_pct == Decimal("100")

    def test_negative_amount_rejected(self):
        """Negative milestone amount should raise ValueError."""
        with pytest.raises(ValueError, match="amount"):
            MilestoneEntry(
                milestone_id="MS-001",
                description="Test",
                amount=Decimal("-1000"),
                completion_pct=Decimal("100"),
            )

    def test_invalid_completion_pct(self):
        """Completion > 100 should raise ValueError."""
        with pytest.raises(ValueError, match="completion_pct"):
            MilestoneEntry(
                milestone_id="MS-001",
                description="Test",
                amount=Decimal("1000"),
                completion_pct=Decimal("101"),
            )


# ============================================================================
# Indirect Cost Calculation Tests
# ============================================================================


class TestIndirectCostCalculation:
    """Tests for calculate_indirect_costs function."""

    def test_standard_cascade(self, standard_costs, standard_rates):
        """Standard cascade should apply rates correctly."""
        fringe, overhead, ga, mat_handling = calculate_indirect_costs(
            standard_costs, standard_rates, "USD"
        )

        # Fringe = 100,000 * 0.35 = 35,000
        assert fringe.amount == Decimal("35000.00")

        # Overhead = (100,000 + 35,000) * 0.45 = 60,750
        assert overhead.amount == Decimal("60750.00")

        # G&A = (188,000 + 35,000 + 60,750) * 0.10 = 28,375
        assert ga.amount == Decimal("28375.00")

        # Material handling = 0 (rate is 0)
        assert mat_handling.amount == Decimal("0")

    def test_zero_rates(self, standard_costs):
        """Zero rates should produce zero indirect costs."""
        rates = IndirectRates()
        fringe, overhead, ga, mat_handling = calculate_indirect_costs(
            standard_costs, rates, "USD"
        )

        assert fringe.amount == Decimal("0")
        assert overhead.amount == Decimal("0")
        assert ga.amount == Decimal("0")
        assert mat_handling.amount == Decimal("0")

    def test_fringe_on_labor_only(self):
        """Fringe should only apply to direct labor."""
        costs = CostBreakdown(
            direct_labor=Money.of("100.00", "USD"),
            direct_material=Money.of("500.00", "USD"),
        )
        rates = IndirectRates(fringe=Decimal("0.30"))
        fringe, _, _, _ = calculate_indirect_costs(costs, rates, "USD")

        # Fringe = 100 * 0.30 = 30, NOT (100 + 500) * 0.30
        assert fringe.amount == Decimal("30.00")

    def test_material_handling_rate(self):
        """Material handling should apply to direct material only."""
        costs = CostBreakdown(
            direct_labor=Money.of("100.00", "USD"),
            direct_material=Money.of("500.00", "USD"),
        )
        rates = IndirectRates(material_handling=Decimal("0.05"))
        _, _, _, mat_handling = calculate_indirect_costs(costs, rates, "USD")

        # Material handling = 500 * 0.05 = 25
        assert mat_handling.amount == Decimal("25.00")


# ============================================================================
# Fee Calculation Tests
# ============================================================================


class TestFeeCalculation:
    """Tests for calculate_fee function."""

    def test_basic_fee(self):
        """Basic fee calculation without ceiling."""
        fee, ceiling_hit = calculate_fee(
            total_cost=Decimal("100000.00"),
            fee_rate=Decimal("0.08"),
            fee_ceiling=None,
            cumulative_fee=Decimal("0"),
            currency="USD",
        )

        assert fee.amount == Decimal("8000.00")
        assert ceiling_hit is False

    def test_fee_within_ceiling(self):
        """Fee within ceiling should not be limited."""
        fee, ceiling_hit = calculate_fee(
            total_cost=Decimal("100000.00"),
            fee_rate=Decimal("0.08"),
            fee_ceiling=Decimal("50000.00"),
            cumulative_fee=Decimal("0"),
            currency="USD",
        )

        assert fee.amount == Decimal("8000.00")
        assert ceiling_hit is False

    def test_fee_exceeds_ceiling(self):
        """Fee exceeding ceiling should be capped."""
        fee, ceiling_hit = calculate_fee(
            total_cost=Decimal("100000.00"),
            fee_rate=Decimal("0.08"),
            fee_ceiling=Decimal("10000.00"),
            cumulative_fee=Decimal("5000.00"),
            currency="USD",
        )

        # Remaining ceiling = 10000 - 5000 = 5000
        # Calculated fee = 8000 > 5000
        assert fee.amount == Decimal("5000.00")
        assert ceiling_hit is True

    def test_fee_ceiling_already_reached(self):
        """Fee should be zero if ceiling already reached."""
        fee, ceiling_hit = calculate_fee(
            total_cost=Decimal("100000.00"),
            fee_rate=Decimal("0.08"),
            fee_ceiling=Decimal("10000.00"),
            cumulative_fee=Decimal("10000.00"),
            currency="USD",
        )

        assert fee.amount == Decimal("0")
        assert ceiling_hit is True

    def test_fee_ceiling_exceeded_cumulative(self):
        """Fee should be zero if cumulative already exceeds ceiling."""
        fee, ceiling_hit = calculate_fee(
            total_cost=Decimal("100000.00"),
            fee_rate=Decimal("0.08"),
            fee_ceiling=Decimal("10000.00"),
            cumulative_fee=Decimal("12000.00"),
            currency="USD",
        )

        assert fee.amount == Decimal("0")
        assert ceiling_hit is True


# ============================================================================
# Withholding Tests
# ============================================================================


class TestWithholding:
    """Tests for apply_withholding function."""

    def test_standard_withholding(self):
        """Standard 15% DCAA withholding."""
        withholding, net = apply_withholding(
            Decimal("100000.00"), Decimal("0.15"), "USD"
        )

        assert withholding.amount == Decimal("15000.00")
        assert net.amount == Decimal("85000.00")

    def test_zero_withholding(self):
        """Zero withholding should return full amount."""
        withholding, net = apply_withholding(
            Decimal("100000.00"), Decimal("0"), "USD"
        )

        assert withholding.amount == Decimal("0")
        assert net.amount == Decimal("100000.00")

    def test_rounding(self):
        """Withholding should round correctly."""
        withholding, net = apply_withholding(
            Decimal("33333.33"), Decimal("0.15"), "USD"
        )

        # 33333.33 * 0.15 = 5000.00 (exact)
        assert withholding.amount == Decimal("5000.00")
        assert net.amount == Decimal("28333.33")


# ============================================================================
# Funding Limit Tests
# ============================================================================


class TestFundingLimit:
    """Tests for apply_funding_limit function."""

    def test_within_funding(self):
        """Billing within funding should not be limited."""
        amount, limited = apply_funding_limit(
            Decimal("50000.00"), Decimal("100000.00"),
            Decimal("200000.00"), "USD"
        )

        assert amount.amount == Decimal("50000.00")
        assert limited is False

    def test_exceeds_funding(self):
        """Billing exceeding remaining funding should be capped."""
        amount, limited = apply_funding_limit(
            Decimal("50000.00"), Decimal("180000.00"),
            Decimal("200000.00"), "USD"
        )

        # Remaining = 200000 - 180000 = 20000
        assert amount.amount == Decimal("20000.00")
        assert limited is True

    def test_no_funding_limit(self):
        """No funding limit should not cap billing."""
        amount, limited = apply_funding_limit(
            Decimal("1000000.00"), Decimal("0"),
            None, "USD"
        )

        assert amount.amount == Decimal("1000000.00")
        assert limited is False

    def test_fully_funded(self):
        """Fully consumed funding should produce zero billing."""
        amount, limited = apply_funding_limit(
            Decimal("50000.00"), Decimal("200000.00"),
            Decimal("200000.00"), "USD"
        )

        assert amount.amount == Decimal("0")
        assert limited is True


# ============================================================================
# CPFF Billing Tests
# ============================================================================


class TestCPFFBilling:
    """Tests for Cost Plus Fixed Fee billing."""

    def test_basic_cpff(self, cpff_input):
        """Basic CPFF billing should calculate all components."""
        result = calculate_billing(cpff_input)

        assert result.contract_type == BillingContractType.CPFF
        assert result.total_direct_cost.amount == Decimal("188000.00")
        assert result.total_indirect_cost.amount > 0
        assert result.fee_amount.amount > 0
        assert result.gross_billing.amount > 0
        assert result.net_billing.amount == result.gross_billing.amount  # No withholding

    def test_cpff_with_withholding(self, standard_costs, standard_rates):
        """CPFF with withholding should reduce net billing."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=standard_costs,
            indirect_rates=standard_rates,
            fee_rate=Decimal("0.08"),
            withholding_pct=Decimal("0.15"),
        )
        result = calculate_billing(bi)

        assert result.withholding_amount.amount > 0
        assert result.net_billing.amount < result.gross_billing.amount
        expected_net = result.gross_billing.amount - result.withholding_amount.amount
        assert result.net_billing.amount == expected_net

    def test_cpff_line_items(self, cpff_input):
        """CPFF should produce correct line items."""
        result = calculate_billing(cpff_input)

        line_types = [li.line_type for li in result.line_items]

        assert BillingLineType.DIRECT_LABOR in line_types
        assert BillingLineType.DIRECT_MATERIAL in line_types
        assert BillingLineType.SUBCONTRACT in line_types
        assert BillingLineType.TRAVEL in line_types
        assert BillingLineType.ODC in line_types
        assert BillingLineType.FRINGE in line_types
        assert BillingLineType.OVERHEAD in line_types
        assert BillingLineType.GA in line_types
        assert BillingLineType.FEE in line_types

    def test_cpff_indirect_amounts(self, cpff_input):
        """CPFF indirect amounts should match cascade calculation."""
        result = calculate_billing(cpff_input)

        fringe_line = next(
            li for li in result.line_items if li.line_type == BillingLineType.FRINGE
        )
        overhead_line = next(
            li for li in result.line_items if li.line_type == BillingLineType.OVERHEAD
        )
        ga_line = next(
            li for li in result.line_items if li.line_type == BillingLineType.GA
        )

        # Fringe = 100,000 * 0.35 = 35,000
        assert fringe_line.amount.amount == Decimal("35000.00")
        assert fringe_line.rate == Decimal("0.35")

        # Overhead = (100,000 + 35,000) * 0.45 = 60,750
        assert overhead_line.amount.amount == Decimal("60750.00")

        # G&A = (188,000 + 35,000 + 60,750) * 0.10 = 28,375
        assert ga_line.amount.amount == Decimal("28375.00")

    def test_cpff_fee_amount(self, cpff_input):
        """CPFF fee should be calculated on total cost."""
        result = calculate_billing(cpff_input)

        fee_line = next(
            li for li in result.line_items if li.line_type == BillingLineType.FEE
        )

        expected_fee = (result.total_cost.amount * Decimal("0.08")).quantize(Decimal("0.01"))
        assert fee_line.amount.amount == expected_fee

    def test_cpff_funding_limited(self, standard_costs, standard_rates):
        """CPFF should respect funding limit."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=standard_costs,
            indirect_rates=standard_rates,
            fee_rate=Decimal("0.08"),
            funding_limit=Decimal("200000.00"),
            cumulative_billed=Decimal("150000.00"),
        )
        result = calculate_billing(bi)

        assert result.funding_limited is True
        assert result.gross_billing.amount <= Decimal("50000.00")

    def test_cpff_ceiling_limited(self, standard_costs, standard_rates):
        """CPFF should respect ceiling amount."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=standard_costs,
            indirect_rates=standard_rates,
            fee_rate=Decimal("0.08"),
            ceiling_amount=Decimal("300000.00"),
            cumulative_billed=Decimal("280000.00"),
        )
        result = calculate_billing(bi)

        assert result.ceiling_limited is True
        assert result.gross_billing.amount <= Decimal("20000.00")

    def test_cpff_missing_costs_raises(self, standard_rates):
        """CPFF without cost breakdown should raise ValueError."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            indirect_rates=standard_rates,
            fee_rate=Decimal("0.08"),
        )
        with pytest.raises(ValueError, match="cost_breakdown"):
            calculate_billing(bi)

    def test_cpff_missing_rates_raises(self, standard_costs):
        """CPFF without indirect rates should raise ValueError."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=standard_costs,
            fee_rate=Decimal("0.08"),
        )
        with pytest.raises(ValueError, match="indirect_rates"):
            calculate_billing(bi)


class TestCPIFBilling:
    """Tests for Cost Plus Incentive Fee billing."""

    def test_cpif_calculates(self, standard_costs, standard_rates):
        """CPIF should produce valid billing result."""
        bi = BillingInput(
            contract_type=BillingContractType.CPIF,
            currency="USD",
            cost_breakdown=standard_costs,
            indirect_rates=standard_rates,
            fee_rate=Decimal("0.10"),
        )
        result = calculate_billing(bi)

        assert result.contract_type == BillingContractType.CPIF
        assert result.fee_amount.amount > 0


class TestCPAFBilling:
    """Tests for Cost Plus Award Fee billing."""

    def test_cpaf_calculates(self, standard_costs, standard_rates):
        """CPAF should produce valid billing result."""
        bi = BillingInput(
            contract_type=BillingContractType.CPAF,
            currency="USD",
            cost_breakdown=standard_costs,
            indirect_rates=standard_rates,
            fee_rate=Decimal("0.05"),
        )
        result = calculate_billing(bi)

        assert result.contract_type == BillingContractType.CPAF
        assert result.fee_amount.amount > 0


# ============================================================================
# T&M Billing Tests
# ============================================================================


class TestTMBilling:
    """Tests for Time & Materials billing."""

    def test_basic_tm(self):
        """Basic T&M billing with labor and materials."""
        bi = BillingInput(
            contract_type=BillingContractType.TM,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
                LaborRateEntry("Analyst", Decimal("80"), Decimal("95.00")),
            ),
            material_passthrough=Money.of("10000.00", "USD"),
        )
        result = calculate_billing(bi)

        assert result.contract_type == BillingContractType.TM

        # Engineer: 160 * 125 = 20,000
        # Analyst: 80 * 95 = 7,600
        # Materials: 10,000
        # Total: 37,600
        assert result.total_direct_cost.amount == Decimal("37600.00")
        assert result.total_indirect_cost.amount == Decimal("0")
        assert result.fee_amount.amount == Decimal("0")

    def test_tm_labor_only(self):
        """T&M with labor only (no materials)."""
        bi = BillingInput(
            contract_type=BillingContractType.TM,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
            ),
        )
        result = calculate_billing(bi)

        assert result.total_direct_cost.amount == Decimal("20000.00")

    def test_tm_line_items(self):
        """T&M should produce per-category labor lines."""
        bi = BillingInput(
            contract_type=BillingContractType.TM,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
                LaborRateEntry("Analyst", Decimal("80"), Decimal("95.00")),
            ),
        )
        result = calculate_billing(bi)

        labor_lines = [
            li for li in result.line_items
            if li.line_type == BillingLineType.DIRECT_LABOR
        ]
        assert len(labor_lines) == 2

        # Check rate and quantity are captured
        eng_line = next(li for li in labor_lines if "Engineer" in li.description)
        assert eng_line.rate == Decimal("125.00")
        assert eng_line.quantity == Decimal("160")

    def test_tm_funding_limit(self):
        """T&M should respect funding limit."""
        bi = BillingInput(
            contract_type=BillingContractType.TM,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
            ),
            funding_limit=Decimal("15000.00"),
        )
        result = calculate_billing(bi)

        assert result.funding_limited is True
        assert result.gross_billing.amount == Decimal("15000.00")

    def test_tm_ceiling_limit(self):
        """T&M should respect ceiling amount."""
        bi = BillingInput(
            contract_type=BillingContractType.TM,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
            ),
            ceiling_amount=Decimal("18000.00"),
            cumulative_billed=Decimal("0"),
        )
        result = calculate_billing(bi)

        assert result.ceiling_limited is True
        assert result.gross_billing.amount == Decimal("18000.00")


# ============================================================================
# LH Billing Tests
# ============================================================================


class TestLHBilling:
    """Tests for Labor Hour billing."""

    def test_basic_lh(self):
        """Basic LH billing with labor only."""
        bi = BillingInput(
            contract_type=BillingContractType.LH,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
                LaborRateEntry("Analyst", Decimal("80"), Decimal("95.00")),
            ),
        )
        result = calculate_billing(bi)

        assert result.contract_type == BillingContractType.LH
        assert result.total_direct_cost.amount == Decimal("27600.00")
        assert result.fee_amount.amount == Decimal("0")

    def test_lh_no_materials(self):
        """LH should not include materials (labor only)."""
        bi = BillingInput(
            contract_type=BillingContractType.LH,
            currency="USD",
            labor_entries=(
                LaborRateEntry("Engineer III", Decimal("160"), Decimal("125.00")),
            ),
        )
        result = calculate_billing(bi)

        material_lines = [
            li for li in result.line_items
            if li.line_type == BillingLineType.DIRECT_MATERIAL
        ]
        assert len(material_lines) == 0


# ============================================================================
# FFP Billing Tests
# ============================================================================


class TestFFPBilling:
    """Tests for Firm Fixed Price billing."""

    def test_basic_ffp_milestone(self):
        """FFP should bill completed milestones."""
        bi = BillingInput(
            contract_type=BillingContractType.FFP,
            currency="USD",
            milestones=(
                MilestoneEntry("MS-001", "Design Review", Decimal("50000.00"), Decimal("100")),
                MilestoneEntry("MS-002", "CDR", Decimal("75000.00"), Decimal("100")),
            ),
        )
        result = calculate_billing(bi)

        assert result.contract_type == BillingContractType.FFP
        assert result.total_direct_cost.amount == Decimal("125000.00")
        assert result.fee_amount.amount == Decimal("0")

    def test_ffp_incomplete_milestone_excluded(self):
        """FFP should not bill incomplete milestones."""
        bi = BillingInput(
            contract_type=BillingContractType.FFP,
            currency="USD",
            milestones=(
                MilestoneEntry("MS-001", "Design Review", Decimal("50000.00"), Decimal("100")),
                MilestoneEntry("MS-002", "CDR", Decimal("75000.00"), Decimal("50")),
            ),
        )
        result = calculate_billing(bi)

        # Only MS-001 (100% complete) should be billed
        assert result.total_direct_cost.amount == Decimal("50000.00")
        assert len(result.line_items) == 1

    def test_ffp_no_milestones(self):
        """FFP with no milestones should produce zero billing."""
        bi = BillingInput(
            contract_type=BillingContractType.FFP,
            currency="USD",
            milestones=(),
        )
        result = calculate_billing(bi)

        assert result.total_direct_cost.amount == Decimal("0")
        assert result.gross_billing.amount == Decimal("0")


# ============================================================================
# FPI Billing Tests
# ============================================================================


class TestFPIBilling:
    """Tests for Fixed Price Incentive billing."""

    def test_basic_fpi(self):
        """FPI should bill like FFP with milestones."""
        bi = BillingInput(
            contract_type=BillingContractType.FPI,
            currency="USD",
            milestones=(
                MilestoneEntry("MS-001", "Design", Decimal("100000.00"), Decimal("100")),
            ),
        )
        result = calculate_billing(bi)

        assert result.contract_type == BillingContractType.FPI
        assert result.total_direct_cost.amount == Decimal("100000.00")


# ============================================================================
# Rate Adjustment Tests
# ============================================================================


class TestRateAdjustment:
    """Tests for rate adjustment calculations."""

    def test_underbilled_adjustment(self):
        """Final rate > provisional should produce positive adjustment."""
        result = calculate_rate_adjustment(RateAdjustmentInput(
            indirect_type="OVERHEAD",
            provisional_rate=Decimal("0.45"),
            final_rate=Decimal("0.50"),
            base_amount=Decimal("500000.00"),
            currency="USD",
        ))

        assert result.provisional_amount.amount == Decimal("225000.00")
        assert result.final_amount.amount == Decimal("250000.00")
        assert result.adjustment_amount.amount == Decimal("25000.00")
        assert result.is_underbilled is True

    def test_overbilled_adjustment(self):
        """Final rate < provisional should produce negative adjustment."""
        result = calculate_rate_adjustment(RateAdjustmentInput(
            indirect_type="OVERHEAD",
            provisional_rate=Decimal("0.50"),
            final_rate=Decimal("0.45"),
            base_amount=Decimal("500000.00"),
            currency="USD",
        ))

        assert result.adjustment_amount.amount == Decimal("-25000.00")
        assert result.is_underbilled is False

    def test_no_adjustment_needed(self):
        """Same rates should produce zero adjustment."""
        result = calculate_rate_adjustment(RateAdjustmentInput(
            indirect_type="FRINGE",
            provisional_rate=Decimal("0.35"),
            final_rate=Decimal("0.35"),
            base_amount=Decimal("100000.00"),
            currency="USD",
        ))

        assert result.adjustment_amount.amount == Decimal("0")
        assert result.is_underbilled is False

    def test_small_rate_difference(self):
        """Small rate differences should still produce exact adjustments."""
        result = calculate_rate_adjustment(RateAdjustmentInput(
            indirect_type="G&A",
            provisional_rate=Decimal("0.100"),
            final_rate=Decimal("0.102"),
            base_amount=Decimal("1000000.00"),
            currency="USD",
        ))

        # 1,000,000 * 0.002 = 2,000
        assert result.adjustment_amount.amount == Decimal("2000.00")


# ============================================================================
# Determinism Tests
# ============================================================================


class TestDeterminism:
    """Test that billing calculations are deterministic."""

    def test_same_input_same_output(self, cpff_input):
        """Same input should always produce same output."""
        result1 = calculate_billing(cpff_input)
        result2 = calculate_billing(cpff_input)

        assert result1.total_direct_cost == result2.total_direct_cost
        assert result1.total_indirect_cost == result2.total_indirect_cost
        assert result1.total_cost == result2.total_cost
        assert result1.fee_amount == result2.fee_amount
        assert result1.gross_billing == result2.gross_billing
        assert result1.net_billing == result2.net_billing

    def test_line_items_immutable(self, cpff_input):
        """Line items should be immutable tuples."""
        result = calculate_billing(cpff_input)

        assert isinstance(result.line_items, tuple)
        # BillingLineItem is frozen dataclass
        with pytest.raises(AttributeError):
            result.line_items[0].amount = Money.of("0", "USD")

    def test_result_immutable(self, cpff_input):
        """BillingResult should be immutable."""
        result = calculate_billing(cpff_input)

        with pytest.raises(AttributeError):
            result.total_cost = Money.of("0", "USD")


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_cost_billing(self):
        """Zero costs should produce zero billing."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=CostBreakdown(
                direct_labor=Money.of("0.00", "USD"),
            ),
            indirect_rates=IndirectRates(),
            fee_rate=Decimal("0.08"),
        )
        result = calculate_billing(bi)

        assert result.total_direct_cost.amount == Decimal("0")
        assert result.total_cost.amount == Decimal("0")
        assert result.fee_amount.amount == Decimal("0")
        assert result.gross_billing.amount == Decimal("0")

    def test_labor_only_cost_plus(self):
        """Cost-plus with only labor should still calculate indirects."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=CostBreakdown(
                direct_labor=Money.of("100000.00", "USD"),
            ),
            indirect_rates=IndirectRates(
                fringe=Decimal("0.35"),
                overhead=Decimal("0.45"),
                ga=Decimal("0.10"),
            ),
            fee_rate=Decimal("0.08"),
        )
        result = calculate_billing(bi)

        assert result.total_direct_cost.amount == Decimal("100000.00")
        assert result.total_indirect_cost.amount > 0
        assert result.fee_amount.amount > 0

    def test_very_small_amounts(self):
        """Very small amounts should be handled without precision loss."""
        bi = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=CostBreakdown(
                direct_labor=Money.of("0.01", "USD"),
            ),
            indirect_rates=IndirectRates(
                fringe=Decimal("0.35"),
            ),
            fee_rate=Decimal("0.08"),
        )
        result = calculate_billing(bi)

        # Should not crash on small amounts
        assert result.total_direct_cost.amount == Decimal("0.01")
