"""
EntityPromoter protocol and PromoteResult (ERP_INGESTION_PLAN Phase 7/8).

Promoters create live ORM entities from mapped_data. Each record promotion
runs inside a SAVEPOINT managed by PromotionService (IM-15).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PromoteResult:
    """Result of a single promotion attempt."""

    success: bool
    entity_id: UUID | None = None
    error: str | None = None


class EntityPromoter(Protocol):
    """Protocol for promoting staged mapped_data to live ORM entities."""

    @property
    def entity_type(self) -> str:
        """Entity type this promoter handles (e.g. 'party', 'vendor')."""
        ...

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
    ) -> PromoteResult:
        """Create the live entity from mapped data. Runs inside SAVEPOINT."""
        ...

    def check_duplicate(
        self,
        mapped_data: dict[str, Any],
        session: Session,
    ) -> bool:
        """Check if this entity already exists (e.g. by code). Return True if duplicate."""
        ...
