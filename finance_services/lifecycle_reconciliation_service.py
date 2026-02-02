"""
LifecycleReconciliationService -- Service wrapper for lifecycle chain checks.

Composes LinkGraphService (graph traversal), JournalSelector (R21 queries),
and the pure LifecycleReconciliationChecker engine.

Architecture: finance_services -- imperative shell.
    The service collects data from the DB via JournalSelector and builds
    a LifecycleChain, then delegates to the pure engine for analysis.

Invariants enforced:
    RC-1 through RC-7 via LifecycleReconciliationChecker.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from finance_kernel.domain.clock import Clock, SystemClock
from finance_kernel.domain.economic_link import ArtifactRef, EconomicLink, LinkType
from finance_kernel.domain.values import Money
from finance_kernel.logging_config import get_logger
from finance_kernel.selectors.journal_selector import JournalSelector
from finance_kernel.services.link_graph_service import LinkGraphService

from finance_engines.reconciliation.checker import LifecycleReconciliationChecker
from finance_engines.reconciliation.lifecycle_types import (
    LifecycleChain,
    LifecycleCheckResult,
    LifecycleEdge,
    LifecycleNode,
)

logger = get_logger("services.lifecycle_reconciliation")


class LifecycleReconciliationService:
    """Service that builds lifecycle chains from the DB and checks them.

    Contract:
        - ``build_chain()`` walks the link graph and populates nodes with
          journal entry R21 metadata.
        - ``check_artifact()`` builds a chain and runs all checks.
        - ``check_artifacts()`` runs checks on multiple roots.

    Non-goals:
        - Does NOT persist check results (caller decides).
        - Does NOT modify any data (read-only).
    """

    def __init__(
        self,
        session: Session,
        link_graph: LinkGraphService,
        clock: Clock | None = None,
        checker: LifecycleReconciliationChecker | None = None,
        aging_threshold_days: int = 90,
        amount_tolerance: Decimal = Decimal("0.01"),
    ) -> None:
        self._session = session
        self._link_graph = link_graph
        self._journal_selector = JournalSelector(session)
        self._clock = clock or SystemClock()
        self._checker = checker or LifecycleReconciliationChecker()
        self._aging_threshold_days = aging_threshold_days
        self._amount_tolerance = amount_tolerance

    # -----------------------------------------------------------------
    # Chain building
    # -----------------------------------------------------------------

    def build_chain(self, root_ref: ArtifactRef) -> LifecycleChain:
        """Walk the link graph from root_ref and build a LifecycleChain.

        Traverses all children recursively, collects nodes with their
        journal entry R21 metadata, and edges from economic links.
        """
        visited_refs: set[ArtifactRef] = set()
        nodes: list[LifecycleNode] = []
        edges: list[LifecycleEdge] = []

        self._walk(root_ref, visited_refs, nodes, edges)

        return LifecycleChain(
            root_ref=root_ref,
            nodes=tuple(nodes),
            edges=tuple(edges),
        )

    def _walk(
        self,
        ref: ArtifactRef,
        visited: set[ArtifactRef],
        nodes: list[LifecycleNode],
        edges: list[LifecycleEdge],
    ) -> None:
        """Recursively walk the link graph collecting nodes and edges."""
        if ref in visited:
            return
        visited.add(ref)

        # Build node from journal entry metadata
        node = self._build_node(ref)
        nodes.append(node)

        # Get all children
        children: list[EconomicLink] = self._link_graph.get_children(ref)
        for link in children:
            child_ref = link.child_ref

            # Build edge
            link_amount = _extract_amount(link)
            edges.append(LifecycleEdge(
                link_type=link.link_type,
                parent_ref=link.parent_ref,
                child_ref=child_ref,
                link_amount=link_amount,
                created_at=link.created_at,
            ))

            # Recurse into child
            self._walk(child_ref, visited, nodes, edges)

    def _build_node(self, ref: ArtifactRef) -> LifecycleNode:
        """Query journal entry for R21 metadata and build a LifecycleNode."""
        # Find the posted journal entry for this artifact via JournalSelector
        # The artifact_id maps to source_event_id on journal entries
        dto = self._journal_selector.get_posted_entry_by_event(ref.artifact_id)

        if dto is None:
            return LifecycleNode(artifact_ref=ref)

        return LifecycleNode(
            artifact_ref=ref,
            journal_entry_id=dto.id,
            event_type=dto.source_event_type,
            effective_date=dto.effective_date,
            posted_at=dto.posted_at,
            coa_version=dto.coa_version,
            dimension_schema_version=dto.dimension_schema_version,
            rounding_policy_version=dto.rounding_policy_version,
            currency_registry_version=dto.currency_registry_version,
            posting_rule_version=dto.posting_rule_version,
        )

    # -----------------------------------------------------------------
    # Check operations
    # -----------------------------------------------------------------

    def check_artifact(
        self,
        root_ref: ArtifactRef,
        as_of_date: date | None = None,
    ) -> LifecycleCheckResult:
        """Build a chain from root_ref and run all checks."""
        chain = self.build_chain(root_ref)
        effective_date = as_of_date or self._clock.now().date()

        return self._checker.run_all_checks(
            chain=chain,
            as_of_date=effective_date,
            aging_threshold_days=self._aging_threshold_days,
            amount_tolerance=self._amount_tolerance,
        )

    def check_artifacts(
        self,
        refs: list[ArtifactRef],
        as_of_date: date | None = None,
    ) -> list[LifecycleCheckResult]:
        """Check multiple root artifacts."""
        return [self.check_artifact(ref, as_of_date) for ref in refs]


# =============================================================================
# Helpers
# =============================================================================


def _extract_amount(link: EconomicLink) -> Money | None:
    """Extract Money amount from link metadata if present."""
    if link.metadata is None:
        return None

    # Try common metadata keys
    for key in ("amount_applied", "amount", "total_amount"):
        raw = link.metadata.get(key)
        if raw is not None:
            try:
                amount = Decimal(str(raw))
                currency = link.metadata.get("currency", "USD")
                return Money.of(amount, str(currency))
            except Exception:
                continue

    return None
