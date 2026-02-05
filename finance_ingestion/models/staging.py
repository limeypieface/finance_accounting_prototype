"""
Staging ORM models for ERP data ingestion (ERP_INGESTION_PLAN Phase 1).

Contract:
    ImportBatchModel and ImportRecordModel persist batches and per-row records
    with raw_data (IM-9), mapped_data, validation_errors, and promotion results.
    Batch-level mapping_version and mapping_hash (IM-11) are frozen at creation.

Architecture: finance_ingestion/models. Imports from finance_kernel.db.base only.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finance_kernel.db.base import TrackedBase, UUIDString

if TYPE_CHECKING:
    from finance_ingestion.domain.types import ImportBatch, ImportRecord


def _to_json_safe(obj: Any) -> Any:
    """Convert values to JSON-serializable form (Decimal -> str, etc.)."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    return obj


def _validation_errors_to_json(errors: tuple) -> list[dict] | None:
    """Serialize ValidationError tuple to JSON-serializable list."""
    if not errors:
        return None
    return [
        {"code": e.code, "message": e.message, "field": e.field, "details": e.details}
        for e in errors
    ]


def _json_to_validation_errors(data: list | None):
    """Deserialize JSON list to ValidationError tuple. Import inline to avoid cycle."""
    if not data:
        return ()
    from finance_kernel.domain.dtos import ValidationError

    return tuple(ValidationError(**item) for item in data)


class ImportBatchModel(TrackedBase):
    """Staging batch for one import file (IM-3, IM-11)."""

    __tablename__ = "import_batches"

    mapping_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mapping_version: Mapped[int] = mapped_column(nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    total_records: Mapped[int] = mapped_column(default=0, nullable=False)
    valid_records: Mapped[int] = mapped_column(default=0, nullable=False)
    invalid_records: Mapped[int] = mapped_column(default=0, nullable=False)
    promoted_records: Mapped[int] = mapped_column(default=0, nullable=False)
    skipped_records: Mapped[int] = mapped_column(default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    records: Mapped[list["ImportRecordModel"]] = relationship(
        "ImportRecordModel",
        back_populates="batch",
        foreign_keys="ImportRecordModel.batch_id",
    )

    def to_dto(self) -> ImportBatch:
        from finance_ingestion.domain.types import ImportBatch, ImportBatchStatus

        return ImportBatch(
            batch_id=self.id,
            mapping_name=self.mapping_name,
            entity_type=self.entity_type,
            source_filename=self.source_filename,
            status=ImportBatchStatus(self.status),
            total_records=self.total_records,
            valid_records=self.valid_records,
            invalid_records=self.invalid_records,
            promoted_records=self.promoted_records,
            skipped_records=self.skipped_records,
            created_at=self.created_at,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_dto(cls, dto: ImportBatch, mapping_version: int, mapping_hash: str, created_by_id: UUID) -> ImportBatchModel:
        return cls(
            id=dto.batch_id,
            mapping_name=dto.mapping_name,
            mapping_version=mapping_version,
            mapping_hash=mapping_hash,
            entity_type=dto.entity_type,
            source_filename=dto.source_filename,
            status=dto.status.value,
            total_records=dto.total_records,
            valid_records=dto.valid_records,
            invalid_records=dto.invalid_records,
            promoted_records=dto.promoted_records,
            skipped_records=dto.skipped_records,
            completed_at=dto.completed_at,
            created_by_id=created_by_id,
            updated_by_id=None,
        )


class ImportRecordModel(TrackedBase):
    """Single staged record within a batch (IM-6, IM-9)."""

    __tablename__ = "import_records"

    __table_args__ = (
        Index("ix_import_records_batch_status", "batch_id", "status"),
        Index("ix_import_records_entity_type", "entity_type"),
    )

    batch_id: Mapped[UUID] = mapped_column(
        UUIDString(),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_row: Mapped[int] = mapped_column(nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    mapped_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    promoted_entity_id: Mapped[UUID | None] = mapped_column(UUIDString(), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped["ImportBatchModel"] = relationship(
        "ImportBatchModel",
        back_populates="records",
        foreign_keys=[batch_id],
    )

    def to_dto(self) -> ImportRecord:
        from finance_ingestion.domain.types import ImportRecord, ImportRecordStatus

        return ImportRecord(
            record_id=self.id,
            batch_id=self.batch_id,
            source_row=self.source_row,
            entity_type=self.entity_type,
            status=ImportRecordStatus(self.status),
            raw_data=self.raw_data,
            mapped_data=self.mapped_data,
            validation_errors=_json_to_validation_errors(self.validation_errors),
            promoted_entity_id=self.promoted_entity_id,
            promoted_at=self.promoted_at,
        )

    @classmethod
    def from_dto(cls, dto: ImportRecord, created_by_id: UUID) -> ImportRecordModel:
        return cls(
            id=dto.record_id,
            batch_id=dto.batch_id,
            source_row=dto.source_row,
            entity_type=dto.entity_type,
            status=dto.status.value,
            raw_data=_to_json_safe(dto.raw_data) if dto.raw_data else dto.raw_data,
            mapped_data=_to_json_safe(dto.mapped_data) if dto.mapped_data else dto.mapped_data,
            validation_errors=_validation_errors_to_json(dto.validation_errors),
            promoted_entity_id=dto.promoted_entity_id,
            promoted_at=dto.promoted_at,
            created_by_id=created_by_id,
            updated_by_id=None,
        )
