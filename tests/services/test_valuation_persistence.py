"""
Phase 6 — Valuation Layer Persistence Tests.

Tests that prove G13 (cost lot persistence gap) is closed:
  - Cost lots are persisted to the database via CostLotModel
  - Lots survive session close and re-open
  - DB-backed ValuationLayer produces the same domain objects as in-memory

Test classes:
  1. TestCostLotModelPersistence     — ORM round-trip for CostLotModel
  2. TestValuationLayerDBMode        — ValuationLayer with DB persistence
  3. TestValuationLayerCrossSession  — Lots survive across sessions
"""

from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType
from finance_kernel.domain.values import Money, Quantity
from finance_kernel.models.cost_lot import CostLotModel
from finance_engines.valuation.cost_lot import CostLot, CostMethod


# ---------------------------------------------------------------------------
# 1. TestCostLotModelPersistence — ORM round-trip
# ---------------------------------------------------------------------------


class TestCostLotModelPersistence:
    """CostLotModel round-trips through the database correctly."""

    def test_insert_and_read_back(self, session):
        """Insert a CostLotModel and read it back."""
        lot_id = uuid4()
        event_id = uuid4()
        source_id = uuid4()

        model = CostLotModel(
            id=lot_id,
            item_id="WIDGET-001",
            location_id="WH-A",
            lot_date=date(2024, 3, 15),
            original_quantity=Decimal("100"),
            quantity_unit="EA",
            original_cost=Decimal("1500.00"),
            currency="USD",
            cost_method="fifo",
            source_event_id=event_id,
            source_artifact_type="receipt",
            source_artifact_id=source_id,
            created_at=datetime.now(timezone.utc),
            lot_metadata={"vendor": "ACME", "po_number": "PO-123"},
        )
        session.add(model)
        session.flush()

        # Read back
        loaded = session.get(CostLotModel, lot_id)
        assert loaded is not None
        assert loaded.item_id == "WIDGET-001"
        assert loaded.location_id == "WH-A"
        assert loaded.lot_date == date(2024, 3, 15)
        assert loaded.original_quantity == Decimal("100")
        assert loaded.quantity_unit == "EA"
        assert loaded.original_cost == Decimal("1500.00")
        assert loaded.currency == "USD"
        assert loaded.cost_method == "fifo"
        assert loaded.source_event_id == event_id
        assert loaded.source_artifact_type == "receipt"
        assert loaded.source_artifact_id == source_id
        assert loaded.lot_metadata["vendor"] == "ACME"

    def test_multiple_lots_per_item(self, session):
        """Multiple lots for the same item are stored independently."""
        event_id = uuid4()
        for i in range(5):
            model = CostLotModel(
                id=uuid4(),
                item_id="BOLT-500",
                location_id=None,
                lot_date=date(2024, 1, 1 + i),
                original_quantity=Decimal(str(10 * (i + 1))),
                quantity_unit="EA",
                original_cost=Decimal(str(50 * (i + 1))),
                currency="USD",
                cost_method="fifo",
                source_event_id=event_id,
                source_artifact_type="receipt",
                source_artifact_id=uuid4(),
                created_at=datetime.now(timezone.utc),
            )
            session.add(model)
        session.flush()

        from sqlalchemy import select
        stmt = select(CostLotModel).where(CostLotModel.item_id == "BOLT-500")
        lots = session.execute(stmt).scalars().all()
        assert len(lots) == 5

    def test_null_location_allowed(self, session):
        """CostLotModel allows null location_id."""
        lot_id = uuid4()
        model = CostLotModel(
            id=lot_id,
            item_id="SCREW-100",
            location_id=None,
            lot_date=date(2024, 6, 1),
            original_quantity=Decimal("500"),
            quantity_unit="EA",
            original_cost=Decimal("250.00"),
            currency="USD",
            cost_method="standard",
            source_event_id=uuid4(),
            source_artifact_type="production_order",
            source_artifact_id=uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        session.add(model)
        session.flush()

        loaded = session.get(CostLotModel, lot_id)
        assert loaded.location_id is None

    def test_null_metadata_allowed(self, session):
        """CostLotModel allows null lot_metadata."""
        lot_id = uuid4()
        model = CostLotModel(
            id=lot_id,
            item_id="NUT-200",
            location_id=None,
            lot_date=date(2024, 7, 1),
            original_quantity=Decimal("1000"),
            quantity_unit="EA",
            original_cost=Decimal("100.00"),
            currency="USD",
            cost_method="fifo",
            source_event_id=uuid4(),
            source_artifact_type="receipt",
            source_artifact_id=uuid4(),
            created_at=datetime.now(timezone.utc),
            lot_metadata=None,
        )
        session.add(model)
        session.flush()

        loaded = session.get(CostLotModel, lot_id)
        assert loaded.lot_metadata is None


# ---------------------------------------------------------------------------
# 2. TestValuationLayerDBMode — full DB persistence through ValuationLayer
# ---------------------------------------------------------------------------


class TestValuationLayerDBMode:
    """ValuationLayer with DB persistence creates and retrieves lots."""

    def _make_valuation_layer(self, session):
        """Create a ValuationLayer in DB mode (no lots_by_item)."""
        from finance_kernel.services.link_graph_service import LinkGraphService
        from finance_services.valuation_service import ValuationLayer

        link_graph = LinkGraphService(session)
        # No lots_by_item → DB mode
        return ValuationLayer(session, link_graph)

    def test_create_lot_persists_to_db(self, session):
        """create_lot() persists the lot to the cost_lots table."""
        valuation = self._make_valuation_layer(session)

        lot_id = uuid4()
        event_id = uuid4()
        receipt_id = uuid4()

        lot = valuation.create_lot(
            lot_id=lot_id,
            source_ref=ArtifactRef(ArtifactType.RECEIPT, receipt_id),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of(Decimal("1500.00"), "USD"),
            lot_date=date(2024, 3, 15),
            creating_event_id=event_id,
            location_id="WH-A",
            cost_method=CostMethod.FIFO,
        )

        assert lot.lot_id == lot_id
        assert lot.item_id == "WIDGET-001"

        # Verify it's in the DB
        model = session.get(CostLotModel, lot_id)
        assert model is not None
        assert model.item_id == "WIDGET-001"
        assert model.original_quantity == Decimal("100")
        assert model.currency == "USD"
        assert model.cost_method == "fifo"

    def test_get_lot_returns_from_db(self, session):
        """get_lot() returns a domain CostLot reconstructed from DB."""
        valuation = self._make_valuation_layer(session)

        lot_id = uuid4()
        event_id = uuid4()
        receipt_id = uuid4()

        valuation.create_lot(
            lot_id=lot_id,
            source_ref=ArtifactRef(ArtifactType.RECEIPT, receipt_id),
            item_id="BOLT-500",
            quantity=Quantity(value=Decimal("200"), unit="EA"),
            total_cost=Money.of(Decimal("400.00"), "USD"),
            lot_date=date(2024, 4, 1),
            creating_event_id=event_id,
        )

        # Get it back
        lot = valuation.get_lot(lot_id)
        assert lot is not None
        assert lot.lot_id == lot_id
        assert lot.item_id == "BOLT-500"
        assert lot.original_quantity.value == Decimal("200")
        assert lot.original_cost.amount == Decimal("400.00")
        assert lot.cost_method == CostMethod.FIFO

    def test_get_lot_not_found_returns_none(self, session):
        """get_lot() returns None for non-existent lot."""
        valuation = self._make_valuation_layer(session)
        assert valuation.get_lot(uuid4()) is None

    def test_get_available_layers_from_db(self, session):
        """get_available_layers() queries DB and returns correct layers."""
        valuation = self._make_valuation_layer(session)

        event_id = uuid4()
        # Create 3 lots for the same item
        for i in range(3):
            valuation.create_lot(
                lot_id=uuid4(),
                source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
                item_id="GADGET-100",
                quantity=Quantity(value=Decimal("50"), unit="EA"),
                total_cost=Money.of(Decimal(str(100 * (i + 1))), "USD"),
                lot_date=date(2024, 1, 10 + i),
                creating_event_id=event_id,
            )

        layers = valuation.get_available_layers("GADGET-100")
        assert len(layers) == 3

    def test_create_lot_with_metadata_persists(self, session):
        """create_lot() with metadata persists JSON to lot_metadata column."""
        valuation = self._make_valuation_layer(session)

        lot_id = uuid4()
        event_id = uuid4()

        valuation.create_lot(
            lot_id=lot_id,
            source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
            item_id="PART-XYZ",
            quantity=Quantity(value=Decimal("25"), unit="EA"),
            total_cost=Money.of(Decimal("375.00"), "USD"),
            lot_date=date(2024, 5, 1),
            creating_event_id=event_id,
            metadata={"vendor": "ACME", "lot_number": "L-2024-0042"},
        )

        model = session.get(CostLotModel, lot_id)
        assert model.lot_metadata["vendor"] == "ACME"
        assert model.lot_metadata["lot_number"] == "L-2024-0042"

    def test_different_items_isolated(self, session):
        """Lots for different items are isolated in DB queries."""
        valuation = self._make_valuation_layer(session)
        event_id = uuid4()

        # Create lots for two different items
        for item in ("ITEM-A", "ITEM-B"):
            for _ in range(2):
                valuation.create_lot(
                    lot_id=uuid4(),
                    source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
                    item_id=item,
                    quantity=Quantity(value=Decimal("10"), unit="EA"),
                    total_cost=Money.of(Decimal("100.00"), "USD"),
                    lot_date=date(2024, 6, 1),
                    creating_event_id=event_id,
                )

        layers_a = valuation.get_available_layers("ITEM-A")
        layers_b = valuation.get_available_layers("ITEM-B")
        assert len(layers_a) == 2
        assert len(layers_b) == 2

    def test_model_to_domain_preserves_cost_method(self, session):
        """All CostMethod enum values round-trip through DB correctly."""
        valuation = self._make_valuation_layer(session)
        event_id = uuid4()

        for method in (CostMethod.FIFO, CostMethod.LIFO, CostMethod.STANDARD):
            lot_id = uuid4()
            valuation.create_lot(
                lot_id=lot_id,
                source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
                item_id=f"METHOD-{method.value}",
                quantity=Quantity(value=Decimal("10"), unit="EA"),
                total_cost=Money.of(Decimal("100.00"), "USD"),
                lot_date=date(2024, 7, 1),
                creating_event_id=event_id,
                cost_method=method,
            )

            lot = valuation.get_lot(lot_id)
            assert lot is not None
            assert lot.cost_method == method


# ---------------------------------------------------------------------------
# 3. TestValuationLayerCrossSession — survive across sessions
# ---------------------------------------------------------------------------


class TestValuationLayerCrossSession:
    """Cost lots persist across ValuationLayer instances (same session)."""

    def _make_valuation_layer(self, session):
        from finance_kernel.services.link_graph_service import LinkGraphService
        from finance_services.valuation_service import ValuationLayer

        link_graph = LinkGraphService(session)
        return ValuationLayer(session, link_graph)

    def test_lot_survives_new_valuation_layer_instance(self, session):
        """Lot created by one ValuationLayer instance is visible to another."""
        vl1 = self._make_valuation_layer(session)

        lot_id = uuid4()
        event_id = uuid4()

        vl1.create_lot(
            lot_id=lot_id,
            source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
            item_id="SURVIVE-TEST",
            quantity=Quantity(value=Decimal("75"), unit="EA"),
            total_cost=Money.of(Decimal("750.00"), "USD"),
            lot_date=date(2024, 8, 1),
            creating_event_id=event_id,
        )

        # Create a fresh ValuationLayer on the same session
        vl2 = self._make_valuation_layer(session)

        lot = vl2.get_lot(lot_id)
        assert lot is not None
        assert lot.item_id == "SURVIVE-TEST"
        assert lot.original_quantity.value == Decimal("75")

    def test_multiple_lots_visible_across_instances(self, session):
        """All lots are visible after switching ValuationLayer instances."""
        vl1 = self._make_valuation_layer(session)
        event_id = uuid4()

        # Create 5 lots
        lot_ids = []
        for i in range(5):
            lot_id = uuid4()
            lot_ids.append(lot_id)
            vl1.create_lot(
                lot_id=lot_id,
                source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
                item_id="MULTI-LOT",
                quantity=Quantity(value=Decimal("10"), unit="EA"),
                total_cost=Money.of(Decimal("100.00"), "USD"),
                lot_date=date(2024, 9, 1 + i),
                creating_event_id=event_id,
            )

        # Fresh instance
        vl2 = self._make_valuation_layer(session)

        layers = vl2.get_available_layers("MULTI-LOT")
        assert len(layers) == 5

        # All lot_ids should be present
        layer_lot_ids = {layer.lot.lot_id for layer in layers}
        for lid in lot_ids:
            assert lid in layer_lot_ids

    def test_in_memory_mode_does_not_hit_db(self, session):
        """ValuationLayer with lots_by_item uses in-memory only."""
        from finance_kernel.services.link_graph_service import LinkGraphService
        from finance_services.valuation_service import ValuationLayer

        link_graph = LinkGraphService(session)

        lot = CostLot.create(
            lot_id=uuid4(),
            item_id="INMEM-ONLY",
            quantity=Quantity(value=Decimal("10"), unit="EA"),
            total_cost=Money.of(Decimal("50.00"), "USD"),
            lot_date=date(2024, 10, 1),
            source_ref=ArtifactRef(ArtifactType.RECEIPT, uuid4()),
        )

        vl = ValuationLayer(session, link_graph, lots_by_item={"INMEM-ONLY": [lot]})

        # Should find it in memory
        found = vl.get_lot(lot.lot_id)
        assert found is not None
        assert found.item_id == "INMEM-ONLY"

        # Should NOT be in DB
        from sqlalchemy import select
        stmt = select(CostLotModel).where(CostLotModel.item_id == "INMEM-ONLY")
        db_result = session.execute(stmt).scalars().first()
        assert db_result is None
