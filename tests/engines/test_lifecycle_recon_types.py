"""
Tests for lifecycle reconciliation domain types -- GAP-REC Phase 0.

Covers LifecycleNode, LifecycleEdge, LifecycleChain, ReconciliationFinding,
LifecycleCheckResult, enums, and factory methods.
"""

from datetime import date, datetime
from uuid import uuid4

import pytest

from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType, LinkType
from finance_kernel.domain.values import Money

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    LifecycleChain,
    LifecycleCheckResult,
    LifecycleEdge,
    LifecycleNode,
    ReconciliationFinding,
)


# =============================================================================
# Fixtures
# =============================================================================


def _po_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.PURCHASE_ORDER, uuid4())


def _receipt_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.RECEIPT, uuid4())


def _invoice_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.INVOICE, uuid4())


# =============================================================================
# Enums
# =============================================================================


class TestEnums:
    def test_check_severity_values(self):
        assert CheckSeverity.ERROR.value == "error"
        assert CheckSeverity.WARNING.value == "warning"
        assert CheckSeverity.INFO.value == "info"

    def test_check_status_values(self):
        assert CheckStatus.PASSED.value == "passed"
        assert CheckStatus.FAILED.value == "failed"
        assert CheckStatus.WARNING.value == "warning"


# =============================================================================
# LifecycleNode
# =============================================================================


class TestLifecycleNode:
    def test_minimal_construction(self):
        ref = _po_ref()
        node = LifecycleNode(artifact_ref=ref)
        assert node.artifact_ref == ref
        assert node.journal_entry_id is None
        assert not node.has_journal_entry

    def test_full_construction(self):
        ref = _po_ref()
        entry_id = uuid4()
        node = LifecycleNode(
            artifact_ref=ref,
            journal_entry_id=entry_id,
            event_type="procurement.po_committed",
            effective_date=date(2026, 1, 15),
            amount=Money.of("10000.00", "USD"),
            coa_version=3,
            posting_rule_version=1,
        )
        assert node.has_journal_entry
        assert node.effective_date == date(2026, 1, 15)

    def test_regime_tuple(self):
        node = LifecycleNode(
            artifact_ref=_po_ref(),
            coa_version=3,
            dimension_schema_version=1,
            rounding_policy_version=1,
            currency_registry_version=2,
            posting_rule_version=1,
        )
        assert node.regime_tuple == (3, 1, 1, 2, 1)

    def test_regime_tuple_partial(self):
        node = LifecycleNode(
            artifact_ref=_po_ref(),
            coa_version=3,
        )
        assert node.regime_tuple == (3, None, None, None, None)
        assert node.has_regime_data

    def test_regime_tuple_empty(self):
        node = LifecycleNode(artifact_ref=_po_ref())
        assert node.regime_tuple == (None, None, None, None, None)
        assert not node.has_regime_data

    def test_frozen(self):
        node = LifecycleNode(artifact_ref=_po_ref())
        with pytest.raises(AttributeError):
            node.coa_version = 5  # type: ignore[misc]


# =============================================================================
# LifecycleEdge
# =============================================================================


class TestLifecycleEdge:
    def test_construction(self):
        p = _po_ref()
        c = _receipt_ref()
        edge = LifecycleEdge(
            link_type=LinkType.FULFILLED_BY,
            parent_ref=p,
            child_ref=c,
            link_amount=Money.of("5000.00", "USD"),
        )
        assert edge.link_type == LinkType.FULFILLED_BY
        assert edge.parent_ref == p
        assert edge.child_ref == c

    def test_frozen(self):
        edge = LifecycleEdge(
            link_type=LinkType.PAID_BY,
            parent_ref=_invoice_ref(),
            child_ref=_po_ref(),
        )
        with pytest.raises(AttributeError):
            edge.link_type = LinkType.FULFILLED_BY  # type: ignore[misc]


# =============================================================================
# LifecycleChain
# =============================================================================


class TestLifecycleChain:
    def test_empty_chain(self):
        root = _po_ref()
        chain = LifecycleChain(root_ref=root)
        assert chain.node_count == 0
        assert chain.edge_count == 0

    def test_get_node(self):
        ref = _po_ref()
        node = LifecycleNode(artifact_ref=ref, coa_version=1)
        chain = LifecycleChain(root_ref=ref, nodes=(node,))
        assert chain.get_node(ref) is node

    def test_get_node_missing(self):
        chain = LifecycleChain(root_ref=_po_ref())
        assert chain.get_node(_receipt_ref()) is None

    def test_get_children_edges(self):
        po = _po_ref()
        r1 = _receipt_ref()
        r2 = _receipt_ref()
        e1 = LifecycleEdge(LinkType.FULFILLED_BY, po, r1)
        e2 = LifecycleEdge(LinkType.FULFILLED_BY, po, r2)
        chain = LifecycleChain(root_ref=po, edges=(e1, e2))
        children = chain.get_children_edges(po)
        assert len(children) == 2

    def test_get_parent_edges(self):
        po = _po_ref()
        r = _receipt_ref()
        e = LifecycleEdge(LinkType.FULFILLED_BY, po, r)
        chain = LifecycleChain(root_ref=po, edges=(e,))
        parents = chain.get_parent_edges(r)
        assert len(parents) == 1
        assert parents[0].parent_ref == po


# =============================================================================
# ReconciliationFinding
# =============================================================================


class TestReconciliationFinding:
    def test_construction(self):
        finding = ReconciliationFinding(
            code="POLICY_REGIME_DRIFT",
            severity=CheckSeverity.WARNING,
            message="Policy changed",
        )
        assert finding.code == "POLICY_REGIME_DRIFT"
        assert finding.severity == CheckSeverity.WARNING

    def test_with_refs(self):
        p = _po_ref()
        c = _receipt_ref()
        finding = ReconciliationFinding(
            code="AMOUNT_FLOW_VIOLATION",
            severity=CheckSeverity.ERROR,
            message="Excess",
            parent_ref=p,
            child_ref=c,
            details={"excess": "100.00"},
        )
        assert finding.parent_ref == p
        assert finding.details["excess"] == "100.00"


# =============================================================================
# LifecycleCheckResult
# =============================================================================


class TestLifecycleCheckResult:
    def test_clean_result(self):
        root = _po_ref()
        result = LifecycleCheckResult(
            root_ref=root,
            status=CheckStatus.PASSED,
            nodes_checked=3,
            edges_checked=2,
        )
        assert result.is_clean
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_from_findings_passed(self):
        root = _po_ref()
        result = LifecycleCheckResult.from_findings(
            root_ref=root,
            findings=(),
            nodes_checked=2,
            edges_checked=1,
            checks_performed=("RC-1",),
            as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.PASSED
        assert result.is_clean

    def test_from_findings_warning(self):
        root = _po_ref()
        findings = (
            ReconciliationFinding(
                code="POLICY_REGIME_DRIFT",
                severity=CheckSeverity.WARNING,
                message="drift",
            ),
        )
        result = LifecycleCheckResult.from_findings(
            root_ref=root,
            findings=findings,
            nodes_checked=2,
            edges_checked=1,
            checks_performed=("RC-1",),
            as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.WARNING
        assert result.warning_count == 1
        assert result.error_count == 0

    def test_from_findings_failed(self):
        root = _po_ref()
        findings = (
            ReconciliationFinding(
                code="ORPHANED_LINK",
                severity=CheckSeverity.ERROR,
                message="orphan",
            ),
            ReconciliationFinding(
                code="POLICY_REGIME_DRIFT",
                severity=CheckSeverity.WARNING,
                message="drift",
            ),
        )
        result = LifecycleCheckResult.from_findings(
            root_ref=root,
            findings=findings,
            nodes_checked=3,
            edges_checked=2,
            checks_performed=("RC-1", "RC-6"),
            as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.FAILED
        assert result.error_count == 1
        assert result.warning_count == 1
        assert not result.is_clean
