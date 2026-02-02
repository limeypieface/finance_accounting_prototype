"""Staging ORM round-trip, batch-level mapping snapshot (Phase 9)."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from finance_ingestion.domain.types import (
    ImportBatch,
    ImportBatchStatus,
    ImportRecord,
    ImportRecordStatus,
)
from finance_ingestion.models.staging import ImportBatchModel, ImportRecordModel


@pytest.fixture
def test_actor_id():
    return uuid4()


class TestImportBatchModelRoundTrip:
    def test_batch_to_dto_and_from_dto_roundtrip(self, session, test_actor_id):
        batch_dto = ImportBatch(
            batch_id=uuid4(),
            mapping_name="test_map",
            entity_type="party",
            source_filename="file.csv",
            status=ImportBatchStatus.STAGED,
            total_records=5,
            valid_records=4,
            invalid_records=1,
            promoted_records=0,
            skipped_records=0,
        )
        model = ImportBatchModel.from_dto(
            batch_dto,
            mapping_version=2,
            mapping_hash="abc123",
            created_by_id=test_actor_id,
        )
        session.add(model)
        session.flush()
        out_dto = model.to_dto()
        assert out_dto.batch_id == batch_dto.batch_id
        assert out_dto.mapping_name == batch_dto.mapping_name
        assert out_dto.entity_type == batch_dto.entity_type
        assert out_dto.source_filename == batch_dto.source_filename
        assert out_dto.status == batch_dto.status
        assert out_dto.total_records == batch_dto.total_records
        assert out_dto.valid_records == batch_dto.valid_records
        assert out_dto.invalid_records == batch_dto.invalid_records

    def test_batch_stores_mapping_version_and_hash(self, session, test_actor_id):
        batch_dto = ImportBatch(
            batch_id=uuid4(),
            mapping_name="m",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED,
        )
        model = ImportBatchModel.from_dto(
            batch_dto,
            mapping_version=3,
            mapping_hash="deadbeef",
            created_by_id=test_actor_id,
        )
        session.add(model)
        session.flush()
        assert model.mapping_version == 3
        assert model.mapping_hash == "deadbeef"


class TestImportRecordModelRoundTrip:
    def test_record_to_dto_and_from_dto_roundtrip(self, session, test_actor_id):
        batch = ImportBatchModel(
            id=uuid4(),
            mapping_name="m",
            mapping_version=1,
            mapping_hash="h",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED.value,
            created_by_id=test_actor_id,
            updated_by_id=None,
        )
        session.add(batch)
        session.flush()
        rec_dto = ImportRecord(
            record_id=uuid4(),
            batch_id=batch.id,
            source_row=1,
            entity_type="party",
            status=ImportRecordStatus.VALID,
            raw_data={"code": "P001", "name": "Acme"},
            mapped_data={"code": "P001", "name": "Acme"},
        )
        rec_model = ImportRecordModel.from_dto(rec_dto, created_by_id=test_actor_id)
        session.add(rec_model)
        session.flush()
        out_dto = rec_model.to_dto()
        assert out_dto.record_id == rec_dto.record_id
        assert out_dto.batch_id == rec_dto.batch_id
        assert out_dto.source_row == rec_dto.source_row
        assert out_dto.entity_type == rec_dto.entity_type
        assert out_dto.status == rec_dto.status
        assert out_dto.raw_data == rec_dto.raw_data
        assert out_dto.mapped_data == rec_dto.mapped_data

    def test_record_validation_errors_serialized(self, session, test_actor_id):
        from finance_kernel.domain.dtos import ValidationError

        batch = ImportBatchModel(
            id=uuid4(),
            mapping_name="m",
            mapping_version=1,
            mapping_hash="h",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED.value,
            created_by_id=test_actor_id,
            updated_by_id=None,
        )
        session.add(batch)
        session.flush()
        errors = (ValidationError(code="REQUIRED", message="code is required", field="code", details=None),)
        rec_dto = ImportRecord(
            record_id=uuid4(),
            batch_id=batch.id,
            source_row=1,
            entity_type="party",
            status=ImportRecordStatus.INVALID,
            raw_data={},
            mapped_data=None,
            validation_errors=errors,
        )
        rec_model = ImportRecordModel.from_dto(rec_dto, created_by_id=test_actor_id)
        session.add(rec_model)
        session.flush()
        out_dto = rec_model.to_dto()
        assert len(out_dto.validation_errors) == 1
        assert out_dto.validation_errors[0].code == "REQUIRED"
        assert out_dto.validation_errors[0].field == "code"


class TestRawDataPreserved:
    """IM-9: Original source data preserved unmodified alongside mapped data."""

    def test_record_raw_data_preserved(self, session, test_actor_id):
        batch = ImportBatchModel(
            id=uuid4(),
            mapping_name="m",
            mapping_version=1,
            mapping_hash="h",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED.value,
            created_by_id=test_actor_id,
            updated_by_id=None,
        )
        session.add(batch)
        session.flush()
        raw = {"code": "P001", "name": "Acme", "extra_col": "ignored"}
        rec_dto = ImportRecord(
            record_id=uuid4(),
            batch_id=batch.id,
            source_row=1,
            entity_type="party",
            status=ImportRecordStatus.STAGED,
            raw_data=raw,
            mapped_data={"code": "P001", "name": "Acme"},
        )
        rec_model = ImportRecordModel.from_dto(rec_dto, created_by_id=test_actor_id)
        session.add(rec_model)
        session.flush()
        out = rec_model.to_dto()
        assert out.raw_data == raw
        assert out.raw_data.get("extra_col") == "ignored"


class TestBatchRecordRelationship:
    def test_batch_has_records(self, session, test_actor_id):
        batch = ImportBatchModel(
            id=uuid4(),
            mapping_name="m",
            mapping_version=1,
            mapping_hash="h",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED.value,
            created_by_id=test_actor_id,
            updated_by_id=None,
        )
        session.add(batch)
        session.flush()
        rec = ImportRecordModel(
            id=uuid4(),
            batch_id=batch.id,
            source_row=1,
            entity_type="party",
            status=ImportRecordStatus.STAGED.value,
            raw_data={},
            created_by_id=test_actor_id,
            updated_by_id=None,
        )
        session.add(rec)
        session.flush()
        session.refresh(batch)
        assert len(batch.records) >= 1
        assert rec.batch_id == batch.id
