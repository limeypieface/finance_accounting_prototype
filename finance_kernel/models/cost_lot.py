"""
CostLot ORM Model.

Persistence layer for inventory cost lots. Each lot represents a batch of
inventory received at a specific cost, used for FIFO/LIFO/standard costing.

Hard invariants:
- C1: Lot quantity must be positive
- C2: Lot cost must be non-negative
- C3: source_event_id is required (NOT NULL) — every lot is traceable
- C4: item_id + lot_date together support FIFO/LIFO ordering
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Numeric,
    String,
)
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from finance_kernel.db.base import Base, UUIDString


class CostLotModel(Base):
    """
    Persistent storage for cost lots.

    Maps CostLot domain objects to the database. Each row represents one
    cost lot created by an inventory receipt, production completion, or
    similar event.

    Remaining quantity is NOT stored — it is derived from CONSUMED_BY
    links in the EconomicLink table via LinkGraphService.
    """

    __tablename__ = "cost_lots"

    __table_args__ = (
        # Query: all lots for an item, ordered by date (FIFO/LIFO)
        Index("idx_cost_lot_item_date", "item_id", "lot_date"),
        # Query: lots by item and location
        Index("idx_cost_lot_item_location", "item_id", "location_id"),
        # Query: lot provenance (which event created this lot)
        Index("idx_cost_lot_source_event", "source_event_id"),
        # Query: lots by cost method
        Index("idx_cost_lot_method", "cost_method"),
        # Query: lots created in time range
        Index("idx_cost_lot_created_at", "created_at"),
    )

    # Item identification
    item_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    location_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # FIFO/LIFO ordering date
    lot_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Original quantity (never changes — remaining is derived from links)
    original_quantity: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )

    quantity_unit: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="EA",
    )

    # Original cost (never changes)
    original_cost: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    # Costing method
    cost_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Provenance
    source_event_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    source_artifact_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    source_artifact_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        nullable=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Optional metadata (lot attributes, vendor, PO number, etc.)
    lot_metadata: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<CostLot {self.id}: item={self.item_id} "
            f"qty={self.original_quantity} @ {self.original_cost} {self.currency}>"
        )
