"""
CSV source adapter (ERP_INGESTION_PLAN Phase 2).

Uses csv.DictReader. Configurable: delimiter, encoding, has_header, quoting,
skip_rows. Handles BOM via utf-8-sig when encoding is utf-8. Streams rows.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterator

from finance_ingestion.adapters.base import SourceProbe


_QUOTING = {
    "minimal": csv.QUOTE_MINIMAL,
    "all": csv.QUOTE_ALL,
    "nonnumeric": csv.QUOTE_NONNUMERIC,
    "none": csv.QUOTE_NONE,
}


def _get_encoding(options: dict[str, Any]) -> str:
    enc = options.get("encoding", "utf-8")
    if enc.lower() == "utf-8":
        return "utf-8-sig"  # Strip BOM if present
    return enc


def _get_quoting(options: dict[str, Any]) -> int:
    q = options.get("quoting", "minimal")
    if isinstance(q, int):
        return q
    return _QUOTING.get(str(q).lower(), csv.QUOTE_MINIMAL)


def _open_and_skip(source_path: Path, encoding: str, skip_rows: int) -> Iterator[str]:
    """Open file and skip the first skip_rows lines."""
    with source_path.open("r", encoding=encoding, newline="") as f:
        for _ in range(skip_rows):
            next(f, None)
        yield from f


class CsvSourceAdapter:
    """Read CSV files as one dict per row. Streams; does not load entire file."""

    def read(self, source_path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
        encoding = _get_encoding(options)
        delimiter = options.get("delimiter", ",")
        has_header = options.get("has_header", True)
        skip_rows = int(options.get("skip_rows", 0))
        quoting = _get_quoting(options)

        with source_path.open("r", encoding=encoding, newline="") as f:
            for _ in range(skip_rows):
                next(f, None)
            if has_header:
                reader = csv.DictReader(f, delimiter=delimiter, quoting=quoting)
                yield from reader
            else:
                columns = options.get("columns")
                if not columns:
                    first = next(f, None)
                    if first is None:
                        return
                    row = next(csv.reader([first], delimiter=delimiter, quoting=quoting))
                    columns = [f"field_{i}" for i in range(len(row))]
                    yield dict(zip(columns, row))
                reader = csv.reader(f, delimiter=delimiter, quoting=quoting)
                for row in reader:
                    yield dict(zip(columns, row))

    def probe(self, source_path: Path, options: dict[str, Any]) -> SourceProbe:
        encoding = _get_encoding(options)
        delimiter = options.get("delimiter", ",")
        has_header = options.get("has_header", True)
        skip_rows = int(options.get("skip_rows", 0))
        quoting = _get_quoting(options)
        sample_size = 5

        with source_path.open("r", encoding=encoding, newline="") as f:
            for _ in range(skip_rows):
                next(f, None)
            if has_header:
                reader = csv.DictReader(f, delimiter=delimiter, quoting=quoting)
                columns_tuple = tuple(reader.fieldnames or ())
                sample: list[dict[str, Any]] = []
                for row in reader:
                    sample.append(dict(row))
                    if len(sample) >= sample_size:
                        break
                count = len(sample)
                for _ in reader:
                    count += 1
            else:
                columns = options.get("columns")
                reader = csv.reader(f, delimiter=delimiter, quoting=quoting)
                rows: list[list[str]] = []
                for row in reader:
                    rows.append(row)
                    if len(rows) >= sample_size:
                        break
                if not rows:
                    return SourceProbe(
                        row_count=0,
                        columns=(),
                        sample_rows=(),
                        encoding=encoding,
                        detected_delimiter=delimiter,
                    )
                if columns is None:
                    columns = [f"field_{i}" for i in range(len(rows[0]))]
                columns_tuple = tuple(columns)
                sample = [dict(zip(columns_tuple, r)) for r in rows]
                count = len(rows)
                for _ in reader:
                    count += 1

        return SourceProbe(
            row_count=count,
            columns=columns_tuple,
            sample_rows=tuple(sample),
            encoding=encoding,
            detected_delimiter=delimiter,
        )
