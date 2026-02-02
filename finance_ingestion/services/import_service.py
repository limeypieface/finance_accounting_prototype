"""
Import service: load -> stage -> validate (ERP_INGESTION_PLAN Phase 6).

Orchestrates source adapters, mapping engine, and domain validators.
Uses structured logging (LogContext, get_logger("ingestion.*")).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import LogContext, get_logger

from finance_ingestion.adapters.base import SourceAdapter, SourceProbe
from finance_ingestion.adapters.csv_adapter import CsvSourceAdapter
from finance_ingestion.adapters.json_adapter import JsonSourceAdapter
from finance_ingestion.domain.types import (
    FieldMapping,
    ImportBatch,
    ImportBatchStatus,
    ImportMapping,
    ImportRecord,
    ImportRecordStatus,
    ImportValidationRule,
)
from finance_ingestion.domain.validators import (
    ENTITY_VALIDATORS,
    validate_batch_uniqueness,
    validate_currency_codes,
    validate_date_ranges_simple,
    validate_decimal_precision,
    validate_field_types,
    validate_required_fields,
)
from finance_ingestion.mapping.engine import apply_mapping
from finance_ingestion.models.staging import ImportBatchModel, ImportRecordModel

logger = get_logger("ingestion.import_service")


def compile_mapping_from_def(def_: Any) -> ImportMapping:
    """Build domain ImportMapping from config ImportMappingDef."""
    from finance_config.schema import ImportFieldDef, ImportMappingDef, ImportValidationDef

    if not isinstance(def_, ImportMappingDef):
        raise TypeError("Expected ImportMappingDef")
    field_mappings = tuple(
        FieldMapping(
            source=f.source,
            target=f.target,
            field_type=f.field_type,
            required=f.required,
            default=f.default,
            format=f.format,
            transform=f.transform,
        )
        for f in def_.field_mappings
    )
    validations = tuple(
        ImportValidationRule(
            rule_type=v.rule_type,
            fields=v.fields,
            scope=v.scope,
            reference_entity=v.reference_entity,
            expression=v.expression,
            message=v.message,
        )
        for v in def_.validations
    )
    return ImportMapping(
        name=def_.name,
        version=def_.version,
        entity_type=def_.entity_type,
        source_format=def_.source_format,
        source_options=dict(def_.source_options),
        field_mappings=field_mappings,
        validations=validations,
        dependency_tier=def_.dependency_tier,
    )


def _validation_errors_to_json(errors: tuple) -> list[dict[str, Any]] | None:
    if not errors:
        return None
    return [{"code": e.code, "message": e.message, "field": e.field, "details": e.details} for e in errors]


def _default_adapters() -> dict[str, SourceAdapter]:
    return {"csv": CsvSourceAdapter(), "json": JsonSourceAdapter()}


class ImportService:
    """Orchestrates load -> stage -> validate. Uses session, clock, adapters, mapping registry."""

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        adapters: dict[str, SourceAdapter] | None = None,
        mapping_registry: Callable[[str], ImportMapping | None] | dict[str, ImportMapping] | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._adapters = adapters if adapters is not None else _default_adapters()
        if callable(mapping_registry):
            self._get_mapping = mapping_registry
        elif isinstance(mapping_registry, dict):
            self._get_mapping = mapping_registry.get
        else:
            self._get_mapping = lambda _: None

    def probe_source(self, source_path: Path, mapping: ImportMapping) -> SourceProbe:
        """Preview source file: row count, columns, sample data."""
        adapter = self._adapters.get(mapping.source_format)
        if not adapter:
            raise ValueError(f"No adapter for source_format {mapping.source_format!r}")
        return adapter.probe(source_path, mapping.source_options)

    def load_batch(
        self,
        source_path: Path,
        mapping: ImportMapping,
        actor_id: UUID,
    ) -> ImportBatch:
        """
        Load source file into staging.
        Flow: create batch (LOADING), read via adapter, apply mapping, create records (STAGED), update batch.
        """
        batch_id = uuid4()
        mapping_hash = hashlib.sha256(f"{mapping.name}:{mapping.version}".encode()).hexdigest()[:64]
        source_filename = source_path.name

        LogContext.bind(correlation_id=str(batch_id), producer="ingestion", actor_id=str(actor_id))
        logger.info(
            "batch_created",
            extra={"mapping_name": mapping.name, "entity_type": mapping.entity_type, "source_filename": source_filename},
        )

        batch_dto = ImportBatch(
            batch_id=batch_id,
            mapping_name=mapping.name,
            entity_type=mapping.entity_type,
            source_filename=source_filename,
            status=ImportBatchStatus.LOADING,
            total_records=0,
        )
        batch_model = ImportBatchModel.from_dto(batch_dto, mapping.version, mapping_hash, actor_id)
        self._session.add(batch_model)
        self._session.flush()

        adapter = self._adapters.get(mapping.source_format)
        if not adapter:
            self._session.rollback()
            raise ValueError(f"No adapter for source_format {mapping.source_format!r}")

        records: list[ImportRecordModel] = []
        for row_index, raw_row in enumerate(adapter.read(source_path, mapping.source_options), start=1):
            result = apply_mapping(raw_row, mapping.field_mappings)
            record_id = uuid4()
            record_dto = ImportRecord(
                record_id=record_id,
                batch_id=batch_id,
                source_row=row_index,
                entity_type=mapping.entity_type,
                status=ImportRecordStatus.STAGED,
                raw_data=dict(raw_row),
                mapped_data=result.mapped_data if result.success else None,
                validation_errors=result.errors,
            )
            rec_model = ImportRecordModel.from_dto(record_dto, actor_id)
            self._session.add(rec_model)
            records.append(rec_model)
            logger.debug("record_staged", extra={"source_row": row_index, "record_id": str(record_id)})

        batch_model.total_records = len(records)
        batch_model.status = ImportBatchStatus.STAGED.value
        self._session.flush()

        logger.info("batch_staged", extra={"total_records": len(records)})
        return batch_model.to_dto()

    def validate_batch(self, batch_id: UUID) -> ImportBatch:
        """
        Validate all staged records. Run record-level and batch-level validators;
        update record status (VALID/INVALID) and batch counters.
        """
        LogContext.bind(correlation_id=str(batch_id), producer="ingestion")
        batch_model = self._session.get(ImportBatchModel, batch_id)
        if not batch_model:
            raise ValueError(f"Batch not found: {batch_id}")
        mapping = self._get_mapping(batch_model.mapping_name)
        if not mapping:
            batch_model.error_message = f"Mapping not found: {batch_model.mapping_name}"
            self._session.flush()
            return batch_model.to_dto()

        stmt = select(ImportRecordModel).where(ImportRecordModel.batch_id == batch_id).order_by(ImportRecordModel.source_row)
        record_models = list(self._session.scalars(stmt))
        logger.info("batch_validation_started", extra={"total_records": len(record_models)})

        currency_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name == "CURRENCY")
        decimal_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name == "DECIMAL")
        date_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name in ("DATE", "DATETIME"))
        batch_unique_rules = [r for r in mapping.validations if r.rule_type == "unique" and r.scope == "batch"]
        batch_unique_fields = tuple(f for r in batch_unique_rules for f in r.fields) if batch_unique_rules else ()

        batch_errors_by_index = (
            validate_batch_uniqueness([r.mapped_data or {} for r in record_models], batch_unique_fields)
            if batch_unique_fields
            else {}
        )
        valid_count = 0
        invalid_count = 0
        for rec_index, rec in enumerate(record_models):
            mapped = rec.mapped_data or {}
            errors: list = []
            errors.extend(validate_required_fields(mapped, mapping.field_mappings))
            errors.extend(validate_field_types(mapped, mapping.field_mappings))
            if currency_fields:
                errors.extend(validate_currency_codes(mapped, currency_fields))
            if decimal_fields:
                errors.extend(validate_decimal_precision(mapped, decimal_fields))
            if date_fields:
                errors.extend(validate_date_ranges_simple(mapped, date_fields))
            for validator in ENTITY_VALIDATORS.get(mapping.entity_type, ()):
                errors.extend(validator(mapped))
            errors.extend(batch_errors_by_index.get(rec_index, []))

            rec.validation_errors = _validation_errors_to_json(tuple(errors))
            if errors:
                rec.status = ImportRecordStatus.INVALID.value
                invalid_count += 1
                logger.debug(
                    "record_validated",
                    extra={"record_id": str(rec.id), "source_row": rec.source_row, "status": "invalid", "error_count": len(errors)},
                )
            else:
                rec.status = ImportRecordStatus.VALID.value
                valid_count += 1
                logger.debug("record_validated", extra={"record_id": str(rec.id), "source_row": rec.source_row, "status": "valid"})

        batch_model.valid_records = valid_count
        batch_model.invalid_records = invalid_count
        batch_model.status = ImportBatchStatus.VALIDATED.value
        self._session.flush()

        logger.info("batch_validated", extra={"valid_records": valid_count, "invalid_records": invalid_count})
        return batch_model.to_dto()

    def get_batch_summary(self, batch_id: UUID) -> ImportBatch:
        """Get batch with summary counts."""
        batch_model = self._session.get(ImportBatchModel, batch_id)
        if not batch_model:
            raise ValueError(f"Batch not found: {batch_id}")
        return batch_model.to_dto()

    def get_batch_errors(self, batch_id: UUID) -> list[ImportRecord]:
        """Get all invalid records with their errors."""
        stmt = (
            select(ImportRecordModel)
            .where(ImportRecordModel.batch_id == batch_id, ImportRecordModel.status == ImportRecordStatus.INVALID.value)
            .order_by(ImportRecordModel.source_row)
        )
        return [r.to_dto() for r in self._session.scalars(stmt)]

    def get_record_detail(self, record_id: UUID) -> ImportRecord:
        """Get full record with raw data, mapped data, and errors."""
        rec = self._session.get(ImportRecordModel, record_id)
        if not rec:
            raise ValueError(f"Record not found: {record_id}")
        return rec.to_dto()

    def retry_record(self, record_id: UUID, corrected_data: dict[str, Any]) -> ImportRecord:
        """Re-validate a single record with corrected raw data. Re-runs mapping and validation."""
        rec = self._session.get(ImportRecordModel, record_id)
        if not rec:
            raise ValueError(f"Record not found: {record_id}")
        mapping = self._get_mapping(rec.batch.mapping_name)
        if not mapping:
            raise ValueError(f"Mapping not found: {rec.batch.mapping_name}")

        rec.raw_data = dict(corrected_data)
        result = apply_mapping(rec.raw_data, mapping.field_mappings)
        rec.mapped_data = result.mapped_data if result.success else None
        rec.validation_errors = _validation_errors_to_json(result.errors)

        if not result.success:
            rec.status = ImportRecordStatus.INVALID.value
            self._session.flush()
            logger.info("record_retried", extra={"record_id": str(record_id), "source_row": rec.source_row, "new_status": "invalid"})
            return rec.to_dto()

        mapped = rec.mapped_data or {}
        errors: list = []
        errors.extend(validate_required_fields(mapped, mapping.field_mappings))
        errors.extend(validate_field_types(mapped, mapping.field_mappings))
        currency_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name == "CURRENCY")
        decimal_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name == "DECIMAL")
        date_fields = tuple(fm.target for fm in mapping.field_mappings if fm.field_type.name in ("DATE", "DATETIME"))
        if currency_fields:
            errors.extend(validate_currency_codes(mapped, currency_fields))
        if decimal_fields:
            errors.extend(validate_decimal_precision(mapped, decimal_fields))
        if date_fields:
            errors.extend(validate_date_ranges_simple(mapped, date_fields))
        for validator in ENTITY_VALIDATORS.get(mapping.entity_type, ()):
            errors.extend(validator(mapped))

        rec.validation_errors = _validation_errors_to_json(tuple(errors))
        rec.status = ImportRecordStatus.VALID.value if not errors else ImportRecordStatus.INVALID.value
        self._session.flush()
        logger.info(
            "record_retried",
            extra={"record_id": str(record_id), "source_row": rec.source_row, "new_status": rec.status},
        )
        return rec.to_dto()
