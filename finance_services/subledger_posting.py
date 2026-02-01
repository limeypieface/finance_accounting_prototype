"""
finance_services.subledger_posting -- Subledger posting bridge.

Responsibility:
    Create SubledgerEntry records from AccountingIntent after a journal
    write succeeds.  Bridges the kernel's AccountingIntent to the
    engine's SubledgerEntry via convention-based entity_id resolution.

Architecture position:
    Services -- stateful orchestration over engines + kernel.
    This module lives in finance_services/ because it imports from
    finance_engines/, which finance_kernel/ must not do (architecture
    boundary).  PostingOrchestrator delegates to this module rather
    than importing finance_engines directly.

Invariants enforced:
    - SL-G1 (atomicity): all subledger entries are created in the same
      transaction as the journal write.  If any entry fails, the entire
      transaction rolls back.
    - SL-G2 (GL linkage): every SubledgerEntry is linked to its
      journal_entry_id via the gl_entry_id parameter.
    - SL-G9 (canonical types): SubledgerType enum is used for all
      type dispatch.

Failure modes:
    - SubledgerType(ledger_intent.ledger_id) raises ValueError for
      non-subledger ledger_ids; these are silently skipped (continue).
    - Missing subledger service for a recognized type: logged as warning,
      entry skipped.
    - Missing entity_id in payload: logged as warning, entry skipped.
    - Missing journal_entry_id for ledger: logged as warning, entry skipped.

Audit relevance:
    Every subledger entry post is logged with subledger_type, entity_id,
    direction, amount, currency, and event_id.  Missing entity_ids are
    logged as warnings for audit follow-up.

Phase 7 (config) will replace the convention-based entity_id resolution
with declarative entity_id_field on LedgerEffect.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from uuid import UUID

from finance_engines.subledger import SubledgerEntry
from finance_kernel.domain.accounting_intent import IntentLineSide
from finance_kernel.domain.subledger_control import SubledgerType
from finance_kernel.logging_config import get_logger

if TYPE_CHECKING:
    from finance_kernel.domain.accounting_intent import AccountingIntent
    from finance_kernel.services.journal_writer import JournalWriteResult
    from finance_services.subledger_service import SubledgerService

logger = get_logger("services.subledger_posting")

# Convention-based entity ID field lookup per subledger type.
# Phase 7 (config) replaces this with declarative entity_id_field on LedgerEffect.
_ENTITY_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "AP": ("vendor_id", "supplier_id"),
    "AR": ("customer_id",),
    "INVENTORY": ("item_id", "sku", "inventory_item_id"),
    "BANK": ("bank_account_id", "account_id"),
    "WIP": ("contract_id",),
}


def _resolve_entity_id(subledger_type_value: str, payload: dict) -> str | None:
    """Resolve entity ID from payload using subledger type conventions."""
    fields = _ENTITY_ID_FIELDS.get(subledger_type_value, ())
    for field_name in fields:
        value = payload.get(field_name)
        if value is not None:
            return str(value)
    return None


def _derive_source_document_type(event_type: str) -> str:
    """Derive source document type from event_type.

    Examples:
        "ap.invoice_received" → "INVOICE_RECEIVED"
        "inventory.receipt"   → "RECEIPT"
    """
    parts = event_type.split(".")
    if len(parts) >= 2:
        return parts[-1].upper()
    return event_type.upper()


def post_subledger_entries(
    subledger_services: dict[SubledgerType, SubledgerService],
    accounting_intent: AccountingIntent,
    journal_result: JournalWriteResult,
    event_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    actor_id: UUID,
) -> None:
    """
    Post subledger entries for subledger ledger intents.

    Called by ModulePostingService (via PostingOrchestrator) after
    journal write succeeds, within the same transaction.

    SL-G1: Atomicity — if this fails, the entire transaction rolls back.

    For each subledger ledger intent in the AccountingIntent:
    1. Find the matching SubledgerService
    2. Find the journal entry created for this ledger
    3. Extract entity_id from payload (convention-based)
    4. Create and post SubledgerEntry per intent line
    """
    # Build lookup: ledger_id → journal_entry_id from write result
    entry_id_by_ledger: dict[str, UUID] = {}
    for we in journal_result.entries:
        entry_id_by_ledger[we.ledger_id] = we.entry_id

    source_doc_type = _derive_source_document_type(event_type)

    for ledger_intent in accounting_intent.ledger_intents:
        try:
            sl_type = SubledgerType(ledger_intent.ledger_id)
        except ValueError:
            continue

        service = subledger_services.get(sl_type)
        if service is None:
            continue

        # Find the journal entry created for this subledger
        journal_entry_id = entry_id_by_ledger.get(ledger_intent.ledger_id)
        if journal_entry_id is None:
            logger.warning(
                "subledger_no_journal_entry",
                extra={
                    "subledger_type": sl_type.value,
                    "event_id": str(event_id),
                },
            )
            continue

        # Resolve entity ID from payload
        entity_id = _resolve_entity_id(sl_type.value, payload)
        if entity_id is None:
            logger.warning(
                "subledger_entity_id_missing",
                extra={
                    "subledger_type": sl_type.value,
                    "event_id": str(event_id),
                    "tried_fields": list(
                        _ENTITY_ID_FIELDS.get(sl_type.value, ())
                    ),
                },
            )
            continue

        # Create and post one SubledgerEntry per intent line
        for line_idx, line in enumerate(ledger_intent.lines):
            if line.side == IntentLineSide.DEBIT:
                debit = line.money
                credit = None
            else:
                debit = None
                credit = line.money

            entry = SubledgerEntry(
                subledger_type=sl_type.value,
                entity_id=entity_id,
                source_document_type=source_doc_type,
                source_document_id=str(event_id),
                source_line_id=str(line_idx),
                debit=debit,
                credit=credit,
                effective_date=accounting_intent.effective_date,
                memo=line.memo or "",
                dimensions=line.dimensions or {},
            )

            service.post(entry, gl_entry_id=journal_entry_id, actor_id=actor_id)

            logger.info(
                "subledger_entry_posted",
                extra={
                    "subledger_type": sl_type.value,
                    "entity_id": entity_id,
                    "direction": "debit" if debit else "credit",
                    "amount": str(line.money.amount),
                    "currency": line.currency,
                    "event_id": str(event_id),
                },
            )
