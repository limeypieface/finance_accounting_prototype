"""
Tests for Payroll Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: payroll run, tax deposit, labor allocation, DCAA cascade, variance
- Engine composition: AllocationEngine, AllocationCascade, VarianceCalculator
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.payroll.service import PayrollService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def payroll_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide PayrollService for integration testing."""
    return PayrollService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Structural Tests
# =============================================================================


class TestPayrollServiceStructure:
    """Verify PayrollService follows the module service pattern."""

    def test_importable(self):
        assert PayrollService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(PayrollService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_payroll_run", "record_payroll_payment", "record_payroll_tax",
            "record_benefits_payment", "record_regular_hours", "record_overtime",
            "record_pto", "allocate_labor_costs",
            "run_dcaa_cascade", "compute_payroll_variance",
        ]
        for method_name in expected:
            assert hasattr(PayrollService, method_name)
            assert callable(getattr(PayrollService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestPayrollServiceIntegration:
    """Integration tests calling real payroll service methods through the posting pipeline."""

    def test_record_payroll_run_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record a payroll run through the real pipeline."""
        result = payroll_service.record_payroll_run(
            run_id=uuid4(),
            employee_id="EMP-001",
            gross_pay=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            federal_tax=Decimal("750.00"),
            state_tax=Decimal("250.00"),
            fica=Decimal("382.50"),
            benefits=Decimal("500.00"),
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_payroll_tax_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record payroll tax deposit through the real pipeline."""
        result = payroll_service.record_payroll_tax(
            run_id=uuid4(),
            tax_type="FEDERAL",
            amount=Decimal("750.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_allocate_labor_costs_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Allocate labor costs across cost centers through the real pipeline."""
        result = payroll_service.allocate_labor_costs(
            run_id=uuid4(),
            allocations=[
                {"target_id": "CC-ENG", "amount": "3000.00", "labor_type": "DIRECT"},
                {"target_id": "CC-ADMIN", "amount": "2000.00", "labor_type": "INDIRECT"},
            ],
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success

    def test_run_dcaa_cascade(self, payroll_service, deterministic_clock, test_actor_id):
        """DCAA cascade computes indirect cost allocations (pure engine)."""
        step_results, final_balances = payroll_service.run_dcaa_cascade(
            pool_balances={
                "DIRECT_LABOR": Decimal("100000"),
                "FRINGE": Decimal("0"),
                "OVERHEAD": Decimal("0"),
                "GA": Decimal("0"),
            },
            rates={
                "fringe": Decimal("0.35"),
                "overhead": Decimal("0.50"),
                "ga": Decimal("0.10"),
            },
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert len(step_results) > 0
        assert isinstance(final_balances, dict)

    def test_compute_payroll_variance(self, payroll_service):
        """Compute payroll budget vs actual variance (pure engine)."""
        variance_result = payroll_service.compute_payroll_variance(
            budget_amount=Decimal("50000.00"),
            actual_amount=Decimal("52000.00"),
        )

        assert variance_result.variance.amount == Decimal("2000.00")
        assert not variance_result.is_favorable

    def test_record_payroll_payment_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record net payroll payment through the real pipeline."""
        result = payroll_service.record_payroll_payment(
            payment_id=uuid4(),
            amount=Decimal("3117.50"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_benefits_payment_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record benefits payment through the real pipeline."""
        result = payroll_service.record_benefits_payment(
            payment_id=uuid4(),
            amount=Decimal("2500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            provider="BlueCross",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_regular_hours_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record regular timesheet hours through the real pipeline."""
        result = payroll_service.record_regular_hours(
            timesheet_id=uuid4(),
            employee_id="EMP-001",
            hours=Decimal("8"),
            rate=Decimal("50.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_overtime_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record overtime hours through the real pipeline."""
        result = payroll_service.record_overtime(
            timesheet_id=uuid4(),
            employee_id="EMP-001",
            hours=Decimal("4"),
            rate=Decimal("75.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_pto_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
    ):
        """Record PTO hours through the real pipeline."""
        result = payroll_service.record_pto(
            timesheet_id=uuid4(),
            employee_id="EMP-001",
            hours=Decimal("8"),
            rate=Decimal("50.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0
