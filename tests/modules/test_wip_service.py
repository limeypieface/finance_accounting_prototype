"""
Tests for WIP (Work in Process) Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: material issue, labor charge, overhead, job completion, variances
- Engine composition: ValuationLayer, AllocationEngine, VarianceCalculator
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.wip.models import (
    ByproductRecord,
    ProductionCostSummary,
    UnitCostBreakdown,
)
from finance_modules.wip.service import WipService
from tests.modules.conftest import TEST_OPERATION_ID, TEST_WORK_ORDER_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def wip_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide WipService for integration testing. party_service + test_actor_party for G14."""
    return WipService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestWipServiceStructure:
    """Verify WipService follows the module service pattern."""

    def test_importable(self):
        assert WipService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(WipService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "workflow_executor" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_material_issue", "record_labor_charge",
            "record_overhead_allocation", "complete_job",
            "record_scrap", "record_rework",
            "record_labor_variance", "record_material_variance",
            "record_overhead_variance",
        ]
        for method_name in expected:
            assert hasattr(WipService, method_name)
            assert callable(getattr(WipService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestWipServiceIntegration:
    """Integration tests calling real WIP service methods through the posting pipeline."""

    def test_record_material_issue_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record material issued to a job through the real pipeline."""
        result = wip_service.record_material_issue(
            issue_id=uuid4(),
            job_id="JOB-100",
            item_id="STEEL-001",
            quantity=Decimal("50"),
            cost=Decimal("1250.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_labor_charge_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record labor charged to a job through the real pipeline."""
        result = wip_service.record_labor_charge(
            charge_id=uuid4(),
            job_id="JOB-100",
            work_order_id=TEST_WORK_ORDER_ID,
            operation_id=TEST_OPERATION_ID,
            hours=Decimal("40"),
            rate=Decimal("50.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_overhead_allocation_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record overhead applied to a job through the real pipeline."""
        result = wip_service.record_overhead_allocation(
            job_id="JOB-100",
            work_order_id=TEST_WORK_ORDER_ID,
            allocation_amount=Decimal("800.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            allocation_base="LABOR_HOURS",
            rate=Decimal("20.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_complete_job_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Complete a job and transfer to finished goods through the real pipeline."""
        result = wip_service.complete_job(
            job_id="JOB-100",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            quantity=Decimal("100"),
            unit_cost=Decimal("25.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_labor_variance(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record labor efficiency variance through the real pipeline."""
        variance_result, posting_result = wip_service.record_labor_variance(
            job_id="JOB-100",
            standard_hours=Decimal("40"),
            actual_hours=Decimal("45"),
            standard_rate=Decimal("50.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert variance_result.variance.amount == Decimal("250.00")
        assert not variance_result.is_favorable

    def test_record_material_variance(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record material usage variance through the real pipeline."""
        variance_result, posting_result = wip_service.record_material_variance(
            job_id="JOB-100",
            standard_quantity=Decimal("100"),
            actual_quantity=Decimal("110"),
            standard_cost=Decimal("10.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert variance_result.variance.amount == Decimal("100.00")
        assert not variance_result.is_favorable

    def test_record_overhead_variance_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record overhead variance through the real pipeline."""
        variance_result, posting_result = wip_service.record_overhead_variance(
            applied_overhead=Decimal("5000.00"),
            actual_overhead=Decimal("5500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert variance_result.variance.amount == Decimal("500.00")
        assert not variance_result.is_favorable
        assert posting_result.status == ModulePostingStatus.POSTED
        assert posting_result.is_success
        assert len(posting_result.journal_entry_ids) > 0

    def test_record_scrap_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record scrap on a work order through the real pipeline."""
        result = wip_service.record_scrap(
            scrap_id=uuid4(),
            job_id="JOB-100",
            quantity=Decimal("5"),
            cost=Decimal("125.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            reason="Defective material",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_rework_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Record rework costs on a work order through the real pipeline."""
        result = wip_service.record_rework(
            rework_id=uuid4(),
            job_id="JOB-100",
            quantity=Decimal("10"),
            cost=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            reason="Tolerance out of spec",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0


# =============================================================================
# Model Tests
# =============================================================================


class TestNewWIPModels:
    """Verify new WIP models are frozen dataclasses."""

    def test_production_cost_summary(self):
        summary = ProductionCostSummary(
            job_id=uuid4(),
            material_cost=Decimal("30000"),
            labor_cost=Decimal("20000"),
            overhead_cost=Decimal("10000"),
            total_cost=Decimal("60000"),
            units_produced=Decimal("100"),
        )
        assert summary.total_cost == Decimal("60000")

    def test_byproduct_record(self):
        bp = ByproductRecord(
            id=uuid4(),
            job_id=uuid4(),
            item_id=uuid4(),
            description="Sawdust",
            value=Decimal("500"),
        )
        assert bp.quantity == Decimal("1")

    def test_unit_cost_breakdown(self):
        ucb = UnitCostBreakdown(
            job_id=uuid4(),
            units_produced=Decimal("100"),
            material_per_unit=Decimal("300"),
            labor_per_unit=Decimal("200"),
            overhead_per_unit=Decimal("100"),
            total_per_unit=Decimal("600"),
        )
        assert ucb.total_per_unit == Decimal("600")


# =============================================================================
# Integration Tests — Production Cost
# =============================================================================


class TestProductionCost:
    """Tests for calculate_production_cost."""

    def test_production_cost_basic(self, wip_service):
        summary = wip_service.calculate_production_cost(
            job_id=uuid4(),
            material_cost=Decimal("30000"),
            labor_cost=Decimal("20000"),
            overhead_cost=Decimal("10000"),
            units_produced=Decimal("100"),
        )
        assert isinstance(summary, ProductionCostSummary)
        assert summary.total_cost == Decimal("60000")
        assert summary.units_produced == Decimal("100")


# =============================================================================
# Integration Tests — Byproduct
# =============================================================================


class TestByproduct:
    """Tests for record_byproduct."""

    def test_byproduct_posts(
        self, wip_service, current_period, test_actor_id, deterministic_clock,
        test_work_order, test_operation,
    ):
        """Byproduct recording posts Dr Inventory / Cr WIP."""
        bp, result = wip_service.record_byproduct(
            job_id=TEST_WORK_ORDER_ID,
            item_id=uuid4(),
            description="Sawdust byproduct",
            value=Decimal("500.00"),
            quantity=Decimal("10"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(bp, ByproductRecord)
        assert bp.value == Decimal("500.00")


# =============================================================================
# Integration Tests — Unit Cost
# =============================================================================


class TestUnitCost:
    """Tests for calculate_unit_cost."""

    def test_unit_cost_basic(self, wip_service):
        ucb = wip_service.calculate_unit_cost(
            job_id=uuid4(),
            material_cost=Decimal("30000"),
            labor_cost=Decimal("20000"),
            overhead_cost=Decimal("10000"),
            units_produced=Decimal("100"),
        )
        assert isinstance(ucb, UnitCostBreakdown)
        assert ucb.material_per_unit == Decimal("300.00")
        assert ucb.labor_per_unit == Decimal("200.00")
        assert ucb.overhead_per_unit == Decimal("100.00")
        assert ucb.total_per_unit == Decimal("600.00")

    def test_unit_cost_zero_units_raises(self, wip_service):
        with pytest.raises(ValueError):
            wip_service.calculate_unit_cost(
                job_id=uuid4(),
                material_cost=Decimal("30000"),
                labor_cost=Decimal("20000"),
                overhead_cost=Decimal("10000"),
                units_produced=Decimal("0"),
            )
