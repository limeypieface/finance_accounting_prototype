"""
Tests for ValuationLayer - FIFO/LIFO cost lot management.

Tests cover:
- Cost lot creation
- Layer queries with remaining calculation
- FIFO consumption
- LIFO consumption
- Specific identification
- Standard costing with variance
- Insufficient inventory errors
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from finance_engines.valuation import (
    ConsumptionResult,
    CostLayer,
    CostLot,
    CostMethod,
)
from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType, LinkType
from finance_kernel.domain.values import Money, Quantity
from finance_kernel.exceptions import (
    InsufficientInventoryError,
    LotDepletedError,
    LotNotFoundError,
    StandardCostNotFoundError,
)
from finance_kernel.services.link_graph_service import LinkGraphService
from finance_services.valuation_service import ValuationLayer


class TestCostLotCreation:
    """Tests for creating cost lots."""

    def test_create_lot_stores_lot(self, session: Session):
        """Creating a lot should store it for later retrieval."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        lot_id = uuid4()
        source_ref = ArtifactRef.receipt(uuid4())
        event_id = uuid4()

        lot = valuation.create_lot(
            lot_id=lot_id,
            source_ref=source_ref,
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=event_id,
        )

        assert lot.lot_id == lot_id
        assert lot.item_id == "WIDGET-001"
        assert lot.original_quantity.value == Decimal("100")
        assert lot.original_cost.amount == Decimal("1000.00")
        assert lot.unit_cost.amount == Decimal("10.00")

        # Should be retrievable
        retrieved = valuation.get_lot(lot_id)
        assert retrieved is not None
        assert retrieved.lot_id == lot_id

    def test_create_lot_creates_sourced_from_link(self, session: Session):
        """Creating a lot should create a SOURCED_FROM link."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        lot_id = uuid4()
        source_ref = ArtifactRef.receipt(uuid4())

        lot = valuation.create_lot(
            lot_id=lot_id,
            source_ref=source_ref,
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )

        # Check link exists
        links = link_graph.get_children(source_ref, frozenset({LinkType.SOURCED_FROM}))
        assert len(links) == 1
        assert links[0].child_ref == lot.lot_ref

    def test_create_lot_with_location(self, session: Session):
        """Creating a lot with location ID."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        lot = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
            location_id="WH-001",
        )

        assert lot.location_id == "WH-001"


class TestLayerQueries:
    """Tests for querying available cost layers."""

    def test_get_available_layers_returns_unconsumed(self, session: Session):
        """Available layers should show full quantity when nothing consumed."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create a lot
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )

        layers = valuation.get_available_layers("WIDGET-001")

        assert len(layers) == 1
        assert layers[0].remaining_quantity.value == Decimal("100")
        assert layers[0].remaining_value.amount == Decimal("1000.00")
        assert layers[0].is_available
        assert not layers[0].is_depleted

    def test_get_total_available_quantity(self, session: Session):
        """Should sum quantities across all lots."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create two lots
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            total_cost=Money.of("600.00", "USD"),
            lot_date=date(2024, 1, 20),
            creating_event_id=uuid4(),
        )

        total = valuation.get_total_available_quantity("WIDGET-001")
        assert total.value == Decimal("150")


class TestFIFOConsumption:
    """Tests for FIFO (First-In, First-Out) consumption."""

    def test_fifo_consumes_oldest_first(self, session: Session):
        """FIFO should consume from oldest lot first."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create two lots with different dates
        old_lot = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),  # $10/unit
            lot_date=date(2024, 1, 10),  # Older
            creating_event_id=uuid4(),
        )
        new_lot = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1200.00", "USD"),  # $12/unit
            lot_date=date(2024, 1, 20),  # Newer
            creating_event_id=uuid4(),
        )

        # Consume 50 units
        consuming_ref = ArtifactRef.shipment(uuid4())
        result = valuation.consume_fifo(
            consuming_ref=consuming_ref,
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            creating_event_id=uuid4(),
        )

        # Should consume from old lot at $10/unit
        assert result.total_quantity.value == Decimal("50")
        assert result.total_cost.amount == Decimal("500.00")  # 50 * $10
        assert len(result.layers_consumed) == 1
        assert result.layers_consumed[0].lot_id == old_lot.lot_id

    def test_fifo_creates_consumed_by_links(self, session: Session):
        """FIFO consumption should create CONSUMED_BY links."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        lot = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )

        consuming_ref = ArtifactRef.shipment(uuid4())
        result = valuation.consume_fifo(
            consuming_ref=consuming_ref,
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("25"), unit="EA"),
            creating_event_id=uuid4(),
        )

        # Check link was created
        links = link_graph.get_children(lot.lot_ref, frozenset({LinkType.CONSUMED_BY}))
        assert len(links) == 1
        assert links[0].child_ref == consuming_ref

    def test_fifo_spanning_multiple_lots(self, session: Session):
        """FIFO should span multiple lots when needed."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create two lots
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("30"), unit="EA"),
            total_cost=Money.of("300.00", "USD"),  # $10/unit
            lot_date=date(2024, 1, 10),
            creating_event_id=uuid4(),
        )
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            total_cost=Money.of("600.00", "USD"),  # $12/unit
            lot_date=date(2024, 1, 20),
            creating_event_id=uuid4(),
        )

        # Consume 50 units (spans both lots)
        result = valuation.consume_fifo(
            consuming_ref=ArtifactRef.shipment(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            creating_event_id=uuid4(),
        )

        # Should consume from both lots
        assert result.total_quantity.value == Decimal("50")
        assert len(result.layers_consumed) == 2


class TestLIFOConsumption:
    """Tests for LIFO (Last-In, First-Out) consumption."""

    def test_lifo_consumes_newest_first(self, session: Session):
        """LIFO should consume from newest lot first."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create two lots with different dates
        old_lot = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),  # $10/unit
            lot_date=date(2024, 1, 10),  # Older
            creating_event_id=uuid4(),
        )
        new_lot = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1200.00", "USD"),  # $12/unit
            lot_date=date(2024, 1, 20),  # Newer
            creating_event_id=uuid4(),
        )

        # Consume 50 units
        result = valuation.consume_lifo(
            consuming_ref=ArtifactRef.shipment(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            creating_event_id=uuid4(),
        )

        # Should consume from new lot at $12/unit
        assert result.total_quantity.value == Decimal("50")
        assert result.total_cost.amount == Decimal("600.00")  # 50 * $12
        assert len(result.layers_consumed) == 1
        assert result.layers_consumed[0].lot_id == new_lot.lot_id


class TestSpecificIdentification:
    """Tests for specific lot selection."""

    def test_consume_specific_lot(self, session: Session):
        """Should consume from specifically identified lot."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create two lots
        lot_a = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 10),
            creating_event_id=uuid4(),
        )
        lot_b = valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1500.00", "USD"),  # Different cost
            lot_date=date(2024, 1, 20),
            creating_event_id=uuid4(),
        )

        # Consume specifically from lot_b
        result = valuation.consume_specific(
            consuming_ref=ArtifactRef.shipment(uuid4()),
            item_id="WIDGET-001",
            lot_id=lot_b.lot_id,
            quantity=Quantity(value=Decimal("25"), unit="EA"),
            creating_event_id=uuid4(),
        )

        assert result.layers_consumed[0].lot_id == lot_b.lot_id
        assert result.total_cost.amount == Decimal("375.00")  # 25 * $15

    def test_consume_specific_lot_not_found_error(self, session: Session):
        """Should raise error when lot doesn't exist."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        with pytest.raises(LotNotFoundError):
            valuation.consume_specific(
                consuming_ref=ArtifactRef.shipment(uuid4()),
                item_id="WIDGET-001",
                lot_id=uuid4(),  # Non-existent
                quantity=Quantity(value=Decimal("25"), unit="EA"),
                creating_event_id=uuid4(),
            )


class TestStandardCosting:
    """Tests for standard costing with variance tracking."""

    def test_standard_cost_with_favorable_variance(self, session: Session):
        """Favorable variance when actual cost < standard."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Set standard cost
        valuation.set_standard_cost("WIDGET-001", Money.of("12.00", "USD"))

        # Create lot with lower actual cost
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),  # $10/unit actual
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )

        # Consume at standard
        result = valuation.consume_at_standard(
            consuming_ref=ArtifactRef.shipment(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            creating_event_id=uuid4(),
        )

        # Standard = 50 * $12 = $600
        # Actual = 50 * $10 = $500
        # Variance = $600 - $500 = $100 (favorable)
        assert result.standard_cost.amount == Decimal("600.00")
        assert result.actual_cost.amount == Decimal("500.00")
        assert result.variance.amount == Decimal("100.00")
        assert result.is_favorable

    def test_standard_cost_not_found_error(self, session: Session):
        """Should raise error when standard cost not set."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create lot without setting standard cost
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("100"), unit="EA"),
            total_cost=Money.of("1000.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )

        with pytest.raises(StandardCostNotFoundError):
            valuation.consume_at_standard(
                consuming_ref=ArtifactRef.shipment(uuid4()),
                item_id="WIDGET-001",
                quantity=Quantity(value=Decimal("50"), unit="EA"),
                creating_event_id=uuid4(),
            )


class TestInsufficientInventory:
    """Tests for insufficient inventory errors."""

    def test_insufficient_inventory_error(self, session: Session):
        """Should raise error when not enough inventory."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        # Create small lot
        valuation.create_lot(
            lot_id=uuid4(),
            source_ref=ArtifactRef.receipt(uuid4()),
            item_id="WIDGET-001",
            quantity=Quantity(value=Decimal("50"), unit="EA"),
            total_cost=Money.of("500.00", "USD"),
            lot_date=date(2024, 1, 15),
            creating_event_id=uuid4(),
        )

        # Try to consume more than available
        with pytest.raises(InsufficientInventoryError) as exc_info:
            valuation.consume_fifo(
                consuming_ref=ArtifactRef.shipment(uuid4()),
                item_id="WIDGET-001",
                quantity=Quantity(value=Decimal("100"), unit="EA"),
                creating_event_id=uuid4(),
            )

        assert exc_info.value.item_id == "WIDGET-001"
        assert exc_info.value.requested_quantity == "100"
        assert exc_info.value.available_quantity == "50"

    def test_insufficient_inventory_no_lots(self, session: Session):
        """Should raise error when no lots exist."""
        link_graph = LinkGraphService(session)
        valuation = ValuationLayer(session, link_graph)

        with pytest.raises(InsufficientInventoryError):
            valuation.consume_fifo(
                consuming_ref=ArtifactRef.shipment(uuid4()),
                item_id="NONEXISTENT",
                quantity=Quantity(value=Decimal("10"), unit="EA"),
                creating_event_id=uuid4(),
            )
