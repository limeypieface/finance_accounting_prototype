"""ORM round-trip tests for Assets module."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from finance_modules.assets.orm import (
    AssetCategoryModel,
    AssetModel,
    DepreciationScheduleModel,
    AssetDisposalModel,
    AssetTransferModel,
    AssetRevaluationModel,
    DepreciationComponentModel,
)


# ---------------------------------------------------------------------------
# Local helpers -- create parent rows with correct ORM field names
# ---------------------------------------------------------------------------

def _make_category(session, test_actor_id, **overrides):
    """Create an AssetCategoryModel with sensible defaults."""
    fields = dict(
        code=f"CAT-{uuid4().hex[:8]}",
        name="Test Category",
        useful_life_years=5,
        depreciation_method="straight_line",
        salvage_value_percent=Decimal("10"),
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    cat = AssetCategoryModel(**fields)
    session.add(cat)
    session.flush()
    return cat


def _make_asset(session, test_actor_id, category_id, **overrides):
    """Create an AssetModel with sensible defaults."""
    fields = dict(
        asset_number=f"AST-{uuid4().hex[:8]}",
        description="Test Asset",
        category_id=category_id,
        acquisition_date=date(2024, 1, 1),
        acquisition_cost=Decimal("10000.00"),
        salvage_value=Decimal("1000.00"),
        useful_life_months=60,
        net_book_value=Decimal("9000.00"),
        status="in_service",
        created_by_id=test_actor_id,
    )
    fields.update(overrides)
    asset = AssetModel(**fields)
    session.add(asset)
    session.flush()
    return asset


# ===================================================================
# AssetCategoryModel
# ===================================================================

class TestAssetCategoryModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(
            session, test_actor_id,
            code="EQUIP-001",
            name="Equipment",
            useful_life_years=10,
            depreciation_method="straight_line",
            salvage_value_percent=Decimal("5.00"),
            gl_asset_account="1500-000",
            gl_depreciation_account="6000-000",
            gl_accumulated_depreciation_account="1550-000",
        )
        queried = session.get(AssetCategoryModel, cat.id)
        assert queried is not None
        assert queried.code == "EQUIP-001"
        assert queried.name == "Equipment"
        assert queried.useful_life_years == 10
        assert queried.depreciation_method == "straight_line"
        assert queried.salvage_value_percent == Decimal("5.00")
        assert queried.gl_asset_account == "1500-000"
        assert queried.gl_depreciation_account == "6000-000"
        assert queried.gl_accumulated_depreciation_account == "1550-000"

    def test_unique_code_constraint(self, session, test_actor_id):
        _make_category(session, test_actor_id, code="DUP-CODE")
        dup = AssetCategoryModel(
            code="DUP-CODE",
            name="Duplicate",
            useful_life_years=3,
            depreciation_method="declining_balance",
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_nullable_gl_accounts(self, session, test_actor_id):
        cat = _make_category(
            session, test_actor_id,
            gl_asset_account=None,
            gl_depreciation_account=None,
            gl_accumulated_depreciation_account=None,
        )
        queried = session.get(AssetCategoryModel, cat.id)
        assert queried.gl_asset_account is None
        assert queried.gl_depreciation_account is None
        assert queried.gl_accumulated_depreciation_account is None

    def test_relationship_to_assets(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        _make_asset(session, test_actor_id, category_id=cat.id)
        _make_asset(session, test_actor_id, category_id=cat.id)
        session.refresh(cat)
        assert len(cat.assets) == 2


# ===================================================================
# AssetModel
# ===================================================================

class TestAssetModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(
            session, test_actor_id,
            category_id=cat.id,
            asset_number="A-12345",
            description="Office Desk",
            acquisition_date=date(2024, 3, 15),
            in_service_date=date(2024, 3, 20),
            acquisition_cost=Decimal("2500.00"),
            salvage_value=Decimal("250.00"),
            useful_life_months=120,
            accumulated_depreciation=Decimal("100.00"),
            net_book_value=Decimal("2150.00"),
            status="in_service",
            serial_number="SN-XYZ-789",
        )
        queried = session.get(AssetModel, asset.id)
        assert queried is not None
        assert queried.asset_number == "A-12345"
        assert queried.description == "Office Desk"
        assert queried.acquisition_date == date(2024, 3, 15)
        assert queried.in_service_date == date(2024, 3, 20)
        assert queried.acquisition_cost == Decimal("2500.00")
        assert queried.salvage_value == Decimal("250.00")
        assert queried.useful_life_months == 120
        assert queried.accumulated_depreciation == Decimal("100.00")
        assert queried.net_book_value == Decimal("2150.00")
        assert queried.status == "in_service"
        assert queried.serial_number == "SN-XYZ-789"
        assert queried.category_id == cat.id

    def test_unique_asset_number_constraint(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        _make_asset(session, test_actor_id, category_id=cat.id, asset_number="UNIQUE-001")
        dup = AssetModel(
            asset_number="UNIQUE-001",
            description="Duplicate",
            category_id=cat.id,
            acquisition_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_fk_to_category(self, session, test_actor_id):
        asset = AssetModel(
            asset_number=f"AST-{uuid4().hex[:8]}",
            description="Orphan asset",
            category_id=uuid4(),
            acquisition_date=date(2024, 1, 1),
            created_by_id=test_actor_id,
        )
        session.add(asset)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_category(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id, name="Vehicles")
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        assert asset.category is not None
        assert asset.category.name == "Vehicles"

    def test_nullable_optional_fields(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(
            session, test_actor_id,
            category_id=cat.id,
            in_service_date=None,
            location_id=None,
            department_id=None,
            custodian_id=None,
            serial_number=None,
            purchase_order_id=None,
            vendor_id=None,
        )
        queried = session.get(AssetModel, asset.id)
        assert queried.in_service_date is None
        assert queried.location_id is None
        assert queried.department_id is None
        assert queried.custodian_id is None
        assert queried.serial_number is None
        assert queried.purchase_order_id is None
        assert queried.vendor_id is None


# ===================================================================
# DepreciationScheduleModel
# ===================================================================

class TestDepreciationScheduleModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        sched = DepreciationScheduleModel(
            asset_id=asset.id,
            period_date=date(2024, 1, 31),
            depreciation_amount=Decimal("150.00"),
            accumulated_depreciation=Decimal("150.00"),
            net_book_value=Decimal("8850.00"),
            is_posted=False,
            created_by_id=test_actor_id,
        )
        session.add(sched)
        session.flush()
        queried = session.get(DepreciationScheduleModel, sched.id)
        assert queried is not None
        assert queried.asset_id == asset.id
        assert queried.period_date == date(2024, 1, 31)
        assert queried.depreciation_amount == Decimal("150.00")
        assert queried.accumulated_depreciation == Decimal("150.00")
        assert queried.net_book_value == Decimal("8850.00")
        assert queried.is_posted is False

    def test_fk_to_asset(self, session, test_actor_id):
        sched = DepreciationScheduleModel(
            asset_id=uuid4(),
            period_date=date(2024, 2, 28),
            depreciation_amount=Decimal("100.00"),
            accumulated_depreciation=Decimal("100.00"),
            net_book_value=Decimal("9900.00"),
            created_by_id=test_actor_id,
        )
        session.add(sched)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_asset_period_constraint(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        sched1 = DepreciationScheduleModel(
            asset_id=asset.id,
            period_date=date(2024, 3, 31),
            depreciation_amount=Decimal("150.00"),
            accumulated_depreciation=Decimal("300.00"),
            net_book_value=Decimal("8700.00"),
            created_by_id=test_actor_id,
        )
        session.add(sched1)
        session.flush()
        sched2 = DepreciationScheduleModel(
            asset_id=asset.id,
            period_date=date(2024, 3, 31),
            depreciation_amount=Decimal("150.00"),
            accumulated_depreciation=Decimal("300.00"),
            net_book_value=Decimal("8700.00"),
            created_by_id=test_actor_id,
        )
        session.add(sched2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_asset(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        sched = DepreciationScheduleModel(
            asset_id=asset.id,
            period_date=date(2024, 4, 30),
            depreciation_amount=Decimal("150.00"),
            accumulated_depreciation=Decimal("450.00"),
            net_book_value=Decimal("8550.00"),
            created_by_id=test_actor_id,
        )
        session.add(sched)
        session.flush()
        assert sched.asset is not None
        assert sched.asset.id == asset.id


# ===================================================================
# AssetDisposalModel
# ===================================================================

class TestAssetDisposalModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        disposal = AssetDisposalModel(
            asset_id=asset.id,
            disposal_date=date(2025, 6, 1),
            disposal_type="sale",
            proceeds=Decimal("5000.00"),
            accumulated_depreciation_at_disposal=Decimal("3000.00"),
            net_book_value_at_disposal=Decimal("7000.00"),
            gain_loss=Decimal("-2000.00"),
            created_by_id=test_actor_id,
        )
        session.add(disposal)
        session.flush()
        queried = session.get(AssetDisposalModel, disposal.id)
        assert queried is not None
        assert queried.asset_id == asset.id
        assert queried.disposal_date == date(2025, 6, 1)
        assert queried.disposal_type == "sale"
        assert queried.proceeds == Decimal("5000.00")
        assert queried.accumulated_depreciation_at_disposal == Decimal("3000.00")
        assert queried.net_book_value_at_disposal == Decimal("7000.00")
        assert queried.gain_loss == Decimal("-2000.00")

    def test_fk_to_asset(self, session, test_actor_id):
        disposal = AssetDisposalModel(
            asset_id=uuid4(),
            disposal_date=date(2025, 6, 1),
            disposal_type="scrap",
            created_by_id=test_actor_id,
        )
        session.add(disposal)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_asset(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        disposal = AssetDisposalModel(
            asset_id=asset.id,
            disposal_date=date(2025, 7, 1),
            disposal_type="donation",
            created_by_id=test_actor_id,
        )
        session.add(disposal)
        session.flush()
        assert disposal.asset.id == asset.id
        session.refresh(asset)
        assert any(d.id == disposal.id for d in asset.disposals)


# ===================================================================
# AssetTransferModel
# ===================================================================

class TestAssetTransferModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        xfer = AssetTransferModel(
            asset_id=asset.id,
            transfer_date=date(2024, 9, 1),
            from_cost_center="CC-100",
            to_cost_center="CC-200",
            transferred_by=uuid4(),
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()
        queried = session.get(AssetTransferModel, xfer.id)
        assert queried is not None
        assert queried.asset_id == asset.id
        assert queried.transfer_date == date(2024, 9, 1)
        assert queried.from_cost_center == "CC-100"
        assert queried.to_cost_center == "CC-200"
        assert queried.transferred_by is not None

    def test_fk_to_asset(self, session, test_actor_id):
        xfer = AssetTransferModel(
            asset_id=uuid4(),
            transfer_date=date(2024, 9, 1),
            from_cost_center="CC-100",
            to_cost_center="CC-200",
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_asset(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        xfer = AssetTransferModel(
            asset_id=asset.id,
            transfer_date=date(2024, 10, 1),
            from_cost_center="CC-300",
            to_cost_center="CC-400",
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()
        assert xfer.asset.id == asset.id
        session.refresh(asset)
        assert any(t.id == xfer.id for t in asset.transfers)

    def test_nullable_transferred_by(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        xfer = AssetTransferModel(
            asset_id=asset.id,
            transfer_date=date(2024, 11, 1),
            from_cost_center="CC-500",
            to_cost_center="CC-600",
            transferred_by=None,
            created_by_id=test_actor_id,
        )
        session.add(xfer)
        session.flush()
        queried = session.get(AssetTransferModel, xfer.id)
        assert queried.transferred_by is None


# ===================================================================
# AssetRevaluationModel
# ===================================================================

class TestAssetRevaluationModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        reval = AssetRevaluationModel(
            asset_id=asset.id,
            revaluation_date=date(2025, 1, 1),
            old_carrying_value=Decimal("8000.00"),
            new_fair_value=Decimal("9500.00"),
            revaluation_surplus=Decimal("1500.00"),
            created_by_id=test_actor_id,
        )
        session.add(reval)
        session.flush()
        queried = session.get(AssetRevaluationModel, reval.id)
        assert queried is not None
        assert queried.asset_id == asset.id
        assert queried.revaluation_date == date(2025, 1, 1)
        assert queried.old_carrying_value == Decimal("8000.00")
        assert queried.new_fair_value == Decimal("9500.00")
        assert queried.revaluation_surplus == Decimal("1500.00")

    def test_fk_to_asset(self, session, test_actor_id):
        reval = AssetRevaluationModel(
            asset_id=uuid4(),
            revaluation_date=date(2025, 1, 1),
            old_carrying_value=Decimal("5000.00"),
            new_fair_value=Decimal("6000.00"),
            created_by_id=test_actor_id,
        )
        session.add(reval)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_asset(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        reval = AssetRevaluationModel(
            asset_id=asset.id,
            revaluation_date=date(2025, 6, 15),
            old_carrying_value=Decimal("7000.00"),
            new_fair_value=Decimal("8500.00"),
            revaluation_surplus=Decimal("1500.00"),
            created_by_id=test_actor_id,
        )
        session.add(reval)
        session.flush()
        assert reval.asset.id == asset.id
        session.refresh(asset)
        assert any(r.id == reval.id for r in asset.revaluations)


# ===================================================================
# DepreciationComponentModel
# ===================================================================

class TestDepreciationComponentModelORM:

    def test_create_and_query(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        comp = DepreciationComponentModel(
            asset_id=asset.id,
            component_name="Engine",
            cost=Decimal("5000.00"),
            useful_life_months=48,
            depreciation_method="straight_line",
            accumulated_depreciation=Decimal("0"),
            created_by_id=test_actor_id,
        )
        session.add(comp)
        session.flush()
        queried = session.get(DepreciationComponentModel, comp.id)
        assert queried is not None
        assert queried.asset_id == asset.id
        assert queried.component_name == "Engine"
        assert queried.cost == Decimal("5000.00")
        assert queried.useful_life_months == 48
        assert queried.depreciation_method == "straight_line"
        assert queried.accumulated_depreciation == Decimal("0")

    def test_fk_to_asset(self, session, test_actor_id):
        comp = DepreciationComponentModel(
            asset_id=uuid4(),
            component_name="Chassis",
            cost=Decimal("3000.00"),
            useful_life_months=60,
            created_by_id=test_actor_id,
        )
        session.add(comp)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_unique_asset_component_name_constraint(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        comp1 = DepreciationComponentModel(
            asset_id=asset.id,
            component_name="Body",
            cost=Decimal("2000.00"),
            useful_life_months=72,
            created_by_id=test_actor_id,
        )
        session.add(comp1)
        session.flush()
        comp2 = DepreciationComponentModel(
            asset_id=asset.id,
            component_name="Body",
            cost=Decimal("2500.00"),
            useful_life_months=84,
            created_by_id=test_actor_id,
        )
        session.add(comp2)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_relationship_to_asset(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset = _make_asset(session, test_actor_id, category_id=cat.id)
        comp = DepreciationComponentModel(
            asset_id=asset.id,
            component_name="Transmission",
            cost=Decimal("4000.00"),
            useful_life_months=60,
            created_by_id=test_actor_id,
        )
        session.add(comp)
        session.flush()
        assert comp.asset.id == asset.id
        session.refresh(asset)
        assert any(c.id == comp.id for c in asset.components)

    def test_same_component_name_different_assets(self, session, test_actor_id):
        cat = _make_category(session, test_actor_id)
        asset_a = _make_asset(session, test_actor_id, category_id=cat.id)
        asset_b = _make_asset(session, test_actor_id, category_id=cat.id)
        comp_a = DepreciationComponentModel(
            asset_id=asset_a.id,
            component_name="Motor",
            cost=Decimal("1000.00"),
            useful_life_months=36,
            created_by_id=test_actor_id,
        )
        comp_b = DepreciationComponentModel(
            asset_id=asset_b.id,
            component_name="Motor",
            cost=Decimal("1200.00"),
            useful_life_months=48,
            created_by_id=test_actor_id,
        )
        session.add_all([comp_a, comp_b])
        session.flush()
        assert session.get(DepreciationComponentModel, comp_a.id) is not None
        assert session.get(DepreciationComponentModel, comp_b.id) is not None
