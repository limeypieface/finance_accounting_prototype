"""
LifecycleReconciliationChecker -- Pure engine for lifecycle chain analysis.

Detects policy drift, account mapping inconsistencies, amount flow violations,
temporal anomalies, chain completeness issues, orphaned links, and
over-allocation across business object lifecycles.

Architecture: finance_engines -- pure calculation, zero I/O, zero DB access.
All inputs are frozen dataclasses populated by the service layer.

Invariants enforced:
    RC-1  Policy regime consistency
    RC-2  Account role stability
    RC-3  Amount flow conservation
    RC-4  Temporal monotonicity
    RC-5  Chain completeness
    RC-6  Link-entry correspondence
    RC-7  Allocation uniqueness
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from finance_kernel.domain.economic_link import ArtifactRef, LinkType
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_engines.tracer import traced_engine

from finance_engines.reconciliation.lifecycle_types import (
    CheckSeverity,
    CheckStatus,
    LifecycleChain,
    LifecycleCheckResult,
    LifecycleEdge,
    LifecycleNode,
    ReconciliationFinding,
)

logger = get_logger("engines.reconciliation.checker")

# Link types that represent fulfillment/payment chains (directional, temporal)
_FULFILLMENT_LINK_TYPES = frozenset({
    LinkType.FULFILLED_BY,
    LinkType.PAID_BY,
})

# Default amount tolerance for flow checks
_DEFAULT_AMOUNT_TOLERANCE = Decimal("0.01")


class LifecycleReconciliationChecker:
    """Pure engine for lifecycle chain reconciliation.

    All methods receive a fully populated LifecycleChain and return
    tuples of ReconciliationFinding. No I/O, no database access.

    Usage:
        checker = LifecycleReconciliationChecker()
        result = checker.run_all_checks(chain, as_of_date=date.today())
    """

    # -----------------------------------------------------------------
    # RC-1: Policy regime consistency
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_policy_regime(
        self,
        chain: LifecycleChain,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-1: Compare R21 snapshot columns across linked nodes.

        Flags WARNING when linked nodes were posted under different
        policy regimes (coa_version, posting_rule_version, etc.).
        """
        findings: list[ReconciliationFinding] = []

        for edge in chain.edges:
            parent_node = chain.get_node(edge.parent_ref)
            child_node = chain.get_node(edge.child_ref)

            if parent_node is None or child_node is None:
                continue  # RC-6 handles orphans

            if not parent_node.has_regime_data or not child_node.has_regime_data:
                continue  # Can't compare if no data

            if parent_node.regime_tuple != child_node.regime_tuple:
                diffs = _describe_regime_diffs(parent_node, child_node)
                findings.append(ReconciliationFinding(
                    code="POLICY_REGIME_DRIFT",
                    severity=CheckSeverity.WARNING,
                    message=(
                        f"Policy regime changed between "
                        f"{edge.parent_ref} and {edge.child_ref}: {diffs}"
                    ),
                    parent_ref=edge.parent_ref,
                    child_ref=edge.child_ref,
                    details={
                        "parent_regime": dict(zip(
                            ("coa", "dim_schema", "rounding", "currency_reg", "posting_rule"),
                            parent_node.regime_tuple,
                        )),
                        "child_regime": dict(zip(
                            ("coa", "dim_schema", "rounding", "currency_reg", "posting_rule"),
                            child_node.regime_tuple,
                        )),
                    },
                ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # RC-2: Account role stability
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_account_role_stability(
        self,
        chain: LifecycleChain,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-2: Same semantic role should resolve to same COA code.

        Only runs when role_bindings are populated on nodes.
        """
        findings: list[ReconciliationFinding] = []

        for edge in chain.edges:
            parent_node = chain.get_node(edge.parent_ref)
            child_node = chain.get_node(edge.child_ref)

            if parent_node is None or child_node is None:
                continue

            if parent_node.role_bindings is None or child_node.role_bindings is None:
                continue

            # Find roles present in both
            common_roles = (
                set(parent_node.role_bindings.keys())
                & set(child_node.role_bindings.keys())
            )

            for role in sorted(common_roles):
                parent_code = parent_node.role_bindings[role]
                child_code = child_node.role_bindings[role]
                if parent_code != child_code:
                    findings.append(ReconciliationFinding(
                        code="ACCOUNT_ROLE_REMAPPED",
                        severity=CheckSeverity.ERROR,
                        message=(
                            f"Role '{role}' resolved to '{parent_code}' on "
                            f"{edge.parent_ref} but '{child_code}' on "
                            f"{edge.child_ref}"
                        ),
                        parent_ref=edge.parent_ref,
                        child_ref=edge.child_ref,
                        details={
                            "role": role,
                            "parent_account": parent_code,
                            "child_account": child_code,
                        },
                    ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # RC-3: Amount flow conservation
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_amount_flow(
        self,
        chain: LifecycleChain,
        tolerance: Decimal = _DEFAULT_AMOUNT_TOLERANCE,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-3: Value flowing through a chain must be conserved.

        For each parent node, the sum of child link amounts must not
        exceed the parent's amount (within tolerance).
        """
        findings: list[ReconciliationFinding] = []

        # Group edges by parent
        edges_by_parent: dict[ArtifactRef, list[LifecycleEdge]] = defaultdict(list)
        for edge in chain.edges:
            edges_by_parent[edge.parent_ref].append(edge)

        for parent_ref, edges in edges_by_parent.items():
            parent_node = chain.get_node(parent_ref)
            if parent_node is None or parent_node.amount is None:
                continue

            # Sum child amounts from link metadata
            children_with_amounts = [
                e for e in edges if e.link_amount is not None
            ]
            if not children_with_amounts:
                continue

            total_child = sum(
                (e.link_amount.amount for e in children_with_amounts),
                Decimal("0"),
            )
            parent_amount = parent_node.amount.amount

            if total_child > parent_amount + tolerance:
                findings.append(ReconciliationFinding(
                    code="AMOUNT_FLOW_VIOLATION",
                    severity=CheckSeverity.ERROR,
                    message=(
                        f"Children of {parent_ref} consume "
                        f"{total_child} but parent amount is "
                        f"{parent_amount} (exceeds by "
                        f"{total_child - parent_amount})"
                    ),
                    parent_ref=parent_ref,
                    child_ref=None,
                    details={
                        "parent_amount": str(parent_amount),
                        "total_child_amount": str(total_child),
                        "excess": str(total_child - parent_amount),
                        "child_count": len(children_with_amounts),
                    },
                ))

            # Check individual edges: child amount exceeds parent
            for edge in children_with_amounts:
                if edge.link_amount is not None:
                    if edge.link_amount.amount > parent_amount + tolerance:
                        findings.append(ReconciliationFinding(
                            code="AMOUNT_FLOW_VIOLATION",
                            severity=CheckSeverity.ERROR,
                            message=(
                                f"Single child {edge.child_ref} amount "
                                f"{edge.link_amount.amount} exceeds parent "
                                f"{parent_ref} amount {parent_amount}"
                            ),
                            parent_ref=parent_ref,
                            child_ref=edge.child_ref,
                            details={
                                "parent_amount": str(parent_amount),
                                "child_amount": str(edge.link_amount.amount),
                            },
                        ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # RC-4: Temporal monotonicity
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_temporal_ordering(
        self,
        chain: LifecycleChain,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-4: Child effective_date must not precede parent effective_date.

        Only checks FULFILLED_BY and PAID_BY edges (directional chains).
        """
        findings: list[ReconciliationFinding] = []

        for edge in chain.edges:
            if edge.link_type not in _FULFILLMENT_LINK_TYPES:
                continue

            parent_node = chain.get_node(edge.parent_ref)
            child_node = chain.get_node(edge.child_ref)

            if parent_node is None or child_node is None:
                continue
            if parent_node.effective_date is None or child_node.effective_date is None:
                continue

            if child_node.effective_date < parent_node.effective_date:
                findings.append(ReconciliationFinding(
                    code="TEMPORAL_ORDER_VIOLATION",
                    severity=CheckSeverity.WARNING,
                    message=(
                        f"Child {edge.child_ref} effective date "
                        f"{child_node.effective_date} precedes parent "
                        f"{edge.parent_ref} effective date "
                        f"{parent_node.effective_date}"
                    ),
                    parent_ref=edge.parent_ref,
                    child_ref=edge.child_ref,
                    details={
                        "parent_date": str(parent_node.effective_date),
                        "child_date": str(child_node.effective_date),
                        "link_type": edge.link_type.value,
                    },
                ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # RC-5: Chain completeness
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_chain_completeness(
        self,
        chain: LifecycleChain,
        as_of_date: date,
        aging_threshold_days: int = 90,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-5: Fulfillment chains should progress to terminal state.

        Flags nodes that have incoming FULFILLED_BY edges but no outgoing
        FULFILLED_BY or PAID_BY edges, and are older than the threshold.
        """
        findings: list[ReconciliationFinding] = []

        # Build sets: nodes with outgoing fulfillment edges
        has_outgoing: set[ArtifactRef] = set()
        has_incoming: set[ArtifactRef] = set()
        for edge in chain.edges:
            if edge.link_type in _FULFILLMENT_LINK_TYPES:
                has_outgoing.add(edge.parent_ref)
                has_incoming.add(edge.child_ref)

        # Leaf nodes: have incoming but no outgoing (terminal)
        # Root has outgoing but no incoming
        # Incomplete: intermediate nodes that should have children but don't
        for node in chain.nodes:
            ref = node.artifact_ref
            # Skip the root (it's the starting point)
            if ref == chain.root_ref and ref not in has_incoming:
                # Root with no outgoing at all
                if ref not in has_outgoing and node.effective_date is not None:
                    days_old = (as_of_date - node.effective_date).days
                    if days_old > aging_threshold_days:
                        findings.append(ReconciliationFinding(
                            code="CHAIN_INCOMPLETE",
                            severity=CheckSeverity.WARNING,
                            message=(
                                f"Root {ref} has no fulfillment children "
                                f"and is {days_old} days old"
                            ),
                            parent_ref=ref,
                            details={
                                "days_old": days_old,
                                "threshold": aging_threshold_days,
                            },
                        ))
                continue

            # Non-root leaf: has incoming but no outgoing
            if ref in has_incoming and ref not in has_outgoing:
                # Check if this is a payment (terminal node) -- skip
                if node.event_type and "payment" in node.event_type.lower():
                    continue

                if node.effective_date is not None:
                    days_old = (as_of_date - node.effective_date).days
                    if days_old > aging_threshold_days:
                        findings.append(ReconciliationFinding(
                            code="CHAIN_INCOMPLETE",
                            severity=CheckSeverity.WARNING,
                            message=(
                                f"Node {ref} has no outgoing fulfillment "
                                f"links and is {days_old} days old"
                            ),
                            parent_ref=ref,
                            details={
                                "days_old": days_old,
                                "threshold": aging_threshold_days,
                                "event_type": node.event_type,
                            },
                        ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # RC-6: Link-entry correspondence
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_link_entry_correspondence(
        self,
        chain: LifecycleChain,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-6: Every link endpoint must have a posted journal entry.

        Nodes with ``journal_entry_id=None`` are orphaned.
        """
        findings: list[ReconciliationFinding] = []

        # Collect all refs that appear in edges
        edge_refs: set[ArtifactRef] = set()
        for edge in chain.edges:
            edge_refs.add(edge.parent_ref)
            edge_refs.add(edge.child_ref)

        for ref in edge_refs:
            node = chain.get_node(ref)
            if node is None:
                findings.append(ReconciliationFinding(
                    code="ORPHANED_LINK",
                    severity=CheckSeverity.ERROR,
                    message=(
                        f"Link references artifact {ref} but no node "
                        f"exists in the chain"
                    ),
                    parent_ref=ref,
                ))
            elif not node.has_journal_entry:
                findings.append(ReconciliationFinding(
                    code="ORPHANED_LINK",
                    severity=CheckSeverity.ERROR,
                    message=(
                        f"Artifact {ref} has no posted journal entry"
                    ),
                    parent_ref=ref,
                    details={
                        "event_type": node.event_type,
                    },
                ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # RC-7: Allocation uniqueness
    # -----------------------------------------------------------------

    @traced_engine("lifecycle_reconciliation", "1.0", fingerprint_fields=("chain",))
    def check_allocation_uniqueness(
        self,
        chain: LifecycleChain,
        tolerance: Decimal = _DEFAULT_AMOUNT_TOLERANCE,
    ) -> tuple[ReconciliationFinding, ...]:
        """RC-7: No artifact should be over-allocated across multiple parents.

        Checks if a child artifact appears in multiple parent edges
        with the same link type, and the total consumed exceeds 100%
        of the child's amount.
        """
        findings: list[ReconciliationFinding] = []

        # Group: (child_ref, link_type) -> list of edges
        child_edges: dict[
            tuple[ArtifactRef, LinkType], list[LifecycleEdge]
        ] = defaultdict(list)
        for edge in chain.edges:
            child_edges[(edge.child_ref, edge.link_type)].append(edge)

        for (child_ref, link_type), edges in child_edges.items():
            if len(edges) <= 1:
                continue

            # Sum amounts from all parent edges pointing to this child
            amounts = [e.link_amount for e in edges if e.link_amount is not None]
            if not amounts:
                continue

            total_allocated = sum(
                (a.amount for a in amounts), Decimal("0"),
            )

            child_node = chain.get_node(child_ref)
            if child_node is not None and child_node.amount is not None:
                if total_allocated > child_node.amount.amount + tolerance:
                    findings.append(ReconciliationFinding(
                        code="DOUBLE_COUNT_RISK",
                        severity=CheckSeverity.ERROR,
                        message=(
                            f"Artifact {child_ref} allocated "
                            f"{total_allocated} across {len(edges)} "
                            f"{link_type.value} parents, but its amount "
                            f"is only {child_node.amount.amount}"
                        ),
                        child_ref=child_ref,
                        details={
                            "link_type": link_type.value,
                            "total_allocated": str(total_allocated),
                            "child_amount": str(child_node.amount.amount),
                            "parent_count": len(edges),
                        },
                    ))

        return tuple(findings)

    # -----------------------------------------------------------------
    # Orchestrator: run all checks
    # -----------------------------------------------------------------

    @traced_engine(
        "lifecycle_reconciliation", "1.0",
        fingerprint_fields=("chain", "as_of_date"),
    )
    def run_all_checks(
        self,
        chain: LifecycleChain,
        as_of_date: date,
        aging_threshold_days: int = 90,
        amount_tolerance: Decimal = _DEFAULT_AMOUNT_TOLERANCE,
    ) -> LifecycleCheckResult:
        """Run all 7 check categories and return aggregated result."""
        all_findings: list[ReconciliationFinding] = []
        checks: list[str] = []

        # RC-1
        all_findings.extend(self.check_policy_regime(chain=chain))
        checks.append("RC-1:policy_regime")

        # RC-2
        all_findings.extend(self.check_account_role_stability(chain=chain))
        checks.append("RC-2:account_role_stability")

        # RC-3
        all_findings.extend(
            self.check_amount_flow(chain=chain, tolerance=amount_tolerance),
        )
        checks.append("RC-3:amount_flow")

        # RC-4
        all_findings.extend(self.check_temporal_ordering(chain=chain))
        checks.append("RC-4:temporal_ordering")

        # RC-5
        all_findings.extend(self.check_chain_completeness(
            chain=chain,
            as_of_date=as_of_date,
            aging_threshold_days=aging_threshold_days,
        ))
        checks.append("RC-5:chain_completeness")

        # RC-6
        all_findings.extend(self.check_link_entry_correspondence(chain=chain))
        checks.append("RC-6:link_entry_correspondence")

        # RC-7
        all_findings.extend(
            self.check_allocation_uniqueness(chain=chain, tolerance=amount_tolerance),
        )
        checks.append("RC-7:allocation_uniqueness")

        return LifecycleCheckResult.from_findings(
            root_ref=chain.root_ref,
            findings=tuple(all_findings),
            nodes_checked=chain.node_count,
            edges_checked=chain.edge_count,
            checks_performed=tuple(checks),
            as_of_date=as_of_date,
        )


# =============================================================================
# Helpers
# =============================================================================


def _describe_regime_diffs(parent: LifecycleNode, child: LifecycleNode) -> str:
    """Describe which R21 columns differ between two nodes."""
    names = ("coa_version", "dim_schema_version", "rounding_version",
             "currency_reg_version", "posting_rule_version")
    diffs = []
    for name, pv, cv in zip(names, parent.regime_tuple, child.regime_tuple):
        if pv != cv:
            diffs.append(f"{name}: {pv} -> {cv}")
    return "; ".join(diffs) if diffs else "unknown"
