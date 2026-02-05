"""
Process a folder of QuickBooks Online exports (XLSX/CSV) and write structured JSON
to the upload directory. Each file is detected by type (accounts, customers, vendors,
journal, general ledger, etc.), read with the appropriate reader, and saved as
upload/qbo_<type>_<basename>.json (or upload/qbo_<type>.json when one file per type).

Each record in the output "rows" array includes _import_row (1-based index) so you can
correlate validation errors (e.g. "Row 724") with the JSON: search for "_import_row": 724.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from scripts.qbo.detect import (
    QBO_ACCOUNTS,
    QBO_CUSTOMERS,
    QBO_GL,
    QBO_JOURNAL,
    QBO_TRIAL_BALANCE,
    QBO_UNKNOWN,
    QBO_VENDORS,
    detect_qbo_type,
)
from scripts.qbo.readers import read_qbo_file


def _default_upload_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "upload"


def _safe_basename(path: Path) -> str:
    """Sanitize filename for use in output name (alphanumeric + underscore)."""
    s = path.stem
    return "".join(c if c.isalnum() or c in " _-" else "_" for c in s).strip() or "export"


def _rows_with_import_marker(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add _import_row (1-based) to each record so validation 'Row N' can be found in the JSON."""
    return [dict(r, _import_row=i) for i, r in enumerate(rows, start=1)]


def run_qbo_convert(
    source_dir: Path,
    output_dir: Path | None = None,
    one_file_per_type: bool = False,
    indent: int = 2,
) -> list[tuple[Path, Path, int]]:
    """
    Convert all QBO XLSX/CSV files in source_dir to structured JSON in output_dir.

    Returns list of (input_path, output_path, row_count) for each file processed.
    If one_file_per_type is True, append rows to a single file per type (e.g. qbo_accounts.json);
    otherwise write one JSON per input file (e.g. qbo_accounts_Company_Account_List.json).
    """
    source_dir = Path(source_dir)
    output_dir = Path(output_dir or _default_upload_dir())
    output_dir.mkdir(parents=True, exist_ok=True)

    extensions = (".xlsx", ".xls", ".csv")
    files = sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in extensions)
    results: list[tuple[Path, Path, int]] = []

    if one_file_per_type:
        # Accumulate per type, then write once per type
        by_type: dict[str, list[dict[str, Any]]] = {}
        for path in files:
            try:
                report_type = detect_qbo_type(path)
            except (BadZipFile, Exception):
                report_type = QBO_UNKNOWN
            try:
                rows = read_qbo_file(path, report_type)
            except BadZipFile:
                print(f"  Skip (not a valid XLSX): {path.name}", file=sys.stderr)
                continue
            except Exception as e:
                print(f"  Skip ({path.name}): {e}", file=sys.stderr)
                continue
            if not rows:
                continue
            out_type = report_type if report_type != QBO_UNKNOWN else "data"
            if out_type not in by_type:
                by_type[out_type] = []
            by_type[out_type].extend(rows)
        for report_type, rows in by_type.items():
            if not rows:
                continue
            out_path = output_dir / f"qbo_{report_type}.json"
            tagged = _rows_with_import_marker(rows)
            payload = {"source": "qbo", "report": report_type, "row_count": len(tagged), "rows": tagged}
            out_path.write_text(json.dumps(payload, indent=indent, default=str), encoding="utf-8")
            results.append((Path(), out_path, len(rows)))
        return results

    for path in files:
        try:
            report_type = detect_qbo_type(path)
        except (BadZipFile, Exception):
            report_type = QBO_UNKNOWN
        # Process every file: known types use type-specific reader; unknown use generic read
        try:
            rows = read_qbo_file(path, report_type)
        except BadZipFile:
            print(f"  Skip (not a valid XLSX): {path.name}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  Skip ({path.name}): {e}", file=sys.stderr)
            continue
        if not rows:
            continue
        base = _safe_basename(path)
        out_type = report_type if report_type != QBO_UNKNOWN else "data"
        out_name = f"qbo_{out_type}_{base}.json"
        out_path = output_dir / out_name
        tagged = _rows_with_import_marker(rows)
        payload = {"source": "qbo", "report": out_type, "source_file": path.name, "row_count": len(tagged), "rows": tagged}
        out_path.write_text(json.dumps(payload, indent=indent, default=str), encoding="utf-8")
        results.append((path, out_path, len(rows)))

    return results


def main() -> int:
    import argparse
    import warnings
    # QBO exports often lack Excel style info; openpyxl warns unnecessarily
    warnings.filterwarnings("ignore", message="Workbook contains no default style", module="openpyxl")
    parser = argparse.ArgumentParser(description="Convert QBO XLSX/CSV exports to structured JSON in the upload directory.")
    parser.add_argument("source_dir", nargs="?", default="upload", help="Folder containing QBO export files (default: upload)")
    parser.add_argument("-o", "--output-dir", help="Output directory for JSON (default: upload)")
    parser.add_argument("--one-file-per-type", action="store_true", help="Merge all files of each type into one JSON (e.g. qbo_accounts.json)")
    parser.add_argument("-q", "--quiet", action="store_true", help="No progress output")
    args = parser.parse_args()
    source = Path(args.source_dir)
    if not source.is_dir():
        print(f"Error: source directory not found: {source}")
        return 1
    results = run_qbo_convert(source, output_dir=args.output_dir or None, one_file_per_type=args.one_file_per_type)
    if not args.quiet:
        for inp, out_path, count in results:
            label = inp.name if inp else out_path.stem
            print(f"  {label} -> {out_path.name} ({count} rows)")
        if not results:
            print("  No QBO export files found or no rows read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
