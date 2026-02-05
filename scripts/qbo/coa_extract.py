"""
Extract input Chart of Accounts from QBO converter JSON.

Reads the structured JSON produced by run_qbo_convert (qbo_accounts_*.json)
and returns a list of input account records (name, account_type, optional code)
for use in CoA recommendation and mapping.

Input JSON shape: {"source": "qbo", "report": "accounts", "rows": [{...}]}
Each row has: name, account_type, detail_type (optional), description (optional).
Code is derived from name when present e.g. "Mercury Checking (3820) - 1" -> "3820".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InputCoARecord:
    """Single account from QBO input CoA."""

    name: str
    account_type: str
    code: str | None
    detail_type: str | None
    import_row: int | None


# Regex to extract parenthesized code from QBO account name e.g. "Mercury Checking (3820) - 1"
_CODE_IN_NAME = re.compile(r"\(([^)]+)\)")


def _extract_code_from_name(name: str) -> str | None:
    """Extract first parenthesized segment as account code if it looks numeric/alphanumeric."""
    if not name:
        return None
    m = _CODE_IN_NAME.search(name)
    if not m:
        return None
    candidate = m.group(1).strip()
    if candidate.replace(".", "").replace("-", "").isalnum():
        return candidate
    return None


def load_qbo_accounts_json(path: Path) -> list[dict[str, Any]]:
    """
    Load QBO accounts JSON file; return the "rows" list.

    Raises:
        FileNotFoundError: path does not exist.
        ValueError: file is not valid QBO accounts JSON (missing "rows").
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"QBO accounts file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object; got {type(data).__name__}")
    rows = data.get("rows")
    if rows is None:
        raise ValueError("QBO accounts JSON has no 'rows' key")
    if not isinstance(rows, list):
        raise ValueError(f"'rows' must be a list; got {type(rows).__name__}")
    return rows


def extract_input_coa(path: Path) -> list[InputCoARecord]:
    """
    Extract input Chart of Accounts from QBO accounts JSON.

    Returns a list of InputCoARecord (name, account_type, code or None, detail_type, import_row).
    Code is taken from row["code"] if present, else derived from name via parentheses.
    """
    rows = load_qbo_accounts_json(path)
    result: list[InputCoARecord] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        account_type = (r.get("account_type") or "").strip()
        if not name and not account_type:
            continue
        code = r.get("code")
        if code is not None and isinstance(code, str):
            code = code.strip() or None
        if code is None and name:
            code = _extract_code_from_name(name)
        detail_type = r.get("detail_type")
        if detail_type is not None and isinstance(detail_type, str):
            detail_type = detail_type.strip() or None
        import_row = r.get("_import_row")
        if import_row is not None and not isinstance(import_row, int):
            import_row = None
        result.append(
            InputCoARecord(
                name=name,
                account_type=account_type,
                code=code,
                detail_type=detail_type,
                import_row=import_row,
            )
        )
    return result
