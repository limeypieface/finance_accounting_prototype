"""
Tests for Government Contracts Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: cost incurrence, billing, funding actions, indirect allocation,
  rate adjustment, allocation cascade, ICE compilation
- Engine composition: BillingEngine, AllocationCascade, ICEEngine
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.contracts.service import GovernmentContractsService
from tests.modules.conftest import TEST_CUSTOMER_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def contracts_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide GovernmentContractsService for integration testing."""
    return GovernmentContractsService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# UUID that the service computes for contract_id="FA8750-21-C-0001"
_CONTRACT_STR = "FA8750-21-C-0001"
_CONTRACT_UUID = uuid5(NAMESPACE_DNS, _CONTRACT_STR)


@pytest.fixture
def test_gov_contract(session, test_actor_id, test_customer_party):
    """Create a kernel Contract whose id matches uuid5(NAMESPACE_DNS, 'FA8750-21-C-0001').

    The GovernmentContractsService converts string contract IDs to UUIDs via
    uuid5(NAMESPACE_DNS, contract_id).  ORM FK constraints require the
    corresponding kernel Contract row to exist.
    """
    from finance_kernel.models.contract import Contract, ContractStatus, ContractType
    existing = session.get(Contract, _CONTRACT_UUID)
    if existing is not None:
        return existing
    contract = Contract(
        id=_CONTRACT_UUID,
        contract_number=_CONTRACT_STR,
        contract_name="FA8750-21-C-0001 Test Contract",
        contract_type=ContractType.COST_PLUS_FIXED_FEE,
        status=ContractStatus.ACTIVE,
        customer_party_id=TEST_CUSTOMER_ID,
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        ceiling_amount=Decimal("1000000.00"),
        funded_amount=Decimal("500000.00"),
        currency="USD",
        created_by_id=test_actor_id,
    )
    session.add(contract)
    session.flush()
    return contract


# =============================================================================
# Structural Tests
# =============================================================================


class TestGovernmentContractsServiceStructure:
    """Verify GovernmentContractsService follows the module service pattern."""

    def test_importable(self):
        assert GovernmentContractsService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(GovernmentContractsService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_cost_incurrence", "generate_billing",
            "record_funding_action", "record_indirect_allocation",
            "record_rate_adjustment", "record_fee_accrual",
            "run_allocation_cascade", "compile_ice",
        ]
        for method_name in expected:
            assert hasattr(GovernmentContractsService, method_name)
            assert callable(getattr(GovernmentContractsService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestContractsServiceIntegration:
    """Integration tests calling real contracts service methods through the posting pipeline."""

    def test_record_cost_incurrence_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Record contract cost incurrence through the real pipeline."""
        result = contracts_service.record_cost_incurrence(
            contract_id="FA8750-21-C-0001",
            cost_type="DIRECT_LABOR",
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            clin_number="0001",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_generate_billing_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_gov_contract,
    ):
        """Generate contract billing through the real pipeline."""
        from finance_engines.billing import (
            BillingContractType,
            BillingInput,
            CostBreakdown,
            IndirectRates,
        )
        from finance_kernel.domain.values import Money

        billing_input = BillingInput(
            contract_type=BillingContractType.CPFF,
            currency="USD",
            cost_breakdown=CostBreakdown(
                direct_labor=Money.of(Decimal("50000.00"), "USD"),
                direct_material=Money.of(Decimal("0"), "USD"),
                subcontract=Money.of(Decimal("0"), "USD"),
                travel=Money.of(Decimal("0"), "USD"),
                odc=Money.of(Decimal("0"), "USD"),
            ),
            indirect_rates=IndirectRates(
                fringe=Decimal("0.35"),
                overhead=Decimal("0.50"),
                ga=Decimal("0.10"),
            ),
            fee_rate=Decimal("0.08"),
        )

        billing_result, posting_result = contracts_service.generate_billing(
            contract_id="FA8750-21-C-0001",
            billing_period="2024-01",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            billing_input=billing_input,
        )

        # Billing engine always produces correct results
        assert billing_result.net_billing.amount > 0

        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert len(posting_result.journal_entry_ids) > 0

    def test_record_funding_action_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_gov_contract,
    ):
        """Record contract funding obligation through the real pipeline."""
        result = contracts_service.record_funding_action(
            contract_id="FA8750-21-C-0001",
            action_type="OBLIGATION",
            amount=Decimal("500000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_indirect_allocation_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Record indirect cost allocation through the real pipeline."""
        result = contracts_service.record_indirect_allocation(
            contract_id="FA8750-21-C-0001",
            indirect_type="FRINGE",
            amount=Decimal("17500.00"),
            rate_applied=Decimal("0.35"),
            base_amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_rate_adjustment_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Record rate adjustment through the real pipeline."""
        result = contracts_service.record_rate_adjustment(
            contract_id="FA8750-21-C-0001",
            indirect_type="OVERHEAD",
            provisional_rate=Decimal("0.50"),
            final_rate=Decimal("0.48"),
            base_amount=Decimal("100000.00"),
            adjustment_amount=Decimal("-2000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_run_allocation_cascade(self, contracts_service):
        """DCAA allocation cascade (pure engine computation)."""
        from finance_kernel.domain.values import Money

        step_results, final_balances = contracts_service.run_allocation_cascade(
            steps=None,
            pool_balances={
                "DIRECT_LABOR": Money.of(Decimal("100000"), "USD"),
                "FRINGE": Money.of(Decimal("0"), "USD"),
                "OVERHEAD": Money.of(Decimal("0"), "USD"),
                "GA": Money.of(Decimal("0"), "USD"),
            },
            rates={
                "fringe": Decimal("0.35"),
                "overhead": Decimal("0.50"),
                "ga": Decimal("0.10"),
            },
        )

        assert len(step_results) > 0

    def test_compile_ice(self, contracts_service, deterministic_clock):
        """Compile ICE submission schedules (pure engine computation)."""
        from datetime import date

        from finance_engines.ice import (
            ContractCostInput,
            ICEInput,
        )
        from finance_kernel.domain.values import Money

        ice_input = ICEInput(
            fiscal_year=2024,
            fiscal_year_start=date(2024, 1, 1),
            fiscal_year_end=date(2024, 12, 31),
            contractor_name="Test Corp",
            currency="USD",
            contract_costs=(
                ContractCostInput(
                    contract_number="FA8750-21-C-0001",
                    contract_type="CPFF",
                    direct_labor=Money.of(Decimal("500000"), "USD"),
                    direct_material=Money.of(Decimal("100000"), "USD"),
                ),
            ),
        )

        submission = contracts_service.compile_ice(ice_input)

        assert submission is not None
        assert submission.fiscal_year == 2024

    def test_record_fee_accrual_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        """Record fixed fee accrual through the real pipeline."""
        result = contracts_service.record_fee_accrual(
            contract_id="FA8750-21-C-0001",
            fee_type="FIXED_FEE",
            amount=Decimal("8200.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            cumulative_fee=Decimal("8200.00"),
            ceiling_fee=Decimal("50000.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
