"""
JSON source adapter (ERP_INGESTION_PLAN Phase 2).

Handles JSON array (file is [{...}, {...}, ...]) and JSON Lines (one object per line).
Configurable: json_path for nested arrays (e.g. "data.records"), format "array" | "jsonl".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from finance_ingestion.adapters.base import SourceProbe


def _get_nested(data: Any, path: str) -> Any:
    """Follow dot-separated path into dict/list. Returns None if key missing."""
    if not path.strip():
        return data
    for key in path.split("."):
        key = key.strip()
        if not key:
            continue
        if isinstance(data, list):
            try:
                data = data[int(key)]
            except (ValueError, IndexError):
                return None
        elif isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    return data


def _all_keys(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    """Union of keys from first 5 rows for column list."""
    seen: set[str] = set()
    for row in rows[:5]:
        seen.update(row.keys())
    return tuple(sorted(seen))


def _normalize_row_keys(item: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with string keys lowercased so mappings (e.g. 'name', 'code') match regardless of JSON casing."""
    return {str(k).strip().lower(): v for k, v in item.items() if isinstance(k, str)}


def _has_required_keys(row: dict[str, Any], required_keys: list[str]) -> bool:
    """True if row has all required keys with valid values. Used to skip incomplete rows (e.g. journal without date/lines)."""
    for key in required_keys:
        val = row.get(key)
        if val is None:
            return False
        if key == "date":
            # Mapping expects date as string (e.g. MM/DD/YYYY). Skip rows with Excel serial or other non-string.
            if not isinstance(val, str) or not val.strip():
                return False
        elif key == "lines":
            if not isinstance(val, list) or len(val) == 0:
                return False
        elif isinstance(val, str) and not val.strip():
            return False
    return True


class JsonSourceAdapter:
    """Read JSON array or JSON Lines files as one dict per record."""

    def read(self, source_path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
        fmt = options.get("format", "array")
        json_path = options.get("json_path")
        encoding = options.get("encoding", "utf-8")

        if fmt == "jsonl":
            required_keys = options.get("required_keys")
            with source_path.open("r", encoding=encoding) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        continue
                    row = _normalize_row_keys(item)
                    if required_keys and not _has_required_keys(row, required_keys):
                        continue
                    yield row
            return

        with source_path.open("r", encoding=encoding) as f:
            data = json.load(f)
        root = _get_nested(data, json_path) if json_path else data
        if not isinstance(root, list):
            return
        required_keys = options.get("required_keys")
        for item in root:
            if not isinstance(item, dict):
                continue
            row = _normalize_row_keys(item)
            if required_keys:
                if not _has_required_keys(row, required_keys):
                    continue
            yield row

    def probe(self, source_path: Path, options: dict[str, Any]) -> SourceProbe:
        fmt = options.get("format", "array")
        json_path = options.get("json_path")
        encoding = options.get("encoding", "utf-8")
        sample_size = 5

        if fmt == "jsonl":
            sample_list: list[dict[str, Any]] = []
            count = 0
            with source_path.open("r", encoding=encoding) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    count += 1
                    if len(sample_list) < sample_size:
                        try:
                            sample_list.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            sample = sample_list
            columns = _all_keys(sample) if sample else ()
            return SourceProbe(
                row_count=count,
                columns=columns,
                sample_rows=tuple(sample),
                encoding=encoding,
                detected_delimiter=None,
            )

        with source_path.open("r", encoding=encoding) as f:
            data = json.load(f)
        root = _get_nested(data, json_path) if json_path else data
        if not isinstance(root, list):
            return SourceProbe(
                row_count=0,
                columns=(),
                sample_rows=(),
                encoding=encoding,
                detected_delimiter=None,
            )
        sample = [r for r in root[:sample_size] if isinstance(r, dict)]
        columns = _all_keys(sample) if sample else ()
        return SourceProbe(
            row_count=len(root),
            columns=columns,
            sample_rows=tuple(sample),
            encoding=encoding,
            detected_delimiter=None,
        )
