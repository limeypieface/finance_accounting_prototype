"""
Tests for Project Accounting Module.

Validates:
- create_project: setup (no posting)
- record_cost: posts Dr PROJECT_WIP / Cr DIRECT_COST
- bill_milestone: posts Dr CONTRACT_RECEIVABLE / Cr CONTRACT_REVENUE
- bill_time_materials: posts Dr UNBILLED_AR / Cr CONTRACT_REVENUE
- recognize_revenue: posts Dr UNBILLED_AR / Cr CONTRACT_REVENUE
- revise_budget: posts Dr PROJECT_WIP / Cr WIP_BILLED
- complete_phase: posts Dr WIP_BILLED / Cr PROJECT_WIP
- calculate_evm: pure EVM calculation
- get_project_status: pure query
- get_wbs_cost_report: pure query

Also validates EVM helpers:
- calculate_bcws, calculate_bcwp, calculate_acwp
- calculate_cpi, calculate_spi, calculate_eac, calculate_etc, calculate_vac
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.project.evm import (
    calculate_bcwp,
    calculate_bcws,
    calculate_cpi,
    calculate_eac,
    calculate_etc,
    calculate_spi,
    calculate_vac,
)
from finance_modules.project.models import (
    EVMSnapshot,
    Milestone,
    Project,
    ProjectBudget,
    WBSElement,
)
from finance_modules.project.service import ProjectService
from tests.modules.conftest import TEST_PROJECT_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def project_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide ProjectService for integration testing. party_service + test_actor_party for G14."""
    return ProjectService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestProjectModels:
    """Verify project models are frozen dataclasses."""

    def test_project_creation(self):
        p = Project(
            id=uuid4(),
            name="Alpha Program",
            project_type="fixed_price",
            total_budget=Decimal("1000000"),
        )
        assert p.status == "active"
        assert p.currency == "USD"

    def test_wbs_element(self):
        w = WBSElement(
            id=uuid4(),
            project_id=uuid4(),
            code="1.1",
            name="Design Phase",
            budget_amount=Decimal("250000"),
        )
        assert w.level == 1
        assert w.actual_cost == Decimal("0")

    def test_project_budget(self):
        pb = ProjectBudget(
            project_id=uuid4(),
            wbs_code="1.1",
            period="2024-Q1",
            budget_amount=Decimal("100000"),
        )
        assert pb.available_amount == Decimal("0")

    def test_milestone(self):
        m = Milestone(
            id=uuid4(),
            project_id=uuid4(),
            name="Design Complete",
            amount=Decimal("50000"),
        )
        assert m.is_billed is False
        assert m.completion_pct == Decimal("0")

    def test_evm_snapshot(self):
        evm = EVMSnapshot(
            project_id=uuid4(),
            as_of_date=date(2024, 6, 30),
            bcws=Decimal("500000"),
            bcwp=Decimal("450000"),
            acwp=Decimal("480000"),
            bac=Decimal("1000000"),
            cpi=Decimal("0.9375"),
            spi=Decimal("0.9000"),
        )
        assert evm.bac == Decimal("1000000")


# =============================================================================
# EVM Helper Tests
# =============================================================================


class TestEVMHelpers:
    """Test pure EVM calculation functions."""

    def test_bcws(self):
        pv = calculate_bcws(Decimal("1000000"), Decimal("0.50"))
        assert pv == Decimal("500000.00")

    def test_bcwp(self):
        ev = calculate_bcwp(Decimal("1000000"), Decimal("0.45"))
        assert ev == Decimal("450000.00")

    def test_cpi_under_budget(self):
        cpi = calculate_cpi(Decimal("500000"), Decimal("450000"))
        assert cpi > Decimal("1")

    def test_cpi_over_budget(self):
        cpi = calculate_cpi(Decimal("450000"), Decimal("500000"))
        assert cpi < Decimal("1")

    def test_cpi_zero_actual(self):
        cpi = calculate_cpi(Decimal("500000"), Decimal("0"))
        assert cpi == Decimal("0")

    def test_spi_ahead(self):
        spi = calculate_spi(Decimal("500000"), Decimal("450000"))
        assert spi > Decimal("1")

    def test_spi_behind(self):
        spi = calculate_spi(Decimal("400000"), Decimal("500000"))
        assert spi < Decimal("1")

    def test_eac(self):
        eac = calculate_eac(Decimal("1000000"), Decimal("0.9375"))
        assert eac > Decimal("1000000")

    def test_etc(self):
        etc = calculate_etc(Decimal("1066666.67"), Decimal("480000"))
        assert etc > Decimal("0")

    def test_vac(self):
        vac = calculate_vac(Decimal("1000000"), Decimal("1066666.67"))
        assert vac < Decimal("0")


# =============================================================================
# Integration Tests -- Project Setup
# =============================================================================


class TestProjectSetup:
    """Tests for create_project."""

    def test_create_project(self, project_service):
        p = project_service.create_project(
            name="Alpha Program",
            project_type="fixed_price",
            total_budget=Decimal("1000000"),
            start_date=date(2024, 1, 1),
        )
        assert isinstance(p, Project)
        assert p.name == "Alpha Program"
        assert p.total_budget == Decimal("1000000")


# =============================================================================
# Integration Tests -- Cost Recording
# =============================================================================


class TestCostRecording:
    """Tests for record_cost."""

    def test_cost_recorded_posts(
        self, project_service, current_period, test_actor_id, deterministic_clock,
        test_project,
    ):
        result = project_service.record_cost(
            project_id=TEST_PROJECT_ID,
            wbs_code="1.1",
            cost_type="labor",
            amount=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests -- Billing
# =============================================================================


class TestBilling:
    """Tests for billing methods."""

    def test_milestone_billing_posts(
        self, project_service, current_period, test_actor_id, deterministic_clock,
    ):
        milestone, result = project_service.bill_milestone(
            project_id=uuid4(),
            milestone_name="Design Complete",
            amount=Decimal("100000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(milestone, Milestone)
        assert milestone.is_billed is True

    def test_tm_billing_posts(
        self, project_service, current_period, test_actor_id, deterministic_clock,
    ):
        result = project_service.bill_time_materials(
            project_id=uuid4(),
            period="2024-Q1",
            amount=Decimal("75000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests -- Revenue
# =============================================================================


class TestRevenueRecognition:
    """Tests for recognize_revenue."""

    def test_revenue_recognized_posts(
        self, project_service, current_period, test_actor_id, deterministic_clock,
    ):
        result = project_service.recognize_revenue(
            project_id=uuid4(),
            method="percentage_of_completion",
            amount=Decimal("200000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests -- Budget & Phase
# =============================================================================


class TestBudgetAndPhase:
    """Tests for budget revision and phase completion."""

    def test_budget_revised_posts(
        self, project_service, current_period, test_actor_id, deterministic_clock,
    ):
        result = project_service.revise_budget(
            project_id=uuid4(),
            wbs_code="1.1",
            new_amount=Decimal("300000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED

    def test_phase_completed_posts(
        self, project_service, current_period, test_actor_id, deterministic_clock,
    ):
        result = project_service.complete_phase(
            project_id=uuid4(),
            phase="Design",
            amount=Decimal("250000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Pure Tests -- EVM
# =============================================================================


class TestEVMCalculation:
    """Tests for calculate_evm."""

    def test_evm_snapshot(self, project_service):
        evm = project_service.calculate_evm(
            project_id=uuid4(),
            as_of_date=date(2024, 6, 30),
            total_budget=Decimal("1000000"),
            planned_pct_complete=Decimal("0.50"),
            actual_pct_complete=Decimal("0.45"),
            actual_costs=Decimal("480000"),
        )
        assert isinstance(evm, EVMSnapshot)
        assert evm.bcws == Decimal("500000.00")
        assert evm.bcwp == Decimal("450000.00")
        assert evm.acwp == Decimal("480000")
        assert evm.cpi < Decimal("1")  # Over budget
        assert evm.spi < Decimal("1")  # Behind schedule
        assert evm.cv < 0  # Negative cost variance
        assert evm.sv < 0  # Negative schedule variance


# =============================================================================
# Pure Tests -- Queries
# =============================================================================


class TestProjectQueries:
    """Tests for query methods."""

    def test_project_status(self, project_service):
        status = project_service.get_project_status(
            project_id=uuid4(),
            project_name="Alpha",
            total_budget=Decimal("1000000"),
            total_actual=Decimal("400000"),
            total_billed=Decimal("350000"),
        )
        assert status["pct_spent"] == "40.00"
        assert status["remaining_budget"] == "600000"

    def test_wbs_report(self, project_service):
        report = project_service.get_wbs_cost_report(
            project_id=uuid4(),
            wbs_elements=[
                {"code": "1.1", "name": "Design", "budget": "250000", "actual": "200000"},
                {"code": "1.2", "name": "Build", "budget": "500000", "actual": "100000"},
            ],
        )
        assert report["element_count"] == 2
        assert report["total_budget"] == "750000"
        assert report["total_actual"] == "300000"
