"""
Tests for Lease Accounting Module — ASC 842.

Validates:
- classify_lease: classification logic (5 criteria)
- record_initial_recognition: finance and operating (posts)
- generate_amortization_schedule: pure calculation
- record_periodic_payment: posts
- accrue_interest: posts
- record_amortization: finance and operating (posts)
- modify_lease: remeasurement (posts)
- terminate_early: derecognition (posts)
- get_lease_portfolio, get_disclosure_data: queries
- calculations: pure functions
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.lease.calculations import (
    build_amortization_schedule,
    calculate_rou_adjustment,
    classify_lease_type,
    present_value,
    remeasure_liability,
)
from finance_modules.lease.models import (
    AmortizationScheduleLine,
    Lease,
    LeaseClassification,
    LeaseLiability,
    LeaseModification,
    LeasePayment,
    ROUAsset,
)
from finance_modules.lease.service import LeaseAccountingService
from tests.modules.conftest import TEST_LEASE_ID, TEST_LESSEE_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def lease_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide LeaseAccountingService for integration testing. party_service + test_actor_party for G14."""
    return LeaseAccountingService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestLeaseModels:
    """Verify lease models are frozen dataclasses."""

    def test_lease_creation(self):
        """Lease is a frozen dataclass with defaults."""
        lease = Lease(
            id=uuid4(),
            lease_number="L-001",
            lessee_id=uuid4(),
            lessor_name="ABC Realty",
            commencement_date=date(2024, 1, 1),
            end_date=date(2029, 1, 1),
        )
        assert lease.classification == LeaseClassification.OPERATING
        assert lease.discount_rate == Decimal("0.05")

    def test_rou_asset_creation(self):
        """ROUAsset is a frozen dataclass."""
        rou = ROUAsset(
            id=uuid4(), lease_id=uuid4(),
            initial_value=Decimal("100000"),
            carrying_value=Decimal("90000"),
        )
        assert rou.accumulated_amortization == Decimal("0")

    def test_lease_liability_creation(self):
        """LeaseLiability is a frozen dataclass."""
        liability = LeaseLiability(
            id=uuid4(), lease_id=uuid4(),
            initial_value=Decimal("100000"),
            current_balance=Decimal("95000"),
        )
        assert liability.current_balance == Decimal("95000")

    def test_amortization_schedule_line(self):
        """AmortizationScheduleLine is frozen."""
        line = AmortizationScheduleLine(
            period=1, payment_date=date(2024, 2, 1),
            payment=Decimal("2000"), interest=Decimal("400"),
            principal=Decimal("1600"), balance=Decimal("98400"),
        )
        assert line.principal == Decimal("1600")


# =============================================================================
# Calculation Tests
# =============================================================================


class TestLeaseCalculations:
    """Test pure calculation functions."""

    def test_present_value_basic(self):
        """PV of annuity calculation."""
        pv = present_value(
            payment=Decimal("1000"),
            rate_per_period=Decimal("0.005"),
            num_periods=12,
        )
        # Approximate: 1000 * 11.62 ~= 11619
        assert pv > Decimal("11000")
        assert pv < Decimal("12000")

    def test_present_value_zero_rate(self):
        """Zero rate = simple sum."""
        pv = present_value(
            payment=Decimal("1000"),
            rate_per_period=Decimal("0"),
            num_periods=12,
        )
        assert pv == Decimal("12000")

    def test_build_amortization_schedule(self):
        """Build schedule with correct number of periods."""
        schedule = build_amortization_schedule(
            principal=Decimal("10000"),
            rate_per_period=Decimal("0.005"),
            payment=Decimal("860"),
            num_periods=12,
            start_date=date(2024, 1, 1),
        )
        assert len(schedule) == 12
        assert schedule[0]["period"] == 1
        assert schedule[-1]["balance"] == Decimal("0")

    def test_classify_finance_ownership_transfer(self):
        """Ownership transfer = finance lease."""
        result = classify_lease_type(
            lease_term_months=36,
            economic_life_months=120,
            pv_payments=Decimal("50000"),
            fair_value=Decimal("100000"),
            transfer_ownership=True,
        )
        assert result == "finance"

    def test_classify_finance_term_ratio(self):
        """Term >= 75% of economic life = finance."""
        result = classify_lease_type(
            lease_term_months=96,
            economic_life_months=120,
            pv_payments=Decimal("50000"),
            fair_value=Decimal("100000"),
        )
        assert result == "finance"

    def test_classify_finance_pv_ratio(self):
        """PV >= 90% of fair value = finance."""
        result = classify_lease_type(
            lease_term_months=36,
            economic_life_months=120,
            pv_payments=Decimal("95000"),
            fair_value=Decimal("100000"),
        )
        assert result == "finance"

    def test_classify_operating(self):
        """No criteria met = operating lease."""
        result = classify_lease_type(
            lease_term_months=36,
            economic_life_months=120,
            pv_payments=Decimal("50000"),
            fair_value=Decimal("100000"),
        )
        assert result == "operating"

    def test_remeasure_liability(self):
        """Remeasurement returns PV of new terms."""
        new_val = remeasure_liability(
            current_balance=Decimal("50000"),
            new_payment=Decimal("2000"),
            new_rate=Decimal("0.004"),
            remaining_periods=24,
        )
        assert new_val > Decimal("0")
        assert new_val < Decimal("50000")

    def test_calculate_rou_adjustment(self):
        """ROU adjustment = new liability - old liability."""
        adj = calculate_rou_adjustment(
            current_rou=Decimal("50000"),
            old_liability=Decimal("45000"),
            new_liability=Decimal("48000"),
        )
        assert adj == Decimal("3000")


# =============================================================================
# Integration Tests — Classification
# =============================================================================


class TestClassifyLease:
    """Tests for classify_lease service method."""

    def test_classify_finance(self, lease_service):
        """Service classifies as finance when PV >= 90% fair value."""
        result = lease_service.classify_lease(
            lease_term_months=60,
            economic_life_months=120,
            monthly_payment=Decimal("2000"),
            discount_rate=Decimal("0.06"),
            fair_value=Decimal("100000"),
        )
        # PV of 60 payments at 0.5%/mo of 2000 ~= 103,451 => 103% of 100k => finance
        assert result == LeaseClassification.FINANCE

    def test_classify_operating(self, lease_service):
        """Service classifies as operating when no criteria met."""
        result = lease_service.classify_lease(
            lease_term_months=24,
            economic_life_months=120,
            monthly_payment=Decimal("500"),
            discount_rate=Decimal("0.06"),
            fair_value=Decimal("100000"),
        )
        assert result == LeaseClassification.OPERATING


# =============================================================================
# Integration Tests — Initial Recognition
# =============================================================================


class TestInitialRecognition:
    """Tests for record_initial_recognition."""

    def test_finance_initial_recognition(
        self, lease_service, current_period, test_actor_id, test_lessee_party,
    ):
        """Finance lease initial recognition posts ROU + liability."""
        rou, liability, result = lease_service.record_initial_recognition(
            lease_id=uuid4(),
            classification=LeaseClassification.FINANCE,
            monthly_payment=Decimal("2000.00"),
            discount_rate=Decimal("0.06"),
            lease_term_months=60,
            commencement_date=date(2024, 1, 1),
            actor_id=test_actor_id,
            lessee_id=TEST_LESSEE_ID,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert isinstance(rou, ROUAsset)
        assert isinstance(liability, LeaseLiability)
        assert rou.initial_value == liability.initial_value
        assert rou.initial_value > Decimal("0")

    def test_operating_initial_recognition(
        self, lease_service, current_period, test_actor_id, test_lessee_party,
    ):
        """Operating lease initial recognition posts ROU + liability."""
        rou, liability, result = lease_service.record_initial_recognition(
            lease_id=uuid4(),
            classification=LeaseClassification.OPERATING,
            monthly_payment=Decimal("1500.00"),
            discount_rate=Decimal("0.05"),
            lease_term_months=36,
            commencement_date=date(2024, 1, 1),
            actor_id=test_actor_id,
            lessee_id=TEST_LESSEE_ID,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success


# =============================================================================
# Integration Tests — Periodic Operations
# =============================================================================


class TestPeriodicOperations:
    """Tests for periodic payment, interest, amortization."""

    def test_periodic_payment_posts(
        self, lease_service, current_period, test_actor_id, deterministic_clock, test_lease,
    ):
        """Lease payment posts Dr Liability / Cr Cash."""
        result = lease_service.record_periodic_payment(
            lease_id=TEST_LEASE_ID,
            payment_amount=Decimal("2000.00"),
            payment_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED

    def test_interest_accrual_posts(
        self, lease_service, current_period, test_actor_id, deterministic_clock, test_lessee_party,
    ):
        """Interest accrual posts Dr Interest / Cr Liability."""
        result = lease_service.accrue_interest(
            lease_id=uuid4(),
            interest_amount=Decimal("400.00"),
            period_end=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED

    def test_finance_amortization_posts(
        self, lease_service, current_period, test_actor_id, deterministic_clock, test_lessee_party,
    ):
        """Finance lease amortization posts."""
        result = lease_service.record_amortization(
            lease_id=uuid4(),
            amortization_amount=Decimal("1600.00"),
            classification=LeaseClassification.FINANCE,
            period_end=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED

    def test_operating_amortization_posts(
        self, lease_service, current_period, test_actor_id, deterministic_clock, test_lessee_party,
    ):
        """Operating lease amortization posts."""
        result = lease_service.record_amortization(
            lease_id=uuid4(),
            amortization_amount=Decimal("1500.00"),
            classification=LeaseClassification.OPERATING,
            period_end=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests — Modification & Termination
# =============================================================================


class TestLeaseModificationAndTermination:
    """Tests for modify_lease and terminate_early."""

    def test_modification_posts(
        self, lease_service, current_period, test_actor_id, test_lease,
    ):
        """Lease modification remeasurement posts."""
        modification, result = lease_service.modify_lease(
            lease_id=TEST_LEASE_ID,
            modification_date=date(2024, 1, 1),
            remeasurement_amount=Decimal("5000.00"),
            actor_id=test_actor_id,
            description="Extended lease term",
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(modification, LeaseModification)

    def test_early_termination_posts(
        self, lease_service, current_period, test_actor_id, test_lessee_party,
    ):
        """Early termination derecognizes ROU and liability."""
        result = lease_service.terminate_early(
            lease_id=uuid4(),
            termination_date=date(2024, 1, 1),
            remaining_liability=Decimal("30000.00"),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED


# =============================================================================
# Integration Tests — Schedule
# =============================================================================


class TestAmortizationSchedule:
    """Tests for generate_amortization_schedule."""

    def test_schedule_generation(self, lease_service):
        """Schedule has correct number of periods."""
        schedule = lease_service.generate_amortization_schedule(
            principal=Decimal("50000.00"),
            monthly_payment=Decimal("1500.00"),
            discount_rate=Decimal("0.06"),
            num_periods=36,
            start_date=date(2024, 1, 1),
        )
        assert len(schedule) == 36
        assert all(isinstance(line, AmortizationScheduleLine) for line in schedule)
        assert schedule[-1].balance == Decimal("0")


# =============================================================================
# Query Tests
# =============================================================================


class TestLeaseQueries:
    """Tests for query methods."""

    def test_get_lease_portfolio(self, lease_service):
        """Portfolio summary."""
        leases = [
            Lease(id=uuid4(), lease_number="L-001", lessee_id=uuid4(),
                  lessor_name="A", commencement_date=date(2024, 1, 1),
                  end_date=date(2029, 1, 1), classification=LeaseClassification.FINANCE),
            Lease(id=uuid4(), lease_number="L-002", lessee_id=uuid4(),
                  lessor_name="B", commencement_date=date(2024, 1, 1),
                  end_date=date(2027, 1, 1), classification=LeaseClassification.OPERATING),
        ]
        portfolio = lease_service.get_lease_portfolio(leases)
        assert portfolio["total_leases"] == 2
        assert portfolio["finance_leases"] == 1
        assert portfolio["operating_leases"] == 1

    def test_get_disclosure_data(self, lease_service):
        """Disclosure data."""
        leases = [Lease(id=uuid4(), lease_number="L-001", lessee_id=uuid4(),
                        lessor_name="A", commencement_date=date(2024, 1, 1),
                        end_date=date(2029, 1, 1))]
        rou_assets = [ROUAsset(id=uuid4(), lease_id=uuid4(),
                               initial_value=Decimal("100000"),
                               carrying_value=Decimal("80000"))]
        liabilities = [LeaseLiability(id=uuid4(), lease_id=uuid4(),
                                      initial_value=Decimal("100000"),
                                      current_balance=Decimal("85000"))]
        data = lease_service.get_disclosure_data(leases, rou_assets, liabilities)
        assert data["total_rou_assets"] == "80000"
        assert data["total_lease_liabilities"] == "85000"
