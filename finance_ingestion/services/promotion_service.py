"""
Promotion service: stage -> live ORM (ERP_INGESTION_PLAN Phase 7).

Promotes valid staged records via EntityPromoter. SAVEPOINT per record (IM-15).
Preflight graph for ready vs blocked; optional skip_blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.logging_config import LogContext, get_logger

from finance_ingestion.domain.types import ImportRecord, ImportRecordStatus
from finance_ingestion.models.staging import ImportBatchModel, ImportRecordModel
from finance_ingestion.promoters.base import EntityPromoter, PromoteResult

logger = get_logger("ingestion.promotion_service")


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionError:
    """Single promotion failure."""

    record_id: UUID
    source_row: int
    error_code: str
    message: str


@dataclass(frozen=True)
class PromotionResult:
    """Result of promote_batch."""

    batch_id: UUID
    total_attempted: int
    promoted: int
    failed: int
    skipped: int
    errors: tuple[PromotionError, ...] = ()


@dataclass(frozen=True)
class PreflightBlocker:
    """One unresolved dependency blocking records."""

    missing_entity_type: str
    missing_key: str
    blocked_records: tuple[UUID, ...]
    blocked_count: int


@dataclass(frozen=True)
class PreflightGraph:
    """Dependency graph: ready vs blocked (v1: no referential resolution)."""

    batch_id: UUID
    ready_count: int
    blocked_count: int
    blockers: tuple[PreflightBlocker, ...] = ()


def _validation_errors_json_for_promotion_failure(message: str) -> list[dict[str, Any]]:
    """Store promotion failure as a single validation-style error in JSON."""
    return [{"code": "PROMOTION_FAILED", "message": message, "field": None, "details": None}]


class PromotionService:
    """Promotes valid staged records to live tables via SAVEPOINT per record."""

    def __init__(
        self,
        session: Session,
        promoters: dict[str, EntityPromoter],
        clock: Clock | None = None,
        auditor_service: Any | None = None,
    ):
        self._session = session
        self._promoters = promoters
        self._clock = clock or SystemClock()
        self._auditor = auditor_service

    def compute_preflight_graph(self, batch_id: UUID) -> PreflightGraph:
        """
        Compute dependency graph of unresolved references.
        v1: All valid records are ready; blocked=0 (no referential resolution yet).
        """
        batch = self._session.get(ImportBatchModel, batch_id)
        if not batch:
            raise ValueError(f"Batch not found: {batch_id}")
        stmt = select(ImportRecordModel).where(
            ImportRecordModel.batch_id == batch_id,
            ImportRecordModel.status == ImportRecordStatus.VALID.value,
        )
        valid_records = list(self._session.scalars(stmt))
        # v1: no "exists" resolution; all valid are ready
        return PreflightGraph(
            batch_id=batch_id,
            ready_count=len(valid_records),
            blocked_count=0,
            blockers=(),
        )

    def promote_batch(
        self,
        batch_id: UUID,
        actor_id: UUID,
        dry_run: bool = False,
        skip_blocked: bool = False,
    ) -> PromotionResult:
        """
        Promote all valid records. SAVEPOINT per record (IM-15).
        skip_blocked: mark blocked as SKIPPED and promote only ready (v1: no-op).
        """
        LogContext.bind(correlation_id=str(batch_id), producer="ingestion", actor_id=str(actor_id))
        batch = self._session.get(ImportBatchModel, batch_id)
        if not batch:
            raise ValueError(f"Batch not found: {batch_id}")

        stmt = (
            select(ImportRecordModel)
            .where(
                ImportRecordModel.batch_id == batch_id,
                ImportRecordModel.status == ImportRecordStatus.VALID.value,
            )
            .order_by(ImportRecordModel.source_row)
        )
        valid_records = list(self._session.scalars(stmt))
        graph = self.compute_preflight_graph(batch_id)
        ready = valid_records if graph.ready_count else []
        blocked_ids: set[UUID] = set()
        if skip_blocked and graph.blocked_count:
            for b in graph.blockers:
                blocked_ids.update(b.blocked_records)
            ready = [r for r in valid_records if r.id not in blocked_ids]
            for rec in valid_records:
                if rec.id in blocked_ids:
                    rec.status = ImportRecordStatus.SKIPPED.value
                    logger.info("record_skipped", extra={"record_id": str(rec.id), "source_row": rec.source_row, "reason": "blocked"})
            self._session.flush()

        logger.info("batch_promotion_started", extra={"valid_records": len(ready)})
        if dry_run:
            return PromotionResult(batch_id=batch_id, total_attempted=len(ready), promoted=0, failed=0, skipped=len(valid_records) - len(ready))

        promoted = 0
        failed = 0
        skipped = batch.skipped_records or 0
        errors: list[PromotionError] = []
        now = self._clock.now()

        for rec in ready:
            savepoint = self._session.begin_nested()
            try:
                promoter = self._promoters.get(rec.entity_type)
                if not promoter:
                    raise ValueError(f"No promoter for entity_type {rec.entity_type!r}")
                mapped = rec.mapped_data or {}
                if promoter.check_duplicate(mapped, self._session):
                    savepoint.rollback()
                    rec.status = ImportRecordStatus.SKIPPED.value
                    skipped += 1
                    logger.info("record_skipped", extra={"record_id": str(rec.id), "source_row": rec.source_row, "reason": "duplicate"})
                    continue
                result = promoter.promote(mapped, self._session, actor_id, self._clock)
                if result.success and result.entity_id is not None:
                    savepoint.commit()
                    rec.status = ImportRecordStatus.PROMOTED.value
                    rec.promoted_entity_id = result.entity_id
                    rec.promoted_at = now
                    promoted += 1
                    if self._auditor:
                        self._auditor.record_import_record_promoted(
                            rec.id, rec.batch_id, rec.source_row, rec.entity_type, result.entity_id, actor_id
                        )
                    logger.info(
                        "record_promoted",
                        extra={"record_id": str(rec.id), "source_row": rec.source_row, "entity_type": rec.entity_type, "promoted_entity_id": str(result.entity_id)},
                    )
                else:
                    savepoint.rollback()
                    rec.status = ImportRecordStatus.PROMOTION_FAILED.value
                    rec.validation_errors = _validation_errors_json_for_promotion_failure(result.error or "Unknown error")
                    failed += 1
                    err = PromotionError(record_id=rec.id, source_row=rec.source_row, error_code="PROMOTION_FAILED", message=result.error or "")
                    errors.append(err)
                    logger.warning("record_promotion_failed", extra={"record_id": str(rec.id), "source_row": rec.source_row, "error_code": err.error_code, "error_msg": err.message})
            except Exception as exc:
                savepoint.rollback()
                rec.status = ImportRecordStatus.PROMOTION_FAILED.value
                rec.validation_errors = _validation_errors_json_for_promotion_failure(str(exc))
                failed += 1
                err = PromotionError(record_id=rec.id, source_row=rec.source_row, error_code="PROMOTION_FAILED", message=str(exc))
                errors.append(err)
                logger.warning("record_promotion_failed", extra={"record_id": str(rec.id), "source_row": rec.source_row, "error_code": err.error_code, "error_msg": err.message})

        batch.promoted_records = (batch.promoted_records or 0) + promoted
        batch.skipped_records = skipped
        batch.completed_at = now
        batch.status = "completed"
        self._session.flush()

        duration_ms = 0  # optional: (now - start).total_seconds() * 1000
        logger.info("batch_completed", extra={"promoted": promoted, "failed": failed, "skipped": skipped, "duration_ms": duration_ms})
        if self._auditor:
            self._auditor.record_import_batch_completed(batch.id, actor_id, promoted, failed, skipped, 0)

        return PromotionResult(
            batch_id=batch_id,
            total_attempted=len(ready),
            promoted=promoted,
            failed=failed,
            skipped=skipped,
            errors=tuple(errors),
        )

    def promote_record(
        self,
        record_id: UUID,
        actor_id: UUID,
        dry_run: bool = False,
    ) -> ImportRecord:
        """Promote a single record (within its own SAVEPOINT)."""
        rec = self._session.get(ImportRecordModel, record_id)
        if not rec:
            raise ValueError(f"Record not found: {record_id}")
        if rec.status != ImportRecordStatus.VALID.value:
            raise ValueError(f"Record {record_id} is not VALID (status={rec.status})")
        if dry_run:
            return rec.to_dto()
        LogContext.bind(correlation_id=str(rec.batch_id), producer="ingestion", actor_id=str(actor_id))
        savepoint = self._session.begin_nested()
        try:
            promoter = self._promoters.get(rec.entity_type)
            if not promoter:
                raise ValueError(f"No promoter for entity_type {rec.entity_type!r}")
            mapped = rec.mapped_data or {}
            result = promoter.promote(mapped, self._session, actor_id, self._clock)
            if result.success and result.entity_id is not None:
                savepoint.commit()
                rec.status = ImportRecordStatus.PROMOTED.value
                rec.promoted_entity_id = result.entity_id
                rec.promoted_at = self._clock.now()
                self._session.flush()
                if self._auditor:
                    self._auditor.record_import_record_promoted(
                        rec.id, rec.batch_id, rec.source_row, rec.entity_type, result.entity_id, actor_id
                    )
                return rec.to_dto()
            savepoint.rollback()
            rec.status = ImportRecordStatus.PROMOTION_FAILED.value
            rec.validation_errors = _validation_errors_json_for_promotion_failure(result.error or "Unknown error")
            self._session.flush()
            return rec.to_dto()
        except Exception as exc:
            savepoint.rollback()
            rec.status = ImportRecordStatus.PROMOTION_FAILED.value
            rec.validation_errors = _validation_errors_json_for_promotion_failure(str(exc))
            self._session.flush()
            return rec.to_dto()

