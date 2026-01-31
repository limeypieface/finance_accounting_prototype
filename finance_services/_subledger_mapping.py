"""
Shared mapping helpers for subledger services.

Converts between ORM models, selector DTOs, and engine value objects.
These functions handle the F16 naming convention (journal_entry_id ↔ gl_entry_id).
"""

from finance_engines.subledger import (
    ReconciliationStatus,
    SubledgerEntry,
)
from finance_kernel.domain.values import Money
from finance_kernel.models.subledger import SubledgerEntryModel


def model_to_entry(model: SubledgerEntryModel) -> SubledgerEntry:
    """Convert ORM model to engine value object."""
    debit = Money.of(model.debit_amount, model.currency) if model.debit_amount else None
    credit = Money.of(model.credit_amount, model.currency) if model.credit_amount else None

    return SubledgerEntry(
        entry_id=model.id,
        subledger_type=model.subledger_type,
        entity_id=model.entity_id,
        source_document_type=model.source_document_type,
        source_document_id=model.source_document_id,
        source_line_id=model.source_line_id,
        gl_entry_id=model.journal_entry_id,  # F16: ORM uses journal_entry_id
        gl_line_id=model.journal_line_id,
        debit=debit,
        credit=credit,
        effective_date=model.effective_date,
        posted_at=model.posted_at,
        reconciliation_status=ReconciliationStatus(model.reconciliation_status),
        reconciled_amount=(
            Money.of(model.reconciled_amount, model.currency)
            if model.reconciled_amount else None
        ),
        memo=model.memo or "",
        reference=model.reference or "",
        dimensions=model.dimensions or {},
    )


def dto_to_entry(dto) -> SubledgerEntry:
    """Convert SubledgerEntryDTO to engine value object."""
    debit = Money.of(dto.debit_amount, dto.currency) if dto.debit_amount else None
    credit = Money.of(dto.credit_amount, dto.currency) if dto.credit_amount else None

    return SubledgerEntry(
        entry_id=dto.id,
        subledger_type=dto.subledger_type,
        entity_id=dto.entity_id,
        source_document_type=dto.source_document_type,
        source_document_id=dto.source_document_id,
        source_line_id=dto.source_line_id,
        gl_entry_id=dto.journal_entry_id,
        gl_line_id=dto.journal_line_id,
        debit=debit,
        credit=credit,
        effective_date=dto.effective_date,
        posted_at=dto.posted_at,
        reconciliation_status=ReconciliationStatus(dto.reconciliation_status),
        reconciled_amount=(
            Money.of(dto.reconciled_amount, dto.currency)
            if dto.reconciled_amount else None
        ),
        memo=dto.memo or "",
        reference=dto.reference or "",
        dimensions=dto.dimensions or {},
    )
