"""
Lifecycle reconciliation domain types -- GAP-REC.

Pure frozen dataclasses and enums for lifecycle chain analysis.
Used by LifecycleReconciliationChecker (pure engine) and
LifecycleReconciliationService (imperative shell).

Architecture: finance_engines/reconciliation -- pure domain, zero I/O.

Invariants supported:
    RC-1 through RC-7 (policy regime, account role, amount flow,
    temporal ordering, chain completeness, link-entry correspondence,
    allocation uniqueness).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from finance_kernel.domain.economic_link import ArtifactRef, LinkType
from finance_kernel.domain.values import Money


# =============================================================================
# Enums
# =============================================================================


class CheckSeverity(str, Enum):
    """Severity level of a reconciliation finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CheckStatus(str, Enum):
    """Overall status of a lifecycle check."""

    PASSED = "passed"
    FAILED = "failed"       # At least one ERROR finding
    WARNING = "warning"     # Warnings only, no errors


# =============================================================================
# Input types (populated by service, consumed by engine)
# =============================================================================


@dataclass(frozen=True)
class LifecycleNode:
    """One node in a lifecycle chain -- an artifact plus its journal metadata.

    The service layer populates these from DB queries. The engine receives
    them as immutable inputs.  A node with ``journal_entry_id=None``
    indicates an orphaned link endpoint (RC-6).
    """

    artifact_ref: ArtifactRef
    journal_entry_id: UUID | None = None
    event_type: str | None = None
    effective_date: date | None = None
    posted_at: datetime | None = None
    amount: Money | None = None

    # R21 snapshot columns
    coa_version: int | None = None
    dimension_schema_version: int | None = None
    rounding_policy_version: int | None = None
    currency_registry_version: int | None = None
    posting_rule_version: int | None = None

    # Optional: resolved account codes per role (from snapshot)
    role_bindings: Mapping[str, str] | None = None

    @property
    def has_journal_entry(self) -> bool:
        """True if this node has a corresponding posted journal entry."""
        return self.journal_entry_id is not None

    @property
    def regime_tuple(self) -> tuple[int | None, ...]:
        """R21 version columns as a comparable tuple."""
        return (
            self.coa_version,
            self.dimension_schema_version,
            self.rounding_policy_version,
            self.currency_registry_version,
            self.posting_rule_version,
        )

    @property
    def has_regime_data(self) -> bool:
        """True if at least one R21 column is populated."""
        return any(v is not None for v in self.regime_tuple)


@dataclass(frozen=True)
class LifecycleEdge:
    """One edge in the lifecycle chain -- an economic link."""

    link_type: LinkType
    parent_ref: ArtifactRef
    child_ref: ArtifactRef
    link_amount: Money | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class LifecycleChain:
    """Complete lifecycle graph for one root artifact.

    Contains all nodes reachable from the root through economic links,
    plus the edges connecting them.
    """

    root_ref: ArtifactRef
    nodes: tuple[LifecycleNode, ...] = ()
    edges: tuple[LifecycleEdge, ...] = ()

    def get_node(self, ref: ArtifactRef) -> LifecycleNode | None:
        """Find a node by its artifact ref."""
        for node in self.nodes:
            if node.artifact_ref == ref:
                return node
        return None

    def get_children_edges(self, parent_ref: ArtifactRef) -> tuple[LifecycleEdge, ...]:
        """Get all edges where parent_ref is the parent."""
        return tuple(e for e in self.edges if e.parent_ref == parent_ref)

    def get_parent_edges(self, child_ref: ArtifactRef) -> tuple[LifecycleEdge, ...]:
        """Get all edges where child_ref is the child."""
        return tuple(e for e in self.edges if e.child_ref == child_ref)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


# =============================================================================
# Output types
# =============================================================================


@dataclass(frozen=True)
class ReconciliationFinding:
    """One specific issue found during lifecycle check.

    Each finding has a machine-readable ``code`` (e.g., POLICY_REGIME_DRIFT),
    a severity level, a human-readable message, and optional context
    identifying the specific edge or nodes involved.
    """

    code: str
    severity: CheckSeverity
    message: str
    parent_ref: ArtifactRef | None = None
    child_ref: ArtifactRef | None = None
    details: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LifecycleCheckResult:
    """Complete result of a lifecycle reconciliation check.

    Aggregates all findings from all check categories.
    ``status`` is derived from the highest-severity finding.
    """

    root_ref: ArtifactRef
    status: CheckStatus
    findings: tuple[ReconciliationFinding, ...] = ()
    nodes_checked: int = 0
    edges_checked: int = 0
    checks_performed: tuple[str, ...] = ()
    as_of_date: date | None = None

    @property
    def is_clean(self) -> bool:
        """True if no findings of any severity."""
        return len(self.findings) == 0

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == CheckSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == CheckSeverity.WARNING)

    @classmethod
    def from_findings(
        cls,
        root_ref: ArtifactRef,
        findings: tuple[ReconciliationFinding, ...],
        nodes_checked: int,
        edges_checked: int,
        checks_performed: tuple[str, ...],
        as_of_date: date,
    ) -> LifecycleCheckResult:
        """Factory that derives status from findings."""
        has_error = any(f.severity == CheckSeverity.ERROR for f in findings)
        has_warning = any(f.severity == CheckSeverity.WARNING for f in findings)

        if has_error:
            status = CheckStatus.FAILED
        elif has_warning:
            status = CheckStatus.WARNING
        else:
            status = CheckStatus.PASSED

        return cls(
            root_ref=root_ref,
            status=status,
            findings=findings,
            nodes_checked=nodes_checked,
            edges_checked=edges_checked,
            checks_performed=checks_performed,
            as_of_date=as_of_date,
        )
