"""
Tests for lifecycle reconciliation audit integration -- GAP-REC Phase 6.

Covers audit event creation for lifecycle check results via AuditorService.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from finance_kernel.domain.clock import DeterministicClock
from finance_kernel.models.audit_event import AuditAction, AuditEvent
from finance_kernel.services.auditor_service import AuditorService


@pytest.fixture
def clock():
    return DeterministicClock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def auditor(session, clock):
    return AuditorService(session, clock)


class TestRecordLifecycleCheck:
    """Tests for AuditorService.record_lifecycle_check()."""

    def test_passed_result(self, auditor, test_actor_id):
        artifact_id = uuid4()
        event = auditor.record_lifecycle_check(
            root_artifact_id=artifact_id,
            root_artifact_type="PurchaseOrder",
            status="passed",
            nodes_checked=4,
            edges_checked=3,
            error_count=0,
            warning_count=0,
            checks_performed=("RC-1", "RC-2", "RC-3", "RC-4", "RC-5", "RC-6", "RC-7"),
            actor_id=test_actor_id,
        )

        assert event.action == AuditAction.LIFECYCLE_CHECK_PASSED
        assert event.entity_type == "PurchaseOrder"
        assert event.entity_id == artifact_id
        assert event.payload["status"] == "passed"
        assert event.payload["nodes_checked"] == 4
        assert event.payload["edges_checked"] == 3

    def test_failed_result(self, auditor, test_actor_id):
        artifact_id = uuid4()
        findings_summary = [
            {"code": "ORPHANED_LINK", "severity": "error", "message": "orphan"},
        ]
        event = auditor.record_lifecycle_check(
            root_artifact_id=artifact_id,
            root_artifact_type="PurchaseOrder",
            status="failed",
            nodes_checked=2,
            edges_checked=1,
            error_count=1,
            warning_count=0,
            checks_performed=("RC-6",),
            actor_id=test_actor_id,
            findings_summary=findings_summary,
        )

        assert event.action == AuditAction.LIFECYCLE_CHECK_FAILED
        assert event.payload["error_count"] == 1
        assert len(event.payload["findings_summary"]) == 1

    def test_warning_result(self, auditor, test_actor_id):
        artifact_id = uuid4()
        event = auditor.record_lifecycle_check(
            root_artifact_id=artifact_id,
            root_artifact_type="PurchaseOrder",
            status="warning",
            nodes_checked=3,
            edges_checked=2,
            error_count=0,
            warning_count=2,
            checks_performed=("RC-1", "RC-4"),
            actor_id=test_actor_id,
        )

        assert event.action == AuditAction.LIFECYCLE_CHECK_WARNING
        assert event.payload["warning_count"] == 2

    def test_audit_chain_integrity(self, auditor, test_actor_id):
        """Multiple lifecycle check events maintain hash chain (R11)."""
        for status in ("passed", "failed", "warning"):
            auditor.record_lifecycle_check(
                root_artifact_id=uuid4(),
                root_artifact_type="PurchaseOrder",
                status=status,
                nodes_checked=1,
                edges_checked=0,
                error_count=1 if status == "failed" else 0,
                warning_count=1 if status == "warning" else 0,
                checks_performed=("RC-1",),
                actor_id=test_actor_id,
            )

        assert auditor.validate_chain() is True

    def test_checks_performed_stored(self, auditor, test_actor_id):
        checks = ("RC-1", "RC-2", "RC-3")
        event = auditor.record_lifecycle_check(
            root_artifact_id=uuid4(),
            root_artifact_type="Invoice",
            status="passed",
            nodes_checked=2,
            edges_checked=1,
            error_count=0,
            warning_count=0,
            checks_performed=checks,
            actor_id=test_actor_id,
        )

        assert event.payload["checks_performed"] == ["RC-1", "RC-2", "RC-3"]

    def test_no_findings_summary_defaults_empty(self, auditor, test_actor_id):
        event = auditor.record_lifecycle_check(
            root_artifact_id=uuid4(),
            root_artifact_type="PurchaseOrder",
            status="passed",
            nodes_checked=1,
            edges_checked=0,
            error_count=0,
            warning_count=0,
            checks_performed=("RC-1",),
            actor_id=test_actor_id,
        )

        assert event.payload["findings_summary"] == []

    def test_unknown_status_defaults_to_failed(self, auditor, test_actor_id):
        """Unknown status string maps to LIFECYCLE_CHECK_FAILED as safety default."""
        event = auditor.record_lifecycle_check(
            root_artifact_id=uuid4(),
            root_artifact_type="PurchaseOrder",
            status="unknown_status",
            nodes_checked=1,
            edges_checked=0,
            error_count=0,
            warning_count=0,
            checks_performed=(),
            actor_id=test_actor_id,
        )

        assert event.action == AuditAction.LIFECYCLE_CHECK_FAILED

    def test_trace_retrievable(self, auditor, test_actor_id):
        """Audit events can be retrieved via get_trace."""
        artifact_id = uuid4()
        auditor.record_lifecycle_check(
            root_artifact_id=artifact_id,
            root_artifact_type="PurchaseOrder",
            status="passed",
            nodes_checked=3,
            edges_checked=2,
            error_count=0,
            warning_count=0,
            checks_performed=("RC-1",),
            actor_id=test_actor_id,
        )

        trace = auditor.get_trace("PurchaseOrder", artifact_id)
        assert not trace.is_empty
        assert trace.last_action == AuditAction.LIFECYCLE_CHECK_PASSED
