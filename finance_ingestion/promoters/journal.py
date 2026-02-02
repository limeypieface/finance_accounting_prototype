"""
Opening balance promoter — Phase 8 stub.

OpeningBalancePromoter would create journal entries via ModulePostingService.post_event()
to maintain posting invariants (R1–R24). Stub until integration is wired.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from finance_ingestion.promoters.base import PromoteResult


class OpeningBalancePromoter:
    """Stub promoter for entity_type 'opening_balance'. Not implemented until event pipeline integration."""

    entity_type: str = "opening_balance"

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
    ) -> PromoteResult:
        return PromoteResult(
            success=False,
            error="Not implemented: opening balance promotion requires ModulePostingService.post_event() integration",
        )

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        return False
