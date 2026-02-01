"""
Tests for Tax Module Deepening.

Validates new methods:
- calculate_deferred_tax: pure calculation
- record_deferred_tax_asset: posts Dr Tax Receivable / Cr Tax Expense
- record_deferred_tax_liability: posts Dr Tax Expense / Cr Tax Payable
- calculate_provision: pure calculation
- record_multi_jurisdiction_tax: posts aggregated tax
- export_tax_return_data: pure query
- record_tax_adjustment: posts adjustment

Also validates helpers:
- calculate_temporary_differences, calculate_dta_valuation_allowance
- calculate_effective_tax_rate, aggregate_multi_jurisdiction
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.services.module_posting_service import ModulePostingStatus
from finance_modules.tax.helpers import (
    aggregate_multi_jurisdiction,
    calculate_dta_valuation_allowance,
    calculate_effective_tax_rate,
    calculate_temporary_differences,
)
from finance_modules.tax.models import (
    DeferredTaxAsset,
    DeferredTaxLiability,
    Jurisdiction,
    TaxProvision,
    TemporaryDifference,
)
from finance_modules.tax.service import TaxService
from tests.modules.conftest import TEST_TAX_JURISDICTION_ID


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tax_service(session, module_role_resolver, deterministic_clock, register_modules):
    """Provide TaxService for integration testing."""
    return TaxService(
        session=session,
        role_resolver=module_role_resolver,
        clock=deterministic_clock,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestNewTaxModels:
    """Verify new tax models are frozen dataclasses."""

    def test_temporary_difference(self):
        td = TemporaryDifference(
            id=uuid4(),
            description="Depreciation difference",
            book_basis=Decimal("100000"),
            tax_basis=Decimal("80000"),
            difference_amount=Decimal("20000"),
            difference_type="taxable",
            tax_rate=Decimal("0.21"),
            deferred_amount=Decimal("4200"),
        )
        assert td.deferred_amount == Decimal("4200")
        assert td.difference_type == "taxable"

    def test_deferred_tax_asset(self):
        dta = DeferredTaxAsset(
            id=uuid4(),
            source="bad_debt_allowance",
            amount=Decimal("5000"),
            valuation_allowance=Decimal("1000"),
            net_amount=Decimal("4000"),
        )
        assert dta.net_amount == Decimal("4000")

    def test_deferred_tax_liability(self):
        dtl = DeferredTaxLiability(
            id=uuid4(),
            source="depreciation",
            amount=Decimal("8000"),
        )
        assert dtl.period == ""

    def test_tax_provision(self):
        prov = TaxProvision(
            period="2024-Q4",
            current_tax_expense=Decimal("50000"),
            deferred_tax_expense=Decimal("10000"),
            total_tax_expense=Decimal("60000"),
            effective_rate=Decimal("0.24"),
            pre_tax_income=Decimal("250000"),
        )
        assert prov.total_tax_expense == Decimal("60000")

    def test_jurisdiction(self):
        j = Jurisdiction(
            code="CA",
            name="California",
            tax_rate=Decimal("0.0884"),
        )
        assert j.jurisdiction_type == "state"
        assert j.is_active is True


# =============================================================================
# Helper Tests
# =============================================================================


class TestTaxHelpers:
    """Test pure tax helper functions."""

    def test_temporary_difference_taxable(self):
        amount, diff_type = calculate_temporary_differences(
            Decimal("100000"), Decimal("80000"),
        )
        assert amount == Decimal("20000")
        assert diff_type == "taxable"

    def test_temporary_difference_deductible(self):
        amount, diff_type = calculate_temporary_differences(
            Decimal("80000"), Decimal("100000"),
        )
        assert amount == Decimal("20000")
        assert diff_type == "deductible"

    def test_temporary_difference_none(self):
        amount, diff_type = calculate_temporary_differences(
            Decimal("100000"), Decimal("100000"),
        )
        assert amount == Decimal("0")
        assert diff_type == "none"

    def test_valuation_allowance_full(self):
        va = calculate_dta_valuation_allowance(
            Decimal("10000"), Decimal("0"),
        )
        assert va == Decimal("10000")

    def test_valuation_allowance_partial(self):
        va = calculate_dta_valuation_allowance(
            Decimal("10000"), Decimal("0.70"),
        )
        assert va == Decimal("3000.00")

    def test_valuation_allowance_none(self):
        va = calculate_dta_valuation_allowance(
            Decimal("10000"), Decimal("1.0"),
        )
        assert va == Decimal("0")

    def test_effective_tax_rate(self):
        rate = calculate_effective_tax_rate(
            Decimal("60000"), Decimal("250000"),
        )
        assert rate == Decimal("0.2400")

    def test_effective_tax_rate_zero_income(self):
        rate = calculate_effective_tax_rate(
            Decimal("60000"), Decimal("0"),
        )
        assert rate == Decimal("0")

    def test_aggregate_multi_jurisdiction(self):
        jurisdictions = [
            {"jurisdiction": "CA", "taxable_amount": "100000", "tax_rate": "0.0884", "tax_amount": "8840"},
            {"jurisdiction": "NY", "taxable_amount": "50000", "tax_rate": "0.0685", "tax_amount": "3425"},
        ]
        result = aggregate_multi_jurisdiction(jurisdictions)
        assert result["jurisdiction_count"] == 2
        assert result["total_taxable"] == Decimal("150000")
        assert result["total_tax"] == Decimal("12265")


# =============================================================================
# Integration Tests — Deferred Tax
# =============================================================================


class TestDeferredTax:
    """Tests for deferred tax calculation and posting."""

    def test_calculate_deferred_tax_taxable(self, tax_service):
        td = tax_service.calculate_deferred_tax(
            book_basis=Decimal("100000"),
            tax_basis=Decimal("80000"),
            tax_rate=Decimal("0.21"),
        )
        assert isinstance(td, TemporaryDifference)
        assert td.difference_type == "taxable"
        assert td.difference_amount == Decimal("20000")
        assert td.deferred_amount == Decimal("4200.00")

    def test_record_dta_posts(
        self, tax_service, current_period, test_actor_id, test_tax_jurisdiction, deterministic_clock,
    ):
        dta, result = tax_service.record_deferred_tax_asset(
            source="bad_debt_allowance",
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(dta, DeferredTaxAsset)
        assert dta.amount == Decimal("5000.00")
        assert dta.net_amount == Decimal("5000.00")

    def test_record_dta_with_valuation_allowance(
        self, tax_service, current_period, test_actor_id, test_tax_jurisdiction, deterministic_clock,
    ):
        dta, result = tax_service.record_deferred_tax_asset(
            source="warranty_reserve",
            amount=Decimal("10000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            realizability_percentage=Decimal("0.70"),
        )
        assert result.status == ModulePostingStatus.POSTED
        assert dta.valuation_allowance == Decimal("3000.00")
        assert dta.net_amount == Decimal("7000.00")

    def test_record_dtl_posts(
        self, tax_service, current_period, test_actor_id, test_tax_jurisdiction, deterministic_clock,
    ):
        dtl, result = tax_service.record_deferred_tax_liability(
            source="depreciation",
            amount=Decimal("8000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert isinstance(dtl, DeferredTaxLiability)
        assert dtl.amount == Decimal("8000.00")


# =============================================================================
# Integration Tests — Provision
# =============================================================================


class TestTaxProvision:
    """Tests for calculate_provision."""

    def test_provision_basic(self, tax_service):
        prov = tax_service.calculate_provision(
            period="2024-Q4",
            current_tax_expense=Decimal("50000"),
            deferred_tax_expense=Decimal("10000"),
            pre_tax_income=Decimal("250000"),
        )
        assert isinstance(prov, TaxProvision)
        assert prov.total_tax_expense == Decimal("60000")
        assert prov.effective_rate == Decimal("0.2400")


# =============================================================================
# Integration Tests — Multi-Jurisdiction
# =============================================================================


class TestMultiJurisdiction:
    """Tests for record_multi_jurisdiction_tax."""

    def test_multi_jurisdiction_posts(
        self, tax_service, current_period, test_actor_id, test_tax_jurisdiction, deterministic_clock,
    ):
        jurisdictions = [
            {"jurisdiction": "CA", "taxable_amount": "100000", "tax_rate": "0.0884", "tax_amount": "8840"},
            {"jurisdiction": "NY", "taxable_amount": "50000", "tax_rate": "0.0685", "tax_amount": "3425"},
        ]
        summary, result = tax_service.record_multi_jurisdiction_tax(
            jurisdictions=jurisdictions,
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            jurisdiction_id=TEST_TAX_JURISDICTION_ID,
        )
        assert result.status == ModulePostingStatus.POSTED
        assert summary["total_tax"] == Decimal("12265")
        assert summary["jurisdiction_count"] == 2


# =============================================================================
# Integration Tests — Export
# =============================================================================


class TestTaxExport:
    """Tests for export_tax_return_data."""

    def test_export_json(self, tax_service):
        data = tax_service.export_tax_return_data(
            period="2024-Q4",
            jurisdiction="CA",
            gross_sales=Decimal("500000"),
            taxable_sales=Decimal("450000"),
            exempt_sales=Decimal("50000"),
            tax_collected=Decimal("39780"),
        )
        assert data["format"] == "JSON"
        assert data["period"] == "2024-Q4"
        assert data["jurisdiction"] == "CA"

    def test_export_unsupported_format(self, tax_service):
        with pytest.raises(ValueError, match="Unsupported export format"):
            tax_service.export_tax_return_data(
                period="2024-Q4",
                jurisdiction="CA",
                gross_sales=Decimal("500000"),
                taxable_sales=Decimal("450000"),
                exempt_sales=Decimal("50000"),
                tax_collected=Decimal("39780"),
                format="XML",
            )


# =============================================================================
# Integration Tests — Tax Adjustment
# =============================================================================


class TestTaxAdjustment:
    """Tests for record_tax_adjustment."""

    def test_adjustment_posts(
        self, tax_service, current_period, test_actor_id, test_tax_jurisdiction, deterministic_clock,
    ):
        result = tax_service.record_tax_adjustment(
            period="2023-Q4",
            amount=Decimal("5000.00"),
            effective_date=deterministic_clock.now().date(),
            actor_id=test_actor_id,
            reason="Prior period understatement",
        )
        assert result.status == ModulePostingStatus.POSTED
