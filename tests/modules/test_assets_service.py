"""
Tests for Fixed Assets Module Service.

Validates:
- Service importability and constructor wiring
- Real integration: acquisition, depreciation, disposal, impairment, scrap
- Engine composition: VarianceCalculator, AllocationEngine
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.assets.service import FixedAssetService
from tests.modules.conftest import TEST_ASSET_CATEGORY_ID, TEST_ASSET_ID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def assets_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide FixedAssetService for integration testing."""
    return FixedAssetService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


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
