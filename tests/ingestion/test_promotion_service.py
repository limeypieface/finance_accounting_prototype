"""Tests for PromotionService (Phase 7)."""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from finance_ingestion.domain.types import FieldMapping, ImportMapping, ImportRecordStatus
from finance_ingestion.models.staging import ImportRecordModel
from finance_ingestion.promoters.party import PartyPromoter
from finance_ingestion.promoters.ap import VendorPromoter
from finance_ingestion.services.import_service import ImportService
from finance_ingestion.services.promotion_service import (
    PreflightGraph,
    PromotionResult,
    PromotionService,
)
from finance_kernel.domain.schemas.base import EventFieldType


def _make_mapping():
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
def promotion_service(session, deterministic_clock, auditor_service):
    promoters = {"party": PartyPromoter()}
    return PromotionService(session, promoters, clock=deterministic_clock, auditor_service=auditor_service)


@pytest.fixture
def import_service(session, deterministic_clock):
    mapping = _make_mapping()
    return ImportService(
        session,
        clock=deterministic_clock,
        mapping_registry={mapping.name: mapping},
    )


class TestComputePreflightGraph:
    def test_preflight_all_ready_when_valid_records(self, promotion_service, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\nP002,Beta\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            graph = promotion_service.compute_preflight_graph(batch.batch_id)
            assert isinstance(graph, PreflightGraph)
            assert graph.batch_id == batch.batch_id
            assert graph.ready_count == 2
            assert graph.blocked_count == 0
            assert graph.blockers == ()
        finally:
            path.unlink(missing_ok=True)


class TestPromoteBatch:
    def test_promote_batch_with_stub_promoter(self, promotion_service, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\nP002,Beta\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            result = promotion_service.promote_batch(batch.batch_id, test_actor_id)
            assert isinstance(result, PromotionResult)
            assert result.batch_id == batch.batch_id
            assert result.total_attempted == 2
            assert result.promoted == 2
            assert result.failed == 0
            assert result.skipped == 0
            assert result.errors == ()
        finally:
            path.unlink(missing_ok=True)

    def test_dry_run_does_not_promote(self, promotion_service, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            result = promotion_service.promote_batch(batch.batch_id, test_actor_id, dry_run=True)
            assert result.promoted == 0
            assert result.total_attempted == 1
            rec = import_service._session.scalars(
                select(ImportRecordModel).where(ImportRecordModel.batch_id == batch.batch_id)
            ).first()
            assert rec.status == "valid"  # unchanged
        finally:
            path.unlink(missing_ok=True)

    def test_no_promoter_fails_record(self, session, deterministic_clock, auditor_service, import_service, test_actor_id):
        # Empty promoters -> no promoter for "party"
        promotion_service = PromotionService(session, promoters={}, clock=deterministic_clock, auditor_service=auditor_service)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            result = promotion_service.promote_batch(batch.batch_id, test_actor_id)
            assert result.promoted == 0
            assert result.failed == 1
            assert len(result.errors) == 1
            assert "No promoter" in result.errors[0].message
        finally:
            path.unlink(missing_ok=True)


class TestPromoteRecord:
    def test_promote_record_single(self, promotion_service, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            rec = import_service._session.scalars(
                select(ImportRecordModel).where(ImportRecordModel.batch_id == batch.batch_id).limit(1)
            ).first()
            assert rec is not None
            updated = promotion_service.promote_record(rec.id, test_actor_id)
            assert updated.status == ImportRecordStatus.PROMOTED
            assert updated.promoted_entity_id is not None
        finally:
            path.unlink(missing_ok=True)


class TestPromoteBatchDuplicateSkips:
    """Promote duplicate party/vendor -> second SKIPPED (idempotent)."""

    def test_promote_duplicate_party_skipped(self, promotion_service, import_service, test_actor_id):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP001,Acme\nP001,Acme Again\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            import_service.validate_batch(batch.batch_id)
            result = promotion_service.promote_batch(batch.batch_id, test_actor_id)
            assert result.promoted >= 1
            assert result.skipped >= 1
            assert result.promoted + result.skipped == 2
        finally:
            path.unlink(missing_ok=True)


class TestPromoteBatchOneFailureOthersSucceed:
    """Promotion failure rolls back individual record, not entire batch (IM-8)."""

    def test_one_invalid_record_others_still_promoted(self, promotion_service, import_service, test_actor_id):
        """Batch with 3 rows: one missing code (invalid). Only valid records promoted; invalid stays invalid."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nP1,One\n,Missing\nP3,Three\n")
            path = Path(f.name)
        try:
            mapping = _make_mapping()
            batch = import_service.load_batch(path, mapping, test_actor_id)
            validated = import_service.validate_batch(batch.batch_id)
            assert validated.valid_records == 2 and validated.invalid_records == 1
            result = promotion_service.promote_batch(batch.batch_id, test_actor_id)
            assert result.total_attempted == 2
            assert result.promoted == 2
            assert result.failed == 0
            summary = import_service.get_batch_summary(batch.batch_id)
            assert summary.promoted_records == 2
            errors = import_service.get_batch_errors(batch.batch_id)
            assert len(errors) == 1
        finally:
            path.unlink(missing_ok=True)


class TestPromoteBatchVendorDuplicateSkips:
    """Promote vendor -> creates Party + VendorProfile; duplicate vendor -> SKIPPED."""

    @pytest.fixture
    def vendor_mapping(self):
        return ImportMapping(
            name="test_vendor",
            version=1,
            entity_type="vendor",
            source_format="csv",
            source_options={"has_header": True},
            field_mappings=(
                FieldMapping(source="code", target="code", field_type=EventFieldType.STRING, required=True),
                FieldMapping(source="name", target="name", field_type=EventFieldType.STRING, required=False),
            ),
            validations=(),
            dependency_tier=2,
        )

    @pytest.fixture
    def vendor_promotion_service(self, session, deterministic_clock, auditor_service):
        return PromotionService(
            session,
            promoters={"vendor": VendorPromoter()},
            clock=deterministic_clock,
            auditor_service=auditor_service,
        )

    @pytest.fixture
    def vendor_import_service(self, session, deterministic_clock, vendor_mapping):
        return ImportService(
            session,
            clock=deterministic_clock,
            mapping_registry={vendor_mapping.name: vendor_mapping},
        )

    def test_duplicate_vendor_second_skipped(
        self, vendor_promotion_service, vendor_import_service, vendor_mapping, test_actor_id
    ):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            f.write("code,name\nV001,Vendor One\nV001,Vendor One Again\n")
            path = Path(f.name)
        try:
            batch = vendor_import_service.load_batch(path, vendor_mapping, test_actor_id)
            vendor_import_service.validate_batch(batch.batch_id)
            result = vendor_promotion_service.promote_batch(batch.batch_id, test_actor_id)
            assert result.promoted == 1
            assert result.skipped == 1
            assert result.total_attempted == 2
        finally:
            path.unlink(missing_ok=True)
