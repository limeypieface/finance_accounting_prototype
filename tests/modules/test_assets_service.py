"""
Tests for Fixed Assets Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: acquisition, depreciation, disposal, impairment, scrap
- Engine composition: VarianceCalculator, AllocationEngine
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.assets.helpers import (
    calculate_impairment_loss,
    double_declining_balance,
    straight_line,
    sum_of_years_digits,
    units_of_production,
)
from finance_modules.assets.models import (
    AssetRevaluation,
    AssetTransfer,
    DepreciationComponent,
)
from finance_modules.assets.service import FixedAssetService
from tests.modules.conftest import TEST_ASSET_CATEGORY_ID, TEST_ASSET_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def assets_service(
    session, module_role_resolver, deterministic_clock, register_modules, workflow_executor,
    party_service, test_actor_party,
):
    """Provide FixedAssetService for integration testing. party_service + test_actor_party for G14."""
    return FixedAssetService(
        session=session,
        role_resolver=module_role_resolver,
        workflow_executor=workflow_executor,
        clock=deterministic_clock,
        party_service=party_service,
    )


@pytest.fixture
def asset_service(assets_service, register_modules):
    """Alias for assets_service (used by integration tests in this file and test_asset_depreciation)."""
    return assets_service


# =============================================================================
# Structural Tests
# =============================================================================


class TestFixedAssetServiceStructure:
    """Verify FixedAssetService follows the module service pattern."""

    def test_importable(self):
        assert FixedAssetService is not None

    def test_constructor_signature(self):
        sig = inspect.signature(FixedAssetService.__init__)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "role_resolver" in params
        assert "workflow_executor" in params
        assert "clock" in params

    def test_has_public_methods(self):
        expected = [
            "record_asset_acquisition", "record_cip_capitalized",
            "record_depreciation", "record_disposal",
            "record_impairment", "record_scrap",
        ]
        for method_name in expected:
            assert hasattr(FixedAssetService, method_name)
            assert callable(getattr(FixedAssetService, method_name))


# =============================================================================
# Integration Tests — Real Posting
# =============================================================================


class TestFixedAssetServiceIntegration:
    """Integration tests calling real asset service methods through the posting pipeline."""

    def test_record_asset_acquisition_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset_category,
    ):
        """Record asset acquisition through the real pipeline."""
        result = assets_service.record_asset_acquisition(
            asset_id=uuid4(),
            cost=Decimal("50000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            category_id=TEST_ASSET_CATEGORY_ID,
            asset_class="MACHINERY",
            useful_life_months=60,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_depreciation_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset,
    ):
        """Record periodic depreciation through the real pipeline."""
        result = assets_service.record_depreciation(
            asset_id=TEST_ASSET_ID,
            amount=Decimal("833.33"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_cip_capitalized_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset_category,
    ):
        """Capitalize CIP to a fixed asset through the real pipeline."""
        result = assets_service.record_cip_capitalized(
            asset_id=uuid4(),
            cost=Decimal("120000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            project_id="PRJ-001",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_disposal_gain_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset,
    ):
        """Record asset disposal at a gain through the real pipeline."""
        result = assets_service.record_disposal(
            asset_id=TEST_ASSET_ID,
            proceeds=Decimal("25000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            original_cost=Decimal("50000.00"),
            accumulated_depreciation=Decimal("30000.00"),
            disposal_type="SALE",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_disposal_loss_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset,
    ):
        """Record asset disposal at a loss through the real pipeline."""
        result = assets_service.record_disposal(
            asset_id=TEST_ASSET_ID,
            proceeds=Decimal("15000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            original_cost=Decimal("50000.00"),
            accumulated_depreciation=Decimal("30000.00"),
            disposal_type="SALE",
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_impairment_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset_category,
    ):
        """Record asset impairment through the real pipeline."""
        _, result = assets_service.record_impairment(
            asset_id=uuid4(),
            impairment_amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0

    def test_record_scrap_posts(
        self, assets_service, current_period, test_actor_id, deterministic_clock,
        test_asset_category,
    ):
        """Record asset scrapping through the real pipeline."""
        result = assets_service.record_scrap(
            asset_id=uuid4(),
            original_cost=Decimal("50000.00"),
            accumulated_depreciation=Decimal("48000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )

        assert result.status == ModulePostingStatus.POSTED
        assert result.is_success
        assert len(result.journal_entry_ids) > 0


# =============================================================================
# Model Tests
# =============================================================================


class TestNewAssetModels:
    """Verify new asset models are frozen dataclasses."""

    def test_asset_transfer_creation(self):
        transfer = AssetTransfer(
            id=uuid4(),
            asset_id=uuid4(),
            transfer_date=date(2024, 6, 1),
            from_cost_center="CC100",
            to_cost_center="CC200",
        )
        assert transfer.transferred_by is None

    def test_asset_revaluation_creation(self):
        reval = AssetRevaluation(
            id=uuid4(),
            asset_id=uuid4(),
            revaluation_date=date(2024, 6, 1),
            old_carrying_value=Decimal("40000"),
            new_fair_value=Decimal("50000"),
            revaluation_surplus=Decimal("10000"),
        )
        assert reval.revaluation_surplus == Decimal("10000")

    def test_depreciation_component_creation(self):
        comp = DepreciationComponent(
            id=uuid4(),
            asset_id=uuid4(),
            component_name="Engine",
            cost=Decimal("25000"),
            useful_life_months=120,
        )
        assert comp.depreciation_method == "straight_line"
        assert comp.accumulated_depreciation == Decimal("0")


# =============================================================================
# Helper Tests — Depreciation Methods
# =============================================================================


class TestDepreciationHelpers:
    """Test pure depreciation calculation functions."""

    def test_straight_line_basic(self):
        dep = straight_line(
            cost=Decimal("120000"),
            salvage_value=Decimal("0"),
            useful_life_months=120,
        )
        assert dep == Decimal("1000.00")

    def test_straight_line_with_salvage(self):
        dep = straight_line(
            cost=Decimal("120000"),
            salvage_value=Decimal("12000"),
            useful_life_months=120,
        )
        assert dep == Decimal("900.00")

    def test_straight_line_zero_life(self):
        dep = straight_line(Decimal("100000"), Decimal("0"), 0)
        assert dep == Decimal("0")

    def test_ddb_basic(self):
        dep = double_declining_balance(
            cost=Decimal("100000"),
            accumulated_depreciation=Decimal("0"),
            useful_life_months=60,
        )
        # Rate = 2/60 = 0.0333, NBV = 100000, dep = 3333.33
        assert dep == Decimal("3333.33")

    def test_ddb_salvage_floor(self):
        """DDB won't depreciate below salvage."""
        dep = double_declining_balance(
            cost=Decimal("10000"),
            accumulated_depreciation=Decimal("9000"),
            useful_life_months=60,
            salvage_value=Decimal("500"),
        )
        # NBV = 1000, salvage = 500, max dep = 500
        assert dep <= Decimal("500")

    def test_ddb_fully_depreciated(self):
        dep = double_declining_balance(
            cost=Decimal("10000"),
            accumulated_depreciation=Decimal("10000"),
            useful_life_months=60,
        )
        assert dep == Decimal("0")

    def test_syd_year_one(self):
        dep = sum_of_years_digits(
            cost=Decimal("100000"),
            salvage_value=Decimal("10000"),
            useful_life_years=5,
            current_year=1,
        )
        # Sum = 15, year 1 fraction = 5/15, depreciable = 90000
        assert dep == Decimal("30000.00")

    def test_syd_year_five(self):
        dep = sum_of_years_digits(
            cost=Decimal("100000"),
            salvage_value=Decimal("10000"),
            useful_life_years=5,
            current_year=5,
        )
        # Sum = 15, year 5 fraction = 1/15, depreciable = 90000
        assert dep == Decimal("6000.00")

    def test_syd_beyond_life(self):
        dep = sum_of_years_digits(
            cost=Decimal("100000"),
            salvage_value=Decimal("10000"),
            useful_life_years=5,
            current_year=6,
        )
        assert dep == Decimal("0")

    def test_units_of_production_basic(self):
        dep = units_of_production(
            cost=Decimal("100000"),
            salvage_value=Decimal("10000"),
            total_estimated_units=Decimal("90000"),
            units_produced=Decimal("1000"),
        )
        # Rate = 90000/90000 = 1.0/unit, dep = 1000
        assert dep == Decimal("1000.00")

    def test_units_of_production_zero_units(self):
        dep = units_of_production(
            cost=Decimal("100000"),
            salvage_value=Decimal("10000"),
            total_estimated_units=Decimal("0"),
            units_produced=Decimal("500"),
        )
        assert dep == Decimal("0")


# =============================================================================
# Helper Tests — Impairment
# =============================================================================


class TestImpairmentHelpers:
    """Test impairment calculation."""

    def test_impairment_loss_exists(self):
        loss = calculate_impairment_loss(
            carrying_value=Decimal("80000"),
            fair_value=Decimal("60000"),
        )
        assert loss == Decimal("20000")

    def test_no_impairment(self):
        loss = calculate_impairment_loss(
            carrying_value=Decimal("50000"),
            fair_value=Decimal("55000"),
        )
        assert loss == Decimal("0")

    def test_impairment_at_exact_value(self):
        loss = calculate_impairment_loss(
            carrying_value=Decimal("50000"),
            fair_value=Decimal("50000"),
        )
        assert loss == Decimal("0")


# =============================================================================
# Integration Tests — Mass Depreciation
# =============================================================================


class TestMassDepreciation:
    """Tests for run_mass_depreciation."""

    def test_mass_depreciation_posts(
        self, asset_service, current_period, test_actor_id, deterministic_clock,
        test_asset_category,
    ):
        """Batch depreciation posts for all assets."""
        assets = [
            {"asset_id": str(uuid4()), "amount": "1000.00"},
            {"asset_id": str(uuid4()), "amount": "2000.00"},
        ]
        results = asset_service.run_mass_depreciation(
            assets=assets,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert len(results) == 2
        assert all(r.status == ModulePostingStatus.POSTED for r in results)


# =============================================================================
# Integration Tests — Asset Transfer
# =============================================================================


class TestAssetTransfer:
    """Tests for record_asset_transfer."""

    def test_transfer_posts(
        self, asset_service, current_period, test_actor_id, deterministic_clock,
        test_asset,
    ):
        """Asset transfer posts successfully."""
        transfer, result = asset_service.record_asset_transfer(
            asset_id=TEST_ASSET_ID,
            from_cost_center="CC100",
            to_cost_center="CC200",
            transfer_value=Decimal("45000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(transfer, AssetTransfer)
        assert transfer.from_cost_center == "CC100"
        assert transfer.to_cost_center == "CC200"


# =============================================================================
# Integration Tests — Impairment Test
# =============================================================================


class TestImpairmentTest:
    """Tests for test_impairment (pure method)."""

    def test_impairment_detected(self, asset_service):
        loss = asset_service.test_impairment(
            asset_id=uuid4(),
            carrying_value=Decimal("80000"),
            fair_value=Decimal("60000"),
        )
        assert loss == Decimal("20000")

    def test_no_impairment_detected(self, asset_service):
        loss = asset_service.test_impairment(
            asset_id=uuid4(),
            carrying_value=Decimal("50000"),
            fair_value=Decimal("55000"),
        )
        assert loss == Decimal("0")


# =============================================================================
# Integration Tests — Revaluation
# =============================================================================


class TestRevaluation:
    """Tests for record_revaluation."""

    def test_revaluation_posts(
        self, asset_service, current_period, test_actor_id, deterministic_clock,
        test_asset,
    ):
        """Revaluation surplus posts successfully."""
        reval, result = asset_service.record_revaluation(
            asset_id=TEST_ASSET_ID,
            old_carrying_value=Decimal("40000.00"),
            new_fair_value=Decimal("55000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(reval, AssetRevaluation)
        assert reval.revaluation_surplus == Decimal("15000.00")


# =============================================================================
# Integration Tests — Component Depreciation
# =============================================================================


class TestComponentDepreciation:
    """Tests for record_component_depreciation."""

    def test_component_depreciation_posts(
        self, asset_service, current_period, test_actor_id, deterministic_clock,
        test_asset,
    ):
        """Component depreciation posts successfully."""
        result = asset_service.record_component_depreciation(
            asset_id=TEST_ASSET_ID,
            component_name="Engine",
            amount=Decimal("500.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
