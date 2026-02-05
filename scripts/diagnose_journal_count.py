#!/usr/bin/env python3
"""
Diagnose journal entry count vs QBO source.

Reads the QBO journal JSON, computes the same deterministic event_id used by the
import, and reports:
  - Total rows in JSON
  - Unique event_ids (each → one journal entry after import)
  - Duplicate rows (same content → same event_id, so only one DB entry)
  - Rows with invalid/missing date (skipped by promoter)

Usage:
  python3 scripts/diagnose_journal_count.py [path/to/qbo_journal_Journal.json]
  # Default: upload/qbo_journal_Journal.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _parse_date_from_row(row: dict) -> None | object:
    """Parse date from JSON row (MM/DD/YYYY or ISO). Returns date or None."""
    from datetime import date, datetime

    val = row.get("date")
    if val is None:
        return None
    if hasattr(val, "isoformat"):  # already date
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
    total = len(rows)

    seen: dict[UUID, int] = {}  # event_id -> first row index
    duplicate_indices: list[int] = []
    invalid_date_count = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid_date_count += 1
            continue
        effective = _parse_date_from_row(row)
        if not effective:
            invalid_date_count += 1
            continue
        event_id = _deterministic_event_id(
            effective,
            row.get("num"),
            row.get("name"),
            row.get("lines") or [],
        )
        if event_id in seen:
            duplicate_indices.append(idx)
        else:
            seen[event_id] = idx

    unique_count = len(seen)
    duplicate_count = len(duplicate_indices)

    print()
    print("  --- Journal count diagnosis ---")
    print(f"  Source file:     {path.name}")
    print(f"  Total rows:      {total}")
    print(f"  Invalid/missing date: {invalid_date_count}")
    print(f"  Unique event_ids: {unique_count}  (→ expected journal entries in DB)")
    print(f"  Duplicate rows:  {duplicate_count}  (same content as earlier row)")
    print()
    if duplicate_count > 0:
        print(f"  First 20 duplicate row indices (0-based): {duplicate_indices[:20]}")
        if len(duplicate_indices) > 20:
            print(f"  ... and {len(duplicate_indices) - 20} more.")
        print()
    if invalid_date_count > 0:
        print(f"  Rows with invalid/missing date are skipped by the promoter (no journal entry).")
        print()
    print("  If your DB has fewer entries than unique event_ids, some imports failed.")
    print("  Re-run: python3 scripts/run_ironflow_full_import.py --dir upload")
    print("  and check the 'Failed:' count and error lines.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
