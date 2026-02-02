"""
Tests for LifecycleReconciliationChecker -- GAP-REC Phases 1-3.

Covers all 7 check categories (RC-1 through RC-7) plus run_all_checks.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from finance_kernel.domain.economic_link import ArtifactRef, ArtifactType, LinkType
from finance_kernel.domain.values import Money

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    LifecycleChain,
    LifecycleEdge,
    LifecycleNode,
)
from finance_engines.reconciliation.checker import LifecycleReconciliationChecker


# =============================================================================
# Helpers
# =============================================================================


def _ref(t: ArtifactType) -> ArtifactRef:
    return ArtifactRef(t, uuid4())


def _po() -> ArtifactRef:
    return _ref(ArtifactType.PURCHASE_ORDER)


def _receipt() -> ArtifactRef:
    return _ref(ArtifactType.RECEIPT)


def _invoice() -> ArtifactRef:
    return _ref(ArtifactType.INVOICE)


def _payment() -> ArtifactRef:
    return _ref(ArtifactType.PAYMENT)


def _node(
    ref: ArtifactRef,
    entry_id: bool = True,
    effective_date: date | None = None,
    amount: Money | None = None,
    coa_version: int | None = None,
    posting_rule_version: int | None = None,
    role_bindings: dict[str, str] | None = None,
    event_type: str | None = None,
    **kwargs,
) -> LifecycleNode:
    return LifecycleNode(
        artifact_ref=ref,
        journal_entry_id=uuid4() if entry_id else None,
        effective_date=effective_date,
        amount=amount,
        coa_version=coa_version,
        posting_rule_version=posting_rule_version,
        role_bindings=role_bindings,
        event_type=event_type,
        **kwargs,
    )


# =============================================================================
# Fixture
# =============================================================================


@pytest.fixture
def checker():
    return LifecycleReconciliationChecker()


# =============================================================================
# RC-1: Policy regime consistency
# =============================================================================


class TestPolicyRegime:
    def test_same_regime_no_findings(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, posting_rule_version=1),
                _node(rcpt, coa_version=3, posting_rule_version=1),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_policy_regime(chain=chain)
        assert len(findings) == 0

    def test_different_coa_version_flags_drift(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, posting_rule_version=1),
                _node(rcpt, coa_version=4, posting_rule_version=1),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_policy_regime(chain=chain)
        assert len(findings) == 1
        assert findings[0].code == "POLICY_REGIME_DRIFT"
        assert findings[0].severity == CheckSeverity.WARNING

    def test_different_posting_rule_flags_drift(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, posting_rule_version=1),
                _node(rcpt, coa_version=3, posting_rule_version=2),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_policy_regime(chain=chain)
        assert len(findings) == 1

    def test_no_regime_data_skipped(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po),  # No regime data
                _node(rcpt, coa_version=3),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_policy_regime(chain=chain)
        assert len(findings) == 0

    def test_single_node_trivially_passes(self, checker):
        po = _po()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(_node(po, coa_version=3),),
        )
        findings = checker.check_policy_regime(chain=chain)
        assert len(findings) == 0

    def test_multi_edge_chain_checks_each(self, checker):
        po, rcpt, inv = _po(), _receipt(), _invoice()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, posting_rule_version=1),
                _node(rcpt, coa_version=4, posting_rule_version=1),
                _node(inv, coa_version=4, posting_rule_version=2),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),
                LifecycleEdge(LinkType.FULFILLED_BY, rcpt, inv),
            ),
        )
        findings = checker.check_policy_regime(chain=chain)
        # po->rcpt: coa 3->4, rcpt->inv: posting_rule 1->2
        assert len(findings) == 2


# =============================================================================
# RC-2: Account role stability
# =============================================================================


class TestAccountRoleStability:
    def test_same_roles_no_findings(self, checker):
        po, rcpt = _po(), _receipt()
        bindings = {"INVENTORY_RECEIVED": "1250", "AP_CONTROL": "2100"}
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, role_bindings=bindings),
                _node(rcpt, role_bindings=bindings),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_account_role_stability(chain=chain)
        assert len(findings) == 0

    def test_remapped_role_flags_error(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, role_bindings={"INVENTORY_RECEIVED": "1250"}),
                _node(rcpt, role_bindings={"INVENTORY_RECEIVED": "1260"}),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_account_role_stability(chain=chain)
        assert len(findings) == 1
        assert findings[0].code == "ACCOUNT_ROLE_REMAPPED"
        assert findings[0].severity == CheckSeverity.ERROR
        assert findings[0].details["role"] == "INVENTORY_RECEIVED"

    def test_no_bindings_skipped(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, role_bindings=None),
                _node(rcpt, role_bindings={"INVENTORY_RECEIVED": "1250"}),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_account_role_stability(chain=chain)
        assert len(findings) == 0

    def test_non_overlapping_roles_ignored(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, role_bindings={"ENCUMBRANCE": "3000"}),
                _node(rcpt, role_bindings={"INVENTORY_RECEIVED": "1250"}),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_account_role_stability(chain=chain)
        assert len(findings) == 0

    def test_multiple_remapped_roles(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, role_bindings={"INV": "1250", "AP": "2100"}),
                _node(rcpt, role_bindings={"INV": "1260", "AP": "2200"}),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_account_role_stability(chain=chain)
        assert len(findings) == 2


# =============================================================================
# RC-3: Amount flow conservation
# =============================================================================


class TestAmountFlow:
    def test_exact_match_no_findings(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, amount=Money.of("10000.00", "USD")),
                _node(rcpt, amount=Money.of("10000.00", "USD")),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("10000.00", "USD"),
            ),),
        )
        findings = checker.check_amount_flow(chain=chain)
        assert len(findings) == 0

    def test_partial_fulfillment_ok(self, checker):
        po, r1, r2 = _po(), _receipt(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, amount=Money.of("10000.00", "USD")),
                _node(r1, amount=Money.of("6000.00", "USD")),
                _node(r2, amount=Money.of("4000.00", "USD")),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po, r1,
                              link_amount=Money.of("6000.00", "USD")),
                LifecycleEdge(LinkType.FULFILLED_BY, po, r2,
                              link_amount=Money.of("4000.00", "USD")),
            ),
        )
        findings = checker.check_amount_flow(chain=chain)
        assert len(findings) == 0

    def test_over_fulfillment_flags_error(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, amount=Money.of("10000.00", "USD")),
                _node(rcpt, amount=Money.of("12000.00", "USD")),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("12000.00", "USD"),
            ),),
        )
        findings = checker.check_amount_flow(chain=chain)
        assert len(findings) >= 1
        codes = {f.code for f in findings}
        assert "AMOUNT_FLOW_VIOLATION" in codes

    def test_sum_exceeds_parent(self, checker):
        po, r1, r2 = _po(), _receipt(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, amount=Money.of("10000.00", "USD")),
                _node(r1),
                _node(r2),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po, r1,
                              link_amount=Money.of("7000.00", "USD")),
                LifecycleEdge(LinkType.FULFILLED_BY, po, r2,
                              link_amount=Money.of("5000.00", "USD")),
            ),
        )
        findings = checker.check_amount_flow(chain=chain)
        assert any(f.code == "AMOUNT_FLOW_VIOLATION" for f in findings)

    def test_no_amounts_skipped(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po),  # No amount
                _node(rcpt),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_amount_flow(chain=chain)
        assert len(findings) == 0

    def test_within_tolerance(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, amount=Money.of("10000.00", "USD")),
                _node(rcpt),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("10000.005", "USD"),
            ),),
        )
        findings = checker.check_amount_flow(chain=chain)
        assert len(findings) == 0  # Within 0.01 tolerance


# =============================================================================
# RC-4: Temporal ordering
# =============================================================================


class TestTemporalOrdering:
    def test_correct_order_no_findings(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 15)),
                _node(rcpt, effective_date=date(2026, 2, 10)),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_temporal_ordering(chain=chain)
        assert len(findings) == 0

    def test_same_day_ok(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 15)),
                _node(rcpt, effective_date=date(2026, 1, 15)),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_temporal_ordering(chain=chain)
        assert len(findings) == 0

    def test_reversed_order_flags_warning(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 3, 1)),
                _node(rcpt, effective_date=date(2026, 1, 15)),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_temporal_ordering(chain=chain)
        assert len(findings) == 1
        assert findings[0].code == "TEMPORAL_ORDER_VIOLATION"
        assert findings[0].severity == CheckSeverity.WARNING

    def test_missing_dates_skipped(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 15)),
                _node(rcpt),  # No date
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_temporal_ordering(chain=chain)
        assert len(findings) == 0

    def test_non_fulfillment_link_skipped(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 3, 1)),
                _node(rcpt, effective_date=date(2026, 1, 15)),
            ),
            edges=(LifecycleEdge(LinkType.MATCHED_WITH, po, rcpt),),
        )
        findings = checker.check_temporal_ordering(chain=chain)
        assert len(findings) == 0  # MATCHED_WITH is not directional

    def test_paid_by_also_checked(self, checker):
        inv, pmt = _invoice(), _payment()
        chain = LifecycleChain(
            root_ref=inv,
            nodes=(
                _node(inv, effective_date=date(2026, 3, 1)),
                _node(pmt, effective_date=date(2026, 1, 15)),
            ),
            edges=(LifecycleEdge(LinkType.PAID_BY, inv, pmt),),
        )
        findings = checker.check_temporal_ordering(chain=chain)
        assert len(findings) == 1


# =============================================================================
# RC-5: Chain completeness
# =============================================================================


class TestChainCompleteness:
    def test_complete_chain_no_findings(self, checker):
        po, rcpt, inv, pmt = _po(), _receipt(), _invoice(), _payment()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2026, 1, 1)),
                _node(rcpt, effective_date=date(2026, 1, 10)),
                _node(inv, effective_date=date(2026, 1, 15)),
                _node(pmt, effective_date=date(2026, 1, 20),
                      event_type="ap.payment"),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),
                LifecycleEdge(LinkType.FULFILLED_BY, rcpt, inv),
                LifecycleEdge(LinkType.PAID_BY, inv, pmt),
            ),
        )
        findings = checker.check_chain_completeness(
            chain=chain, as_of_date=date(2026, 6, 1),
        )
        assert len(findings) == 0

    def test_stale_root_no_children(self, checker):
        po = _po()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(_node(po, effective_date=date(2025, 1, 1)),),
        )
        findings = checker.check_chain_completeness(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert len(findings) == 1
        assert findings[0].code == "CHAIN_INCOMPLETE"

    def test_fresh_root_no_children_ok(self, checker):
        po = _po()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(_node(po, effective_date=date(2026, 1, 15)),),
        )
        findings = checker.check_chain_completeness(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert len(findings) == 0  # Only 17 days old, under 90

    def test_stale_intermediate_no_children(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, effective_date=date(2025, 1, 1)),
                _node(rcpt, effective_date=date(2025, 1, 10),
                      event_type="procurement.receipt"),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),
            ),
        )
        findings = checker.check_chain_completeness(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        # Receipt has incoming but no outgoing and is >90 days old
        assert any(f.code == "CHAIN_INCOMPLETE" for f in findings)

    def test_payment_terminal_not_flagged(self, checker):
        inv, pmt = _invoice(), _payment()
        chain = LifecycleChain(
            root_ref=inv,
            nodes=(
                _node(inv, effective_date=date(2025, 1, 1)),
                _node(pmt, effective_date=date(2025, 1, 15),
                      event_type="ap.payment"),
            ),
            edges=(
                LifecycleEdge(LinkType.PAID_BY, inv, pmt),
            ),
        )
        findings = checker.check_chain_completeness(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        # Payment is terminal -- should not be flagged
        payment_findings = [
            f for f in findings
            if f.parent_ref == pmt
        ]
        assert len(payment_findings) == 0

    def test_custom_threshold(self, checker):
        po = _po()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(_node(po, effective_date=date(2026, 1, 1)),),
        )
        # 31 days old, threshold 30
        findings = checker.check_chain_completeness(
            chain=chain, as_of_date=date(2026, 2, 1),
            aging_threshold_days=30,
        )
        assert len(findings) == 1


# =============================================================================
# RC-6: Link-entry correspondence
# =============================================================================


class TestLinkEntryCorrespondence:
    def test_all_entries_present_no_findings(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, entry_id=True),
                _node(rcpt, entry_id=True),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_link_entry_correspondence(chain=chain)
        assert len(findings) == 0

    def test_missing_entry_flags_orphan(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, entry_id=True),
                _node(rcpt, entry_id=False),  # No journal entry
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        findings = checker.check_link_entry_correspondence(chain=chain)
        assert len(findings) == 1
        assert findings[0].code == "ORPHANED_LINK"
        assert findings[0].severity == CheckSeverity.ERROR

    def test_missing_node_flags_orphan(self, checker):
        po = _po()
        ghost = _receipt()  # Not in nodes
        chain = LifecycleChain(
            root_ref=po,
            nodes=(_node(po),),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, ghost),),
        )
        findings = checker.check_link_entry_correspondence(chain=chain)
        assert any(f.code == "ORPHANED_LINK" for f in findings)

    def test_both_endpoints_missing(self, checker):
        ghost_parent = _po()
        ghost_child = _receipt()
        chain = LifecycleChain(
            root_ref=ghost_parent,
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, ghost_parent, ghost_child),),
        )
        findings = checker.check_link_entry_correspondence(chain=chain)
        assert len(findings) == 2


# =============================================================================
# RC-7: Allocation uniqueness
# =============================================================================


class TestAllocationUniqueness:
    def test_single_parent_no_findings(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po),
                _node(rcpt, amount=Money.of("5000.00", "USD")),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("5000.00", "USD"),
            ),),
        )
        findings = checker.check_allocation_uniqueness(chain=chain)
        assert len(findings) == 0

    def test_double_allocation_flags_error(self, checker):
        po1, po2 = _po(), _po()
        rcpt = _receipt()
        chain = LifecycleChain(
            root_ref=po1,
            nodes=(
                _node(po1),
                _node(po2),
                _node(rcpt, amount=Money.of("5000.00", "USD")),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po1, rcpt,
                              link_amount=Money.of("5000.00", "USD")),
                LifecycleEdge(LinkType.FULFILLED_BY, po2, rcpt,
                              link_amount=Money.of("3000.00", "USD")),
            ),
        )
        findings = checker.check_allocation_uniqueness(chain=chain)
        assert len(findings) == 1
        assert findings[0].code == "DOUBLE_COUNT_RISK"
        assert findings[0].severity == CheckSeverity.ERROR

    def test_within_child_amount_ok(self, checker):
        po1, po2 = _po(), _po()
        rcpt = _receipt()
        chain = LifecycleChain(
            root_ref=po1,
            nodes=(
                _node(po1),
                _node(po2),
                _node(rcpt, amount=Money.of("10000.00", "USD")),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, po1, rcpt,
                              link_amount=Money.of("5000.00", "USD")),
                LifecycleEdge(LinkType.FULFILLED_BY, po2, rcpt,
                              link_amount=Money.of("5000.00", "USD")),
            ),
        )
        findings = checker.check_allocation_uniqueness(chain=chain)
        assert len(findings) == 0

    def test_different_link_types_separate(self, checker):
        parent1, parent2 = _po(), _invoice()
        child = _payment()
        chain = LifecycleChain(
            root_ref=parent1,
            nodes=(
                _node(parent1),
                _node(parent2),
                _node(child, amount=Money.of("5000.00", "USD")),
            ),
            edges=(
                LifecycleEdge(LinkType.FULFILLED_BY, parent1, child,
                              link_amount=Money.of("5000.00", "USD")),
                LifecycleEdge(LinkType.PAID_BY, parent2, child,
                              link_amount=Money.of("5000.00", "USD")),
            ),
        )
        findings = checker.check_allocation_uniqueness(chain=chain)
        # Different link types -- separate allocations, no double count
        assert len(findings) == 0


# =============================================================================
# run_all_checks
# =============================================================================


class TestRunAllChecks:
    def test_clean_chain_passes(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, effective_date=date(2026, 1, 1),
                      amount=Money.of("10000.00", "USD")),
                _node(rcpt, coa_version=3, effective_date=date(2026, 1, 10),
                      amount=Money.of("10000.00", "USD")),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("10000.00", "USD"),
            ),),
        )
        result = checker.run_all_checks(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.PASSED
        assert result.is_clean
        assert result.nodes_checked == 2
        assert result.edges_checked == 1
        assert len(result.checks_performed) == 7

    def test_drift_produces_warning(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, effective_date=date(2026, 1, 1),
                      amount=Money.of("10000.00", "USD")),
                _node(rcpt, coa_version=4, effective_date=date(2026, 1, 10),
                      amount=Money.of("10000.00", "USD")),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("10000.00", "USD"),
            ),),
        )
        result = checker.run_all_checks(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.WARNING
        assert result.warning_count >= 1

    def test_orphan_produces_failed(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, effective_date=date(2026, 1, 1)),
                _node(rcpt, entry_id=False, effective_date=date(2026, 1, 10)),
            ),
            edges=(LifecycleEdge(LinkType.FULFILLED_BY, po, rcpt),),
        )
        result = checker.run_all_checks(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.FAILED
        assert result.error_count >= 1

    def test_empty_chain_passes(self, checker):
        po = _po()
        chain = LifecycleChain(root_ref=po)
        result = checker.run_all_checks(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.PASSED

    def test_multiple_issues_aggregated(self, checker):
        po, rcpt = _po(), _receipt()
        chain = LifecycleChain(
            root_ref=po,
            nodes=(
                _node(po, coa_version=3, effective_date=date(2026, 3, 1),
                      amount=Money.of("10000.00", "USD"),
                      role_bindings={"INV": "1250"}),
                _node(rcpt, coa_version=4, effective_date=date(2026, 1, 1),
                      entry_id=False,
                      role_bindings={"INV": "1260"}),
            ),
            edges=(LifecycleEdge(
                LinkType.FULFILLED_BY, po, rcpt,
                link_amount=Money.of("12000.00", "USD"),
            ),),
        )
        result = checker.run_all_checks(
            chain=chain, as_of_date=date(2026, 2, 1),
        )
        assert result.status == CheckStatus.FAILED
        # Should have: drift (W), role remap (E), amount (E),
        # temporal (W), orphan (E)
        assert result.error_count >= 2
        assert result.warning_count >= 1
