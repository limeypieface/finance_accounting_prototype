"""
finance_services.subledger_ap -- Accounts Payable subledger service.

Responsibility:
    Manage vendor-level subledger entries linked to GL journal entries.
    Track invoices, payments, credit memos, reversals, and adjustments
    per vendor.  Also provides shared implementation helpers (_post_entry,
    _get_balance) used by all concrete subledger services.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    Extends SubledgerService ABC with AP-specific document type validation
    and delegates to SubledgerSelector for persistence.
    Entity type: Vendor (Party with type SUPPLIER).

Invariants enforced:
    - SL-G1 (single-sided entries): delegated to SubledgerEntry.__post_init__.
    - SL-G2 (GL linkage): _post_entry stores journal_entry_id on the ORM model.
    - SL-G2 (idempotency): _post_entry catches IntegrityError on duplicate
      (journal_entry_id, subledger_type, source_line_id) and returns the
      existing entry.
    - Document type whitelist: only INVOICE, PAYMENT, CREDIT_MEMO,
      REVERSAL, ADJUSTMENT are accepted.

Failure modes:
    - ValueError from post() if entry validation fails (validate_entry).
    - ValueError from post() if source_document_type is not in
      AP_DOCUMENT_TYPES.
    - IntegrityError from _post_entry on duplicate posts; handled by
      returning the existing entry (idempotent).

Audit relevance:
    Every post is logged with entry_id, subledger_type, entity_id, and
    journal_entry_id.  Idempotent re-posts are logged separately.

Usage:
    from finance_services.subledger_ap import APSubledgerService

    service = APSubledgerService(session=session, clock=clock)
    posted = service.post(entry, gl_entry_id=journal_entry_id)
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from finance_engines.subledger import SubledgerBalance, SubledgerEntry
from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.models.subledger import (
    ReconciliationStatus as ORMReconciliationStatus,
    SubledgerEntryModel,
)
from finance_kernel.selectors.subledger_selector import SubledgerSelector
from finance_services._subledger_mapping import dto_to_entry, model_to_entry
from finance_services.subledger_service import SubledgerService

logger = get_logger("services.subledger.ap")

# Allowed source document types for AP
AP_DOCUMENT_TYPES = frozenset({
    "INVOICE", "PAYMENT", "CREDIT_MEMO", "REVERSAL", "ADJUSTMENT",
})


class APSubledgerService(SubledgerService):
    """
    Accounts Payable subledger service.

    Contract:
        Manages vendor-level subledger entries linked to GL journal entries.
        Receives Session and Clock via constructor injection.
    Guarantees:
        - ``post`` validates entry fields and document type before persisting.
        - ``get_balance`` returns a SubledgerBalance with credit-normal
          convention (AP is a liability).
        - ``get_open_items`` returns only unreconciled entries.
    Non-goals:
        - Does not enforce vendor credit limits; that is the AP module's
          responsibility.
        - Does not manage payment scheduling or terms.
    """

    subledger_type = SubledgerType.AP

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
        """Post entry to AP subledger with GL link."""
        errors = self.validate_entry(entry)
        if errors:
            raise ValueError(f"AP subledger entry validation failed: {errors}")

        if entry.source_document_type not in AP_DOCUMENT_TYPES:
            raise ValueError(
                f"Invalid AP source document type: {entry.source_document_type}. "
                f"Allowed: {sorted(AP_DOCUMENT_TYPES)}"
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
        """Get vendor balance."""
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
        """Get open AP items for vendor."""
        dtos = self._selector.get_open_items(
            entity_id=str(entity_id),
            subledger_type=self.subledger_type,
            currency=currency,
        )
        return [dto_to_entry(d) for d in dtos]


# =============================================================================
# Shared implementation helpers (used by all concrete subledger services)
# =============================================================================


def _post_entry(
    session: Session,
    selector: SubledgerSelector,
    subledger_type: SubledgerType,
    entry: SubledgerEntry,
    gl_entry_id: str | UUID,
    posted_at,
    logger,
    actor_id: UUID,
) -> SubledgerEntry:
    """Shared post logic for all subledger services.

    Preconditions:
        entry has passed validate_entry() (no validation errors).
        gl_entry_id references a valid journal entry.
        session is an active SQLAlchemy session within a transaction.

    Postconditions:
        Returns a SubledgerEntry constructed from the persisted ORM model.
        On IntegrityError (duplicate post), returns the existing entry
        (SL-G2 idempotency).

    Raises:
        IntegrityError: Re-raised if the duplicate lookup fails.
    """
    model = SubledgerEntryModel(
        subledger_type=subledger_type.value,
        entity_id=str(entry.entity_id),
        journal_entry_id=UUID(str(gl_entry_id)),
        journal_line_id=UUID(str(entry.gl_line_id)) if entry.gl_line_id else None,
        source_document_type=entry.source_document_type,
        source_document_id=str(entry.source_document_id),
        source_line_id=str(entry.source_line_id) if entry.source_line_id else None,
        debit_amount=entry.debit.amount if entry.debit else None,
        credit_amount=entry.credit.amount if entry.credit else None,
        currency=entry.currency,
        effective_date=entry.effective_date,
        posted_at=posted_at,
        reconciliation_status=ORMReconciliationStatus.OPEN.value,
        memo=entry.memo or None,
        reference=entry.reference or None,
        dimensions=entry.dimensions or None,
        created_by_id=actor_id,
    )

    # SL-G2: Idempotency — handle unique constraint violation
    try:
        session.add(model)
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.query(SubledgerEntryModel).filter_by(
            journal_entry_id=UUID(str(gl_entry_id)),
            subledger_type=subledger_type.value,
            source_line_id=str(entry.source_line_id) if entry.source_line_id else None,
        ).first()
        if existing:
            logger.info("subledger_idempotent_post", extra={
                "entry_id": str(existing.id),
                "journal_entry_id": str(gl_entry_id),
            })
            return model_to_entry(existing)
        raise

    logger.info("subledger_entry_posted", extra={
        "entry_id": str(model.id),
        "subledger_type": subledger_type.value,
        "entity_id": str(entry.entity_id),
        "journal_entry_id": str(gl_entry_id),
    })

    return model_to_entry(model)


def _get_balance(
    selector: SubledgerSelector,
    subledger_type: SubledgerType,
    entity_id: str | UUID,
    as_of_date: date,
    currency: str,
) -> SubledgerBalance:
    """Shared get_balance logic for all subledger services.

    Preconditions:
        as_of_date is an explicit date (never date.today()).
        currency is a valid ISO 4217 code.

    Postconditions:
        Returns a SubledgerBalance with debit_total, credit_total,
        and balance computed from the selector DTO.
    """
    dto = selector.get_balance(
        entity_id=str(entity_id),
        subledger_type=subledger_type,
        as_of_date=as_of_date,
        currency=currency,
    )

    return SubledgerBalance(
        entity_id=dto.entity_id,
        subledger_type=dto.subledger_type,
        as_of_date=dto.as_of_date,
        debit_total=Money.of(dto.debit_total, dto.currency),
        credit_total=Money.of(dto.credit_total, dto.currency),
        balance=Money.of(dto.balance, dto.currency),
        open_item_count=dto.open_item_count,
        currency=dto.currency,
    )
