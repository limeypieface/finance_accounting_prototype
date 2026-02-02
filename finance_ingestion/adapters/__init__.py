"""Source adapters for ERP ingestion (file I/O only, no DB)."""

from finance_ingestion.adapters.base import SourceAdapter, SourceProbe
from finance_ingestion.adapters.csv_adapter import CsvSourceAdapter
from finance_ingestion.adapters.json_adapter import JsonSourceAdapter

__all__ = ["SourceAdapter", "SourceProbe", "CsvSourceAdapter", "JsonSourceAdapter"]
