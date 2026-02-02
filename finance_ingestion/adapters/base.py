"""
Source adapter protocol and probe DTO (ERP_INGESTION_PLAN Phase 2).

Contract:
    SourceAdapter.read() yields one dict per source record (streaming).
    SourceAdapter.probe() returns a quick snapshot: row count, columns, sample rows.

Architecture: finance_ingestion/adapters. File I/O only, no DB or kernel imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol for reading structured source files into record dicts."""

    def read(self, source_path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield one dict per source record. Streams; does not load entire file."""
        ...

    def probe(self, source_path: Path, options: dict[str, Any]) -> "SourceProbe":
        """Quick probe: row count, detected columns, sample rows."""
        ...


@dataclass(frozen=True)
class SourceProbe:
    """Result of probing a source file (row count, columns, first N rows)."""

    row_count: int
    columns: tuple[str, ...]
    sample_rows: tuple[dict[str, Any], ...]  # First 5 rows; do not mutate
    encoding: str | None = None
    detected_delimiter: str | None = None
