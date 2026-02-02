"""Tests for source adapters (Phase 2)."""

import tempfile
from pathlib import Path

import pytest

from finance_ingestion.adapters import CsvSourceAdapter, JsonSourceAdapter, SourceProbe


class TestCsvSourceAdapter:
    """CSV adapter: read and probe with delimiter, encoding, has_header, skip_rows."""

    def test_read_with_header_yields_dicts(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("a,b,c\n1,2,3\n4,5,6\n")
            path = Path(f.name)
        try:
            adapter = CsvSourceAdapter()
            rows = list(adapter.read(path, {}))
            assert rows == [{"a": "1", "b": "2", "c": "3"}, {"a": "4", "b": "5", "c": "6"}]
        finally:
            path.unlink()

    def test_probe_returns_row_count_and_columns(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("x,y\n1,2\n3,4\n5,6\n")
            path = Path(f.name)
        try:
            adapter = CsvSourceAdapter()
            probe = adapter.probe(path, {})
            assert probe.row_count == 3
            assert probe.columns == ("x", "y")
            assert len(probe.sample_rows) == 3
        finally:
            path.unlink()

    def test_skip_rows_skips_header_or_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("comment\n# skip\nh1,h2\n1,2\n")
            path = Path(f.name)
        try:
            adapter = CsvSourceAdapter()
            rows = list(adapter.read(path, {"skip_rows": 2, "has_header": True}))
            assert rows == [{"h1": "1", "h2": "2"}]
        finally:
            path.unlink()

    def test_custom_delimiter(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("a;b;c\n1;2;3\n")
            path = Path(f.name)
        try:
            adapter = CsvSourceAdapter()
            rows = list(adapter.read(path, {"delimiter": ";"}))
            assert rows == [{"a": "1", "b": "2", "c": "3"}]
        finally:
            path.unlink()


class TestJsonSourceAdapter:
    """JSON adapter: array and jsonl format, json_path for nested."""

    def test_read_array_yields_dicts(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('[{"x":1},{"x":2,"y":3}]')
            path = Path(f.name)
        try:
            adapter = JsonSourceAdapter()
            rows = list(adapter.read(path, {"format": "array"}))
            assert rows == [{"x": 1}, {"x": 2, "y": 3}]
        finally:
            path.unlink()

    def test_probe_array_returns_count_and_columns(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('[{"a":1},{"a":2,"b":3}]')
            path = Path(f.name)
        try:
            adapter = JsonSourceAdapter()
            probe = adapter.probe(path, {"format": "array"})
            assert probe.row_count == 2
            assert set(probe.columns) == {"a", "b"}
            assert len(probe.sample_rows) == 2
        finally:
            path.unlink()

    def test_read_jsonl_yields_one_dict_per_line(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"id":1}\n{"id":2,"name":"b"}\n')
            path = Path(f.name)
        try:
            adapter = JsonSourceAdapter()
            rows = list(adapter.read(path, {"format": "jsonl"}))
            assert rows == [{"id": 1}, {"id": 2, "name": "b"}]
        finally:
            path.unlink()

    def test_probe_jsonl_counts_non_empty_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"a":1}\n{"a":2}\n\n{"a":3}\n')
            path = Path(f.name)
        try:
            adapter = JsonSourceAdapter()
            probe = adapter.probe(path, {"format": "jsonl"})
            assert probe.row_count == 3
        finally:
            path.unlink()

    def test_json_path_extracts_nested_array(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"data":{"records":[{"k":1},{"k":2}]}}')
            path = Path(f.name)
        try:
            adapter = JsonSourceAdapter()
            rows = list(adapter.read(path, {"format": "array", "json_path": "data.records"}))
            assert rows == [{"k": 1}, {"k": 2}]
            probe = adapter.probe(path, {"format": "array", "json_path": "data.records"})
            assert probe.row_count == 2
            assert probe.columns == ("k",)
        finally:
            path.unlink()


class TestSourceAdapterProtocol:
    """SourceAdapter protocol: read yields dicts, probe returns SourceProbe."""

    def test_csv_implements_protocol(self):
        from finance_ingestion.adapters.base import SourceAdapter

        assert isinstance(CsvSourceAdapter(), SourceAdapter)

    def test_json_implements_protocol(self):
        from finance_ingestion.adapters.base import SourceAdapter

        assert isinstance(JsonSourceAdapter(), SourceAdapter)
