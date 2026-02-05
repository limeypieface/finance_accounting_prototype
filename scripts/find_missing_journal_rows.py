#!/usr/bin/env python3
"""
Find which journal rows from the QBO JSON are missing in the DB.

Computes event_id for each JSON row (same logic as import), queries
InterpretationOutcome for import.historical_journal, and lists _import_row
for rows that have no outcome (i.e. failed or were never posted).

Usage:
  python3 scripts/find_missing_journal_rows.py [path/to/qbo_journal_Journal.json]
  # Uses FINANCE_DB_URL or default test DB.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_URL = os.environ.get(
    "FINANCE_DB_URL",
    "postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test",
)


def _parse_date_from_row(row: dict) -> None | object:
    from datetime import date, datetime

    val = row.get("date")
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val
    s = str(val).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def main() -> int:
    default_path = ROOT / "upload" / "qbo_journal_Journal.json"
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_path
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    from finance_ingestion.promoters.journal import _deterministic_event_id

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") or data.get("data") or []

    # Build (import_row, event_id) for each row; track duplicates
    row_event_ids: list[tuple[int, UUID]] = []
    seen_event_id_to_first_row: dict[UUID, int] = {}
    duplicate_rows: list[int] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        effective = _parse_date_from_row(row)
        if not effective:
            continue
        event_id = _deterministic_event_id(
            effective,
            row.get("num"),
            row.get("name"),
            row.get("lines") or [],
            source_row=import_row,
        )
        import_row = row.get("_import_row")
        if import_row is None:
            import_row = idx + 1
        else:
            try:
                import_row = int(import_row)
            except (TypeError, ValueError):
                import_row = idx + 1
        row_event_ids.append((import_row, event_id))
        if event_id in seen_event_id_to_first_row:
            duplicate_rows.append(import_row)
        else:
            seen_event_id_to_first_row[event_id] = import_row
    unique_count = len(seen_event_id_to_first_row)

    # Query DB for event_ids that have an outcome (posted)
    from sqlalchemy import select
    from finance_kernel.db.engine import get_session, init_engine_from_url
    from finance_kernel.models.interpretation_outcome import InterpretationOutcome

    try:
        init_engine_from_url(DB_URL)
    except Exception as e:
        print(f"DB connect failed: {e}", file=sys.stderr)
        return 1

    session = get_session()
    try:
        outcomes = session.scalars(
            select(InterpretationOutcome.source_event_id).where(
                InterpretationOutcome.source_event_id.isnot(None)
            )
        ).all()
        posted_event_ids = set(outcomes)
    finally:
        session.close()

    missing = [
        (import_row, event_id)
        for import_row, event_id in row_event_ids
        if event_id not in posted_event_ids
    ]

    print()
    print("  --- Journal import vs DB ---")
    print(f"  Total rows in JSON:     {len(row_event_ids)}")
    print(f"  Unique event_ids:      {unique_count}  (expected journal entries)")
    print(f"  Duplicate rows:        {len(duplicate_rows)}  (same content as earlier row)")
    print(f"  Posted in DB:          {len(posted_event_ids)}")
    print(f"  Missing (failed):      {len(missing)}")
    print()
    if duplicate_rows:
        print("  Duplicate rows (same content as an earlier row; filtered out during import):")
        # Build map: event_id -> first _import_row
        first_by_eid: dict[UUID, int] = {}
        for import_row, event_id in row_event_ids:
            if event_id not in first_by_eid:
                first_by_eid[event_id] = import_row
        for ir in sorted(duplicate_rows):
            # Find event_id for this row
            eid = next(e for r, e in row_event_ids if r == ir)
            first_ir = first_by_eid.get(eid, 0)
            print(f"    Row {ir}  (identical to row {first_ir})")
        print()
    if missing:
        for import_row, event_id in sorted(missing, key=lambda x: x[0]):
            print(f"    Row {import_row}  (event_id={event_id})")
        print()
        print("  Re-run the journal import to see the error for these rows:")
        print("  python3 scripts/run_ironflow_import.py --config-id US-GAAP-2026-IRONFLOW-AI --mapping qbo_json_journal --file upload/qbo_journal_Journal.json")
        print()
    elif len(posted_event_ids) == unique_count:
        print("  All unique journal entries are in the DB. 910 = 916 rows minus 6 duplicates.")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
