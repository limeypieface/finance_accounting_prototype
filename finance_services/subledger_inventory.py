"""
Inventory Subledger Service.

Inventory subledger — tracks item-level receipts, issues, adjustments, and revaluations.
Entity type: Item/SKU identifier.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from finance_engines.subledger import SubledgerBalance, SubledgerEntry
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.logging_config import get_logger
from finance_kernel.selectors.subledger_selector import SubledgerSelector
from finance_services._subledger_mapping import dto_to_entry
from finance_services.subledger_ap import _get_balance, _post_entry
from finance_services.subledger_service import SubledgerService

logger = get_logger("services.subledger.inventory")

INVENTORY_DOCUMENT_TYPES = frozenset({
    "RECEIPT", "ISSUE", "ADJUSTMENT", "REVALUATION", "TRANSFER", "REVERSAL",
})


class InventorySubledgerService(SubledgerService):
    """
    Inventory subledger service.

    Manages item-level subledger entries linked to GL journal entries.
    """

    subledger_type = SubledgerType.INVENTORY

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or SystemClock()
        self._selector = SubledgerSelector(session)

    def post(
        self,
        entry: SubledgerEntry,
        gl_entry_id: str | UUID,
        actor_id: UUID,
    ) -> SubledgerEntry:
        """Post entry to Inventory subledger with GL link."""
        errors = self.validate_entry(entry)
        if errors:
            raise ValueError(f"Inventory subledger entry validation failed: {errors}")

        if entry.source_document_type not in INVENTORY_DOCUMENT_TYPES:
            raise ValueError(
                f"Invalid Inventory source document type: {entry.source_document_type}. "
                f"Allowed: {sorted(INVENTORY_DOCUMENT_TYPES)}"
            )

        return _post_entry(
            session=self._session,
            selector=self._selector,
            subledger_type=self.subledger_type,
            entry=entry,
            gl_entry_id=gl_entry_id,
            posted_at=self._clock.now_utc(),
            logger=logger,
            actor_id=actor_id,
        )

    def get_balance(
        self,
        entity_id: str | UUID,
        as_of_date: date | None = None,
        currency: str | None = None,
    ) -> SubledgerBalance:
        """Get item/SKU balance."""
        return _get_balance(
            selector=self._selector,
            subledger_type=self.subledger_type,
            entity_id=entity_id,
            as_of_date=as_of_date or self._clock.now_utc().date(),
            currency=currency or "USD",
        )

    def get_open_items(
        self,
        entity_id: str | UUID,
        currency: str | None = None,
    ) -> list[SubledgerEntry]:
        """Get open inventory items for SKU."""
        dtos = self._selector.get_open_items(
            entity_id=str(entity_id),
            subledger_type=self.subledger_type,
            currency=currency,
        )
        return [dto_to_entry(d) for d in dtos]
