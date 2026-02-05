"""
QuickBooks Online export readers: XLSX/CSV -> list of normalized dicts.

Each reader returns rows suitable for structured JSON output. Column names
are normalized (e.g. "Full name" -> "name", "Account" -> "account") so
output is consistent across minor QBO export variations.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from scripts.qbo.detect import (
    QBO_ACCOUNTS,
    QBO_CUSTOMERS,
    QBO_GL,
    QBO_JOURNAL,
    QBO_TRIAL_BALANCE,
    QBO_VENDORS,
    detect_qbo_type,
)


def _xlsx_rows(
    path: Path, sheet_index: int = 0, max_rows: int = 50_000
) -> tuple[list[str], list[list[Any]]]:
    """Load first sheet: return (headers, data_rows). Headers and rows are normalized."""
    headers, data, _start, _sheet = _xlsx_rows_with_traceability(path, sheet_index, max_rows)
    return headers, data


def _xlsx_rows_with_traceability(
    path: Path, sheet_index: int = 0, max_rows: int = 50_000
) -> tuple[list[str], list[list[Any]], int, str]:
    """
    Load first sheet: (headers, data_rows, data_start_row_1based, sheet_name).
    data_start_row_1based is the Excel row number (1-based) of the first data row.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = wb.worksheets[sheet_index] if sheet_index < len(wb.worksheets) else wb.active
        sheet_name = sheet.title
        rows_iter = sheet.iter_rows(min_row=1, max_row=max_rows + 20)
        all_rows = [[_cell_val(c) for c in row] for row in rows_iter]
    finally:
        wb.close()

    if not all_rows:
        return [], [], 1, ""
    hi = 0
    for i, row in enumerate(all_rows[:15]):
        non_empty = sum(1 for c in row if c != "" and c is not None)
        if non_empty >= 2:
            hi = i
            break
    data_start_row_1based = hi + 2  # 1-based Excel row of first data row
    header_row = all_rows[hi]
    ncols = max(len(header_row), 1)
    headers = []
    for c in range(ncols):
        v = header_row[c] if c < len(header_row) else ""
        h = (str(v).strip() if v not in (None, "") else f"Column_{c+1}")
        base = h
        k = 0
        while h in headers:
            k += 1
            h = f"{base}_{k}"
        headers.append(h)
    data = []
    for row in all_rows[hi + 1 :]:
        if not row:
            continue
        vals = [row[c] if c < len(row) else "" for c in range(ncols)]
        if not any(v != "" and v is not None for v in vals):
            continue
        data.append(vals)
    return headers, data, data_start_row_1based, sheet_name


def _cell_val(cell: Any) -> Any:
    v = getattr(cell, "value", None)
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def _row_to_dict(headers: list[str], values: list[Any]) -> dict[str, Any]:
    d = {}
    for i, h in enumerate(headers):
        v = values[i] if i < len(values) else ""
        if h in d:
            continue
        d[h] = v
    return d


def _normalize_num(s: Any) -> str | float | int:
    """Parse number from string (remove commas, handle parentheses for negative)."""
    if s is None or s == "":
        return ""
    if isinstance(s, (int, float)):
        return s
    s = str(s).strip().replace(",", "")
    if not s:
        return ""
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return str(s)


# ---- Account List ----
def _read_accounts_xlsx(path: Path) -> list[dict[str, Any]]:
    headers, data, start_row, sheet_name = _xlsx_rows_with_traceability(path)
    key_map = _build_key_map(headers, [
        ("full name", "name"), ("account name", "name"), ("name", "name"),
        ("account number", "code"), ("account", "code"), ("number", "code"),
        ("type", "account_type"), ("detail type", "detail_type"),
        ("description", "description"), ("total balance", "total_balance"), ("balance", "total_balance"),
    ])
    out = []
    for i, vals in enumerate(data):
        row = _row_to_dict(headers, vals)
        rec = {}
        for our_key, source_keys in key_map.items():
            for sk in source_keys:
                if sk in row and row[sk] not in (None, ""):
                    rec[our_key] = row[sk] if our_key != "total_balance" else _normalize_num(row[sk])
                    break
        if rec.get("code") or rec.get("name"):
            if not rec.get("code") and rec.get("name"):
                rec["code"] = rec["name"]
            if "account_type" not in rec:
                for k in row:
                    if k.strip().lower() == "type" and row[k]:
                        rec["account_type"] = str(row[k]).strip().lower()
                        break
            rec["_source_file"] = path.name
            rec["_source_sheet"] = sheet_name
            rec["_excel_row"] = start_row + i
            out.append(rec)
    return out


# ---- Customer / Vendor lists ----
def _read_customers_xlsx(path: Path) -> list[dict[str, Any]]:
    headers, data, start_row, sheet_name = _xlsx_rows_with_traceability(path)
    key_map = _build_key_map(headers, [
        ("name", "name"), ("company", "company"), ("full name", "name"),
        ("email", "email"), ("phone", "phone"), ("phone numbers", "phone"),
        ("billing address", "billing_address"), ("account #", "account_number"),
    ])
    out = []
    for i, vals in enumerate(data):
        row = _row_to_dict(headers, vals)
        rec = {k: next((row[sk] for sk in v if sk in row and row[sk] not in (None, "")), None) for k, v in key_map.items()}
        rec = {k: v for k, v in rec.items() if v is not None and v != ""}
        if rec.get("name") or rec.get("company"):
            rec["code"] = rec.get("name") or rec.get("company") or ""
            rec["_source_file"] = path.name
            rec["_source_sheet"] = sheet_name
            rec["_excel_row"] = start_row + i
            out.append(rec)
    return out


def _read_vendors_xlsx(path: Path) -> list[dict[str, Any]]:
    headers, data, start_row, sheet_name = _xlsx_rows_with_traceability(path)
    key_map = _build_key_map(headers, [
        ("vendor", "name"), ("full name", "name"), ("name", "name"),
        ("email", "email"), ("phone", "phone"), ("phone numbers", "phone"),
        ("billing address", "billing_address"), ("account #", "account_number"),
    ])
    out = []
    for i, vals in enumerate(data):
        row = _row_to_dict(headers, vals)
        rec = {k: next((row[sk] for sk in v if sk in row and row[sk] not in (None, "")), None) for k, v in key_map.items()}
        rec = {k: v for k, v in rec.items() if v is not None and v != ""}
        if rec.get("name"):
            rec["code"] = rec.get("name", "")
            rec["_source_file"] = path.name
            rec["_source_sheet"] = sheet_name
            rec["_excel_row"] = start_row + i
            out.append(rec)
    return out


# ---- Journal / GL ----
def _journal_row_get(row: dict[str, Any], key_map: dict[str, list[str]]) -> dict[str, Any]:
    """Extract one row's values using key_map; normalize numbers for debit/credit/balance."""
    rec = {}
    for our_key, source_keys in key_map.items():
        for sk in source_keys:
            if sk in row and row[sk] not in (None, ""):
                val = row[sk]
                if our_key in ("debit", "credit", "balance"):
                    val = _normalize_num(val)
                rec[our_key] = val
                break
    return rec


def _is_journal_total_row(rec: dict[str, Any]) -> bool:
    """True if this row is a QBO summary/total line (same amount in debit and credit, no real account)."""
    d = rec.get("debit")
    c = rec.get("credit")
    if d == "" or c == "":
        return False
    if d is None or c is None:
        return False
    try:
        dd = float(d)
        cc = float(c)
    except (TypeError, ValueError):
        return False
    if dd != cc:
        return False
    # Same amount on both sides: skip if no account or account is clearly a total label
    acc = (rec.get("account") or "")
    if isinstance(acc, str):
        acc = acc.strip().lower()
    else:
        acc = str(acc).strip().lower()
    if not acc:
        return True
    if acc in ("total", "totals", "subtotal", "balance"):
        return True
    # Some exports put "$ 1,234.56" in the first column for total rows
    if isinstance(rec.get("account"), str) and rec["account"].strip().startswith("$"):
        return True
    return False


def _line_sum(amount: Any) -> float:
    """Coerce to float for sum; 0 if missing."""
    if amount is None or amount == "":
        return 0.0
    try:
        return float(amount)
    except (TypeError, ValueError):
        return 0.0


def _read_journal_xlsx(path: Path) -> list[dict[str, Any]]:
    """
    Read QBO Journal export. Pairs debit and credit lines into one journal entry each.
    Each emitted entry includes _source_file, _source_sheet, _excel_row (first line's Excel row) for traceability.
    """
    headers, data, start_row, sheet_name = _xlsx_rows_with_traceability(path)
    key_map = _build_key_map(headers, [
        ("date", "date"), ("transaction date", "date"), ("trans date", "date"), ("journal date", "date"),
        ("transaction type", "transaction_type"), ("type", "transaction_type"), ("trans type", "transaction_type"),
        ("num", "num"), ("number", "num"), ("ref", "num"), ("transaction", "num"),
        ("name", "name"),
        ("account", "account"), ("account name", "account"),
        ("memo", "memo"), ("description", "memo"), ("memo/description", "memo"), ("details", "memo"),
        ("debit", "debit"), ("debits", "debit"),
        ("credit", "credit"), ("credits", "credit"),
        ("balance", "balance"), ("running balance", "balance"),
    ])
    entries_out: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []
    buffer_sum_debit = 0.0
    buffer_sum_credit = 0.0
    carried = {"date": None, "transaction_type": None, "num": None, "name": None}

    def flush_entry() -> None:
        if not buffer:
            return
        first = buffer[0]
        date_val = first.get("date") if first.get("date") not in (None, "") else carried.get("date")
        type_val = first.get("transaction_type") if first.get("transaction_type") not in (None, "") else carried.get("transaction_type")
        num_val = first.get("num") if first.get("num") not in (None, "") else carried.get("num")
        name_val = first.get("name") if first.get("name") not in (None, "") else carried.get("name")
        lines = []
        for b in buffer:
            lines.append({
                "account": b.get("account"),
                "memo": b.get("memo"),
                "debit": b.get("debit") if _line_sum(b.get("debit")) else None,
                "credit": b.get("credit") if _line_sum(b.get("credit")) else None,
            })
        if date_val is None or date_val == "" or not lines:
            return
        entry = {
            "date": date_val,
            "transaction_type": type_val,
            "num": num_val,
            "name": name_val,
            "lines": lines,
            "_source_file": path.name,
            "_source_sheet": sheet_name,
            "_excel_row": first.get("_excel_row"),
        }
        entries_out.append(entry)

    for i, vals in enumerate(data):
        row = _row_to_dict(headers, vals)
        rec = _journal_row_get(row, key_map)
        if not rec:
            continue
        rec["_excel_row"] = start_row + i
        for key in ("date", "transaction_type", "num", "name"):
            if rec.get(key) not in (None, ""):
                carried[key] = rec[key]
            elif carried.get(key) is not None:
                rec[key] = carried[key]
        if _is_journal_total_row(rec):
            continue
        if not rec.get("account") and rec.get("debit") in (None, "") and rec.get("credit") in (None, ""):
            continue
        buffer.append(rec)
        buffer_sum_debit += _line_sum(rec.get("debit"))
        buffer_sum_credit += _line_sum(rec.get("credit"))
        if buffer_sum_debit > 0 and abs(buffer_sum_debit - buffer_sum_credit) < 0.005:
            flush_entry()
            buffer = []
            buffer_sum_debit = 0.0
            buffer_sum_credit = 0.0
    if buffer:
        flush_entry()
    return entries_out


def _read_gl_xlsx(path: Path) -> list[dict[str, Any]]:
    return _read_journal_xlsx(path)  # Same shape; GL often has account as first column with sub-rows


def _read_trial_balance_xlsx(path: Path) -> list[dict[str, Any]]:
    headers, data = _xlsx_rows(path)
    key_map = _build_key_map(headers, [
        ("account", "account"), ("account name", "account"), ("name", "account"),
        ("debit", "debit"), ("credits", "credit"), ("credit", "credit"),
        ("balance", "balance"), ("total", "balance"),
    ])
    out = []
    for vals in data:
        row = _row_to_dict(headers, vals)
        rec = {}
        for our_key, source_keys in key_map.items():
            for sk in source_keys:
                if sk in row and row[sk] not in (None, ""):
                    rec[our_key] = _normalize_num(row[sk]) if our_key in ("debit", "credit", "balance") else row[sk]
                    break
        if rec:
            out.append(rec)
    return out


def _build_key_map(headers: list[str], pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Build our_key -> [actual header names from file that map to it]."""
    result = {}
    for our_key in {p[1] for p in pairs}:
        result[our_key] = []
    for h in headers:
        h_lower = h.lower().strip()
        for source_name, our_key in pairs:
            if source_name.lower().strip() == h_lower:
                result[our_key].append(h)
                break
    return result


# ---- CSV fallback ----
def _read_csv_rows(path: Path) -> tuple[list[str], list[list[Any]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        row0 = next(reader, None)
        if not row0:
            return [], []
        headers = [h.strip() or f"Col_{i+1}" for i, h in enumerate(row0)]
        data = list(reader)
    return headers, data


def _read_accounts_csv(path: Path) -> list[dict[str, Any]]:
    headers, data = _read_csv_rows(path)
    key_map = _build_key_map(headers, [
        ("full name", "name"), ("account name", "name"), ("account number", "code"), ("type", "account_type"),
        ("detail type", "detail_type"), ("description", "description"), ("total balance", "total_balance"),
    ])
    out = []
    for row in data:
        d = dict(zip(headers, row))
        rec = {}
        for our_key, source_keys in key_map.items():
            for sk in source_keys:
                if sk in d and d[sk]:
                    rec[our_key] = _normalize_num(d[sk]) if our_key == "total_balance" else d[sk].strip()
                    break
        if rec.get("code") or rec.get("name"):
            if not rec.get("code") and rec.get("name"):
                rec["code"] = rec["name"]
            out.append(rec)
    return out


def _read_list_csv(path: Path, name_key: str) -> list[dict[str, Any]]:
    headers, data = _read_csv_rows(path)
    out = []
    for row in data:
        d = dict(zip(headers, row))
        name = d.get(name_key) or d.get("Name") or d.get("Full name") or ""
        if not str(name).strip():
            continue
        rec = {"code": str(name).strip(), "name": str(name).strip()}
        for h in headers:
            if h and h not in ("Vendor", "Name", "Full name"):
                rec[h] = d.get(h, "")
        out.append(rec)
    return out


# ---- Public API ----
def read_qbo_file(path: Path, report_type: str | None = None) -> list[dict[str, Any]]:
    """
    Read a QBO export file and return a list of normalized row dicts.

    If report_type is None, it is detected from the filename and content.
    """
    path = Path(path)
    if report_type is None:
        report_type = detect_qbo_type(path)
    suf = path.suffix.lower()
    is_xlsx = suf in (".xlsx", ".xls")

    if report_type == QBO_ACCOUNTS:
        return _read_accounts_xlsx(path) if is_xlsx else _read_accounts_csv(path)
    if report_type == QBO_CUSTOMERS:
        return _read_customers_xlsx(path) if is_xlsx else _read_list_csv(path, "Name")
    if report_type == QBO_VENDORS:
        return _read_vendors_xlsx(path) if is_xlsx else _read_list_csv(path, "Vendor")
    if report_type == QBO_JOURNAL:
        return _read_journal_xlsx(path)
    if report_type == QBO_GL:
        return _read_gl_xlsx(path)
    if report_type == QBO_TRIAL_BALANCE:
        return _read_trial_balance_xlsx(path)
    if report_type in ("balance_sheet", "profit_loss"):
        return _read_journal_xlsx(path)  # Similar tabular layout
    # Unknown: try generic XLSX read with first row as header
    if is_xlsx:
        headers, data = _xlsx_rows(path)
        return [_row_to_dict(headers, vals) for vals in data]
    return []
