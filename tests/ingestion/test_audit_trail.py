"""Audit event creation for import lifecycle: IMPORT_RECORD_PROMOTED, IMPORT_BATCH_COMPLETED (Phase 9)."""

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.services.auditor_service import AuditorService

from finance_ingestion.domain.types import FieldMapping, ImportMapping
from finance_ingestion.promoters.party import PartyPromoter
from finance_ingestion.services.import_service import ImportService
from finance_ingestion.services.promotion_service import PromotionService

from finance_kernel.domain.schemas.base import EventFieldType


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


@pytest.fixture
def promotion_service_with_auditor(session, deterministic_clock, auditor_service):
    promoters = {"party": PartyPromoter()}
    return PromotionService(
        session,
        promoters,
        clock=deterministic_clock,
        auditor_service=auditor_service,
    )


@pytest.fixture
def import_service(session, deterministic_clock):
    mapping = _party_mapping()
    return ImportService(
        session,
        clock=deterministic_clock,
        mapping_registry={mapping.name: mapping},
    )


class TestImportRecordPromotedAuditEvent:
    """Each promotion emits IMPORT_RECORD_PROMOTED audit event with entity link."""

    def test_record_promoted_creates_audit_event(
        self,
        session,
        promotion_service_with_auditor,
        import_service,
        test_actor_id,
    ):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\n")
            path = Path(f.name)
        try:
            mapping = _party_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            before_count = session.scalars(
                select(AuditEvent).where(AuditEvent.action == "import_record_promoted")
            ).all()
            n_before = len(before_count) if before_count else 0

            result = promotion_service_with_auditor.promote_batch(batch.batch_id, test_actor_id)
            session.flush()

            assert result.promoted >= 1
            after = session.scalars(
                select(AuditEvent).where(AuditEvent.action == "import_record_promoted")
            ).all()
            assert len(after) >= n_before + 1
            ev = after[-1]
            assert ev.entity_type == "ImportRecord"
            assert "batch_id" in (ev.payload or {})
            assert "source_row" in (ev.payload or {})
            assert "entity_type" in (ev.payload or {})
            assert "promoted_entity_id" in (ev.payload or {})
        finally:
            path.unlink(missing_ok=True)

    def test_audit_payload_contains_promoted_entity_id(
        self,
        session,
        promotion_service_with_auditor,
        import_service,
        test_actor_id,
    ):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nPAUDIT,Audit Test\n")
            path = Path(f.name)
        try:
            mapping = _party_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            promotion_service_with_auditor.promote_batch(batch.batch_id, test_actor_id)
            session.flush()

            # Find the most recent IMPORT_RECORD_PROMOTED audit event; payload must contain promoted_entity_id
            ev = session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "import_record_promoted")
                .order_by(AuditEvent.seq.desc())
                .limit(1)
            ).first()
            assert ev is not None
            payload = ev.payload or {}
            assert "promoted_entity_id" in payload
            assert "source_row" in payload
            assert "entity_type" in payload
            assert payload["entity_type"] == "party"
        finally:
            path.unlink(missing_ok=True)


class TestImportBatchCompletedAuditEvent:
    """Batch completion emits IMPORT_BATCH_COMPLETED audit event with summary."""

    def test_batch_completed_creates_audit_event(
        self,
        session,
        promotion_service_with_auditor,
        import_service,
        test_actor_id,
    ):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP1,One\nP2,Two\n")
            path = Path(f.name)
        try:
            mapping = _party_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)

            before = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == batch.batch_id,
                    AuditEvent.action == "import_batch_completed",
                )
            ).first()

            promotion_service_with_auditor.promote_batch(batch.batch_id, test_actor_id)
            session.flush()

            after = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id == batch.batch_id,
                    AuditEvent.action == "import_batch_completed",
                )
            ).first()
            assert after is not None
            assert after.entity_type == "ImportBatch"
            payload = after.payload or {}
            assert "promoted" in payload
            assert "failed" in payload
            assert "skipped" in payload
        finally:
            path.unlink(missing_ok=True)


class TestAuditEventHashChain:
    """Audit events are hash-chained (prev_hash links to prior audit event)."""

    def test_import_audit_events_are_chained(
        self,
        session,
        promotion_service_with_auditor,
        import_service,
        test_actor_id,
    ):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nPCHAIN,Chain Test\n")
            path = Path(f.name)
        try:
            mapping = _party_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            promotion_service_with_auditor.promote_batch(batch.batch_id, test_actor_id)
            session.flush()

            events = list(
                session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action.in_([AuditAction.IMPORT_RECORD_PROMOTED, AuditAction.IMPORT_BATCH_COMPLETED])
                    ).order_by(AuditEvent.seq)
                ).all()
            )
            events = [e for e in events if e.seq is not None][-2:]
            if len(events) >= 2:
                assert events[1].prev_hash == events[0].hash
        finally:
            path.unlink(missing_ok=True)
