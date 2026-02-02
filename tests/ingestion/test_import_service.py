"""Tests for ImportService (Phase 6)."""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from finance_ingestion.domain.types import (
    FieldMapping,
    ImportBatchStatus,
    ImportMapping,
    ImportRecordStatus,
    ImportValidationRule,
)
from finance_ingestion.models.staging import ImportRecordModel
from finance_ingestion.services.import_service import ImportService, compile_mapping_from_def
from finance_kernel.domain.schemas.base import EventFieldType


def _make_mapping(name: str = "test_party") -> ImportMapping:
    return ImportMapping(
        name=name,
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


class TestCompileMappingFromDef:
    def test_compile_from_def(self):
        from finance_config.schema import ImportFieldDef, ImportMappingDef, ImportValidationDef

        def_ = ImportMappingDef(
            name="sap_vendors",
            version=1,
            entity_type="vendor",
            source_format="csv",
            field_mappings=(
                ImportFieldDef(source="LIFNR", target="code", field_type=EventFieldType.STRING, required=True),
            ),
            validations=(ImportValidationDef(rule_type="unique", fields=("code",), scope="batch", message="dup"),),
            dependency_tier=2,
        )
        mapping = compile_mapping_from_def(def_)
        assert mapping.name == "sap_vendors" and mapping.entity_type == "vendor"
        assert len(mapping.field_mappings) == 1 and mapping.field_mappings[0].source == "LIFNR"
        assert len(mapping.validations) == 1 and mapping.validations[0].rule_type == "unique"


class TestImportServiceProbe:
    def test_probe_source_csv(self, session, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\nP002,Beta\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            service = ImportService(session, mapping_registry={})
            probe = service.probe_source(path, mapping)
            assert probe.row_count == 2
            assert "code" in probe.columns and "name" in probe.columns
        finally:
            path.unlink(missing_ok=True)


class TestImportServiceLoadAndValidate:
    @pytest.fixture
    def import_service(self, session, deterministic_clock):
        mapping = _make_mapping()
        return ImportService(
            session,
            clock=deterministic_clock,
            mapping_registry={mapping.name: mapping},
        )

    def test_load_batch_and_validate(self, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\nP002,Beta\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            assert batch.batch_id is not None
            assert batch.status == ImportBatchStatus.STAGED
            assert batch.total_records == 2

            validated = import_service.validate_batch(batch.batch_id)
            assert validated.status == ImportBatchStatus.VALIDATED
            assert validated.valid_records == 2
            assert validated.invalid_records == 0

            summary = import_service.get_batch_summary(batch.batch_id)
            assert summary.total_records == 2 and summary.valid_records == 2

            errors = import_service.get_batch_errors(batch.batch_id)
            assert len(errors) == 0
        finally:
            path.unlink(missing_ok=True)

    def test_get_record_detail(self, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            rec = import_service._session.scalars(
                select(ImportRecordModel).where(ImportRecordModel.batch_id == batch.batch_id).limit(1)
            ).first()
            assert rec is not None
            detail = import_service.get_record_detail(rec.id)
            assert detail.record_id == rec.id
            assert detail.raw_data.get("code") == "P001"
        finally:
            path.unlink(missing_ok=True)

    def test_retry_record(self, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\n,Missing\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            validated = import_service.validate_batch(batch.batch_id)
            assert validated.invalid_records == 1
            errors = import_service.get_batch_errors(batch.batch_id)
            assert len(errors) == 1
            record_id = errors[0].record_id
            corrected = {"code": "P001", "name": "Acme"}
            updated = import_service.retry_record(record_id, corrected)
            assert updated.status == ImportRecordStatus.VALID
            assert updated.mapped_data and updated.mapped_data.get("code") == "P001"
        finally:
            path.unlink(missing_ok=True)

    def test_missing_required_fields_marked_invalid_with_clear_error(self, import_service, test_actor_id):
        """CSV with missing required fields -> record marked INVALID with clear error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\n,No Code\nP002,Has Code\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            validated = import_service.validate_batch(batch.batch_id)
            assert validated.total_records == 2
            assert validated.valid_records == 1
            assert validated.invalid_records == 1
            errors = import_service.get_batch_errors(batch.batch_id)
            assert len(errors) == 1
            assert errors[0].source_row == 1
            assert len(errors[0].validation_errors) >= 1
            assert any("code" in (e.field or "") or "required" in (e.message or "").lower() for e in errors[0].validation_errors)
        finally:
            path.unlink(missing_ok=True)

    def test_batch_with_mixed_valid_invalid_proceeds(self, import_service, test_actor_id):
        """Batch with some invalid records -> valid/invalid counts correct, batch proceeds."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP1,One\n,Missing\nP3,Three\n,Bad\nP5,Five\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            validated = import_service.validate_batch(batch.batch_id)
            assert validated.total_records == 5
            assert validated.valid_records == 3
            assert validated.invalid_records == 2
            errors = import_service.get_batch_errors(batch.batch_id)
            assert len(errors) == 2
            summary = import_service.get_batch_summary(batch.batch_id)
            assert summary.valid_records == 3 and summary.invalid_records == 2
        finally:
            path.unlink(missing_ok=True)

    def test_empty_file_batch_with_zero_records(self, import_service, test_actor_id):
        """Empty file (header only) -> batch with 0 records."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            assert batch.total_records == 0
            import_service.validate_batch(batch.batch_id)
            summary = import_service.get_batch_summary(batch.batch_id)
            assert summary.total_records == 0 and summary.valid_records == 0
        finally:
            path.unlink(missing_ok=True)

    def test_duplicate_code_within_batch_caught_by_validation(self, import_service, test_actor_id):
        """Duplicate vendor/party code within batch -> cross-record validation catches it."""
        mapping = ImportMapping(
            name="test_party",
            version=1,
            entity_type="party",
            source_format="csv",
            source_options={"has_header": True},
            field_mappings=(
                FieldMapping(source="code", target="code", field_type=EventFieldType.STRING, required=True),
                FieldMapping(source="name", target="name", field_type=EventFieldType.STRING, required=False),
            ),
            validations=(ImportValidationRule(rule_type="unique", fields=("code",), scope="batch"),),
            dependency_tier=1,
        )
        service = ImportService(
            import_service._session,
            clock=import_service._clock,
            mapping_registry={mapping.name: mapping},
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nDUP,Dup One\nDUP,Dup Two\n")
            path = Path(f.name)
        try:
            batch = service.load_batch(path, mapping, test_actor_id)
            validated = service.validate_batch(batch.batch_id)
            assert validated.invalid_records >= 1
            errors = service.get_batch_errors(batch.batch_id)
            assert validated.invalid_records >= 1
            has_dup_error = any(
                (e.code or "").startswith("DUPLICATE") or "duplicate" in (e.message or "").lower()
                for rec in errors for e in rec.validation_errors
            )
            assert has_dup_error
        finally:
            path.unlink(missing_ok=True)
