"""
Module: finance_kernel.models.cost_lot
Responsibility: ORM persistence for inventory cost lots.  Each lot represents a
    discrete batch of inventory received at a specific cost, forming the basis
    for FIFO, LIFO, weighted-average, and standard costing methods.
Architecture position: Kernel > Models.  May import from db/base.py only.
    MUST NOT import from services/, selectors/, domain/, or outer layers.

Invariants enforced:
    C1 -- Lot quantity must be positive.  original_quantity > 0 is required at
          creation; remaining quantity is derived from CONSUMED_BY economic links.
    C2 -- Lot cost must be non-negative.  original_cost >= 0 (zero for donated/
          sample inventory).
    C3 -- Provenance traceability.  source_event_id is NOT NULL -- every lot must
          be traceable to the event that created it.
    C4 -- FIFO/LIFO ordering support.  (item_id, lot_date) composite index enables
          deterministic cost-layer selection ordered by receipt date.
    R10 -- Immutability.  original_quantity and original_cost MUST NOT change after
           creation; remaining quantity is derived via EconomicLink graph.

Failure modes:
    - IntegrityError on missing source_event_id (C3, NOT NULL constraint).
    - Application-level validation rejects quantity <= 0 (C1) or cost < 0 (C2).

Audit relevance:
    Cost lots are the foundation of inventory valuation.  Each lot's original
    quantity and cost are frozen at creation, with consumption tracked via
    CONSUMED_BY links in the EconomicLink table.  This append-only pattern
    ensures full auditability of cost-of-goods-sold calculations.
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
    Persistent storage for inventory cost lots.

    Contract:
        Each CostLotModel row records one cost lot created by a receipt,
        production completion, or similar event.  The original_quantity and
        original_cost are frozen at creation and MUST NOT change (R10).
        Remaining quantity is derived from CONSUMED_BY economic links.

    Guarantees:
        - source_event_id is always set (C3 provenance traceability).
        - (item_id, lot_date) index supports FIFO/LIFO ordering (C4).
        - currency is a 3-character ISO 4217 code (R16).
        - cost_method records which costing strategy was in effect.

    Non-goals:
        - This model does NOT store remaining quantity; that is derived
          from the EconomicLink graph via LinkGraphService.
        - This model does NOT enforce C1/C2 at the ORM level; that is
          the responsibility of the inventory service at creation time.
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

    # INVARIANT C1: original_quantity > 0 (enforced at service layer)
    # INVARIANT R10: Immutable after creation -- remaining derived from links
    original_quantity: Mapped[Decimal] = mapped_column(
        Numeric(38, 9),
        nullable=False,
    )

    quantity_unit: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="EA",
    )

    # INVARIANT C2: original_cost >= 0 (enforced at service layer)
    # INVARIANT R10: Immutable after creation
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

    # INVARIANT C3: source_event_id is NOT NULL -- every lot is traceable
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
