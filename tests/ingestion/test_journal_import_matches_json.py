"""
Compare imported journal entries (DB) to the source QBO journal JSON.

Checks for mismatches in:
- Debit/credit totals per journal entry
- Per-line: account (mapped to target code), side, amount

Run against the DB that has the import (e.g. finance_kernel_test):

  DATABASE_URL=postgresql://finance:finance_test_pwd@localhost:5432/finance_kernel_test \\
  JOURNAL_JSON_PATH=upload/qbo_journal_Journal.json \\
  python3 -m pytest tests/ingestion/test_journal_import_matches_json.py -v -s

Or with default JSON path (upload/qbo_journal_Journal.json):

  DATABASE_URL=postgresql://.../finance_kernel_test python3 -m pytest tests/ingestion/test_journal_import_matches_json.py -v -s

If JOURNAL_JSON_PATH is missing or the file has no rows, the test is skipped.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from finance_ingestion.promoters.journal import _deterministic_event_id, _parse_date
from finance_kernel.models.account import Account
from finance_kernel.models.interpretation_outcome import InterpretationOutcome
from finance_kernel.models.journal import JournalEntry, JournalLine, LineSide


def _parse_date_from_row(row: dict) -> date | None:
    """Parse date from JSON row (MM/DD/YYYY or ISO)."""
    val = row.get("date")
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    s = str(val).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return _parse_date(s)


def _decimalize(v) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _yaml_load(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_coa_mapping(config_set_dir: Path) -> dict[str, str]:
    """input_name -> target_code from qbo_coa_mapping.yaml."""
    path = config_set_dir / "import_mappings" / "qbo_coa_mapping.yaml"
    if not path.exists():
        return {}
    data = _yaml_load(path)
    mappings = data.get("mappings") or []
    out = {}
    for m in mappings:
        if not isinstance(m, dict):
            continue
        target = m.get("target_code")
        if not target:
            continue
        name = m.get("input_name")
        if name is not None and str(name).strip():
            out[str(name).strip()] = str(target).strip()
    return out


@dataclass
class EntryMismatch:
    json_row_index: int
    import_row: int | None
    event_id: UUID
    kind: str
    expected: str
    actual: str
    details: str = ""


def _compare_entry(
    session: Session,
    json_row: dict,
    row_index: int,
    event_id: UUID,
    coa_map: dict[str, str],
) -> list[EntryMismatch]:
    """Compare one JSON row to the DB journal entry. Returns list of mismatches."""
    mismatches: list[EntryMismatch] = []
    lines_data = json_row.get("lines") or []
    if not isinstance(lines_data, list):
        return [EntryMismatch(row_index, json_row.get("_import_row"), event_id, "invalid", "list of lines", str(lines_data))]

    # Expected totals from JSON
    exp_debit = sum(_decimalize(line.get("debit")) for line in lines_data if isinstance(line, dict))
    exp_credit = sum(_decimalize(line.get("credit")) for line in lines_data if isinstance(line, dict))

    # Find DB entry by event_id
    outcome = session.query(InterpretationOutcome).filter(InterpretationOutcome.source_event_id == event_id).first()
    if not outcome:
        mismatches.append(EntryMismatch(
            row_index, json_row.get("_import_row"), event_id,
            "missing", "InterpretationOutcome", "none",
            details="No outcome for this event_id; entry may not have been imported.",
        ))
        return mismatches
    if not outcome.journal_entry_ids:
        mismatches.append(EntryMismatch(
            row_index, json_row.get("_import_row"), event_id,
            "no_entries", "journal_entry_ids", "empty",
            details="Outcome has no journal entry ids.",
        ))
        return mismatches

    entry_id = outcome.journal_entry_ids[0]
    if isinstance(entry_id, str):
        entry_id = UUID(entry_id)
    entry = session.get(JournalEntry, entry_id)
    if not entry:
        mismatches.append(EntryMismatch(
            row_index, json_row.get("_import_row"), event_id,
            "missing_entry", "JournalEntry", "not found",
            details=f"entry_id={entry_id}",
        ))
        return mismatches

    # Compare entry-level totals
    act_debit = entry.total_debits
    act_credit = entry.total_credits
    if act_debit != exp_debit:
        mismatches.append(EntryMismatch(
            row_index, json_row.get("_import_row"), event_id,
            "debit_total", str(exp_debit), str(act_debit),
            details=f"JSON sum(debit) vs DB total_debits",
        ))
    if act_credit != exp_credit:
        mismatches.append(EntryMismatch(
            row_index, json_row.get("_import_row"), event_id,
            "credit_total", str(exp_credit), str(act_credit),
            details=f"JSON sum(credit) vs DB total_credits",
        ))

    # Build expected line list (account name, target_code, side, amount); sort by (side, amount, code) for stable match
    exp_lines: list[tuple[str, str, str, Decimal]] = []
    for line in lines_data:
        if not isinstance(line, dict):
            continue
        acc_name = (line.get("account") or "").strip() or str(line.get("account"))
        exp_code = coa_map.get(acc_name) or acc_name
        dr = _decimalize(line.get("debit"))
        cr = _decimalize(line.get("credit"))
        if dr > 0:
            exp_lines.append((acc_name, exp_code, "debit", dr))
        if cr > 0:
            exp_lines.append((acc_name, exp_code, "credit", cr))
    exp_lines.sort(key=lambda x: (x[2], x[3], x[1]))  # side, amount, code

    # Build actual line list (account code, side, amount)
    act_lines: list[tuple[str, str, Decimal]] = []
    for line in sorted(entry.lines, key=lambda l: (l.line_seq or 0)):
        if getattr(line, "is_rounding", False):
            continue
        acc = session.get(Account, line.account_id)
        code = acc.code if acc else str(line.account_id)
        side_val = line.side.value if hasattr(line.side, "value") else str(line.side)
        act_lines.append((code, side_val, line.amount))
    act_lines.sort(key=lambda x: (x[1], x[2], x[0]))  # side, amount, code

    if len(exp_lines) != len(act_lines):
        mismatches.append(EntryMismatch(
            row_index, json_row.get("_import_row"), event_id,
            "line_count", str(len(exp_lines)), str(len(act_lines)),
            details="Excluding rounding lines",
        ))

    # Match by position (both sorted by side, amount, account code)
    for i, (exp_acc_name, exp_code, exp_side, exp_amt) in enumerate(exp_lines):
        if i >= len(act_lines):
            mismatches.append(EntryMismatch(
                row_index, json_row.get("_import_row"), event_id,
                "line_missing", f"{exp_code} {exp_side} {exp_amt}", "no DB line",
                details=f"Line index {i}",
            ))
            continue
        act_code, act_side, act_amt = act_lines[i]
        if act_side != exp_side:
            mismatches.append(EntryMismatch(
                row_index, json_row.get("_import_row"), event_id,
                "line_side", exp_side, act_side,
                details=f"Line {i} account {exp_acc_name!r} -> {exp_code}",
            ))
        if act_amt != exp_amt:
            mismatches.append(EntryMismatch(
                row_index, json_row.get("_import_row"), event_id,
                "line_amount", str(exp_amt), str(act_amt),
                details=f"Line {i} {exp_side} account {exp_acc_name!r}",
            ))
        if act_code != exp_code:
            mismatches.append(EntryMismatch(
                row_index, json_row.get("_import_row"), event_id,
                "line_account", exp_code, act_code,
                details=f"Line {i} JSON account {exp_acc_name!r} maps to target {exp_code}",
            ))

    return mismatches


def load_json_rows(path: Path) -> list[dict]:
    """Load QBO journal JSON and return rows list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rows") or data.get("data") or []


def test_journal_entries_match_json(session: Session) -> None:
    """
    Compare every row in the QBO journal JSON to the corresponding imported journal entry.
    Fails with a detailed report of any mismatch in debit/credit totals or per-line account/amount/side.
    """
    json_path = os.environ.get("JOURNAL_JSON_PATH", "upload/qbo_journal_Journal.json")
    path = Path(json_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    if not path.exists():
        pytest.skip(f"JOURNAL_JSON_PATH not found: {path}")
    rows = load_json_rows(path)
    if not rows:
        pytest.skip(f"No rows in {path}")

    # Optional: load CoA mapping for account code comparison (input_name -> target_code)
    config_dir = Path(__file__).resolve().parent.parent.parent / "finance_config" / "sets" / "US-GAAP-2026-IRONFLOW-AI"
    coa_map = _load_coa_mapping(config_dir) if config_dir.is_dir() else {}

    all_mismatches: list[EntryMismatch] = []
    missing = 0
    compared = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        effective = _parse_date_from_row(row)
        if not effective:
            all_mismatches.append(EntryMismatch(
                idx, row.get("_import_row"), UUID("00000000-0000-0000-0000-000000000000"),
                "parse_date", "date", str(row.get("date")),
                details="Could not parse date from row",
            ))
            continue
        event_id = _deterministic_event_id(
            effective,
            row.get("num"),
            row.get("name"),
            row.get("lines") or [],
        )
        mismatches = _compare_entry(session, row, idx, event_id, coa_map)
        if any(m.kind == "missing" for m in mismatches):
            missing += 1
        elif mismatches:
            all_mismatches.extend(mismatches)
        else:
            compared += 1

    # Report
    if all_mismatches:
        lines = [
            "",
            "--- Journal vs JSON mismatch report ---",
            f"  Rows in JSON: {len(rows)}",
            f"  Compared OK:  {compared}",
            f"  Missing in DB: {missing}",
            f"  Mismatches:  {len(all_mismatches)}",
            "",
        ]
        for m in all_mismatches[:50]:
            lines.append(
                f"  Row {m.json_row_index} (import_row={m.import_row}) event={m.event_id!s}: "
                f"{m.kind} expected={m.expected!r} actual={m.actual!r} — {m.details}"
            )
        if len(all_mismatches) > 50:
            lines.append(f"  ... and {len(all_mismatches) - 50} more.")
        lines.append("")
        pytest.fail("\n".join(lines))

    # Sanity: at least some entries should have been compared
    if compared == 0 and missing == len(rows):
        pytest.fail(
            f"No journal entries in DB matched the JSON (all {len(rows)} rows missing). "
            "Ensure import has been run and DATABASE_URL points at the DB with the import."
        )
