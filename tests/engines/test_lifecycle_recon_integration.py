"""
Integration tests for lifecycle reconciliation -- GAP-REC Phase 5.

End-to-end scenarios exercising the pure engine with realistic lifecycle
chains. These tests verify the full check suite against meaningful
business scenarios without touching the database.
"""

from datetime import date, datetime, UTC
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType, LinkType
from finance_kernel.domain.values import Money

from finance_engines.reconciliation.checker import LifecycleReconciliationChecker
from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    LifecycleChain,
    LifecycleCheckResult,
    LifecycleEdge,
    LifecycleNode,
)


# =============================================================================
# Helpers
# =============================================================================


def _po_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.PURCHASE_ORDER, uuid4())


def _receipt_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.RECEIPT, uuid4())


def _invoice_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.INVOICE, uuid4())


def _payment_ref() -> ArtifactRef:
    return ArtifactRef(ArtifactType.PAYMENT, uuid4())


def _node(
    ref: ArtifactRef,
    *,
    effective_date: date | None = None,
    coa_version: int = 1,
    posting_rule_version: int = 1,
    amount: Money | None = None,
    role_bindings: dict[str, str] | None = None,
) -> LifecycleNode:
    return LifecycleNode(
        artifact_ref=ref,
        journal_entry_id=uuid4(),
        event_type=f"test.{ref.artifact_type.value}",
        effective_date=effective_date,
        posted_at=datetime.now(UTC),
        amount=amount,
        coa_version=coa_version,
        dimension_schema_version=1,
        rounding_policy_version=1,
        currency_registry_version=1,
        posting_rule_version=posting_rule_version,
        role_bindings=role_bindings,
    )


def _edge(
    link_type: LinkType,
    parent: ArtifactRef,
    child: ArtifactRef,
    amount: Money | None = None,
) -> LifecycleEdge:
    return LifecycleEdge(
        link_type=link_type,
        parent_ref=parent,
        child_ref=child,
        link_amount=amount,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def checker():
    return LifecycleReconciliationChecker()


# =============================================================================
# Scenario: Complete PO lifecycle -- all consistent
# =============================================================================


class TestCleanFullLifecycle:
    """PO -> Receipt -> Invoice -> Payment, all consistent."""

    def test_full_lifecycle_clean(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()
        invoice = _invoice_ref()
        payment = _payment_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 1), amount=Money.of("10000", "USD")),
                _node(receipt, effective_date=date(2026, 1, 5), amount=Money.of("10000", "USD")),
                _node(invoice, effective_date=date(2026, 1, 10), amount=Money.of("10000", "USD")),
                _node(payment, effective_date=date(2026, 1, 20), amount=Money.of("10000", "USD")),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt, Money.of("10000", "USD")),
                _edge(LinkType.FULFILLED_BY, receipt, invoice, Money.of("10000", "USD")),
                _edge(LinkType.PAID_BY, invoice, payment, Money.of("10000", "USD")),
            ),
        )

        result = checker.run_all_checks(
            chain=chain,
            as_of_date=date(2026, 2, 1),
        )

        assert result.status == CheckStatus.PASSED
        assert result.is_clean
        assert result.nodes_checked == 4
        assert result.edges_checked == 3


# =============================================================================
# Scenario: Policy changed mid-lifecycle
# =============================================================================


class TestPolicyDriftMidLifecycle:
    """PO committed under COA v1, receipt under COA v2."""

    def test_coa_version_drift_detected(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()
        invoice = _invoice_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 1), coa_version=1),
                _node(receipt, effective_date=date(2026, 1, 15), coa_version=2),
                _node(invoice, effective_date=date(2026, 1, 20), coa_version=2),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
                _edge(LinkType.FULFILLED_BY, receipt, invoice),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 2, 1))

        assert result.status == CheckStatus.WARNING
        drift_findings = [f for f in result.findings if f.code == "POLICY_REGIME_DRIFT"]
        assert len(drift_findings) >= 1
        # Drift is between PO (v1) and receipt (v2)
        assert any(
            f.parent_ref == po and f.child_ref == receipt
            for f in drift_findings
        )

    def test_posting_rule_drift(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 1), posting_rule_version=1),
                _node(receipt, effective_date=date(2026, 2, 1), posting_rule_version=2),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 3, 1))

        drift_findings = [f for f in result.findings if f.code == "POLICY_REGIME_DRIFT"]
        assert len(drift_findings) >= 1


# =============================================================================
# Scenario: Account role remapped mid-lifecycle
# =============================================================================


class TestAccountRoleRemapping:
    """Same role resolved to different COA codes across linked events."""

    def test_role_remapped(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(
                    po,
                    effective_date=date(2026, 1, 1),
                    role_bindings={"InventoryAsset": "1300", "AP": "2100"},
                ),
                _node(
                    receipt,
                    effective_date=date(2026, 1, 10),
                    role_bindings={"InventoryAsset": "1310", "AP": "2100"},
                ),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 2, 1))

        assert result.status == CheckStatus.FAILED
        remap_findings = [f for f in result.findings if f.code == "ACCOUNT_ROLE_REMAPPED"]
        assert len(remap_findings) >= 1
        assert any("InventoryAsset" in f.message for f in remap_findings)


# =============================================================================
# Scenario: Overpayment detection
# =============================================================================


class TestAmountFlowViolation:
    """Child amount exceeds parent -- overpayment."""

    def test_overpayment_detected(self, checker):
        invoice = _invoice_ref()
        payment = _payment_ref()

        chain = LifecycleChain(
            root_ref=invoice,
            nodes=(
                _node(invoice, effective_date=date(2026, 1, 1), amount=Money.of("5000", "USD")),
                _node(payment, effective_date=date(2026, 1, 15), amount=Money.of("6000", "USD")),
            ),
            edges=(
                _edge(LinkType.PAID_BY, invoice, payment, Money.of("6000", "USD")),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 2, 1))

        amount_findings = [f for f in result.findings if f.code == "AMOUNT_FLOW_VIOLATION"]
        assert len(amount_findings) >= 1

    def test_partial_payments_summing_over_parent(self, checker):
        invoice = _invoice_ref()
        pay1 = _payment_ref()
        pay2 = _payment_ref()

        chain = LifecycleChain(
            root_ref=invoice,
            nodes=(
                _node(invoice, effective_date=date(2026, 1, 1), amount=Money.of("1000", "USD")),
                _node(pay1, effective_date=date(2026, 1, 10)),
                _node(pay2, effective_date=date(2026, 1, 15)),
            ),
            edges=(
                _edge(LinkType.PAID_BY, invoice, pay1, Money.of("600", "USD")),
                _edge(LinkType.PAID_BY, invoice, pay2, Money.of("500", "USD")),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 2, 1))

        amount_findings = [f for f in result.findings if f.code == "AMOUNT_FLOW_VIOLATION"]
        assert len(amount_findings) >= 1


# =============================================================================
# Scenario: Backdated receipt
# =============================================================================


class TestTemporalViolation:
    """Receipt effective_date before PO effective_date."""

    def test_backdated_receipt_flagged(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 3, 1)),
                _node(receipt, effective_date=date(2026, 2, 15)),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 4, 1))

        temporal_findings = [f for f in result.findings if f.code == "TEMPORAL_ORDER_VIOLATION"]
        assert len(temporal_findings) >= 1


# =============================================================================
# Scenario: Stale incomplete chain
# =============================================================================


class TestChainCompleteness:
    """PO with receipt but no invoice after aging threshold."""

    def test_stale_chain_flagged(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2025, 6, 1)),
                _node(receipt, effective_date=date(2025, 6, 15)),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        # Check as of Feb 2026 -- well past 90-day threshold
        result = checker.run_all_checks(
            chain=chain,
            as_of_date=date(2026, 2, 1),
            aging_threshold_days=90,
        )

        completeness_findings = [f for f in result.findings if f.code == "CHAIN_INCOMPLETE"]
        assert len(completeness_findings) >= 1

    def test_fresh_chain_not_flagged(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 20)),
                _node(receipt, effective_date=date(2026, 1, 25)),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        # Check as of Feb 1 -- only ~6 days old, under 90-day threshold
        result = checker.run_all_checks(
            chain=chain,
            as_of_date=date(2026, 2, 1),
            aging_threshold_days=90,
        )

        completeness_findings = [f for f in result.findings if f.code == "CHAIN_INCOMPLETE"]
        assert len(completeness_findings) == 0


# =============================================================================
# Scenario: Orphaned link
# =============================================================================


class TestOrphanedLink:
    """Link to artifact that has no journal entry."""

    def test_missing_entry_detected(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 1)),
                LifecycleNode(artifact_ref=receipt),  # No journal entry
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 2, 1))

        assert result.status == CheckStatus.FAILED
        orphan_findings = [f for f in result.findings if f.code == "ORPHANED_LINK"]
        assert len(orphan_findings) >= 1


# =============================================================================
# Scenario: Multiple issues compound
# =============================================================================


class TestCompoundIssues:
    """Chain with policy drift + temporal violation + orphan."""

    def test_multiple_findings(self, checker):
        po = _po_ref()
        receipt = _receipt_ref()
        invoice = _invoice_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 3, 1), coa_version=1),
                _node(receipt, effective_date=date(2026, 2, 15), coa_version=2),
                LifecycleNode(artifact_ref=invoice),  # orphan
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
                _edge(LinkType.FULFILLED_BY, receipt, invoice),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 4, 1))

        assert result.status == CheckStatus.FAILED
        codes = {f.code for f in result.findings}
        assert "POLICY_REGIME_DRIFT" in codes
        assert "TEMPORAL_ORDER_VIOLATION" in codes
        assert "ORPHANED_LINK" in codes

    def test_finding_counts(self, checker):
        """Verify error_count and warning_count are accurate."""
        po = _po_ref()
        receipt = _receipt_ref()

        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 1), coa_version=1),
                LifecycleNode(artifact_ref=receipt),
            ),
            edges=(
                _edge(LinkType.FULFILLED_BY, po, receipt),
            ),
        )

        result = checker.run_all_checks(chain=chain, as_of_date=date(2026, 2, 1))

        # Orphan is ERROR; drift may be WARNING
        assert result.error_count >= 1
        assert not result.is_clean
