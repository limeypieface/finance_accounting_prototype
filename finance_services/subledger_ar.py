"""
finance_services.subledger_ar -- Accounts Receivable subledger service.

Responsibility:
    Manage customer-level subledger entries linked to GL journal entries.
    Track invoices, payments, credit memos, reversals, and adjustments
    per customer.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Extends SubledgerService ABC with AR-specific document type validation.
    Delegates to shared _post_entry/_get_balance helpers in subledger_ap.
    Entity type: Customer (Party with type CUSTOMER).

Invariants enforced:
    - SL-G1 (single-sided entries): delegated to SubledgerEntry.__post_init__.
    - SL-G2 (GL linkage): _post_entry stores journal_entry_id on the ORM model.
    - Document type whitelist: only INVOICE, PAYMENT, CREDIT_MEMO,
      REVERSAL, ADJUSTMENT are accepted.

Failure modes:
    - ValueError from post() if entry validation fails.
    - ValueError from post() if source_document_type is not in
      AR_DOCUMENT_TYPES.

Audit relevance:
    Every post is logged with entry_id, subledger_type, entity_id, and
    journal_entry_id.
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

logger = get_logger("services.subledger.ar")

AR_DOCUMENT_TYPES = frozenset({
    "INVOICE", "PAYMENT", "CREDIT_MEMO", "REVERSAL", "ADJUSTMENT",
})


class ARSubledgerService(SubledgerService):
    """
    Accounts Receivable subledger service.

    Contract:
        Manages customer-level subledger entries linked to GL journal entries.
        Receives Session and Clock via constructor injection.
    Guarantees:
        - ``post`` validates entry fields and document type before persisting.
        - ``get_balance`` returns a SubledgerBalance with debit-normal
          convention (AR is an asset).
        - ``get_open_items`` returns only unreconciled entries.
    Non-goals:
        - Does not enforce customer credit limits; that is the AR module's
          responsibility.
        - Does not manage payment terms or dunning.
    """

    subledger_type = SubledgerType.AR

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
        """Post entry to AR subledger with GL link."""
        errors = self.validate_entry(entry)
        if errors:
            raise ValueError(f"AR subledger entry validation failed: {errors}")

        if entry.source_document_type not in AR_DOCUMENT_TYPES:
            raise ValueError(
                f"Invalid AR source document type: {entry.source_document_type}. "
                f"Allowed: {sorted(AR_DOCUMENT_TYPES)}"
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
        """Get customer balance."""
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
        """Get open AR items for customer."""
        dtos = self._selector.get_open_items(
            entity_id=str(entity_id),
            subledger_type=self.subledger_type,
            currency=currency,
        )
        return [dto_to_entry(d) for d in dtos]
