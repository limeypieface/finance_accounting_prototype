"""
finance_services.subledger_contract -- Contract/WIP subledger service.

Responsibility:
    Manage contract-level subledger entries linked to GL journal entries.
    Track cost incurrence, billing, fees, reversals, and adjustments
    per government contract.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Extends SubledgerService ABC with Contract/WIP-specific document type
    validation.  Delegates to shared _post_entry/_get_balance helpers in
    subledger_ap.
    Entity type: Contract identifier (DCAA/FAR contract number).

Invariants enforced:
    - SL-G1 (single-sided entries): delegated to SubledgerEntry.__post_init__.
    - SL-G2 (GL linkage): _post_entry stores journal_entry_id on the ORM model.
    - Document type whitelist: only COST_INCURRENCE, BILLING, FEE,
      REVERSAL, ADJUSTMENT are accepted.

Failure modes:
    - ValueError from post() if entry validation fails.
    - ValueError from post() if source_document_type is not in
      CONTRACT_DOCUMENT_TYPES.

Audit relevance:
    Contract subledger entries are the primary evidence for DCAA incurred
    cost audits.  Every post is logged with entry_id, subledger_type,
    entity_id (contract_id), and journal_entry_id.  Uses period-end
    reconciliation (not real-time).
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

logger = get_logger("services.subledger.contract")

CONTRACT_DOCUMENT_TYPES = frozenset({
    "COST_INCURRENCE", "BILLING", "FEE", "REVERSAL", "ADJUSTMENT",
})


class ContractSubledgerService(SubledgerService):
    """
    Contract/WIP subledger service.

    Contract:
        Manages contract-level subledger entries linked to GL journal entries.
        Receives Session and Clock via constructor injection.
    Guarantees:
        - ``post`` validates entry fields and document type before persisting.
        - ``get_balance`` returns a SubledgerBalance with debit-normal
          convention (WIP is an asset).
        - ``get_open_items`` returns only unreconciled entries.
    Non-goals:
        - Does not manage contract lifecycle (CLINs, modifications);
          that is the Contracts module's responsibility.
        - Does not perform DCAA compliance checks beyond the subledger.

    Uses period-end reconciliation (not real-time).
    """

    subledger_type = SubledgerType.WIP

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
        """Post entry to Contract subledger with GL link."""
        errors = self.validate_entry(entry)
        if errors:
            raise ValueError(f"Contract subledger entry validation failed: {errors}")

        if entry.source_document_type not in CONTRACT_DOCUMENT_TYPES:
            raise ValueError(
                f"Invalid Contract source document type: {entry.source_document_type}. "
                f"Allowed: {sorted(CONTRACT_DOCUMENT_TYPES)}"
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
        """Get contract balance."""
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
        """Get open contract items."""
        dtos = self._selector.get_open_items(
            entity_id=str(entity_id),
            subledger_type=self.subledger_type,
            currency=currency,
        )
        return [dto_to_entry(d) for d in dtos]
