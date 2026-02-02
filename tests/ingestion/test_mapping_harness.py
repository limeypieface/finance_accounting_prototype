"""Mapping test harness: report generation, pure function, no DB (Phase 9)."""

import tempfile
from pathlib import Path

import pytest

from finance_kernel.domain.schemas.base import EventFieldType

from finance_ingestion.domain.types import FieldMapping, ImportMapping
from finance_ingestion.mapping.test_harness import (
    MappingTestReport,
    MappingTestRow,
    run_mapping_test,
)


def _party_mapping():
    return ImportMapping(
        name="test_party",
        version=1,
        entity_type="party",
        source_format="csv",
        source_options={"has_header": True},
        field_mappings=(
            FieldMapping(source="code", target="code", field_type=EventFieldType.STRING, required=True),
            FieldMapping(source="name", target="name", field_type=EventFieldType.STRING, required=False),
        ),
        validations=(),
        dependency_tier=1,
    )


class TestTestMappingValidRows:
    """run_mapping_test() with valid sample rows -> all success, mapped data returned."""

    def test_all_success(self):
        mapping = _party_mapping()
        sample_rows = [
            {"code": "P001", "name": "Acme"},
            {"code": "P002", "name": "Beta"},
        ]
        report = run_mapping_test(mapping, sample_rows)
        assert isinstance(report, MappingTestReport)
        assert report.mapping_name == "test_party"
        assert report.mapping_version == 1
        assert report.sample_count == 2
        assert report.success_count == 2
        assert report.error_count == 0
        assert len(report.rows) == 2
        assert report.summary_errors == ()

    def test_mapped_data_returned(self):
        mapping = _party_mapping()
        sample_rows = [{"code": "P001", "name": "Acme Corp"}]
        report = run_mapping_test(mapping, sample_rows)
        row = report.rows[0]
        assert row.success is True
        assert row.mapped_data == {"code": "P001", "name": "Acme Corp"}
        assert row.errors == ()

    def test_source_row_one_indexed(self):
        mapping = _party_mapping()
        sample_rows = [{"code": "A"}, {"code": "B"}]
        report = run_mapping_test(mapping, sample_rows)
        assert report.rows[0].source_row == 1
        assert report.rows[1].source_row == 2


class TestTestMappingInvalidRows:
    """run_mapping_test() with invalid rows -> per-row errors with field-level detail."""

    def test_missing_required(self):
        mapping = _party_mapping()
        sample_rows = [{"name": "No Code"}]
        report = run_mapping_test(mapping, sample_rows)
        assert report.success_count == 0
        assert report.error_count == 1
        assert len(report.rows[0].errors) > 0
        assert any("code" in (e.field or "") or "required" in (e.message or "").lower() for e in report.rows[0].errors)
        assert len(report.summary_errors) > 0

    def test_mixed_valid_invalid(self):
        mapping = _party_mapping()
        sample_rows = [
            {"code": "P001", "name": "Good"},
            {"name": "Bad"},
            {"code": "P003", "name": "Good2"},
        ]
        report = run_mapping_test(mapping, sample_rows)
        assert report.sample_count == 3
        assert report.success_count == 2
        assert report.error_count == 1
        assert report.rows[0].success is True
        assert report.rows[1].success is False
        assert report.rows[2].success is True


class TestTestMappingPureNoDb:
    """run_mapping_test() does not write to database (pure function)."""

    def test_no_session_required(self):
        mapping = _party_mapping()
        sample_rows = [{"code": "P001", "name": "Acme"}]
        report = run_mapping_test(mapping, sample_rows)
        assert report.success_count == 1
        # No session/db passed; pure function

    def test_idempotent_same_input_same_output(self):
        mapping = _party_mapping()
        sample_rows = [{"code": "P001", "name": "Acme"}]
        r1 = run_mapping_test(mapping, sample_rows)
        r2 = run_mapping_test(mapping, sample_rows)
        assert r1.success_count == r2.success_count
        assert r1.rows[0].mapped_data == r2.rows[0].mapped_data


class TestMappingTestReportShape:
    def test_report_has_summary_errors(self):
        mapping = _party_mapping()
        sample_rows = [{}]
        report = run_mapping_test(mapping, sample_rows)
        assert hasattr(report, "summary_errors")
        assert isinstance(report.summary_errors, tuple)
        assert len(report.summary_errors) >= 1

    def test_row_has_raw_data_preserved(self):
        mapping = _party_mapping()
        sample_rows = [{"code": "P001", "name": "Acme", "extra": "ignored"}]
        report = run_mapping_test(mapping, sample_rows)
        assert report.rows[0].raw_data.get("extra") == "ignored"


class TestTestMappingEmptySample:
    def test_empty_sample_rows(self):
        mapping = _party_mapping()
        report = run_mapping_test(mapping, [])
        assert report.sample_count == 0
        assert report.success_count == 0
        assert report.error_count == 0
        assert report.rows == ()


class TestRunMappingTestWithProbeSource:
    """run_mapping_test() + probe_source() compose: probe provides sample_rows."""

    def test_probe_sample_rows_feed_run_mapping_test(self, session):
        """probe_source returns sample_rows; run_mapping_test(mapping, sample_rows) produces report."""
        from finance_ingestion.services.import_service import ImportService

        mapping = _party_mapping()
        service = ImportService(session, mapping_registry={mapping.name: mapping})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\nP002,Beta\n")
            path = Path(f.name)
        try:
            probe = service.probe_source(path, mapping)
            assert probe.row_count == 2
            assert len(probe.sample_rows) == 2
            report = run_mapping_test(mapping, probe.sample_rows)
            assert report.sample_count == 2
            assert report.success_count == 2
            assert report.error_count == 0
        finally:
            path.unlink(missing_ok=True)
