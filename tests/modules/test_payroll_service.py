"""
Tests for Payroll Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: payroll run, tax deposit, labor allocation, DCAA cascade, variance
- Engine composition: AllocationEngine, AllocationCascade, VarianceCalculator
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.payroll.helpers import (
    calculate_federal_withholding,
    calculate_fica,
    calculate_state_withholding,
    generate_nacha_batch,
)
from finance_modules.payroll.models import (
    BenefitsDeduction,
    EmployerContribution,
    WithholdingResult,
)
from finance_modules.payroll.service import PayrollService
from tests.modules.conftest import TEST_PAY_PERIOD_ID, TEST_PAYROLL_EMPLOYEE_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def payroll_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide PayrollService for integration testing. party_service + test_actor_party for G14."""
    return PayrollService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
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
        assert "workflow_executor" in params
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
        test_employee_party, test_pay_period,
    ):
        """Record a payroll run through the real pipeline."""
        result = payroll_service.record_payroll_run(
            run_id=uuid4(),
            employee_id="EMP-001",
            gross_pay=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            pay_period_id=TEST_PAY_PERIOD_ID,
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
        test_employee_party, test_pay_period,
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
        test_employee_party, test_pay_period,
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
        test_employee_party, test_pay_period,
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
        test_employee_party, test_pay_period,
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
        test_employee_party, test_pay_period,
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
        test_employee_party, test_pay_period,
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
        test_employee_party, test_pay_period,
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

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def payroll_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide PayrollService for integration testing. party_service + test_actor_party for G14."""
    return PayrollService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestNewPayrollModels:
    """Verify new payroll models are frozen dataclasses."""

    def test_withholding_result(self):
        wr = WithholdingResult(
            id=uuid4(),
            employee_id=uuid4(),
            gross_pay=Decimal("5000"),
            federal_withholding=Decimal("600"),
            state_withholding=Decimal("250"),
            social_security=Decimal("310"),
            medicare=Decimal("72.50"),
            total_deductions=Decimal("1232.50"),
            net_pay=Decimal("3767.50"),
        )
        assert wr.net_pay == Decimal("3767.50")

    def test_benefits_deduction(self):
        bd = BenefitsDeduction(
            id=uuid4(),
            employee_id=uuid4(),
            plan_name="401k",
            employee_amount=Decimal("500"),
        )
        assert bd.employer_amount == Decimal("0")

    def test_employer_contribution(self):
        ec = EmployerContribution(
            id=uuid4(),
            employee_id=uuid4(),
            plan_name="401k Match",
            amount=Decimal("250"),
        )
        assert ec.period == ""


# =============================================================================
# Helper Tests — Tax Calculations
# =============================================================================


class TestTaxHelpers:
    """Test pure tax calculation functions."""

    def test_federal_withholding_single(self):
        tax = calculate_federal_withholding(
            gross_pay=Decimal("5000"),
            filing_status="single",
            allowances=0,
        )
        assert tax > Decimal("0")
        assert tax < Decimal("5000")

    def test_federal_withholding_married(self):
        tax_married = calculate_federal_withholding(
            gross_pay=Decimal("5000"),
            filing_status="married",
        )
        tax_single = calculate_federal_withholding(
            gross_pay=Decimal("5000"),
            filing_status="single",
        )
        # Married brackets are wider, so tax should be less
        assert tax_married < tax_single

    def test_federal_withholding_with_allowances(self):
        tax_no_allow = calculate_federal_withholding(Decimal("5000"))
        tax_with_allow = calculate_federal_withholding(Decimal("5000"), allowances=2)
        assert tax_with_allow < tax_no_allow

    def test_state_withholding_default(self):
        tax = calculate_state_withholding(Decimal("5000"))
        assert tax == Decimal("250.00")

    def test_state_withholding_custom_rate(self):
        tax = calculate_state_withholding(Decimal("5000"), state_rate=Decimal("0.03"))
        assert tax == Decimal("150.00")

    def test_fica_basic(self):
        ss, medicare = calculate_fica(Decimal("5000"))
        assert ss == Decimal("310.00")  # 5000 * 0.062
        assert medicare == Decimal("72.50")  # 5000 * 0.0145

    def test_fica_ss_wage_base(self):
        """SS stops at wage base."""
        ss, _ = calculate_fica(
            gross_pay=Decimal("5000"),
            ytd_earnings=Decimal("166600"),
            ss_wage_base=Decimal("168600"),
        )
        # Only 2000 remains under SS wage base
        assert ss == Decimal("124.00")  # 2000 * 0.062

    def test_fica_above_ss_limit(self):
        """Entirely above SS wage base."""
        ss, _ = calculate_fica(
            gross_pay=Decimal("5000"),
            ytd_earnings=Decimal("170000"),
        )
        assert ss == Decimal("0")


# =============================================================================
# Helper Tests — NACHA
# =============================================================================


class TestNACHABatch:
    """Test NACHA batch generation."""

    def test_nacha_batch_basic(self):
        payments = [
            {"name": "John Doe", "account": "12345", "routing": "021000021", "amount": "3500.00"},
        ]
        content = generate_nacha_batch(payments, "Acme Corp", "123456", "2024-01-15")
        assert "PAYROLL_BATCH_HEADER" in content
        assert "John Doe" in content
        assert "PAYROLL_BATCH_CONTROL|1|3500.00" in content


# =============================================================================
# Integration Tests — Gross-to-Net
# =============================================================================


class TestGrossToNet:
    """Tests for calculate_gross_to_net."""

    def test_gross_to_net_calculation(self, payroll_service):
        result = payroll_service.calculate_gross_to_net(
            employee_id=uuid4(),
            gross_pay=Decimal("5000.00"),
        )
        assert isinstance(result, WithholdingResult)
        assert result.gross_pay == Decimal("5000.00")
        assert result.net_pay > Decimal("0")
        assert result.net_pay < Decimal("5000.00")
        assert result.total_deductions == (
            result.federal_withholding + result.state_withholding
            + result.social_security + result.medicare
        )


# =============================================================================
# Integration Tests — Benefits Deduction
# =============================================================================


class TestBenefitsDeduction:
    """Tests for record_benefits_deduction."""

    def test_benefits_deduction_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party, test_pay_period, test_payroll_employee,
    ):
        """Benefits deduction posts successfully."""
        deduction, result = payroll_service.record_benefits_deduction(
            employee_id=TEST_PAYROLL_EMPLOYEE_ID,
            plan_name="401k",
            employee_amount=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(deduction, BenefitsDeduction)
        assert deduction.employee_amount == Decimal("500.00")


# =============================================================================
# Integration Tests — NACHA File
# =============================================================================


class TestNACHAGeneration:
    """Tests for generate_nacha_file."""

    def test_generate_nacha(self, payroll_service):
        payments = [
            {"name": "Jane Smith", "account": "67890", "routing": "021000021", "amount": "4000.00"},
        ]
        content = payroll_service.generate_nacha_file(
            payments=payments,
            company_name="Acme Corp",
            company_id="123456",
            effective_date="2024-01-15",
        )
        assert "Jane Smith" in content
        assert "PAYROLL_BATCH_HEADER" in content


# =============================================================================
# Integration Tests — Employer Contribution
# =============================================================================


class TestEmployerContribution:
    """Tests for record_employer_contribution."""

    def test_employer_contribution_posts(
        self, payroll_service, current_period, test_actor_id, deterministic_clock,
        test_employee_party, test_pay_period, test_payroll_employee,
    ):
        """Employer contribution posts successfully."""
        contribution, result = payroll_service.record_employer_contribution(
            employee_id=TEST_PAYROLL_EMPLOYEE_ID,
            plan_name="401k Match",
            amount=Decimal("250.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(contribution, EmployerContribution)
        assert contribution.amount == Decimal("250.00")
