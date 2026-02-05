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

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.dtos import ValidationError
from finance_kernel.logging_config import LogContext, get_logger

from finance_ingestion.adapters.base import SourceAdapter, SourceProbe
from finance_ingestion.adapters.csv_adapter import CsvSourceAdapter
from finance_ingestion.adapters.json_adapter import JsonSourceAdapter
from finance_ingestion.adapters.xlsx_adapter import XlsxSourceAdapter
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


def _existing_account_codes(session: Session) -> set[str]:
    """Return set of existing account codes (for conflict-free auto-assign)."""
    from finance_kernel.models.account import Account

    rows = session.execute(select(Account.code)).fetchall()
    return {r[0] for r in rows if r[0]}


def _existing_party_codes(session: Session, party_type: str) -> set[str]:
    """Return set of existing party_code for the given type (supplier/customer)."""
    from finance_kernel.models.party import Party, PartyType

    pt = PartyType(party_type) if party_type in ("supplier", "customer") else None
    if pt is None:
        return set()
    rows = session.execute(select(Party.party_code).where(Party.party_type == pt)).fetchall()
    return {r[0] for r in rows if r[0]}


def compile_mapping_from_def(def_: Any) -> ImportMapping:
    """Build domain ImportMapping from config ImportMappingDef."""
    from finance_config.schema import ImportFieldDef, ImportMappingDef, ImportValidationDef

    if not isinstance(def_, ImportMappingDef):
        raise TypeError("Expected ImportMappingDef")
    return _compile_mapping_from_def_impl(def_)


def _compile_mapping_from_def_impl(def_: Any) -> ImportMapping:
    """Implementation of compile_mapping_from_def (ImportMappingDef assumed)."""
    from finance_config.schema import ImportFieldDef, ImportMappingDef, ImportValidationDef
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


def build_mapping_registry_from_defs(
    import_mapping_defs: tuple[Any, ...],
) -> dict[str, ImportMapping]:
    """Build a mapping name -> ImportMapping registry from config ImportMappingDefs.

    Use with pack.import_mappings from get_active_config() so the import pipeline
    can resolve mappings by name. Returns an empty dict if defs is empty.
    """
    from finance_config.schema import ImportMappingDef

    registry: dict[str, ImportMapping] = {}
    for def_ in import_mapping_defs:
        if isinstance(def_, ImportMappingDef):
            m = _compile_mapping_from_def_impl(def_)
            registry[m.name] = m
    return registry


def _validation_errors_to_json(errors: tuple) -> list[dict[str, Any]] | None:
    if not errors:
        return None
    return [{"code": e.code, "message": e.message, "field": e.field, "details": e.details} for e in errors]


def _default_adapters() -> dict[str, SourceAdapter]:
    return {
        "csv": CsvSourceAdapter(),
        "json": JsonSourceAdapter(),
        "xlsx": XlsxSourceAdapter(),
    }


class ImportService:
    """Orchestrates load -> stage -> validate. Uses session, clock, adapters, mapping registry. Optional auditor for full trace."""

    def __init__(
        self,
        session: Session,
        clock: Clock | None = None,
        adapters: dict[str, SourceAdapter] | None = None,
        mapping_registry: Callable[[str], ImportMapping | None] | dict[str, ImportMapping] | None = None,
        auditor_service: Any | None = None,
    ):
        self._session = session
        self._clock = clock or SystemClock()
        self._adapters = adapters if adapters is not None else _default_adapters()
        self._auditor = auditor_service
        if callable(mapping_registry):
            self._get_mapping = mapping_registry
        elif isinstance(mapping_registry, dict):
            self._get_mapping = mapping_registry.get
        else:
            self._get_mapping = lambda _: None

    def _check_system_uniqueness(
        self,
        entity_type: str,
        mapped_data: dict[str, Any],
        rule: ImportValidationRule,
    ) -> list[ValidationError]:
        """Run scope=system unique rules: flag if value already exists in live tables."""
        errors: list[ValidationError] = []
        if rule.rule_type != "unique" or rule.scope != "system" or not rule.fields:
            return errors
        # Map entity_type to (ORM model, unique column name)
        if entity_type == "account":
            from finance_kernel.models.account import Account
            model, col = Account, "code"
        elif entity_type == "party":
            from finance_kernel.models.party import Party
            model, col = Party, "party_code"
        elif entity_type == "vendor":
            from finance_modules.ap.orm import VendorProfileModel
            model, col = VendorProfileModel, "code"
        elif entity_type == "customer":
            from finance_modules.ar.orm import CustomerProfileModel
            model, col = CustomerProfileModel, "code"
        else:
            return errors
        for field_name in rule.fields:
            value = mapped_data.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            col_attr = getattr(model, col)
            stmt = select(model).where(col_attr == value).limit(1)
            exists = self._session.scalars(stmt).first() is not None
            if exists:
                msg = rule.message or f"{field_name!r} already exists in system"
                errors.append(ValidationError(code="DUPLICATE_IN_SYSTEM", message=msg, field=field_name, details=None))
        return errors

    def _check_entity_exists(
        self,
        mapped_data: dict[str, Any],
        rule: ImportValidationRule,
    ) -> list[ValidationError]:
        """Run rule_type=exists: flag if referenced entity is not in live tables."""
        errors: list[ValidationError] = []
        if rule.rule_type != "exists" or not rule.reference_entity or not rule.fields:
            return errors
        ref_entity = rule.reference_entity
        if ref_entity == "account":
            from finance_kernel.models.account import Account
            model, col = Account, "code"
        elif ref_entity == "party":
            from finance_kernel.models.party import Party
            model, col = Party, "party_code"
        elif ref_entity == "vendor":
            from finance_modules.ap.orm import VendorProfileModel
            model, col = VendorProfileModel, "code"
        elif ref_entity == "customer":
            from finance_modules.ar.orm import CustomerProfileModel
            model, col = CustomerProfileModel, "code"
        else:
            return errors
        for field_name in rule.fields:
            value = mapped_data.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                msg = rule.message or f"Required reference {field_name!r} is missing"
                errors.append(ValidationError(code="MISSING_REFERENCE", message=msg, field=field_name, details=None))
                continue
            col_attr = getattr(model, col)
            stmt = select(model).where(col_attr == value).limit(1)
            exists = self._session.scalars(stmt).first() is not None
            if not exists:
                msg = rule.message or f"{ref_entity} with {field_name}={value!r} does not exist"
                errors.append(ValidationError(code="REFERENCE_NOT_FOUND", message=msg, field=field_name, details=None))
        return errors

    def probe_source(self, source_path: Path, mapping: ImportMapping) -> SourceProbe:
        """Preview source file: row count, columns, sample data."""
        adapter = self._adapters.get(mapping.source_format)
        if not adapter:
            raise ValueError(f"No adapter for source_format {mapping.source_format!r}")
        return adapter.probe(source_path, mapping.source_options)

    def read_rows(self, source_path: Path, mapping: ImportMapping) -> list[dict[str, Any]]:
        """Read all raw rows from source (for chunked processing). Returns list of dicts."""
        adapter = self._adapters.get(mapping.source_format)
        if not adapter:
            raise ValueError(f"No adapter for source_format {mapping.source_format!r}")
        return list(adapter.read(source_path, mapping.source_options))

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
        first_row_source_file = None
        for row_index, raw_row in enumerate(adapter.read(source_path, mapping.source_options), start=1):
            import_row = row_index
            if isinstance(raw_row, dict):
                # Prefer _excel_row (Excel traceability) then _import_row
                for key in ("_excel_row", "_import_row"):
                    ir = raw_row.get(key)
                    if ir is not None:
                        try:
                            n = int(ir)
                            if n >= 1:
                                import_row = n
                                break
                        except (TypeError, ValueError):
                            pass
                if first_row_source_file is None and raw_row.get("_source_file"):
                    first_row_source_file = str(raw_row.get("_source_file", "")).strip()
            result = apply_mapping(raw_row, mapping.field_mappings)
            record_id = uuid4()
            record_dto = ImportRecord(
                record_id=record_id,
                batch_id=batch_id,
                source_row=import_row,
                entity_type=mapping.entity_type,
                status=ImportRecordStatus.STAGED,
                raw_data=dict(raw_row),
                mapped_data=result.mapped_data if result.success else None,
                validation_errors=result.errors,
            )
            rec_model = ImportRecordModel.from_dto(record_dto, actor_id)
            self._session.add(rec_model)
            records.append(rec_model)
            logger.debug("record_staged", extra={"source_row": import_row, "record_id": str(record_id)})

        if first_row_source_file and batch_model.source_filename != first_row_source_file:
            batch_model.source_filename = first_row_source_file
        batch_model.total_records = len(records)
        batch_model.status = ImportBatchStatus.STAGED.value
        self._session.flush()

        if self._auditor:
            self._auditor.record_import_batch_created(
                batch_id=batch_id,
                actor_id=actor_id,
                mapping_name=mapping.name,
                mapping_version=mapping.version,
                mapping_hash=mapping_hash,
                source_filename=source_filename,
                total_records=len(records),
            )
        logger.info("batch_staged", extra={"total_records": len(records)})
        return batch_model.to_dto()

    def load_batch_from_rows(
        self,
        source_path: Path,
        mapping: ImportMapping,
        actor_id: UUID,
        raw_rows: list[dict[str, Any]],
        row_offset: int = 1,
    ) -> ImportBatch:
        """
        Stage a list of raw rows into a single batch (for chunked import).
        Same semantics as load_batch; use when you have already read rows (e.g. a slice of 100).
        row_offset: 1-based index of the first row in this chunk (for source_row when _import_row is absent).
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

        records: list[ImportRecordModel] = []
        first_row_source_file = None
        for i, raw_row in enumerate(raw_rows):
            row_index = row_offset + i
            import_row = row_index
            if isinstance(raw_row, dict):
                for key in ("_excel_row", "_import_row"):
                    ir = raw_row.get(key)
                    if ir is not None:
                        try:
                            n = int(ir)
                            if n >= 1:
                                import_row = n
                                break
                        except (TypeError, ValueError):
                            pass
                if first_row_source_file is None and raw_row.get("_source_file"):
                    first_row_source_file = str(raw_row.get("_source_file", "")).strip()
            result = apply_mapping(raw_row, mapping.field_mappings)
            record_id = uuid4()
            record_dto = ImportRecord(
                record_id=record_id,
                batch_id=batch_id,
                source_row=import_row,
                entity_type=mapping.entity_type,
                status=ImportRecordStatus.STAGED,
                raw_data=dict(raw_row),
                mapped_data=result.mapped_data if result.success else None,
                validation_errors=result.errors,
            )
            rec_model = ImportRecordModel.from_dto(record_dto, actor_id)
            self._session.add(rec_model)
            records.append(rec_model)
            logger.debug("record_staged", extra={"source_row": import_row, "record_id": str(record_id)})

        if first_row_source_file and batch_model.source_filename != first_row_source_file:
            batch_model.source_filename = first_row_source_file
        batch_model.total_records = len(records)
        batch_model.status = ImportBatchStatus.STAGED.value
        self._session.flush()

        if self._auditor:
            self._auditor.record_import_batch_created(
                batch_id=batch_id,
                actor_id=actor_id,
                mapping_name=mapping.name,
                mapping_version=mapping.version,
                mapping_hash=mapping_hash,
                source_filename=source_filename,
                total_records=len(records),
            )
        logger.info("batch_staged", extra={"total_records": len(records)})
        return batch_model.to_dto()

    def validate_batch(self, batch_id: UUID, actor_id: UUID | None = None) -> ImportBatch:
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
            # Referential / system validators (full validation engine)
            for rule in mapping.validations:
                errors.extend(self._check_system_uniqueness(mapping.entity_type, mapped, rule))
                errors.extend(self._check_entity_exists(mapped, rule))

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

        if self._auditor and actor_id is not None:
            self._auditor.record_import_batch_validated(
                batch_id=batch_id,
                actor_id=actor_id,
                valid_records=valid_count,
                invalid_records=invalid_count,
                validation_duration_ms=0,
            )
        logger.info("batch_validated", extra={"valid_records": valid_count, "invalid_records": invalid_count})
        return batch_model.to_dto()

    def get_batch_summary(self, batch_id: UUID) -> ImportBatch:
        """Get batch with summary counts."""
        batch_model = self._session.get(ImportBatchModel, batch_id)
        if not batch_model:
            raise ValueError(f"Batch not found: {batch_id}")
        return batch_model.to_dto()

    def list_batches(
        self,
        statuses: tuple[ImportBatchStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[ImportBatch]:
        """List import batches, most recent first. Default: STAGED, VALIDATED, COMPLETED."""
        if statuses is None:
            statuses = (
                ImportBatchStatus.STAGED,
                ImportBatchStatus.VALIDATED,
                ImportBatchStatus.COMPLETED,
            )
        status_values = [s.value for s in statuses]
        stmt = (
            select(ImportBatchModel)
            .where(ImportBatchModel.status.in_(status_values))
            .order_by(ImportBatchModel.created_at.desc())
            .limit(limit)
        )
        models = list(self._session.scalars(stmt))
        return [m.to_dto() for m in models]

    def get_batch_errors(self, batch_id: UUID) -> list[ImportRecord]:
        """Get all invalid records with their errors."""
        stmt = (
            select(ImportRecordModel)
            .where(ImportRecordModel.batch_id == batch_id, ImportRecordModel.status == ImportRecordStatus.INVALID.value)
            .order_by(ImportRecordModel.source_row)
        )
        return [r.to_dto() for r in self._session.scalars(stmt)]

    def get_record_by_batch_and_row(self, batch_id: UUID, import_row: int) -> ImportRecord:
        """Get a single record by batch and import row number (source_row). Use for Open/Edit by row."""
        stmt = (
            select(ImportRecordModel)
            .where(ImportRecordModel.batch_id == batch_id, ImportRecordModel.source_row == import_row)
            .limit(1)
        )
        rec = self._session.scalars(stmt).first()
        if not rec:
            raise ValueError(f"No record at import row {import_row} in this batch")
        return rec.to_dto()

    def auto_assign_codes(self, batch_id: UUID) -> int:
        """
        For records in the batch with missing or empty 'code' in mapped_data,
        assign a unique code that does not conflict with existing DB or other records.
        Returns the number of records updated. Caller should re-validate the batch after.
        """
        batch_model = self._session.get(ImportBatchModel, batch_id)
        if not batch_model:
            raise ValueError(f"Batch not found: {batch_id}")
        entity_type = (batch_model.entity_type or "").lower()
        stmt = select(ImportRecordModel).where(ImportRecordModel.batch_id == batch_id).order_by(ImportRecordModel.source_row)
        record_models = list(self._session.scalars(stmt))

        if entity_type == "account":
            used = set(_existing_account_codes(self._session))
        elif entity_type == "vendor":
            used = set(_existing_party_codes(self._session, "supplier"))
        elif entity_type == "customer":
            used = set(_existing_party_codes(self._session, "customer"))
        else:
            used = set()

        # Collect codes already present in this batch
        for rec in record_models:
            m = rec.mapped_data or {}
            c = m.get("code")
            if c is not None and str(c).strip():
                used.add(str(c).strip())

        def _next_account_code() -> str:
            numerics = [int(c) for c in used if isinstance(c, str) and c.isdigit()]
            start = max(numerics, default=0) + 1
            while str(start) in used:
                start += 1
            return str(start)

        def _next_vendor_code() -> str:
            n = 1
            while f"V-{n:03d}" in used:
                n += 1
            return f"V-{n:03d}"

        def _next_customer_code() -> str:
            n = 1
            while f"C-{n:03d}" in used:
                n += 1
            return f"C-{n:03d}"

        if entity_type == "account":
            next_code = _next_account_code
        elif entity_type == "vendor":
            next_code = _next_vendor_code
        elif entity_type == "customer":
            next_code = _next_customer_code
        else:
            next_code = lambda: f"IMP-{len(used)+1}"

        assigned = 0
        for rec in record_models:
            m = rec.mapped_data or {}
            code = m.get("code")
            if code is not None and str(code).strip():
                continue
            new_code = next_code()
            used.add(new_code)
            rec.mapped_data = dict(m)
            rec.mapped_data["code"] = new_code
            if not rec.mapped_data.get("name"):
                rec.mapped_data["name"] = new_code
            assigned += 1

        if assigned:
            self._session.flush()
            logger.info("auto_assign_codes", extra={"batch_id": str(batch_id), "entity_type": entity_type, "assigned_count": assigned})
        return assigned

    def delete_batch(self, batch_id: UUID) -> None:
        """Remove a batch and its records from staging. Delete records first to avoid ORM nulling batch_id."""
        batch_model = self._session.get(ImportBatchModel, batch_id)
        if not batch_model:
            raise ValueError(f"Batch not found: {batch_id}")
        # Delete child records first; otherwise SQLAlchemy may UPDATE import_records SET batch_id=NULL
        # before deleting the batch, violating NOT NULL on batch_id.
        self._session.execute(delete(ImportRecordModel).where(ImportRecordModel.batch_id == batch_id))
        self._session.delete(batch_model)
        self._session.flush()
        logger.info("batch_deleted", extra={"batch_id": str(batch_id)})

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
        for rule in mapping.validations:
            errors.extend(self._check_system_uniqueness(mapping.entity_type, mapped, rule))
            errors.extend(self._check_entity_exists(mapped, rule))

        rec.validation_errors = _validation_errors_to_json(tuple(errors))
        rec.status = ImportRecordStatus.VALID.value if not errors else ImportRecordStatus.INVALID.value
        self._session.flush()
        logger.info(
            "record_retried",
            extra={"record_id": str(record_id), "source_row": rec.source_row, "new_status": rec.status},
        )
        return rec.to_dto()
