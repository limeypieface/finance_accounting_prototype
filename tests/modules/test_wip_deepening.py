"""
Tests for WIP Module Deepening.

Validates new methods:
- calculate_production_cost: pure calculation
- record_byproduct: posts Dr Inventory / Cr WIP
- calculate_unit_cost: pure calculation
"""

from __future__ import annotations

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


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def wip_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide WIPService for integration testing."""
    return WipService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


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
    ):
        """Byproduct recording posts Dr Inventory / Cr WIP."""
        bp, result = wip_service.record_byproduct(
            job_id=uuid4(),
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
