"""
Tests for WIP (Work in Process) Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: material issue, labor charge, overhead, job completion, variances
- Engine composition: ValuationLayer, AllocationEngine, VarianceCalculator
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.wip.service import WipService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def wip_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide WipService for integration testing."""
    return WipService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
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
    ):
        """Record labor charged to a job through the real pipeline."""
        result = wip_service.record_labor_charge(
            charge_id=uuid4(),
            job_id="JOB-100",
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
    ):
        """Record overhead applied to a job through the real pipeline."""
        result = wip_service.record_overhead_allocation(
            job_id="JOB-100",
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
