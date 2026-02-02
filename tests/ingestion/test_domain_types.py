"""Domain type construction, immutability, enum values (Phase 9)."""

import dataclasses
from uuid import uuid4

import pytest

from finance_kernel.domain.dtos import ValidationError
from finance_kernel.domain.schemas.base import EventFieldType

from finance_ingestion.domain.types import (
    FieldMapping,
    ImportBatch,
    ImportBatchStatus,
    ImportMapping,
    ImportRecord,
    ImportRecordStatus,
    ImportValidationRule,
)


class TestImportRecordStatus:
    def test_enum_values(self):
        assert ImportRecordStatus.STAGED.value == "staged"
        assert ImportRecordStatus.VALID.value == "valid"
        assert ImportRecordStatus.INVALID.value == "invalid"
        assert ImportRecordStatus.PROMOTED.value == "promoted"
        assert ImportRecordStatus.PROMOTION_FAILED.value == "promotion_failed"
        assert ImportRecordStatus.SKIPPED.value == "skipped"

    def test_from_string(self):
        assert ImportRecordStatus("valid") == ImportRecordStatus.VALID
        assert ImportRecordStatus("promoted") == ImportRecordStatus.PROMOTED


class TestImportBatchStatus:
    def test_enum_values(self):
        assert ImportBatchStatus.LOADING.value == "loading"
        assert ImportBatchStatus.STAGED.value == "staged"
        assert ImportBatchStatus.VALIDATED.value == "validated"
        assert ImportBatchStatus.COMPLETED.value == "completed"
        assert ImportBatchStatus.FAILED.value == "failed"


class TestImportBatch:
    def test_construction(self):
        bid = uuid4()
        batch = ImportBatch(
            batch_id=bid,
            mapping_name="test",
            entity_type="party",
            source_filename="file.csv",
            status=ImportBatchStatus.STAGED,
            total_records=10,
            valid_records=8,
            invalid_records=2,
        )
        assert batch.batch_id == bid
        assert batch.mapping_name == "test"
        assert batch.entity_type == "party"
        assert batch.source_filename == "file.csv"
        assert batch.status == ImportBatchStatus.STAGED
        assert batch.total_records == 10
        assert batch.valid_records == 8
        assert batch.invalid_records == 2
        assert batch.promoted_records == 0
        assert batch.skipped_records == 0

    def test_immutable(self):
        batch = ImportBatch(
            batch_id=uuid4(),
            mapping_name="x",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED,
        )
        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
            batch.mapping_name = "y"  # type: ignore[misc]

    def test_batch_immutable_status(self):
        batch = ImportBatch(
            batch_id=uuid4(),
            mapping_name="x",
            entity_type="party",
            source_filename="f.csv",
            status=ImportBatchStatus.STAGED,
        )
        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
            batch.status = ImportBatchStatus.COMPLETED  # type: ignore[misc]


class TestImportRecord:
    def test_construction(self):
        rid, bid = uuid4(), uuid4()
        rec = ImportRecord(
            record_id=rid,
            batch_id=bid,
            source_row=1,
            entity_type="party",
            status=ImportRecordStatus.VALID,
            raw_data={"code": "P001", "name": "Acme"},
            mapped_data={"code": "P001", "name": "Acme"},
        )
        assert rec.record_id == rid
        assert rec.batch_id == bid
        assert rec.source_row == 1
        assert rec.entity_type == "party"
        assert rec.status == ImportRecordStatus.VALID
        assert rec.raw_data == {"code": "P001", "name": "Acme"}
        assert rec.mapped_data == {"code": "P001", "name": "Acme"}
        assert rec.validation_errors == ()
        assert rec.promoted_entity_id is None
        assert rec.promoted_at is None

    def test_immutable(self):
        rec = ImportRecord(
            record_id=uuid4(),
            batch_id=uuid4(),
            source_row=1,
            entity_type="party",
            status=ImportRecordStatus.STAGED,
            raw_data={},
        )
        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
            rec.status = ImportRecordStatus.VALID  # type: ignore[misc]


class TestFieldMapping:
    def test_construction(self):
        fm = FieldMapping(
            source="col_a",
            target="code",
            field_type=EventFieldType.STRING,
            required=True,
            transform="strip",
        )
        assert fm.source == "col_a"
        assert fm.target == "code"
        assert fm.field_type == EventFieldType.STRING
        assert fm.required is True
        assert fm.transform == "strip"
        assert fm.default is None
        assert fm.format is None

    def test_immutable(self):
        fm = FieldMapping(source="x", target="y", field_type=EventFieldType.STRING)
        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
            fm.target = "z"  # type: ignore[misc]


class TestImportValidationRule:
    def test_construction(self):
        r = ImportValidationRule(
            rule_type="unique",
            fields=("code",),
            scope="batch",
        )
        assert r.rule_type == "unique"
        assert r.fields == ("code",)
        assert r.scope == "batch"
        assert r.reference_entity is None
        assert r.expression is None


class TestImportMapping:
    def test_construction(self):
        fm = FieldMapping(source="code", target="code", field_type=EventFieldType.STRING, required=True)
        m = ImportMapping(
            name="sap_party",
            version=2,
            entity_type="party",
            source_format="csv",
            source_options={"has_header": True},
            field_mappings=(fm,),
            validations=(),
            dependency_tier=1,
        )
        assert m.name == "sap_party"
        assert m.version == 2
        assert m.entity_type == "party"
        assert m.source_format == "csv"
        assert m.source_options == {"has_header": True}
        assert len(m.field_mappings) == 1
        assert m.field_mappings[0].source == "code"
        assert m.dependency_tier == 1

    def test_immutable(self):
        m = ImportMapping(
            name="x",
            version=1,
            entity_type="party",
            source_format="csv",
        )
        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
            m.name = "y"  # type: ignore[misc]
