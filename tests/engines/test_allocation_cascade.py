"""
Tests for Cascading Indirect Cost Allocation Engine.

Covers:
- DCAA indirect cost cascade (Fringe → Overhead → G&A)
- Pool-based vs cumulative allocation bases
- Deterministic rounding
- Custom cascade configurations
- Edge cases and error handling
"""

from decimal import Decimal

import pytest

from finance_engines.allocation_cascade import (
    AllocationBase,
    AllocationStep,
    AllocationStepResult,
    build_dcaa_cascade,
    calculate_contract_total,
    execute_cascade,
)
from finance_kernel.domain.values import Money


class TestAllocationStep:
    """Tests for AllocationStep value object."""

    def test_valid_pool_balance_step(self):
        """Creates valid pool balance step."""
        step = AllocationStep(
            pool_from="DIRECT_LABOR",
            pool_to="FRINGE",
            rate_type="fringe",
            base=AllocationBase.POOL_BALANCE,
            description="Apply fringe rate to direct labor",
        )

        assert step.pool_from == "DIRECT_LABOR"
        assert step.pool_to == "FRINGE"
        assert step.rate_type == "fringe"
        assert step.base == AllocationBase.POOL_BALANCE

    def test_valid_cumulative_step(self):
        """Creates valid cumulative step."""
        step = AllocationStep(
            pool_from="TOTAL_DIRECT",
            pool_to="G&A",
            rate_type="g&a",
            base=AllocationBase.CUMULATIVE,
        )

        assert step.base == AllocationBase.CUMULATIVE

    def test_invalid_base_type_raises(self):
        """Rejects invalid base type."""
        with pytest.raises(ValueError, match="Invalid base type"):
            AllocationStep(
                pool_from="X",
                pool_to="Y",
                rate_type="z",
                base="invalid_base",
            )

    def test_step_is_immutable(self):
        """Step is frozen dataclass."""
        step = AllocationStep(
            pool_from="A",
            pool_to="B",
            rate_type="test",
        )

        with pytest.raises(AttributeError):
            step.pool_from = "C"


class TestExecuteCascade:
    """Tests for execute_cascade function."""

    def test_single_step_pool_balance(self):
        """Single step with pool balance base."""
        steps = [
            AllocationStep(
                pool_from="DIRECT_LABOR",
                pool_to="FRINGE",
                rate_type="fringe",
                base=AllocationBase.POOL_BALANCE,
            ),
        ]
        balances = {"DIRECT_LABOR": Money.of("100000.00", "USD")}
        rates = {"fringe": Decimal("0.35")}

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        assert len(results) == 1
        assert results[0].source_balance == Money.of("100000.00", "USD")
        assert results[0].rate_applied == Decimal("0.35")
        assert results[0].amount_allocated == Money.of("35000.00", "USD")
        assert final_balances["FRINGE"] == Money.of("35000.00", "USD")

    def test_cumulative_base_calculation(self):
        """Cumulative base includes previous amounts."""
        steps = [
            AllocationStep(
                pool_from="DIRECT_LABOR",
                pool_to="FRINGE",
                rate_type="fringe",
                base=AllocationBase.POOL_BALANCE,
            ),
            AllocationStep(
                pool_from="DIRECT_COST",
                pool_to="OVERHEAD",
                rate_type="overhead",
                base=AllocationBase.CUMULATIVE,
            ),
        ]
        balances = {"DIRECT_LABOR": Money.of("100000.00", "USD")}
        rates = {
            "fringe": Decimal("0.35"),
            "overhead": Decimal("0.50"),
        }

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        # Step 1: Direct Labor 100,000 × 35% = 35,000 Fringe
        # Cumulative after step 1: 100,000 + 35,000 = 135,000
        # Step 2: Cumulative 135,000 × 50% = 67,500 Overhead
        assert results[0].amount_allocated == Money.of("35000.00", "USD")
        assert results[0].cumulative_base == Money.of("135000.00", "USD")
        assert results[1].amount_allocated == Money.of("67500.00", "USD")
        assert final_balances["FRINGE"] == Money.of("35000.00", "USD")
        assert final_balances["OVERHEAD"] == Money.of("67500.00", "USD")

    def test_missing_pool_treated_as_zero(self):
        """Missing pool balance defaults to zero."""
        steps = [
            AllocationStep(
                pool_from="NONEXISTENT",
                pool_to="TARGET",
                rate_type="rate",
                base=AllocationBase.POOL_BALANCE,
            ),
        ]
        balances = {}
        rates = {"rate": Decimal("0.25")}

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        assert results[0].source_balance == Money.zero("USD")
        assert results[0].amount_allocated == Money.zero("USD")

    def test_missing_rate_treated_as_zero(self):
        """Missing rate defaults to zero (no allocation)."""
        steps = [
            AllocationStep(
                pool_from="DIRECT_LABOR",
                pool_to="TARGET",
                rate_type="nonexistent_rate",
            ),
        ]
        balances = {"DIRECT_LABOR": Money.of("100000.00", "USD")}
        rates = {}

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        assert results[0].rate_applied == Decimal("0")
        assert results[0].amount_allocated == Money.zero("USD")

    def test_input_balances_not_mutated(self):
        """Input pool_balances dict is not mutated."""
        steps = [
            AllocationStep(
                pool_from="A",
                pool_to="B",
                rate_type="rate",
            ),
        ]
        original_balances = {"A": Money.of("1000.00", "USD")}
        rates = {"rate": Decimal("0.10")}

        _, final_balances = execute_cascade(steps, original_balances, rates, "USD")

        # Original should be unchanged
        assert "B" not in original_balances
        # Final should have the new pool
        assert "B" in final_balances

    def test_deterministic_rounding(self):
        """Rounding is deterministic (ROUND_HALF_UP to 2 decimal places)."""
        steps = [
            AllocationStep(
                pool_from="SOURCE",
                pool_to="TARGET",
                rate_type="rate",
            ),
        ]
        # 1000 × 0.333333... should round to 333.33
        balances = {"SOURCE": Money.of("1000.00", "USD")}
        rates = {"rate": Decimal("0.333333333333")}

        results, _ = execute_cascade(steps, balances, rates, "USD")

        assert results[0].amount_allocated == Money.of("333.33", "USD")

    def test_empty_steps_returns_unchanged_balances(self):
        """Empty steps list returns original balances unchanged."""
        balances = {"A": Money.of("1000.00", "USD")}
        rates = {"rate": Decimal("0.10")}

        results, final_balances = execute_cascade([], balances, rates, "USD")

        assert results == []
        assert final_balances == balances

    def test_multiple_steps_accumulate_to_same_target(self):
        """Multiple steps can allocate to the same target pool."""
        steps = [
            AllocationStep(
                pool_from="A",
                pool_to="COMMON",
                rate_type="rate_a",
            ),
            AllocationStep(
                pool_from="B",
                pool_to="COMMON",
                rate_type="rate_b",
            ),
        ]
        balances = {
            "A": Money.of("1000.00", "USD"),
            "B": Money.of("2000.00", "USD"),
        }
        rates = {
            "rate_a": Decimal("0.10"),
            "rate_b": Decimal("0.20"),
        }

        _, final_balances = execute_cascade(steps, balances, rates, "USD")

        # 1000 × 10% + 2000 × 20% = 100 + 400 = 500
        assert final_balances["COMMON"] == Money.of("500.00", "USD")


class TestDCAACascade:
    """Tests for DCAA indirect cost allocation cascade."""

    def test_build_dcaa_cascade_returns_three_steps(self):
        """Standard DCAA cascade has three steps."""
        steps = build_dcaa_cascade()

        assert len(steps) == 3
        assert steps[0].pool_to == "FRINGE"
        assert steps[1].pool_to == "OVERHEAD"
        assert steps[2].pool_to == "G&A"

    def test_full_dcaa_cascade_calculation(self):
        """Full DCAA cascade calculates correctly."""
        steps = build_dcaa_cascade()
        balances = {
            "DIRECT_LABOR": Money.of("100000.00", "USD"),
            "DIRECT_MATERIAL": Money.of("50000.00", "USD"),
        }
        rates = {
            "fringe": Decimal("0.35"),
            "overhead": Decimal("0.45"),
            "g&a": Decimal("0.10"),
        }

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        # Step 1: Fringe on Direct Labor
        # 100,000 × 35% = 35,000
        assert results[0].amount_allocated == Money.of("35000.00", "USD")

        # Step 2: Overhead on cumulative (running total)
        # After step 1: cumulative = 100,000 + 35,000 = 135,000
        # DIRECT_COST pool doesn't exist, but cumulative includes previous
        # 135,000 × 45% = 60,750
        assert results[1].amount_allocated == Money.of("60750.00", "USD")

        # Step 3: G&A on cumulative
        # After step 2: cumulative = 135,000 + 0 + 60,750 = 195,750
        # 195,750 × 10% = 19,575
        assert results[2].amount_allocated == Money.of("19575.00", "USD")

        assert final_balances["FRINGE"] == Money.of("35000.00", "USD")
        assert final_balances["OVERHEAD"] == Money.of("60750.00", "USD")
        assert final_balances["G&A"] == Money.of("19575.00", "USD")

    def test_dcaa_with_all_cost_pools(self):
        """DCAA with all standard cost pools populated."""
        steps = build_dcaa_cascade()
        balances = {
            "DIRECT_LABOR": Money.of("200000.00", "USD"),
            "DIRECT_COST": Money.of("100000.00", "USD"),
            "TOTAL_DIRECT": Money.of("300000.00", "USD"),
        }
        rates = {
            "fringe": Decimal("0.30"),
            "overhead": Decimal("0.40"),
            "g&a": Decimal("0.08"),
        }

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        # Fringe: 200,000 × 30% = 60,000
        assert results[0].amount_allocated == Money.of("60000.00", "USD")

        # Note: The cascade uses cumulative for overhead/G&A steps
        # This is the DCAA pattern - overhead and G&A are on running totals

    def test_dcaa_zero_rates(self):
        """DCAA with zero rates produces no allocations."""
        steps = build_dcaa_cascade()
        balances = {"DIRECT_LABOR": Money.of("100000.00", "USD")}
        rates = {
            "fringe": Decimal("0"),
            "overhead": Decimal("0"),
            "g&a": Decimal("0"),
        }

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        for result in results:
            assert result.amount_allocated == Money.zero("USD")


class TestCalculateContractTotal:
    """Tests for calculate_contract_total function."""

    def test_sums_direct_and_indirect_pools(self):
        """Sums all specified pool balances."""
        pool_balances = {
            "DIRECT_LABOR": Money.of("100000.00", "USD"),
            "DIRECT_MATERIAL": Money.of("50000.00", "USD"),
            "FRINGE": Money.of("35000.00", "USD"),
            "OVERHEAD": Money.of("67500.00", "USD"),
            "G&A": Money.of("25250.00", "USD"),
        }

        total = calculate_contract_total(
            pool_balances,
            direct_pools=["DIRECT_LABOR", "DIRECT_MATERIAL"],
            indirect_pools=["FRINGE", "OVERHEAD", "G&A"],
            currency="USD",
        )

        # 100,000 + 50,000 + 35,000 + 67,500 + 25,250 = 277,750
        assert total == Money.of("277750.00", "USD")

    def test_missing_pools_treated_as_zero(self):
        """Missing pools default to zero."""
        pool_balances = {"DIRECT_LABOR": Money.of("1000.00", "USD")}

        total = calculate_contract_total(
            pool_balances,
            direct_pools=["DIRECT_LABOR", "NONEXISTENT"],
            indirect_pools=["ALSO_NONEXISTENT"],
            currency="USD",
        )

        assert total == Money.of("1000.00", "USD")

    def test_empty_pools_returns_zero(self):
        """Empty pool lists return zero."""
        total = calculate_contract_total(
            pool_balances={},
            direct_pools=[],
            indirect_pools=[],
            currency="USD",
        )

        assert total == Money.zero("USD")


class TestAllocationStepResult:
    """Tests for AllocationStepResult value object."""

    def test_result_captures_all_fields(self):
        """Result contains all allocation details."""
        step = AllocationStep(
            pool_from="A",
            pool_to="B",
            rate_type="rate",
        )
        result = AllocationStepResult(
            step=step,
            source_balance=Money.of("1000.00", "USD"),
            rate_applied=Decimal("0.25"),
            amount_allocated=Money.of("250.00", "USD"),
            cumulative_base=Money.of("1250.00", "USD"),
        )

        assert result.step == step
        assert result.source_balance == Money.of("1000.00", "USD")
        assert result.rate_applied == Decimal("0.25")
        assert result.amount_allocated == Money.of("250.00", "USD")
        assert result.cumulative_base == Money.of("1250.00", "USD")

    def test_result_is_immutable(self):
        """Result is frozen dataclass."""
        step = AllocationStep("A", "B", "rate")
        result = AllocationStepResult(
            step=step,
            source_balance=Money.of("1000.00", "USD"),
            rate_applied=Decimal("0.25"),
            amount_allocated=Money.of("250.00", "USD"),
            cumulative_base=Money.of("1250.00", "USD"),
        )

        with pytest.raises(AttributeError):
            result.rate_applied = Decimal("0.50")


class TestEdgeCases:
    """Edge case and boundary tests."""

    def test_very_small_amounts(self):
        """Handles very small amounts correctly."""
        steps = [
            AllocationStep("A", "B", "rate"),
        ]
        balances = {"A": Money.of("0.01", "USD")}
        rates = {"rate": Decimal("0.10")}

        results, _ = execute_cascade(steps, balances, rates, "USD")

        # 0.01 × 10% = 0.001, rounds to 0.00
        assert results[0].amount_allocated == Money.zero("USD")

    def test_very_large_amounts(self):
        """Handles very large amounts correctly."""
        steps = [
            AllocationStep("A", "B", "rate"),
        ]
        balances = {"A": Money.of("999999999999.99", "USD")}
        rates = {"rate": Decimal("0.01")}

        results, _ = execute_cascade(steps, balances, rates, "USD")

        # Should handle without overflow
        assert results[0].amount_allocated == Money.of("10000000000.00", "USD")

    def test_high_precision_rate(self):
        """High precision rates round correctly."""
        steps = [
            AllocationStep("A", "B", "rate"),
        ]
        balances = {"A": Money.of("10000.00", "USD")}
        rates = {"rate": Decimal("0.123456789")}

        results, _ = execute_cascade(steps, balances, rates, "USD")

        # 10000 × 0.123456789 = 1234.56789, rounds to 1234.57
        assert results[0].amount_allocated == Money.of("1234.57", "USD")

    def test_rate_of_one(self):
        """Rate of 1.0 (100%) works correctly."""
        steps = [
            AllocationStep("A", "B", "rate"),
        ]
        balances = {"A": Money.of("1000.00", "USD")}
        rates = {"rate": Decimal("1.0")}

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        assert results[0].amount_allocated == Money.of("1000.00", "USD")
        assert final_balances["B"] == Money.of("1000.00", "USD")

    def test_rate_greater_than_one(self):
        """Rate > 1.0 works (uncommon but valid)."""
        steps = [
            AllocationStep("A", "B", "rate"),
        ]
        balances = {"A": Money.of("1000.00", "USD")}
        rates = {"rate": Decimal("1.5")}

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        assert results[0].amount_allocated == Money.of("1500.00", "USD")


class TestCurrencyHandling:
    """Tests for currency handling."""

    def test_different_currencies(self):
        """Different currency pools work independently."""
        steps = [
            AllocationStep("USD_LABOR", "USD_FRINGE", "fringe"),
        ]
        balances = {"USD_LABOR": Money.of("1000.00", "USD")}
        rates = {"fringe": Decimal("0.30")}

        results, final_balances = execute_cascade(steps, balances, rates, "USD")

        assert final_balances["USD_FRINGE"].currency.code == "USD"

    def test_eur_currency(self):
        """Works with EUR currency."""
        steps = [
            AllocationStep("A", "B", "rate"),
        ]
        balances = {"A": Money.of("1000.00", "EUR")}
        rates = {"rate": Decimal("0.20")}

        results, final_balances = execute_cascade(steps, balances, rates, "EUR")

        assert results[0].amount_allocated == Money.of("200.00", "EUR")
        assert final_balances["B"] == Money.of("200.00", "EUR")


class TestCustomCascades:
    """Tests for custom cascade configurations."""

    def test_single_rate_cascade(self):
        """Single rate applied to multiple pools."""
        steps = [
            AllocationStep("LABOR", "OVERHEAD", "burden", AllocationBase.POOL_BALANCE),
            AllocationStep("MATERIAL", "OVERHEAD", "burden", AllocationBase.POOL_BALANCE),
        ]
        balances = {
            "LABOR": Money.of("100.00", "USD"),
            "MATERIAL": Money.of("200.00", "USD"),
        }
        rates = {"burden": Decimal("0.50")}

        _, final_balances = execute_cascade(steps, balances, rates, "USD")

        # Both allocate to OVERHEAD: 50 + 100 = 150
        assert final_balances["OVERHEAD"] == Money.of("150.00", "USD")

    def test_four_step_cascade(self):
        """Custom four-step cascade."""
        steps = [
            AllocationStep("STEP1", "STEP2", "r1"),
            AllocationStep("STEP2", "STEP3", "r2"),
            AllocationStep("STEP3", "STEP4", "r3"),
            AllocationStep("STEP4", "STEP5", "r4"),
        ]
        balances = {"STEP1": Money.of("1000.00", "USD")}
        rates = {
            "r1": Decimal("0.10"),
            "r2": Decimal("0.10"),
            "r3": Decimal("0.10"),
            "r4": Decimal("0.10"),
        }

        results, _ = execute_cascade(steps, balances, rates, "USD")

        # All steps should execute
        assert len(results) == 4
        assert results[0].amount_allocated == Money.of("100.00", "USD")
