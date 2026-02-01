"""ORM round-trip tests for Tax module.

Verifies that every Tax ORM model can be persisted, queried back with
correct field values, and that FK / unique constraints are enforced by
the database.

Models under test (10):
    TaxJurisdictionModel, TaxRateModel, TaxExemptionModel,
    TaxTransactionModel, TaxReturnModel, TemporaryDifferenceModel,
    DeferredTaxAssetModel, DeferredTaxLiabilityModel, TaxProvisionModel,
    JurisdictionModel
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.tax.orm import (
    DeferredTaxAssetModel,
    DeferredTaxLiabilityModel,
    JurisdictionModel,
    TaxExemptionModel,
    TaxJurisdictionModel,
    TaxProvisionModel,
    TaxRateModel,
    TaxReturnModel,
    TaxTransactionModel,
    TemporaryDifferenceModel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jurisdiction(session, test_actor_id, **overrides):
    """Create and flush a TaxJurisdictionModel with sensible defaults."""
    defaults = dict(
        code=f"JUR-{uuid4().hex[:8]}",
        name="Test Jurisdiction",
        jurisdiction_type="state",
        tax_type="sales",
        is_active=True,
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = TaxJurisdictionModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


def _make_return(session, test_actor_id, jurisdiction_id, **overrides):
    """Create and flush a TaxReturnModel with sensible defaults."""
    defaults = dict(
        jurisdiction_id=jurisdiction_id,
        tax_type="sales",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 3, 31),
        filing_due_date=date(2024, 4, 30),
        gross_sales=Decimal("100000.00"),
        taxable_sales=Decimal("80000.00"),
        exempt_sales=Decimal("20000.00"),
        tax_collected=Decimal("6400.00"),
        tax_due=Decimal("6400.00"),
        status="draft",
        created_by_id=test_actor_id,
    )
    defaults.update(overrides)
    obj = TaxReturnModel(**defaults)
    session.add(obj)
    session.flush()
    return obj


# ===================================================================
# TaxJurisdictionModel
# ===================================================================


class TestTaxJurisdictionModelORM:
    """Round-trip persistence tests for TaxJurisdictionModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = TaxJurisdictionModel(
            code="US-CA",
            name="California",
            jurisdiction_type="state",
            tax_type="sales",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxJurisdictionModel, obj.id)
        assert queried is not None
        assert queried.code == "US-CA"
        assert queried.name == "California"
        assert queried.jurisdiction_type == "state"
        assert queried.tax_type == "sales"
        assert queried.is_active is True
        assert queried.created_by_id == test_actor_id

    def test_self_referential_parent(self, session, test_actor_id):
        parent = _make_jurisdiction(session, test_actor_id, code="US", name="United States", jurisdiction_type="country")
        child = TaxJurisdictionModel(
            code="US-TX",
            name="Texas",
            jurisdiction_type="state",
            parent_id=parent.id,
            tax_type="sales",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(child)
        session.flush()

        queried = session.get(TaxJurisdictionModel, child.id)
        assert queried.parent_id == parent.id
        assert queried.parent is not None
        assert queried.parent.code == "US"

    def test_unique_code_constraint(self, session, test_actor_id):
        _make_jurisdiction(session, test_actor_id, code="UNIQUE-TAX-JUR")
        dup = TaxJurisdictionModel(
            code="UNIQUE-TAX-JUR",
            name="Duplicate",
            jurisdiction_type="state",
            tax_type="income",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_parent_nonexistent(self, session, test_actor_id):
        obj = TaxJurisdictionModel(
            code="ORPHAN",
            name="Orphan",
            jurisdiction_type="state",
            parent_id=uuid4(),
            tax_type="sales",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# TaxRateModel
# ===================================================================


class TestTaxRateModelORM:
    """Round-trip persistence tests for TaxRateModel."""

    def test_create_and_query(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        obj = TaxRateModel(
            jurisdiction_id=jur.id,
            tax_category="standard",
            rate=Decimal("0.0725"),
            effective_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxRateModel, obj.id)
        assert queried is not None
        assert queried.jurisdiction_id == jur.id
        assert queried.tax_category == "standard"
        assert queried.rate == Decimal("0.0725")
        assert queried.effective_date == date(2024, 1, 1)
        assert queried.end_date == date(2024, 12, 31)

    def test_jurisdiction_relationship(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id, code="NY", name="New York")
        obj = TaxRateModel(
            jurisdiction_id=jur.id,
            tax_category="reduced",
            rate=Decimal("0.04"),
            effective_date=date(2024, 6, 1),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxRateModel, obj.id)
        assert queried.jurisdiction is not None
        assert queried.jurisdiction.code == "NY"

    def test_unique_jurisdiction_category_date(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        TaxRateModel(
            jurisdiction_id=jur.id,
            tax_category="food",
            rate=Decimal("0.02"),
            effective_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(TaxRateModel(
            jurisdiction_id=jur.id,
            tax_category="food",
            rate=Decimal("0.02"),
            effective_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = TaxRateModel(
            jurisdiction_id=jur.id,
            tax_category="food",
            rate=Decimal("0.03"),
            effective_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_jurisdiction_nonexistent(self, session, test_actor_id):
        obj = TaxRateModel(
            jurisdiction_id=uuid4(),
            tax_category="standard",
            rate=Decimal("0.05"),
            effective_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# TaxExemptionModel
# ===================================================================


class TestTaxExemptionModelORM:
    """Round-trip persistence tests for TaxExemptionModel."""

    def test_create_and_query(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        obj = TaxExemptionModel(
            exemption_type="resale",
            jurisdiction_id=jur.id,
            certificate_number="CERT-001",
            effective_date=date(2024, 1, 1),
            expiration_date=date(2025, 12, 31),
            is_verified=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxExemptionModel, obj.id)
        assert queried is not None
        assert queried.exemption_type == "resale"
        assert queried.certificate_number == "CERT-001"
        assert queried.effective_date == date(2024, 1, 1)
        assert queried.expiration_date == date(2025, 12, 31)
        assert queried.is_verified is True

    def test_jurisdiction_relationship(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id, code="FL", name="Florida")
        obj = TaxExemptionModel(
            exemption_type="nonprofit",
            jurisdiction_id=jur.id,
            certificate_number="NP-FL-001",
            effective_date=date(2024, 1, 1),
            is_verified=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxExemptionModel, obj.id)
        assert queried.jurisdiction.code == "FL"

    def test_unique_jurisdiction_cert(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        TaxExemptionModel(
            exemption_type="resale",
            jurisdiction_id=jur.id,
            certificate_number="DUP-CERT",
            effective_date=date(2024, 1, 1),
            is_verified=False,
            created_by_id=test_actor_id,
        )
        session.add(TaxExemptionModel(
            exemption_type="resale",
            jurisdiction_id=jur.id,
            certificate_number="DUP-CERT",
            effective_date=date(2024, 1, 1),
            is_verified=False,
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = TaxExemptionModel(
            exemption_type="nonprofit",
            jurisdiction_id=jur.id,
            certificate_number="DUP-CERT",
            effective_date=date(2024, 6, 1),
            is_verified=True,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_jurisdiction_nonexistent(self, session, test_actor_id):
        obj = TaxExemptionModel(
            exemption_type="resale",
            jurisdiction_id=uuid4(),
            certificate_number="BAD-FK",
            effective_date=date(2024, 1, 1),
            is_verified=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# TaxTransactionModel
# ===================================================================


class TestTaxTransactionModelORM:
    """Round-trip persistence tests for TaxTransactionModel."""

    def test_create_and_query(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        source_id = uuid4()
        obj = TaxTransactionModel(
            source_type="invoice",
            source_id=source_id,
            transaction_date=date(2024, 3, 15),
            jurisdiction_id=jur.id,
            tax_type="sales",
            taxable_amount=Decimal("1000.00"),
            exempt_amount=Decimal("0.00"),
            tax_amount=Decimal("72.50"),
            tax_rate=Decimal("0.0725"),
            is_reported=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxTransactionModel, obj.id)
        assert queried is not None
        assert queried.source_type == "invoice"
        assert queried.source_id == source_id
        assert queried.transaction_date == date(2024, 3, 15)
        assert queried.taxable_amount == Decimal("1000.00")
        assert queried.tax_amount == Decimal("72.50")
        assert queried.tax_rate == Decimal("0.0725")
        assert queried.is_reported is False

    def test_jurisdiction_relationship(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id, code="WA", name="Washington")
        obj = TaxTransactionModel(
            source_type="credit_memo",
            source_id=uuid4(),
            transaction_date=date(2024, 5, 1),
            jurisdiction_id=jur.id,
            tax_type="sales",
            taxable_amount=Decimal("500.00"),
            tax_amount=Decimal("50.00"),
            tax_rate=Decimal("0.10"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxTransactionModel, obj.id)
        assert queried.jurisdiction.code == "WA"

    def test_tax_return_relationship(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        ret = _make_return(session, test_actor_id, jur.id)
        obj = TaxTransactionModel(
            source_type="invoice",
            source_id=uuid4(),
            transaction_date=date(2024, 2, 10),
            jurisdiction_id=jur.id,
            tax_type="sales",
            taxable_amount=Decimal("2000.00"),
            tax_amount=Decimal("145.00"),
            tax_rate=Decimal("0.0725"),
            is_reported=True,
            tax_return_id=ret.id,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxTransactionModel, obj.id)
        assert queried.tax_return_id == ret.id
        assert queried.tax_return is not None

    def test_fk_jurisdiction_nonexistent(self, session, test_actor_id):
        obj = TaxTransactionModel(
            source_type="invoice",
            source_id=uuid4(),
            transaction_date=date(2024, 1, 1),
            jurisdiction_id=uuid4(),
            tax_type="sales",
            taxable_amount=Decimal("100.00"),
            tax_amount=Decimal("10.00"),
            tax_rate=Decimal("0.10"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_tax_return_nonexistent(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        obj = TaxTransactionModel(
            source_type="invoice",
            source_id=uuid4(),
            transaction_date=date(2024, 1, 1),
            jurisdiction_id=jur.id,
            tax_type="sales",
            taxable_amount=Decimal("100.00"),
            tax_amount=Decimal("10.00"),
            tax_rate=Decimal("0.10"),
            tax_return_id=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# TaxReturnModel
# ===================================================================


class TestTaxReturnModelORM:
    """Round-trip persistence tests for TaxReturnModel."""

    def test_create_and_query(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        obj = TaxReturnModel(
            jurisdiction_id=jur.id,
            tax_type="sales",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 3, 31),
            filing_due_date=date(2024, 4, 30),
            gross_sales=Decimal("250000.00"),
            taxable_sales=Decimal("200000.00"),
            exempt_sales=Decimal("50000.00"),
            tax_collected=Decimal("14500.00"),
            tax_due=Decimal("14500.00"),
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxReturnModel, obj.id)
        assert queried is not None
        assert queried.jurisdiction_id == jur.id
        assert queried.tax_type == "sales"
        assert queried.period_start == date(2024, 1, 1)
        assert queried.period_end == date(2024, 3, 31)
        assert queried.filing_due_date == date(2024, 4, 30)
        assert queried.gross_sales == Decimal("250000.00")
        assert queried.taxable_sales == Decimal("200000.00")
        assert queried.exempt_sales == Decimal("50000.00")
        assert queried.tax_collected == Decimal("14500.00")
        assert queried.tax_due == Decimal("14500.00")
        assert queried.status == "draft"
        assert queried.filed_date is None
        assert queried.confirmation_number is None

    def test_filed_return(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        obj = TaxReturnModel(
            jurisdiction_id=jur.id,
            tax_type="income",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 12, 31),
            filing_due_date=date(2025, 4, 15),
            gross_sales=Decimal("500000.00"),
            taxable_sales=Decimal("400000.00"),
            exempt_sales=Decimal("100000.00"),
            tax_collected=Decimal("50000.00"),
            tax_due=Decimal("48000.00"),
            status="filed",
            filed_date=date(2025, 3, 20),
            confirmation_number="CONF-20250320-001",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxReturnModel, obj.id)
        assert queried.status == "filed"
        assert queried.filed_date == date(2025, 3, 20)
        assert queried.confirmation_number == "CONF-20250320-001"

    def test_jurisdiction_relationship(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id, code="IL", name="Illinois")
        ret = _make_return(session, test_actor_id, jur.id)

        queried = session.get(TaxReturnModel, ret.id)
        assert queried.jurisdiction.code == "IL"

    def test_transactions_relationship(self, session, test_actor_id):
        jur = _make_jurisdiction(session, test_actor_id)
        ret = _make_return(session, test_actor_id, jur.id)
        txn = TaxTransactionModel(
            source_type="invoice",
            source_id=uuid4(),
            transaction_date=date(2024, 2, 15),
            jurisdiction_id=jur.id,
            tax_type="sales",
            taxable_amount=Decimal("5000.00"),
            tax_amount=Decimal("362.50"),
            tax_rate=Decimal("0.0725"),
            is_reported=True,
            tax_return_id=ret.id,
            created_by_id=test_actor_id,
        )
        session.add(txn)
        session.flush()

        # Expire to force reload of relationship
        session.expire(ret, ["transactions"])
        queried = session.get(TaxReturnModel, ret.id)
        assert len(queried.transactions) == 1
        assert queried.transactions[0].id == txn.id

    def test_fk_jurisdiction_nonexistent(self, session, test_actor_id):
        obj = TaxReturnModel(
            jurisdiction_id=uuid4(),
            tax_type="sales",
            period_start=date(2024, 1, 1),
            period_end=date(2024, 3, 31),
            filing_due_date=date(2024, 4, 30),
            status="draft",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# TemporaryDifferenceModel
# ===================================================================


class TestTemporaryDifferenceModelORM:
    """Round-trip persistence tests for TemporaryDifferenceModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = TemporaryDifferenceModel(
            description="Depreciation timing difference",
            book_basis=Decimal("100000.00"),
            tax_basis=Decimal("80000.00"),
            difference_amount=Decimal("20000.00"),
            difference_type="taxable",
            tax_rate=Decimal("0.21"),
            deferred_amount=Decimal("4200.00"),
            period="2024-Q4",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TemporaryDifferenceModel, obj.id)
        assert queried is not None
        assert queried.description == "Depreciation timing difference"
        assert queried.book_basis == Decimal("100000.00")
        assert queried.tax_basis == Decimal("80000.00")
        assert queried.difference_amount == Decimal("20000.00")
        assert queried.difference_type == "taxable"
        assert queried.tax_rate == Decimal("0.21")
        assert queried.deferred_amount == Decimal("4200.00")
        assert queried.period == "2024-Q4"

    def test_deductible_difference(self, session, test_actor_id):
        obj = TemporaryDifferenceModel(
            description="Warranty reserve",
            book_basis=Decimal("50000.00"),
            tax_basis=Decimal("0.00"),
            difference_amount=Decimal("50000.00"),
            difference_type="deductible",
            tax_rate=Decimal("0.21"),
            deferred_amount=Decimal("10500.00"),
            period="2024-Q4",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TemporaryDifferenceModel, obj.id)
        assert queried.difference_type == "deductible"


# ===================================================================
# DeferredTaxAssetModel
# ===================================================================


class TestDeferredTaxAssetModelORM:
    """Round-trip persistence tests for DeferredTaxAssetModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = DeferredTaxAssetModel(
            source="Warranty reserve",
            amount=Decimal("10500.00"),
            valuation_allowance=Decimal("2000.00"),
            net_amount=Decimal("8500.00"),
            period="2024-Q4",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(DeferredTaxAssetModel, obj.id)
        assert queried is not None
        assert queried.source == "Warranty reserve"
        assert queried.amount == Decimal("10500.00")
        assert queried.valuation_allowance == Decimal("2000.00")
        assert queried.net_amount == Decimal("8500.00")
        assert queried.period == "2024-Q4"

    def test_zero_valuation_allowance(self, session, test_actor_id):
        obj = DeferredTaxAssetModel(
            source="Bad debt reserve",
            amount=Decimal("5000.00"),
            valuation_allowance=Decimal("0.00"),
            net_amount=Decimal("5000.00"),
            period="2024-Q2",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(DeferredTaxAssetModel, obj.id)
        assert queried.valuation_allowance == Decimal("0.00")
        assert queried.net_amount == Decimal("5000.00")


# ===================================================================
# DeferredTaxLiabilityModel
# ===================================================================


class TestDeferredTaxLiabilityModelORM:
    """Round-trip persistence tests for DeferredTaxLiabilityModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = DeferredTaxLiabilityModel(
            source="Accelerated depreciation",
            amount=Decimal("15000.00"),
            period="2024-Q4",
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(DeferredTaxLiabilityModel, obj.id)
        assert queried is not None
        assert queried.source == "Accelerated depreciation"
        assert queried.amount == Decimal("15000.00")
        assert queried.period == "2024-Q4"


# ===================================================================
# TaxProvisionModel
# ===================================================================


class TestTaxProvisionModelORM:
    """Round-trip persistence tests for TaxProvisionModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = TaxProvisionModel(
            period="2024-Q4",
            current_tax_expense=Decimal("30000.00"),
            deferred_tax_expense=Decimal("5000.00"),
            total_tax_expense=Decimal("35000.00"),
            effective_rate=Decimal("0.245"),
            pre_tax_income=Decimal("142857.14"),
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(TaxProvisionModel, obj.id)
        assert queried is not None
        assert queried.period == "2024-Q4"
        assert queried.current_tax_expense == Decimal("30000.00")
        assert queried.deferred_tax_expense == Decimal("5000.00")
        assert queried.total_tax_expense == Decimal("35000.00")
        assert queried.effective_rate == Decimal("0.245")
        assert queried.pre_tax_income == Decimal("142857.14")

    def test_unique_period_constraint(self, session, test_actor_id):
        TaxProvisionModel(
            period="2024-UNIQUE",
            current_tax_expense=Decimal("10000.00"),
            deferred_tax_expense=Decimal("1000.00"),
            total_tax_expense=Decimal("11000.00"),
            effective_rate=Decimal("0.22"),
            pre_tax_income=Decimal("50000.00"),
            created_by_id=test_actor_id,
        )
        session.add(TaxProvisionModel(
            period="2024-UNIQUE",
            current_tax_expense=Decimal("10000.00"),
            deferred_tax_expense=Decimal("1000.00"),
            total_tax_expense=Decimal("11000.00"),
            effective_rate=Decimal("0.22"),
            pre_tax_income=Decimal("50000.00"),
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = TaxProvisionModel(
            period="2024-UNIQUE",
            current_tax_expense=Decimal("20000.00"),
            deferred_tax_expense=Decimal("2000.00"),
            total_tax_expense=Decimal("22000.00"),
            effective_rate=Decimal("0.24"),
            pre_tax_income=Decimal("91666.67"),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ===================================================================
# JurisdictionModel (lightweight config entity)
# ===================================================================


class TestJurisdictionModelORM:
    """Round-trip persistence tests for JurisdictionModel."""

    def test_create_and_query(self, session, test_actor_id):
        obj = JurisdictionModel(
            code="NY",
            name="New York",
            tax_rate=Decimal("0.08"),
            jurisdiction_type="state",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(JurisdictionModel, obj.id)
        assert queried is not None
        assert queried.code == "NY"
        assert queried.name == "New York"
        assert queried.tax_rate == Decimal("0.08")
        assert queried.jurisdiction_type == "state"
        assert queried.is_active is True

    def test_unique_code_constraint(self, session, test_actor_id):
        JurisdictionModel(
            code="UNIQUE-JUR-CFG",
            name="First",
            tax_rate=Decimal("0.05"),
            jurisdiction_type="state",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(JurisdictionModel(
            code="UNIQUE-JUR-CFG",
            name="First",
            tax_rate=Decimal("0.05"),
            jurisdiction_type="state",
            is_active=True,
            created_by_id=test_actor_id,
        ))
        session.flush()
        dup = JurisdictionModel(
            code="UNIQUE-JUR-CFG",
            name="Second",
            tax_rate=Decimal("0.06"),
            jurisdiction_type="city",
            is_active=True,
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_inactive_jurisdiction(self, session, test_actor_id):
        obj = JurisdictionModel(
            code="OLD-JUR",
            name="Old Jurisdiction",
            tax_rate=Decimal("0.10"),
            jurisdiction_type="city",
            is_active=False,
            created_by_id=test_actor_id,
        )
        session.add(obj)
        session.flush()

        queried = session.get(JurisdictionModel, obj.id)
        assert queried.is_active is False
