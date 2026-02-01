"""
Tests for Contracts Module Deepening.

Validates new methods:
- record_contract_modification: posts modification
- record_subcontract_cost: posts subcontract flow-down
- record_equitable_adjustment: posts REA
- run_dcaa_audit_prep: pure compliance check
- generate_sf1034: pure voucher generation
- record_cost_disallowance: posts disallowance
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.contracts.models import (
    AuditFinding,
    ContractModification,
    CostDisallowance,
    Subcontract,
)
from finance_modules.contracts.service import GovernmentContractsService


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


# =============================================================================
# Model Tests
# =============================================================================


class TestContractModels:
    """Verify new contract models are frozen dataclasses."""

    def test_contract_modification(self):
        mod = ContractModification(
            id=uuid4(),
            contract_id="FA8750-21-C-0001",
            modification_number="P00003",
            modification_type="scope_change",
            effective_date=date(2024, 6, 1),
            amount_change=Decimal("500000"),
        )
        assert mod.modification_type == "scope_change"
        assert mod.amount_change == Decimal("500000")

    def test_subcontract(self):
        sub = Subcontract(
            id=uuid4(),
            contract_id="FA8750-21-C-0001",
            subcontractor_name="Sub Corp",
            subcontract_number="SUB-001",
            amount=Decimal("100000"),
        )
        assert sub.cost_type == "SUBCONTRACT"
        assert sub.period == ""

    def test_audit_finding(self):
        finding = AuditFinding(
            id=uuid4(),
            contract_id="FA8750-21-C-0001",
            finding_type="questioned_cost",
            description="Unsupported travel expense",
            amount=Decimal("5000"),
        )
        assert finding.severity == "medium"
        assert finding.status == "open"

    def test_cost_disallowance(self):
        dis = CostDisallowance(
            id=uuid4(),
            contract_id="FA8750-21-C-0001",
            cost_type="TRAVEL",
            amount=Decimal("3000"),
            reason="Exceeded per diem rate",
        )
        assert dis.is_disputed is False


# =============================================================================
# Integration Tests — Contract Modification
# =============================================================================


class TestContractModification:
    """Tests for record_contract_modification."""

    def test_modification_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        mod, result = contracts_service.record_contract_modification(
            contract_id="FA8750-21-C-0001",
            modification_number="P00003",
            modification_type="scope_change",
            amount_change=Decimal("500000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(mod, ContractModification)
        assert mod.amount_change == Decimal("500000.00")


# =============================================================================
# Integration Tests — Subcontract Cost
# =============================================================================


class TestSubcontractCost:
    """Tests for record_subcontract_cost."""

    def test_subcontract_cost_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        sub, result = contracts_service.record_subcontract_cost(
            contract_id="FA8750-21-C-0001",
            subcontractor_name="Sub Corp",
            subcontract_number="SUB-001",
            amount=Decimal("75000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(sub, Subcontract)
        assert sub.amount == Decimal("75000.00")


# =============================================================================
# Integration Tests — Equitable Adjustment
# =============================================================================


class TestEquitableAdjustment:
    """Tests for record_equitable_adjustment."""

    def test_equitable_adjustment_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        result = contracts_service.record_equitable_adjustment(
            contract_id="FA8750-21-C-0001",
            amount=Decimal("150000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            reason="Directed scope change",
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Pure Tests — Audit Prep
# =============================================================================


class TestDCAAAuditPrep:
    """Tests for run_dcaa_audit_prep."""

    def test_audit_prep_basic(self, contracts_service):
        report = contracts_service.run_dcaa_audit_prep(
            contract_id="FA8750-21-C-0001",
            period="2024-Q4",
        )
        assert report["contract_id"] == "FA8750-21-C-0001"
        assert report["period"] == "2024-Q4"
        assert "schedules_compiled" in report
        assert "is_valid" in report
        assert "is_complete" in report


# =============================================================================
# Pure Tests — SF-1034
# =============================================================================


class TestSF1034:
    """Tests for generate_sf1034."""

    def test_sf1034_generation(self, contracts_service):
        voucher = contracts_service.generate_sf1034(
            contract_id="FA8750-21-C-0001",
            period="2024-Q4",
            billing_amount=Decimal("250000"),
            fee_amount=Decimal("20000"),
        )
        assert voucher["form"] == "SF-1034"
        assert voucher["total_voucher"] == str(Decimal("270000"))
        assert voucher["contract_id"] == "FA8750-21-C-0001"

    def test_sf1034_no_fee(self, contracts_service):
        voucher = contracts_service.generate_sf1034(
            contract_id="FA8750-21-C-0001",
            period="2024-Q4",
            billing_amount=Decimal("100000"),
        )
        assert voucher["total_voucher"] == str(Decimal("100000"))
        assert voucher["fee_amount"] == str(Decimal("0"))


# =============================================================================
# Integration Tests — Cost Disallowance
# =============================================================================


class TestCostDisallowance:
    """Tests for record_cost_disallowance."""

    def test_disallowance_posts(
        self, contracts_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        dis, result = contracts_service.record_cost_disallowance(
            contract_id="FA8750-21-C-0001",
            cost_type="TRAVEL",
            amount=Decimal("3000.00"),
            reason="Exceeded per diem rate",
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(dis, CostDisallowance)
        assert dis.amount == Decimal("3000.00")
        assert dis.reason == "Exceeded per diem rate"
