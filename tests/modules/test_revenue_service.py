"""
Tests for Revenue Recognition Module — ASC 606.

Validates:
- Step 1: identify_contract (pure domain record)
- Step 2: identify_performance_obligations (pure domain records)
- Step 3: determine_transaction_price (uses helpers)
- Step 4: allocate_transaction_price (uses AllocationEngine, posts)
- Step 5: recognize_revenue (3 methods, posts)
- modify_contract (modification type assessment, posts)
- update_variable_consideration (posts)
- get_contract_status, get_unbilled_revenue, get_deferred_revenue (queries)
- helpers: pure calculation tests
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.revenue.helpers import (
    assess_modification_type,
    calculate_ssp,
    estimate_variable_consideration,
    measure_progress_input,
    measure_progress_output,
)
from finance_modules.revenue.models import (
    ContractModification,
    ContractStatus,
    ModificationType,
    PerformanceObligation,
    RecognitionMethod,
    RecognitionSchedule,
    RevenueContract,
    SSPAllocation,
    TransactionPrice,
)
from finance_services.workflow_executor import WorkflowExecutor
from finance_modules.revenue.service import RevenueRecognitionService
from tests.modules.conftest import TEST_CUSTOMER_ID, TEST_REVENUE_CONTRACT_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def revenue_service(
    session, module_role_resolver, workflow_executor, deterministic_clock, register_modules,
    party_service, test_actor_party,
):
    """Provide RevenueRecognitionService for integration testing. party_service + test_actor_party for G14."""
    return RevenueRecognitionService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestRevenueModels:
    """Verify revenue models are frozen dataclasses with correct defaults."""

    def test_revenue_contract_creation(self):
        """RevenueContract is a frozen dataclass."""
        contract = RevenueContract(
            id=uuid4(),
            customer_id=uuid4(),
            contract_number="C-001",
            start_date=date(2024, 1, 1),
            total_consideration=Decimal("100000.00"),
        )
        assert contract.status == ContractStatus.IDENTIFIED
        assert contract.currency == "USD"
        assert contract.variable_consideration == Decimal("0")

    def test_revenue_contract_is_frozen(self):
        """RevenueContract is immutable."""
        contract = RevenueContract(
            id=uuid4(), customer_id=uuid4(),
            contract_number="C-001", start_date=date(2024, 1, 1),
        )
        with pytest.raises(AttributeError):
            contract.status = ContractStatus.ACTIVE  # type: ignore[misc]

    def test_performance_obligation_creation(self):
        """PerformanceObligation has correct defaults."""
        po = PerformanceObligation(
            id=uuid4(),
            contract_id=uuid4(),
            description="Software license",
        )
        assert po.is_distinct is True
        assert po.standalone_selling_price == Decimal("0")
        assert po.recognition_method == RecognitionMethod.POINT_IN_TIME
        assert po.satisfied is False

    def test_transaction_price_creation(self):
        """TransactionPrice is a frozen dataclass."""
        tp = TransactionPrice(
            id=uuid4(),
            contract_id=uuid4(),
            base_price=Decimal("50000.00"),
            variable_consideration=Decimal("10000.00"),
            total_transaction_price=Decimal("60000.00"),
        )
        assert tp.total_transaction_price == Decimal("60000.00")
        assert tp.financing_component == Decimal("0")

    def test_ssp_allocation_creation(self):
        """SSPAllocation is a frozen dataclass."""
        alloc = SSPAllocation(
            id=uuid4(),
            contract_id=uuid4(),
            obligation_id=uuid4(),
            standalone_selling_price=Decimal("30000.00"),
            allocated_amount=Decimal("25000.00"),
            allocation_percentage=Decimal("0.50"),
        )
        assert alloc.allocation_percentage == Decimal("0.50")

    def test_contract_modification_creation(self):
        """ContractModification is a frozen dataclass."""
        mod = ContractModification(
            id=uuid4(),
            contract_id=uuid4(),
            modification_date=date(2024, 6, 1),
            modification_type=ModificationType.CUMULATIVE_CATCH_UP,
            description="Added new deliverable",
            price_change=Decimal("20000.00"),
        )
        assert mod.modification_type == ModificationType.CUMULATIVE_CATCH_UP

    def test_recognition_schedule_creation(self):
        """RecognitionSchedule is a frozen dataclass."""
        sched = RecognitionSchedule(
            id=uuid4(),
            contract_id=uuid4(),
            obligation_id=uuid4(),
            period="2024-01",
            amount=Decimal("5000.00"),
        )
        assert sched.recognized is False
        assert sched.recognized_date is None


# =============================================================================
# Helper Tests — Pure Calculations
# =============================================================================


class TestRevenueHelpers:
    """Test pure calculation functions in helpers.py."""

    def test_variable_consideration_expected_value(self):
        """Expected value method sums probability-weighted amounts."""
        scenarios = [
            {"probability": "0.60", "amount": "100000"},
            {"probability": "0.30", "amount": "80000"},
            {"probability": "0.10", "amount": "50000"},
        ]
        result = estimate_variable_consideration(
            base_amount=Decimal("100000"),
            scenarios=scenarios,
            method="expected_value",
        )
        # 0.60*100000 + 0.30*80000 + 0.10*50000 = 60000 + 24000 + 5000 = 89000
        assert result == Decimal("89000")

    def test_variable_consideration_most_likely(self):
        """Most likely amount selects highest-probability scenario."""
        scenarios = [
            {"probability": "0.60", "amount": "100000"},
            {"probability": "0.30", "amount": "80000"},
            {"probability": "0.10", "amount": "50000"},
        ]
        result = estimate_variable_consideration(
            base_amount=Decimal("100000"),
            scenarios=scenarios,
            method="most_likely_amount",
        )
        assert result == Decimal("100000")

    def test_variable_consideration_empty_scenarios(self):
        """Empty scenarios returns 0 for most_likely."""
        result = estimate_variable_consideration(
            base_amount=Decimal("100000"),
            scenarios=[],
            method="most_likely_amount",
        )
        assert result == Decimal("0")

    def test_calculate_ssp_observable(self):
        """Observable price takes priority."""
        result = calculate_ssp(
            observable_price=Decimal("50000"),
            adjusted_market_price=Decimal("45000"),
        )
        assert result == Decimal("50000")

    def test_calculate_ssp_adjusted_market(self):
        """Adjusted market used when no observable."""
        result = calculate_ssp(adjusted_market_price=Decimal("45000"))
        assert result == Decimal("45000")

    def test_calculate_ssp_cost_plus(self):
        """Cost plus margin calculation."""
        result = calculate_ssp(
            expected_cost_plus_margin=(Decimal("30000"), Decimal("0.25")),
        )
        assert result == Decimal("37500.00")

    def test_calculate_ssp_residual(self):
        """Residual approach."""
        result = calculate_ssp(
            residual_total=Decimal("100000"),
            residual_other_ssp_sum=Decimal("70000"),
        )
        assert result == Decimal("30000")

    def test_calculate_ssp_no_data(self):
        """No data returns 0."""
        result = calculate_ssp()
        assert result == Decimal("0")

    def test_measure_progress_input(self):
        """Input method: costs incurred / total estimated costs."""
        progress = measure_progress_input(
            costs_incurred=Decimal("60000"),
            total_estimated_costs=Decimal("100000"),
        )
        assert progress == Decimal("0.6")

    def test_measure_progress_input_zero_total(self):
        """Zero total costs returns 0 progress."""
        progress = measure_progress_input(
            costs_incurred=Decimal("10000"),
            total_estimated_costs=Decimal("0"),
        )
        assert progress == Decimal("0")

    def test_measure_progress_input_capped_at_one(self):
        """Progress is capped at 1.0."""
        progress = measure_progress_input(
            costs_incurred=Decimal("120000"),
            total_estimated_costs=Decimal("100000"),
        )
        assert progress == Decimal("1")

    def test_measure_progress_output(self):
        """Output method: units delivered / total units."""
        progress = measure_progress_output(
            units_delivered=Decimal("3"),
            total_units=Decimal("10"),
        )
        assert progress == Decimal("0.3")

    def test_assess_modification_separate_contract(self):
        """Distinct goods at SSP = separate contract."""
        result = assess_modification_type(
            adds_distinct_goods=True,
            price_reflects_ssp=True,
            remaining_goods_distinct=True,
        )
        assert result == "separate_contract"

    def test_assess_modification_prospective(self):
        """Remaining distinct but not separate = prospective."""
        result = assess_modification_type(
            adds_distinct_goods=False,
            price_reflects_ssp=False,
            remaining_goods_distinct=True,
        )
        assert result == "prospective"

    def test_assess_modification_cumulative(self):
        """Remaining not distinct = cumulative catch-up."""
        result = assess_modification_type(
            adds_distinct_goods=False,
            price_reflects_ssp=False,
            remaining_goods_distinct=False,
        )
        assert result == "cumulative_catch_up"


# =============================================================================
# Integration Tests — Step 1: Identify Contract
# =============================================================================


class TestIdentifyContract:
    """Tests for Step 1: identify_contract."""

    def test_identify_contract_returns_model(self, revenue_service, test_customer_party):
        """Step 1 returns a RevenueContract with IDENTIFIED status."""
        contract = revenue_service.identify_contract(
            contract_id=uuid4(),
            customer_id=TEST_CUSTOMER_ID,
            contract_number="C-2024-001",
            start_date=date(2024, 1, 1),
            total_consideration=Decimal("200000.00"),
            end_date=date(2024, 12, 31),
        )
        assert isinstance(contract, RevenueContract)
        assert contract.status == ContractStatus.IDENTIFIED
        assert contract.total_consideration == Decimal("200000.00")

    def test_identify_contract_with_variable(self, revenue_service, test_customer_party):
        """Contract with variable consideration."""
        contract = revenue_service.identify_contract(
            contract_id=uuid4(),
            customer_id=TEST_CUSTOMER_ID,
            contract_number="C-2024-002",
            start_date=date(2024, 1, 1),
            total_consideration=Decimal("100000.00"),
            variable_consideration=Decimal("20000.00"),
        )
        assert contract.variable_consideration == Decimal("20000.00")


# =============================================================================
# Integration Tests — Step 2: Identify POs
# =============================================================================


class TestIdentifyPerformanceObligations:
    """Tests for Step 2: identify_performance_obligations."""

    def test_identify_obligations(self, revenue_service, test_revenue_contract):
        """Step 2 returns tuple of PerformanceObligations."""
        deliverables = [
            {"description": "Software license", "standalone_selling_price": "60000"},
            {"description": "Implementation", "standalone_selling_price": "30000",
             "recognition_method": "over_time_input"},
            {"description": "1-year support", "standalone_selling_price": "10000",
             "recognition_method": "over_time_output"},
        ]

        obligations = revenue_service.identify_performance_obligations(
            contract_id=TEST_REVENUE_CONTRACT_ID,
            deliverables=deliverables,
        )

        assert len(obligations) == 3
        assert obligations[0].recognition_method == RecognitionMethod.POINT_IN_TIME
        assert obligations[1].recognition_method == RecognitionMethod.OVER_TIME_INPUT
        assert obligations[2].recognition_method == RecognitionMethod.OVER_TIME_OUTPUT
        assert obligations[0].standalone_selling_price == Decimal("60000")


# =============================================================================
# Integration Tests — Step 3: Determine Transaction Price
# =============================================================================


class TestDetermineTransactionPrice:
    """Tests for Step 3: determine_transaction_price."""

    def test_simple_price(self, revenue_service, test_revenue_contract):
        """Simple fixed-price contract."""
        tp = revenue_service.determine_transaction_price(
            contract_id=TEST_REVENUE_CONTRACT_ID,
            base_price=Decimal("100000.00"),
        )
        assert isinstance(tp, TransactionPrice)
        assert tp.total_transaction_price == Decimal("100000.00")
        assert tp.variable_consideration == Decimal("0")

    def test_price_with_variable(self, revenue_service, test_revenue_contract):
        """Contract with variable consideration."""
        scenarios = [
            {"probability": "0.70", "amount": "120000"},
            {"probability": "0.30", "amount": "100000"},
        ]
        tp = revenue_service.determine_transaction_price(
            contract_id=TEST_REVENUE_CONTRACT_ID,
            base_price=Decimal("80000.00"),
            variable_scenarios=scenarios,
            variable_method="expected_value",
        )
        # variable = 0.70*120000 + 0.30*100000 = 84000 + 30000 = 114000
        assert tp.variable_consideration == Decimal("114000")
        assert tp.total_transaction_price == Decimal("194000.00")


# =============================================================================
# Integration Tests — Step 4: Allocate Transaction Price
# =============================================================================


class TestAllocateTransactionPrice:
    """Tests for Step 4: allocate_transaction_price."""

    def test_allocate_proportional(
        self, revenue_service, current_period, test_actor_id, deterministic_clock,
        test_revenue_contract, session,
    ):
        """Proportional SSP allocation and posting."""
        from finance_modules.revenue.orm import PerformanceObligationModel

        ob1_id = uuid4()
        ob2_id = uuid4()

        # Create PerformanceObligationModel rows so SSPAllocationModel FK is satisfied
        session.add(PerformanceObligationModel(
            id=ob1_id,
            contract_id=TEST_REVENUE_CONTRACT_ID,
            description="License",
            standalone_selling_price=Decimal("60000"),
            created_by_id=test_actor_id,
        ))
        session.add(PerformanceObligationModel(
            id=ob2_id,
            contract_id=TEST_REVENUE_CONTRACT_ID,
            description="Support",
            standalone_selling_price=Decimal("40000"),
            created_by_id=test_actor_id,
        ))
        session.flush()

        obligations = (
            PerformanceObligation(
                id=ob1_id, contract_id=TEST_REVENUE_CONTRACT_ID,
                description="License",
                standalone_selling_price=Decimal("60000"),
            ),
            PerformanceObligation(
                id=ob2_id, contract_id=TEST_REVENUE_CONTRACT_ID,
                description="Support",
                standalone_selling_price=Decimal("40000"),
            ),
        )

        allocations, result = revenue_service.allocate_transaction_price(
            contract_id=TEST_REVENUE_CONTRACT_ID,
            total_price=Decimal("90000.00"),
            obligations=obligations,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert len(allocations) == 2
        # 60% of 90000 = 54000, 40% of 90000 = 36000
        assert allocations[0].allocated_amount == Decimal("54000.00")
        assert allocations[1].allocated_amount == Decimal("36000.00")


# =============================================================================
# Integration Tests — Step 5: Recognize Revenue
# =============================================================================


class TestRecognizeRevenue:
    """Tests for Step 5: recognize_revenue."""

    def test_recognize_point_in_time(
        self, revenue_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Point-in-time recognition posts via Dr AR / Cr Revenue."""
        result = revenue_service.recognize_revenue(
            contract_id=uuid4(),
            obligation_id=uuid4(),
            amount=Decimal("50000.00"),
            method=RecognitionMethod.POINT_IN_TIME,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success

    def test_recognize_over_time_input(
        self, revenue_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Over-time input recognition posts via Dr Unbilled / Cr Revenue."""
        result = revenue_service.recognize_revenue(
            contract_id=uuid4(),
            obligation_id=uuid4(),
            amount=Decimal("20000.00"),
            method=RecognitionMethod.OVER_TIME_INPUT,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            progress=Decimal("0.40"),
        )
        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success

    def test_recognize_over_time_output(
        self, revenue_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Over-time output recognition posts via Dr Unbilled / Cr Revenue."""
        result = revenue_service.recognize_revenue(
            contract_id=uuid4(),
            obligation_id=uuid4(),
            amount=Decimal("15000.00"),
            method=RecognitionMethod.OVER_TIME_OUTPUT,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success


# =============================================================================
# Integration Tests — Contract Modification
# =============================================================================


class TestModifyContract:
    """Tests for modify_contract."""

    def test_modification_cumulative(
        self, revenue_service, current_period, test_actor_id, test_revenue_contract,
    ):
        """Cumulative catch-up modification posts."""
        modification, result = revenue_service.modify_contract(
            contract_id=TEST_REVENUE_CONTRACT_ID,
            modification_date=date(2024, 1, 1),
            price_change=Decimal("25000.00"),
            adds_distinct_goods=False,
            price_reflects_ssp=False,
            remaining_goods_distinct=False,
            actor_id=test_actor_id,
            description="Scope reduction",
        )
        assert result.status == ModulePostingStatus.POSTED
        assert modification.modification_type == ModificationType.CUMULATIVE_CATCH_UP

    def test_modification_prospective(
        self, revenue_service, current_period, test_actor_id, test_revenue_contract,
    ):
        """Prospective modification posts via different profile."""
        modification, result = revenue_service.modify_contract(
            contract_id=TEST_REVENUE_CONTRACT_ID,
            modification_date=date(2024, 1, 1),
            price_change=Decimal("15000.00"),
            adds_distinct_goods=False,
            price_reflects_ssp=False,
            remaining_goods_distinct=True,
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert modification.modification_type == ModificationType.PROSPECTIVE


# =============================================================================
# Integration Tests — Variable Consideration Update
# =============================================================================


class TestVariableConsiderationUpdate:
    """Tests for update_variable_consideration."""

    def test_variable_update_posts(
        self, revenue_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Variable consideration update posts."""
        result = revenue_service.update_variable_consideration(
            contract_id=uuid4(),
            new_estimate=Decimal("15000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success


# =============================================================================
# Query Tests
# =============================================================================


class TestRevenueQueries:
    """Tests for query methods."""

    def test_get_contract_status(self, revenue_service):
        """Contract status summary."""
        contract = RevenueContract(
            id=uuid4(), customer_id=uuid4(),
            contract_number="C-001", start_date=date(2024, 1, 1),
            total_consideration=Decimal("100000"),
            status=ContractStatus.ACTIVE,
        )
        obligations = [
            PerformanceObligation(
                id=uuid4(), contract_id=contract.id,
                description="License", satisfied=True,
            ),
            PerformanceObligation(
                id=uuid4(), contract_id=contract.id,
                description="Support", satisfied=False,
            ),
        ]
        status = revenue_service.get_contract_status(contract, obligations)
        assert status["obligations_total"] == 2
        assert status["obligations_satisfied"] == 1
        assert status["obligations_remaining"] == 1

    def test_get_unbilled_revenue(self, revenue_service):
        """Unbilled revenue from satisfied obligations."""
        obligations = [
            PerformanceObligation(
                id=uuid4(), contract_id=uuid4(),
                description="License", satisfied=True,
                allocated_price=Decimal("50000"),
            ),
            PerformanceObligation(
                id=uuid4(), contract_id=uuid4(),
                description="Support", satisfied=False,
                allocated_price=Decimal("10000"),
            ),
        ]
        total = revenue_service.get_unbilled_revenue([], obligations)
        assert total == Decimal("50000")

    def test_get_deferred_revenue(self, revenue_service):
        """Deferred revenue from unsatisfied obligations."""
        obligations = [
            PerformanceObligation(
                id=uuid4(), contract_id=uuid4(),
                description="License", satisfied=True,
                allocated_price=Decimal("50000"),
            ),
            PerformanceObligation(
                id=uuid4(), contract_id=uuid4(),
                description="Support", satisfied=False,
                allocated_price=Decimal("10000"),
            ),
        ]
        total = revenue_service.get_deferred_revenue([], obligations)
        assert total == Decimal("10000")
