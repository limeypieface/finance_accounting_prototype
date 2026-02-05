"""
Tests for Credit Loss Module (ASC 326 / CECL).

Validates:
- calculate_ecl: pure ECL calculation (loss rate + PD/LGD)
- record_provision: posts Dr BAD_DEBT_EXPENSE / Cr ALLOWANCE_DOUBTFUL
- adjust_provision: posts adjustment
- record_write_off: posts Dr ALLOWANCE_DOUBTFUL / Cr ACCOUNTS_RECEIVABLE
- record_recovery: posts Dr ACCOUNTS_RECEIVABLE / Cr ALLOWANCE_DOUBTFUL
- run_vintage_analysis: pure vintage calculation
- apply_forward_looking: pure forward-looking adjustment
- get_disclosure_data: pure query

Also validates calculations:
- calculate_ecl_loss_rate, calculate_ecl_pd_lgd
- calculate_vintage_loss_curve, apply_forward_looking_adjustment
- calculate_provision_change
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.credit_loss.calculations import (
    apply_forward_looking_adjustment,
    calculate_ecl_loss_rate,
    calculate_ecl_pd_lgd,
    calculate_provision_change,
    calculate_vintage_loss_curve,
)
from finance_modules.credit_loss.models import (
    CreditPortfolio,
    ECLEstimate,
    ForwardLookingAdjustment,
    LossRate,
    VintageAnalysis,
)
from finance_modules.credit_loss.service import CreditLossService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def credit_loss_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide CreditLossService for integration testing. party_service + test_actor_party for G14."""
    return CreditLossService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestCreditLossModels:
    """Verify credit loss models are frozen dataclasses."""

    def test_ecl_estimate(self):
        ecl = ECLEstimate(
            id=uuid4(),
            segment="commercial",
            as_of_date=date(2024, 12, 31),
            gross_receivable=Decimal("1000000"),
            loss_rate=Decimal("0.035"),
            ecl_amount=Decimal("35000"),
        )
        assert ecl.method == "loss_rate"

    def test_vintage_analysis(self):
        va = VintageAnalysis(
            segment="consumer",
            origination_period="2023-Q1",
            original_balance=Decimal("500000"),
            current_balance=Decimal("450000"),
            cumulative_losses=Decimal("15000"),
            loss_rate=Decimal("0.030"),
        )
        assert va.periods_aged == 0

    def test_loss_rate(self):
        lr = LossRate(
            segment="commercial",
            period="2024-Q4",
            gross_balance=Decimal("1000000"),
            write_offs=Decimal("25000"),
        )
        assert lr.recoveries == Decimal("0")

    def test_credit_portfolio(self):
        cp = CreditPortfolio(
            segment="commercial",
            balance=Decimal("5000000"),
        )
        assert cp.risk_rating == "standard"
        assert cp.currency == "USD"

    def test_forward_looking_adjustment(self):
        fla = ForwardLookingAdjustment(
            factor_name="GDP_forecast",
            base_rate=Decimal("0.035"),
            adjustment_pct=Decimal("0.10"),
            adjusted_rate=Decimal("0.0385"),
        )
        assert fla.rationale == ""


# =============================================================================
# Calculation Tests
# =============================================================================


class TestCreditLossCalculations:
    """Test pure CECL calculation functions."""

    def test_ecl_loss_rate(self):
        ecl = calculate_ecl_loss_rate(Decimal("1000000"), Decimal("0.035"))
        assert ecl == Decimal("35000.00")

    def test_ecl_pd_lgd(self):
        ecl = calculate_ecl_pd_lgd(
            Decimal("1000000"),
            Decimal("0.05"),
            Decimal("0.40"),
        )
        assert ecl == Decimal("20000.00")

    def test_ecl_pd_lgd_with_ead(self):
        ecl = calculate_ecl_pd_lgd(
            Decimal("1000000"),
            Decimal("0.05"),
            Decimal("0.40"),
            exposure_at_default=Decimal("800000"),
        )
        assert ecl == Decimal("16000.00")

    def test_vintage_loss_curve(self):
        rate = calculate_vintage_loss_curve(Decimal("500000"), Decimal("15000"))
        assert rate == Decimal("0.0300")

    def test_vintage_loss_curve_zero_balance(self):
        rate = calculate_vintage_loss_curve(Decimal("0"), Decimal("0"))
        assert rate == Decimal("0")

    def test_forward_looking_adjustment(self):
        adjusted = apply_forward_looking_adjustment(
            Decimal("0.035"), Decimal("0.10"),
        )
        assert adjusted == Decimal("0.0385")

    def test_provision_change_increase(self):
        change = calculate_provision_change(Decimal("40000"), Decimal("35000"))
        assert change == Decimal("5000")

    def test_provision_change_decrease(self):
        change = calculate_provision_change(Decimal("30000"), Decimal("35000"))
        assert change == Decimal("-5000")


# =============================================================================
# Integration Tests — ECL Calculation
# =============================================================================


class TestECLCalculation:
    """Tests for calculate_ecl."""

    def test_ecl_loss_rate_method(self, credit_loss_service):
        ecl = credit_loss_service.calculate_ecl(
            segment="commercial",
            gross_balance=Decimal("1000000"),
            historical_loss_rate=Decimal("0.035"),
        )
        assert isinstance(ecl, ECLEstimate)
        assert ecl.ecl_amount == Decimal("35000.00")
        assert ecl.method == "loss_rate"

    def test_ecl_pd_lgd_method(self, credit_loss_service):
        ecl = credit_loss_service.calculate_ecl(
            segment="consumer",
            gross_balance=Decimal("500000"),
            historical_loss_rate=Decimal("0"),
            method="pd_lgd",
            probability_of_default=Decimal("0.08"),
            loss_given_default=Decimal("0.50"),
        )
        assert isinstance(ecl, ECLEstimate)
        assert ecl.ecl_amount == Decimal("20000.00")
        assert ecl.method == "pd_lgd"


# =============================================================================
# Integration Tests — Provision
# =============================================================================


class TestProvision:
    """Tests for provision posting."""

    def test_provision_posts(
        self, credit_loss_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        result = credit_loss_service.record_provision(
            segment="commercial",
            amount=Decimal("35000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED

    def test_adjustment_posts(
        self, credit_loss_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        result = credit_loss_service.adjust_provision(
            segment="commercial",
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests — Write-Off & Recovery
# =============================================================================


class TestWriteOffRecovery:
    """Tests for write-off and recovery."""

    def test_write_off_posts(
        self, credit_loss_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        result = credit_loss_service.record_write_off(
            customer_id=uuid4(),
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED

    def test_recovery_posts(
        self, credit_loss_service, current_period, test_actor_id, deterministic_clock,
        test_customer_party,
    ):
        result = credit_loss_service.record_recovery(
            customer_id=uuid4(),
            amount=Decimal("3000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Pure Tests — Vintage & Forward-Looking
# =============================================================================


class TestVintageAndForwardLooking:
    """Tests for vintage analysis and forward-looking adjustments."""

    def test_vintage_analysis(self, credit_loss_service):
        va = credit_loss_service.run_vintage_analysis(
            segment="consumer",
            origination_period="2023-Q1",
            original_balance=Decimal("500000"),
            current_balance=Decimal("450000"),
            cumulative_losses=Decimal("15000"),
            periods_aged=8,
        )
        assert isinstance(va, VintageAnalysis)
        assert va.loss_rate == Decimal("0.0300")
        assert va.periods_aged == 8

    def test_forward_looking(self, credit_loss_service):
        fla = credit_loss_service.apply_forward_looking(
            base_rate=Decimal("0.035"),
            adjustment_pct=Decimal("0.10"),
            factor_name="GDP_forecast",
            rationale="Expected economic slowdown",
        )
        assert isinstance(fla, ForwardLookingAdjustment)
        assert fla.adjusted_rate == Decimal("0.0385")


# =============================================================================
# Pure Tests — Disclosure
# =============================================================================


class TestDisclosure:
    """Tests for disclosure data."""

    def test_disclosure_data(self, credit_loss_service):
        data = credit_loss_service.get_disclosure_data(
            as_of_date=date(2024, 12, 31),
            segments=[
                {"segment": "commercial", "gross_balance": "1000000", "allowance": "35000"},
                {"segment": "consumer", "gross_balance": "500000", "allowance": "20000"},
            ],
        )
        assert data["segment_count"] == 2
        assert data["total_gross_receivable"] == "1500000"
        assert data["total_allowance"] == "55000"
        assert data["net_receivable"] == "1445000"
