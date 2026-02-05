"""
Journal and opening-balance promoters.

JournalPromoter: when module_posting_service and account_key_to_role are provided,
shapes mapped QBO-style data into an event payload and calls ModulePostingService.post_event()
so the full kernel pipeline (ingest → interpret → meaning → intent → journal write) runs
and all validation (double-entry, period lock, idempotency) applies. No bypass.

When those are not provided, returns an error directing the caller to use the pipeline.

OpeningBalancePromoter: Phase 8 stub for event-pipeline integration.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy.orm import Session

from finance_ingestion.promoters.base import PromoteResult

# Namespace for deterministic import journal event_ids. Same logical row => same event_id => idempotent re-run.
_IMPORT_JOURNAL_NAMESPACE = UUID("b2c3d4e5-f6a7-8901-bcde-f12345678902")


def _parse_date(value: Any) -> date | None:
    """Parse date from ISO string or date object."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    return None


def _str_strip(value: Any) -> str:
    """Normalize to string and strip; accept int/float/Decimal from JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _content_signature(
    effective: date,
    num: str | None,
    name: str | None,
    lines_data: list[dict],
    source_row: int | None = None,
) -> str:
    """Build a stable string for event_id. Includes memo. If source_row is set, each row gets a unique event_id."""
    parts = [effective.isoformat(), _str_strip(num), _str_strip(name)]
    if source_row is not None:
        parts.append(f"row:{source_row}")
    line_parts = []
    for line in (lines_data or []):
        if not isinstance(line, dict):
            continue
        acc = _str_strip(line.get("account"))
        memo = _str_strip(line.get("memo"))
        dr = _str_strip(line.get("debit")) or "0"
        cr = _str_strip(line.get("credit")) or "0"
        line_parts.append(f"{acc}|{memo}|{dr}|{cr}")
    line_parts.sort()
    parts.append("|".join(line_parts))
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _deterministic_event_id(
    effective: date,
    num: str | None,
    name: str | None,
    lines_data: list,
    source_row: int | None = None,
) -> UUID:
    """Return a stable UUID for this journal row. If source_row is set, every row gets a unique event_id (all rows load)."""
    sig = _content_signature(
        effective, num, name,
        lines_data if isinstance(lines_data, list) else [],
        source_row=source_row,
    )
    return uuid5(_IMPORT_JOURNAL_NAMESPACE, sig)


class JournalPromoter:
    """Promotes staged journal records via the full kernel posting pipeline.

    Requires module_posting_service and account_key_to_role so data passes through
    ingest → interpret → meaning → intent → journal write with all validation applied.
    """

    entity_type: str = "journal"

    def promote(
        self,
        mapped_data: dict[str, Any],
        session: Session,
        actor_id: UUID,
        clock: Any,
        **kwargs: Any,
    ) -> PromoteResult:
        module_posting_service = kwargs.get("module_posting_service")
        account_key_to_role = kwargs.get("account_key_to_role")
        if module_posting_service is None or account_key_to_role is None:
            return PromoteResult(
                success=False,
                error="Journal promotion requires module_posting_service and account_key_to_role "
                "so data passes through the full posting pipeline (no bypass).",
            )

        effective = _parse_date(mapped_data.get("date"))
        if not effective:
            return PromoteResult(
                success=False,
                error="Invalid or missing date for journal entry",
            )
        lines_data = mapped_data.get("lines")
        if not isinstance(lines_data, list) or len(lines_data) == 0:
            return PromoteResult(
                success=False,
                error="Journal entry must have a non-empty lines array",
            )

        metadata = dict(mapped_data.get("metadata") or {})
        if kwargs.get("batch_id") is not None:
            metadata["migration_batch_id"] = str(kwargs["batch_id"])
        payload = {
            "date": effective.isoformat(),
            "transaction_type": mapped_data.get("transaction_type"),
            "num": mapped_data.get("num"),
            "name": mapped_data.get("name"),
            "currency": mapped_data.get("currency", "USD"),
            "metadata": metadata,
            "lines": [
                {
                    "account": line.get("account"),
                    "memo": line.get("memo"),
                    "debit": str(line.get("debit")) if line.get("debit") is not None else None,
                    "credit": str(line.get("credit")) if line.get("credit") is not None else None,
                }
                for line in lines_data
                if isinstance(line, dict)
            ],
        }

        occurred_at = (
            clock.now() if hasattr(clock, "now") and callable(clock.now) else datetime.now()
        )
        # Include source_row so every row gets a unique event_id (all 916 load; re-import is idempotent per row).
        source_row = kwargs.get("source_row")
        event_id = _deterministic_event_id(
            effective,
            mapped_data.get("num"),
            mapped_data.get("name"),
            lines_data,
            source_row=int(source_row) if source_row is not None else None,
        )
        result = module_posting_service.post_event(
            event_type="import.historical_journal",
            payload=payload,
            effective_date=effective,
            actor_id=actor_id,
            amount=Decimal("0"),
            currency=payload.get("currency", "USD"),
            producer="ingestion",
            event_id=event_id,
            occurred_at=occurred_at,
            schema_version=1,
            is_adjustment=False,
            description=mapped_data.get("name") or mapped_data.get("transaction_type") or "Import",
            account_key_to_role=account_key_to_role,
        )

        # Idempotent duplicate: event already ingested and posted; treat as success.
        if getattr(result.status, "value", str(result.status)) == "already_posted":
            from finance_kernel.models.interpretation_outcome import InterpretationOutcome

            outcome = (
                session.query(InterpretationOutcome)
                .filter(InterpretationOutcome.source_event_id == event_id)
                .first()
            )
            if outcome and outcome.journal_entry_ids:
                entry_id = outcome.journal_entry_ids[0]
                if isinstance(entry_id, str):
                    entry_id = UUID(entry_id)
                return PromoteResult(success=True, entity_id=entry_id)
            return PromoteResult(success=True, entity_id=None)

        if not result.is_ledger_fact:
            return PromoteResult(
                success=False,
                error=result.message or f"Posting pipeline returned {result.status.value}",
            )
        entity_id = result.journal_entry_ids[0] if result.journal_entry_ids else None
        return PromoteResult(success=True, entity_id=entity_id)

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        """Idempotency is per batch/row via idempotency_key; no duplicate check by content."""
        return False


class OpeningBalancePromoter:
    """Stub promoter for entity_type 'opening_balance'. Not implemented until event pipeline integration."""

    entity_type: str = "opening_balance"

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
            error="Not implemented: opening balance promotion requires ModulePostingService.post_event() integration",
        )

    def check_duplicate(self, mapped_data: dict[str, Any], session: Session) -> bool:
        return False
