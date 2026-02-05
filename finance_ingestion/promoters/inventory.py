"""
Inventory entity promoters: item, location — Phase 8 stubs.

ItemPromoter and LocationPromoter are stubs until InventoryItemModel and
InventoryLocationModel exist in the inventory module (plan references
inventory_items / inventory_locations; current inventory.orm has receipts,
issues, etc., with item_id/location_id referencing external Sindri entities).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from finance_ingestion.promoters.base import PromoteResult


class ItemPromoter:
    """Stub promoter for entity_type 'item'. Not implemented until InventoryItemModel exists."""

    entity_type: str = "item"

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
        **kwargs: Any,
    ) -> PromoteResult:
        return PromoteResult(
            success=False,
            error="Not implemented: item promotion requires InventoryItemModel (inventory_items table)",
        )

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        return False


class LocationPromoter:
    """Stub promoter for entity_type 'location'. Not implemented until InventoryLocationModel exists."""

    entity_type: str = "location"

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
        **kwargs: Any,
    ) -> PromoteResult:
        return PromoteResult(
            success=False,
            error="Not implemented: location promotion requires InventoryLocationModel (inventory_locations table)",
        )

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        return False
